#!/usr/bin/env python3
"""Bugs #287-#289 — drift between the backends, and what it cost.

  * #287 — the transcoder filed staging checksums by output FILENAME. Two
    sources with the same basename (a/photo.jpg and b/photo.jpg, both becoming
    photo.jxl in different destination folders) collapsed to one key: both
    hashes went to one folder, the other got none. The "unmatched" fallback
    appended lines to the first successful task's folder, so a later decode
    there verifies a good file against a foreign hash and reports MD5-FAIL.
  * #288 — the transcoder's libjxl gate tested (0, 11) while its message named
    0.11.2 as the minimum, so cjxl 0.11.0/0.11.1 passed in silence there and
    were warned about by the encoder and decoder. Its _get_exiftool_cmd also
    ordered the candidates differently, so a machine with both exiftool-k and
    exiftool(-k) got a different binary from this script than from the others.
  * #289 — --mode 1 ignores the output positional. The encoder and decoder have
    warned about that since v1.9.3; the transcoder never did.
"""

import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
TRANSCODER = str(REPO / "jxl_jpeg_transcoder.py")


def _tr_run(*args, cwd):
    return subprocess.run([sys.executable, TRANSCODER, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=600, cwd=str(cwd), stdin=subprocess.DEVNULL)


def _jpeg(path: Path, seed: int):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:64, 0:64]
    a = ((np.sin((x + seed) / 7.0) * .25 + np.cos((y + seed) / 9.0) * .25 + .5) * 255)
    Image.fromarray(np.stack([a.astype(np.uint8)] * 3, axis=2)).save(str(path), quality=92)


# ── #287: checksums belong to the folder their output landed in ─────────────

def _db_names(folder: Path):
    db = folder / tr.CHECKSUMS_FILENAME
    if not db.exists():
        return []
    return [ln.split("  ", 1)[1].strip()
            for ln in db.read_text(encoding="utf-8").splitlines() if "  " in ln]


def test_same_named_outputs_each_get_their_own_checksum(tmp_path):
    """Mode 3 keeps the folder structure, so a/photo.jpg and b/photo.jpg both
    produce photo.jxl — in different folders."""
    staging = tmp_path / "stg"
    staging.mkdir()
    _jpeg(tmp_path / "root" / "a" / "photo.jpg", 0)
    _jpeg(tmp_path / "root" / "b" / "photo.jpg", 40)

    r = _tr_run("root", "--force-transcode", "--mode", "3", "--staging", str(staging),
                cwd=tmp_path)
    assert r.returncode == 0, r.stdout

    for sub in ("a", "b"):
        out_dir = next(p.parent for p in (tmp_path / "root" / sub).rglob("photo.jxl"))
        names = _db_names(out_dir)
        # The ".jxl-md5" companion line (the JXL's own hash, content-binding
        # for the decode-side delete gates) is expected alongside the output's
        # own checksum — but no FOREIGN output's line may appear here (#287).
        plain = [n for n in names if not n.endswith(tr.JXL_SELF_HASH_SUFFIX)]
        companions = [n for n in names if n.endswith(tr.JXL_SELF_HASH_SUFFIX)]
        assert plain == ["photo.jxl"] and companions == [f"photo.jxl{tr.JXL_SELF_HASH_SUFFIX}"], (
            f"{sub}: checksums.md5 holds {names}")


def test_no_checksum_is_filed_for_a_file_that_is_not_there(tmp_path):
    """The old fallback appended unmatched lines to the first successful task's
    folder, claiming coverage of a file that folder never received."""
    staging = tmp_path / "stg"
    staging.mkdir()
    _jpeg(tmp_path / "root" / "a" / "photo.jpg", 0)
    _jpeg(tmp_path / "root" / "b" / "photo.jpg", 40)
    assert _tr_run("root", "--force-transcode", "--mode", "3",
                   "--staging", str(staging), cwd=tmp_path).returncode == 0

    for db in (tmp_path / "root").rglob(tr.CHECKSUMS_FILENAME):
        names = _db_names(db.parent)
        for name in names:
            # A ".jxl-md5" companion line vouches for the JXL it is SUFFIXED
            # after — the file it claims coverage of is the un-suffixed name.
            target = (name[:-len(tr.JXL_SELF_HASH_SUFFIX)]
                      if name.endswith(tr.JXL_SELF_HASH_SUFFIX) else name)
            assert (db.parent / target).exists(), (
                f"{db} claims a checksum for {name}, which is not in that folder")
        # One plain line per output, not two: the collapsed key used to file
        # BOTH sources' hashes here, and read_md5_db returns the last one — a
        # foreign hash for this folder's file.
        plain = [n for n in names if not n.endswith(tr.JXL_SELF_HASH_SUFFIX)]
        assert len(plain) == len(set(plain)) == len(list(db.parent.glob("*.jxl"))), (
            f"{db} holds {names}")


def test_the_staging_checksum_db_is_cleaned_up(tmp_path):
    staging = tmp_path / "stg"
    staging.mkdir()
    _jpeg(tmp_path / "root" / "a" / "photo.jpg", 0)
    assert _tr_run("root", "--force-transcode", "--mode", "3",
                   "--staging", str(staging), cwd=tmp_path).returncode == 0
    assert not (staging / tr.CHECKSUMS_FILENAME).exists()


def test_a_stored_checksum_is_the_one_the_decode_side_reads_back(tmp_path):
    """End to end: the hash filed by the encode must verify the recovery."""
    staging = tmp_path / "stg"
    staging.mkdir()
    _jpeg(tmp_path / "root" / "a" / "photo.jpg", 0)
    _jpeg(tmp_path / "root" / "b" / "photo.jpg", 40)
    assert _tr_run("root", "--force-transcode", "--mode", "3",
                   "--staging", str(staging), cwd=tmp_path).returncode == 0

    for sub in ("a", "b"):
        jxl = next((tmp_path / "root" / sub).rglob("photo.jxl"))
        r = _tr_run(str(jxl.parent), "--force-transcode", "--mode", "3", cwd=tmp_path)
        assert "MD5-FAIL" not in r.stdout, r.stdout


# ── #288: the gates the three scripts must agree on ─────────────────────────

@pytest.mark.parametrize("mod", [enc, dec], ids=["encoder", "decoder"])
def test_every_script_checks_the_patch_level_of_the_minimum(mod, monkeypatch):
    """0.11.2 is the documented minimum: 0.11.1 is warned about, 0.11.2 is not
    (the behaviour, not the text of the check)."""
    warned = []
    monkeypatch.setattr(mod.logger, "warning", lambda m, *a, **k: warned.append(str(m)))
    for version, expect in (("0.11.1", True), ("0.11.2", False)):
        warned.clear()
        if mod is enc:
            monkeypatch.setattr(enc, "_tool_version",
                                lambda exe, v=version: tuple(int(x) for x in v.split(".")))
        else:
            monkeypatch.setattr(dec.subprocess, "run", lambda *a, v=version, **k:
                                types.SimpleNamespace(stdout=f"djxl v{v} [AVX2]", stderr=""))
        mod._warn_if_libjxl_too_old("cjxl" if mod is enc else "djxl")
        assert bool(warned) is expect, (version, warned)


def test_the_transcoder_checks_the_patch_level_of_the_minimum(tmp_path, monkeypatch, capsys):
    """The transcoder's copy is in main(): 0.11.1 is warned about, 0.11.2 is
    not (it used to test major.minor only, so 0.11.0/0.11.1 passed in silence)."""
    src = tmp_path / "250118_吾妻山 [FINAL] 100%off"
    src.mkdir()
    monkeypatch.setattr(tr.shutil, "which", lambda c, *a, **k: "C:/tools/" + str(c))
    for name in ("cmd_transcode", "cmd_convert", "cmd_auto"):
        monkeypatch.setattr(tr, name, lambda *a, **k: (0, False))
    for version, expect in (((0, 11, 1), True), ((0, 11, 2), False)):
        monkeypatch.setattr(tr, "_tool_version", lambda exe, v=version: v)
        monkeypatch.setattr(sys, "argv", ["jxl_jpeg_transcoder.py", str(src),
                                          str(tmp_path / "out")])
        try:
            tr.main()
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert ("older than the supported minimum (0.11.2)" in out) is expect, out[-1500:]


@pytest.mark.parametrize("mod", [enc, dec, tr], ids=["encoder", "decoder", "transcoder"])
def test_exiftool_candidates_are_ordered_the_same_everywhere(mod, monkeypatch):
    """A machine carrying both exiftool-k and exiftool(-k) must not get a
    different binary depending on which script is running."""
    seen = []
    monkeypatch.setattr(mod.shutil, "which", lambda c: seen.append(c) or None)
    monkeypatch.setattr(mod, "_exiftool_cmd", None)
    mod._get_exiftool_cmd()
    assert seen == ["exiftool", "exiftool-k", "exiftool(-k)"]


# ── #289: the mode-1 warning ────────────────────────────────────────────────

def test_transcoder_warns_that_mode_1_ignores_the_output(tmp_path):
    _jpeg(tmp_path / "root" / "photo.jpg", 0)
    r = _tr_run("root", "elsewhere", "--force-transcode", "--mode", "1", cwd=tmp_path)
    assert "--mode 1 ignores the output folder" in r.stdout, r.stdout


def test_transcoder_says_nothing_when_no_output_was_given(tmp_path):
    _jpeg(tmp_path / "root" / "photo.jpg", 0)
    r = _tr_run("root", "--force-transcode", "--mode", "1", cwd=tmp_path)
    assert "ignores the output folder" not in r.stdout
