"""The transcoder's delete gates, tested directly (refactor plan 261113, A2).

_delete_transcoded_sources (the lossless JPEG <-> JXL gate) and
_delete_converted_sources (the lossy gate, shared by --force-convert and the
auto mode's convert since its two copies became one function). Each test
runs ONE real transcoder run (real cjxl/djxl/exiftool; a folder named with
Japanese, brackets, a space and %) with the gate replaced by a recorder, then
calls the real gate with the recorded arguments after one controlled change.
"""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr  # noqa: E402

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off"


def _image(path: Path, fmt: str) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (64, 48))
    img.putdata([((x * 4) % 256, (y * 5) % 256, (x * y) % 256) for y in range(48) for x in range(64)])
    img.save(path, fmt, **({"quality": 90} if fmt == "JPEG" else {}))


def _recorded_run(tmp_path, monkeypatch, gate_name, src_name, fmt, extra=()):
    src = tmp_path / HOSTILE / "in"
    _image(src / src_name, fmt)
    out = tmp_path / HOSTILE / "out"
    seen = {}
    monkeypatch.setattr(tr, gate_name, lambda *a: seen.setdefault("args", a))
    monkeypatch.setattr(sys, "argv", ["jxl_jpeg_transcoder.py", str(src), str(out), "--workers", "1",
                                      "--delete-source", "--delete-confirm-off", *extra])
    try:
        tr.main()
    except SystemExit:
        pass
    monkeypatch.undo()
    assert "args" in seen, f"{gate_name} was never reached: the test proves nothing"
    for key in tr._delete_stats:
        tr._delete_stats[key] = 0
    return src, out, seen["args"]


# --- the lossless gate (JPEG -> JXL, jbrd) ------------------------------------

def _transcode(tmp_path, monkeypatch):
    return _recorded_run(tmp_path, monkeypatch, "_delete_transcoded_sources",
                         "DSC_0001 (2).jpg", "JPEG", ("--force-transcode",))


def test_transcode_a_proven_source_is_deleted(tmp_path, monkeypatch):
    src, out, args = _transcode(tmp_path, monkeypatch)
    tr._delete_transcoded_sources(*args)
    assert not (src / "DSC_0001 (2).jpg").exists()
    assert (out / "DSC_0001 (2).jxl").exists()
    assert tr._delete_stats["deleted"] == 1 and tr._delete_stats["kept"] == 0


def test_transcode_a_missing_output_keeps_the_source(tmp_path, monkeypatch, caplog):
    src, out, args = _transcode(tmp_path, monkeypatch)
    (out / "DSC_0001 (2).jxl").unlink()
    tr._delete_transcoded_sources(*args)
    assert (src / "DSC_0001 (2).jpg").exists()
    assert tr._delete_stats["deleted"] == 0 and tr._delete_stats["kept"] == 1
    assert "KEEP (final output missing)" in caplog.text


def test_transcode_a_damaged_output_keeps_the_source(tmp_path, monkeypatch):
    src, out, args = _transcode(tmp_path, monkeypatch)
    jxl = out / "DSC_0001 (2).jxl"
    jxl.write_bytes(jxl.read_bytes()[: jxl.stat().st_size // 2])
    tr._delete_transcoded_sources(*args)
    assert (src / "DSC_0001 (2).jpg").exists()
    assert tr._delete_stats["deleted"] == 0 and tr._delete_stats["kept"] == 1


# --- the lossy gate (PNG -> JXL) -------------------------------------------------

def _convert(tmp_path, monkeypatch):
    return _recorded_run(tmp_path, monkeypatch, "_delete_converted_sources",
                         "shot [b] (1).png", "PNG", ("--force-convert",))


def test_convert_a_proven_source_is_deleted(tmp_path, monkeypatch):
    src, out, args = _convert(tmp_path, monkeypatch)
    tr._delete_converted_sources(*args)
    assert not (src / "shot [b] (1).png").exists()
    assert (out / "shot [b] (1).jxl").exists()
    assert tr._delete_stats["deleted"] == 1 and tr._delete_stats["kept"] == 0


def test_convert_a_damaged_output_keeps_the_source(tmp_path, monkeypatch):
    src, out, args = _convert(tmp_path, monkeypatch)
    jxl = out / "shot [b] (1).jxl"
    jxl.write_bytes(jxl.read_bytes()[: jxl.stat().st_size // 2])
    tr._delete_converted_sources(*args)
    assert (src / "shot [b] (1).png").exists()
    assert tr._delete_stats["deleted"] == 0 and tr._delete_stats["kept"] == 1


def test_transcode_an_output_that_does_not_reconstruct_keeps_the_source(tmp_path, monkeypatch):
    """The lossless gate's last proof: djxl --reconstruct_jpeg must give the
    JPEG back byte for byte. A damaged file fails earlier checks, so here the
    reconstruction itself answers 'no'."""
    src, _out, args = _transcode(tmp_path, monkeypatch)
    monkeypatch.setattr(tr, "_jxl_reconstructs_to", lambda *a, **k: False)
    tr._delete_transcoded_sources(*args)
    assert (src / "DSC_0001 (2).jpg").exists()
    assert tr._delete_stats["deleted"] == 0 and tr._delete_stats["kept"] == 1
