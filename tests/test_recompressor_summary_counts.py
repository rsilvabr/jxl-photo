"""The recompressor's --summary-json counts like the other three scripts.

The wrapper's manifest table reads "ovw" as PART of "OK" (v1.8.3: the encoder,
the decoder and the transcoder count an overwrite in both; #257 brought the
transcoder's auto mode in line). The recompressor sent its overwrites as a
bucket of their own, so a --overwrite run that rewrote every file read
"OK 0 | ovw 8438" in the table — a run with no error that looked like nothing
was written. A verbatim copy (a jbrd archive) over an existing output was in
neither "ovw" count.

Real cjxl/exiftool, in a hostile folder (Japanese, [ ], a space, %).
"""

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import jxl_recompressor as rec  # noqa: E402

real = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool")

HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"
LOSSY = "DSC_0001 (2).jxl"      # re-encoded
ARCHIVE = "写真 [b].jxl"         # a jbrd archive: copied verbatim


def _image(i):
    img = np.zeros((48, 64, 3), dtype=np.uint8)
    img[..., 0] = np.arange(64, dtype=np.uint8)[None, :] * 3 + i * 40
    img[..., 1] = np.arange(48, dtype=np.uint8)[:, None] * 4
    img[..., 2] = 90
    return img


def _make_sources(tmp_path: Path) -> Path:
    from PIL import Image
    src = tmp_path / HOSTILE
    src.mkdir()
    png = tmp_path / "p.png"
    Image.fromarray(_image(0)).save(png)
    jpg = tmp_path / "p.jpg"
    Image.fromarray(_image(1)).save(jpg, quality=90)
    for inp, name, extra in ((png, LOSSY, ["-d", "0.3"]), (jpg, ARCHIVE, [])):
        r = subprocess.run(["cjxl", str(inp), str(src / name), "-e", "1",
                            "--container=1", *extra],
                           capture_output=True, timeout=60)
        assert r.returncode == 0, r.stderr
    assert rec.has_jbrd_box(src / ARCHIVE)
    assert not rec.has_jbrd_box(src / LOSSY)
    return src


def _run_main(monkeypatch, src: Path, *extra):
    seen = {}
    monkeypatch.setattr(rec, "emit_summary_json", lambda *a, **k: seen.update(k))
    monkeypatch.setattr(sys, "argv", [
        "jxl_recompressor.py", str(src), "--mode", "1", "--distance", "2",
        "--effort", "3", "--workers", "1", "--no-preflight",
        "--on-unknown", "convert", "--summary-json", *extra])
    with pytest.raises(SystemExit) as e:
        rec.main()
    assert e.value.code in (0, None)
    return seen


@real
def test_new_outputs_are_ok_and_no_overwrite(tmp_path, monkeypatch):
    src = _make_sources(tmp_path)
    s = _run_main(monkeypatch, src, "--no-keep-smaller")
    assert (s["ok"], s["overwritten"], s["errors"]) == (2, 0, 0), s
    assert s["extras"]["recompressed"] == 1, s["extras"]
    assert s["extras"]["copied (policy/not smaller)"] == 1, s["extras"]


# Without --no-keep-smaller the tiny re-encode is larger than its source, so
# the lossy file is a "COPY (not smaller)" too: the third copy route.
@real
@pytest.mark.parametrize("keep_smaller, recompressed, copied", [
    (False, 1, 1),
    (True, 0, 2),
])
def test_overwrite_run_counts_every_file_in_ok_and_in_ovw(tmp_path, monkeypatch,
                                                          keep_smaller, recompressed,
                                                          copied):
    flags = () if keep_smaller else ("--no-keep-smaller",)
    src = _make_sources(tmp_path)
    _run_main(monkeypatch, src, *flags)
    out_dir = src / rec.CONVERTED_JXL_FOLDER
    assert (out_dir / LOSSY).exists() and (out_dir / ARCHIVE).exists()

    s = _run_main(monkeypatch, src, "--overwrite", *flags)
    # Pre-fix: the re-encode was in ovw but not in ok, a copy in ok but not in ovw.
    assert s["ok"] == 2, s
    assert s["overwritten"] == 2, s
    assert s["errors"] == 0, s
    assert s["extras"]["recompressed"] == recompressed, s["extras"]
    assert s["extras"]["copied (policy/not smaller)"] == copied, s["extras"]

    # Run state: the next run starts from nothing.
    s = _run_main(monkeypatch, src, *flags)
    assert (s["ok"], s["overwritten"], s["skipped"]) == (0, 0, 2), s
