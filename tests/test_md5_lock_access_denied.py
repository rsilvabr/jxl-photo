"""#547: the transcoder's checksums.md5 lock gave up on ACCESS DENIED.

The lock is a sibling file created O_EXCL. On Windows a create on a lock file
another writer is still deleting ("delete pending") fails with ACCESS DENIED
(PermissionError), not "exists". That was treated as a hard failure: the
provenance line was dropped (fail closed) and a deleting run then KEPT a JPEG
whose JXL was fine ("KEEP (MD5 not confirmed)") — two of 25 in one battery run
of jxl-photo-exe. It now waits like for a held lock, within the same timeout,
and still fails closed when the lock never comes. Folder named with Japanese,
brackets, a space and %.
"""
import logging
import os
import threading

import pytest

import jxl_jpeg_transcoder as tra

HOSTILE = "吾妻山 [lock] 100%"


@pytest.fixture
def db(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    return d / tra.CHECKSUMS_FILENAME


def test_a_lock_being_deleted_is_waited_for(db, monkeypatch, caplog):
    """ACCESS DENIED twice (the other writer's delete still pending), then
    the create succeeds: the line is written and nothing is logged."""
    real_open = os.open
    denied = {"n": 2}

    def flaky_open(path, flags, *a, **k):
        if str(path).endswith(".lock") and denied["n"]:
            denied["n"] -= 1
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(tra.os, "open", flaky_open)
    with caplog.at_level(logging.WARNING, logger=tra.logger.name):
        tra._append_checksum_line(db, "0123  a (1).jxl\n")
    assert db.read_text(encoding="utf-8") == "0123  a (1).jxl\n"
    assert denied["n"] == 0
    assert not caplog.records
    assert not db.with_name(db.name + ".lock").exists()


def test_a_lock_never_granted_still_fails_closed(db, monkeypatch, caplog):
    def denied_open(path, flags, *a, **k):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(tra, "_MD5_LOCK_TIMEOUT_S", 0.3)
    monkeypatch.setattr(tra.os, "open", denied_open)
    with caplog.at_level(logging.WARNING, logger=tra.logger.name):
        tra._append_checksum_line(db, "0123  a (1).jxl\n")
    assert not db.exists()
    assert any("NOT recorded" in r.getMessage() for r in caplog.records)


def test_many_writers_lose_no_line(db, caplog):
    """The real race: 16 threads appending to one checksums.md5. Before the
    fix about half of such runs lost a line or more to ACCESS DENIED."""
    n, k = 16, 60

    def work(i):
        for j in range(k):
            tra._append_checksum_line(db, f"{i:02d}{j:03d}  f{i}_{j}.jxl\n")

    with caplog.at_level(logging.WARNING, logger=tra.logger.name):
        ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    assert len(db.read_text(encoding="utf-8").splitlines()) == n * k
    assert not caplog.records
