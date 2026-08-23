#!/usr/bin/env python3
"""M1 — the decode-side provenance gates were name-keyed, not content-bound.

The DECODE direction (JXL -> JPEG lossless) keeps the ORIGINAL JPEG's md5 in
checksums.md5, keyed by the JXL's NAME. Both pre-fix gates compared that
stored hash against the JPEG on disk and never looked at the JXL's own bytes:

  * the --delete-skipped gate in process_group_transcode (was_skipped branch)
  * _provenance_filter(decode_lossless=True)

So replacing photo.jxl with a DIFFERENT same-named JXL passed both gates: the
old JPEG still matched the stored hash, and the replacement JXL — never
archived anywhere — was deleted (or had the old output overwritten).

The fix binds both gates to the JXL's CONTENT:

  * the encoder now also stores the JXL's OWN md5 under "<name>.jxl-md5"
    (a suffixed key read_md5_db's exact-name lookups can never return, so old
    databases and plain-name lookups are unaffected);
  * decode-side gates compare that self-hash against the current file, and on
    legacy databases without it fall back to djxl --reconstruct_jpeg into a
    temp file, comparing the reconstruction against the archived JPEG;
  * a mismatch — or a proof that cannot run at all (djxl<0.12, tool error) —
    fails CLOSED: KEEP / refuse.
"""

import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr


class _FakeRun:
    def __init__(self, stdout="", stderr=b"", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture(autouse=True)
def _reset_globals():
    yield
    tr.DELETE_SOURCE = False
    tr.DELETE_SKIPPED = False
    tr.TEMP2_DIR = None
    tr.STORE_MD5 = True


ORIGINAL_JPEG = b"\xff\xd8" + b"original-jpeg-payload" + b"\xff\xd9"
REAL_JXL = b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"real-jxl-bytes"
SWAPPED_JXL = b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"swapped-different-jxl"


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _make_newer(target: Path, than: Path):
    stamp = than.stat().st_mtime + 100
    os.utime(target, (stamp, stamp))


def _write_db(folder: Path, jxl_name: str, *, jpeg_md5, self_hash=None):
    lines = []
    if jpeg_md5 is not None:
        lines.append(f"{jpeg_md5}  {jxl_name}")
    if self_hash is not None:
        lines.append(f"{self_hash}  {jxl_name}{tr.JXL_SELF_HASH_SUFFIX}")
    (folder / tr.CHECKSUMS_FILENAME).write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")


def _decode_skip_run(tmp_path, monkeypatch, *, jxl_bytes, self_hash="absent",
                     reconstruct_bytes=None, djxl_new=True):
    """One JXL whose recovered JPEG already exists -> SKIP on the decode path.

    The existing photo.jpg holds the ORIGINAL JPEG's bytes, so the name-keyed
    stored-hash check passes. Only content-binding can tell whether the JXL
    on disk (jxl_bytes) is the one that made it.

    self_hash: "match" | "wrong" | "absent" — the <name>.jxl-md5 db entry.
    reconstruct_bytes: what the mocked djxl --reconstruct_jpeg writes to its
    output argument (only consulted when self_hash == "absent").
    """
    src = tmp_path / "photo.jxl"
    src.write_bytes(jxl_bytes)
    final = tmp_path / "photo.jpg"
    final.write_bytes(ORIGINAL_JPEG)
    _make_newer(final, src)  # smart mode skips an up-to-date output

    stored_jpeg_md5 = _md5(ORIGINAL_JPEG)
    self_entry = ("absent" if self_hash == "absent"
                  else _md5(jxl_bytes) if self_hash == "match"
                  else "0" * 32)
    _write_db(tmp_path, src.name, jpeg_md5=stored_jpeg_md5,
              self_hash=None if self_entry == "absent" else self_entry)

    tr.setup_logger()
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "DELETE_SKIPPED", True)
    monkeypatch.setattr(tr, "DELETE_SOURCE_REQUIRE_MD5", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "_delete_stats",
                        {"deleted": 0, "deleted_archived": 0, "kept": 0})
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    monkeypatch.setattr(tr, "_tool_at_least", lambda *a: djxl_new)

    def fake_djxl(cmd, **kw):
        assert cmd[0] == "djxl"
        Path(cmd[-1]).write_bytes(reconstruct_bytes or b"")
        return _FakeRun()

    monkeypatch.setattr(tr.subprocess, "run", fake_djxl)

    tr.process_group_transcode([(src, final)], 1, decode=True, verify=True,
                               mode=3, reconvert_val=False, smart=True)
    return src


# ---------------------------------------------------------------------------
# 1. Swapped same-named JXL, self-hash stored -> KEEP (was: deleted)
# ---------------------------------------------------------------------------

def test_swapped_jxl_kept_when_self_hash_stored(tmp_path, monkeypatch):
    """checksums.md5 was written when REAL_JXL was archived; the JXL on disk
    is now SWAPPED_JXL. The self-hash mismatch must KEEP it."""
    src = tmp_path / "photo.jxl"
    src.write_bytes(SWAPPED_JXL)
    final = tmp_path / "photo.jpg"
    final.write_bytes(ORIGINAL_JPEG)
    _make_newer(final, src)
    _write_db(tmp_path, src.name, jpeg_md5=_md5(ORIGINAL_JPEG),
              self_hash=_md5(REAL_JXL))

    tr.setup_logger()
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "DELETE_SKIPPED", True)
    monkeypatch.setattr(tr, "DELETE_SOURCE_REQUIRE_MD5", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "_delete_stats",
                        {"deleted": 0, "deleted_archived": 0, "kept": 0})
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)

    tr.process_group_transcode([(src, final)], 1, decode=True, verify=True,
                               mode=3, reconvert_val=False, smart=True)
    assert src.exists(), "a swapped same-named JXL was DELETED"
    assert tr._delete_stats["kept"] == 1


# ---------------------------------------------------------------------------
# 2. Legacy db (no self-hash) -> djxl --reconstruct_jpeg fallback, KEEP on
#    mismatch
# ---------------------------------------------------------------------------

def test_legacy_db_reconstruct_mismatch_keeps(tmp_path, monkeypatch):
    src = _decode_skip_run(tmp_path, monkeypatch, jxl_bytes=SWAPPED_JXL,
                           self_hash="absent",
                           reconstruct_bytes=b"\xff\xd8not-the-original\xff\xd9")
    assert src.exists(), "reconstruction does not match the archived JPEG, yet the JXL was DELETED"
    assert tr._delete_stats["kept"] == 1


def test_legacy_db_reconstruct_match_still_deletes(tmp_path, monkeypatch):
    """The fallback is a proof, not a roadblock: a JXL that really
    reconstructs to the archived JPEG is still deletable."""
    src = _decode_skip_run(tmp_path, monkeypatch, jxl_bytes=REAL_JXL,
                           self_hash="absent",
                           reconstruct_bytes=ORIGINAL_JPEG)
    assert not src.exists()


def test_legacy_db_without_djxl_012_fails_closed(tmp_path, monkeypatch):
    """No self-hash and no --reconstruct_jpeg: the proof cannot run, so the
    irreversible step must not happen."""
    src = _decode_skip_run(tmp_path, monkeypatch, jxl_bytes=REAL_JXL,
                           self_hash="absent", djxl_new=False)
    assert src.exists()
    assert tr._delete_stats["kept"] == 1


# ---------------------------------------------------------------------------
# 3. A matching JXL still deletes exactly as before
# ---------------------------------------------------------------------------

def test_matching_jxl_self_hash_still_deletes(tmp_path, monkeypatch):
    src = _decode_skip_run(tmp_path, monkeypatch, jxl_bytes=REAL_JXL,
                           self_hash="match")
    assert not src.exists()
    assert tr._delete_stats["deleted"] == 1
    assert tr._delete_stats["deleted_archived"] == 1


# ---------------------------------------------------------------------------
# 4. _provenance_filter(decode_lossless=True) refuses a swapped JXL
# ---------------------------------------------------------------------------

def _provenance_setup(tmp_path, monkeypatch, *, jxl_on_disk, jxl_in_db):
    src = tmp_path / "photo.jxl"
    src.write_bytes(jxl_on_disk)
    out = tmp_path / "out" / "photo.jpg"
    out.parent.mkdir()
    out.write_bytes(ORIGINAL_JPEG)
    _write_db(tmp_path, src.name, jpeg_md5=_md5(ORIGINAL_JPEG),
              self_hash=_md5(jxl_in_db))

    tr.setup_logger()
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    return src, out


def test_provenance_filter_refuses_swapped_jxl(tmp_path, monkeypatch):
    src, out = _provenance_setup(tmp_path, monkeypatch,
                                 jxl_on_disk=SWAPPED_JXL, jxl_in_db=REAL_JXL)
    kept, refused = tr._provenance_filter([(src, out)], 2, decode_lossless=True)
    assert kept == []
    assert len(refused) == 1 and refused[0][0] == src


def test_provenance_filter_keeps_the_real_jxl(tmp_path, monkeypatch):
    src, out = _provenance_setup(tmp_path, monkeypatch,
                                 jxl_on_disk=REAL_JXL, jxl_in_db=REAL_JXL)
    kept, refused = tr._provenance_filter([(src, out)], 2, decode_lossless=True)
    assert refused == []
    assert kept == [(src, out)]


# ---------------------------------------------------------------------------
# db key scheme: the suffixed key never interferes with plain-name lookups
# ---------------------------------------------------------------------------

def test_self_hash_key_does_not_shadow_plain_lookup(tmp_path):
    jxl = tmp_path / "photo.jxl"
    tr.store_md5_db(jxl, "a" * 32)
    tr.store_jxl_self_hash_db(jxl, "b" * 32)
    assert tr.read_md5_db(jxl) == "a" * 32
    assert tr.read_jxl_self_hash_db(jxl) == "b" * 32


def test_self_hash_read_is_none_on_legacy_db(tmp_path):
    jxl = tmp_path / "photo.jxl"
    tr.store_md5_db(jxl, "a" * 32)
    assert tr.read_jxl_self_hash_db(jxl) is None
