"""_stderr_tail: what a tool said about why it failed, on ONE line (#688).

The old tail was the last 200 characters of a tool's stderr with the line
breaks kept. cjxl prints its version banner and its progress first and the
cause after them, so the cut fell inside a line ("cjxl:  be created. Possibly
there is too much tail data.") or inside the banner, and the cause went to the
next lines of the log, which carry no time and do not start with ERROR —
photo-backup reads the lines that do. Found by photo-backup on 19 real JPEGs
and a CMYK one (2026-11-22). The samples below are what cjxl/djxl 0.13.0,
ImageMagick and ExifTool printed (captured 2026-11-22).
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo_common as common  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"
# (getattr: the pre-fix proof runs this file against a common module without it)
LIMIT = getattr(common, "_STDERR_LIMIT", 400)

ENC = "JPEG XL encoder v0.13.0 5a7da83 [_AVX2_,SSE4,SSE2] {Clang 22.1.3}\r\n"
DEC = "JPEG XL decoder v0.13.0 5a7da83 [_AVX2_,SSE4,SSE2] {Clang 22.1.3}\r\n"
NOTE = ("Note: Implicit-default for JPEG is lossless-transcoding. To silence this "
        "message, set --lossless_jpeg=(1|0).\r\n")
TAIL_DATA = ("JPEG bitstream reconstruction data could not be created. Possibly there is "
             "too much tail data.")
TRY = ("Try using --allow_jpeg_reconstruction 0, to losslessly recompress the JPEG image "
       "data without bitstream reconstruction data.")
LONG_PATH = "C:\\" + "\\".join("very long folder name %02d" % i for i in range(20)) + "\\nope.png"


def test_the_cause_without_the_banner():
    assert common._stderr_tail(ENC + "Getting pixel data failed.\r\n") == \
        "Getting pixel data failed."


def test_whole_lines_on_one_line():
    stderr = (ENC + NOTE + "Encoding [JPEG, lossless transcode, effort: 7]\r\n"
              + TAIL_DATA + "\r\n" + TRY + "\r\nEncodeImageJXL() failed.\r\n")
    assert common._stderr_tail(stderr) == f"{TAIL_DATA} | {TRY} | EncodeImageJXL() failed."


def test_djxl_s_cause_without_its_banner():
    stderr = (DEC + "Input file is truncated (total bytes: 25, processed bytes: 25) and "
              "--allow_partial_files is not present.\r\nDecompressJxlToPackedPixelFile failed\r\n")
    assert common._stderr_tail(stderr) == (
        "Input file is truncated (total bytes: 25, processed bytes: 25) and "
        "--allow_partial_files is not present. | DecompressJxlToPackedPixelFile failed")


def test_only_noise_is_kept_rather_than_nothing():
    stderr = (ENC + "Encoding [VarDCT, d1.000, effort: 7]\r\nCompressed to 51 bytes "
              "(0.266 bpp).\r\n48 x 32, 0.267 MP/s, 32 threads.\r\n")
    tail = common._stderr_tail(stderr)
    assert tail.startswith("JPEG XL encoder v0.13.0") and tail.endswith("32 threads.")


def test_a_long_line_keeps_its_start_and_its_end():
    line = (f"magick: unable to open image '{LONG_PATH}': No such file or directory @ "
            f"error/blob.c/OpenBlob/3655.")
    assert len(line) > LIMIT
    tail = common._stderr_tail(line + "\r\n")
    assert len(tail) == LIMIT
    assert tail.startswith("magick: unable to open image 'C:\\very long folder name 00")
    assert tail.endswith("nope.png': No such file or directory @ error/blob.c/OpenBlob/3655.")
    assert " ... " in tail


def test_an_earlier_line_that_does_not_fit_is_cut_not_dropped():
    last = "magick: no images defined `x.png' @ error/convert.c/3342."
    tail = common._stderr_tail(f"magick: unable to open image '{LONG_PATH}': No such file\r\n"
                               f"{last}\r\n")
    assert len(tail) <= LIMIT
    assert tail.startswith("magick: unable to open image 'C:\\very")
    assert tail.endswith(f"': No such file | {last}")


@pytest.mark.parametrize("limit", [LIMIT, 200])
def test_the_last_lines_that_fit_none_cut_in_its_start(limit):
    lines = [f"Warning: [minor] Bad MakerNotes entry {i} - C:/some/where/file.tif"
             for i in range(30)] + ["Error: cannot write - C:/some/where/file.tif"]
    tail = common._stderr_tail("\r\n".join(lines) + "\r\n", limit)
    assert len(tail) <= limit
    kept = tail.split(" | ")
    assert kept == lines[-len(kept):]       # whole lines, the last ones, in order
    assert len(kept) >= 2


# An f-string field that quotes a tool's stdout/stderr raw: `.strip()` keeps
# its line breaks, a `[:N]`/`[-N:]` slice cuts inside a sentence. A `!r`
# field is one line already (repr).
_RAW_QUOTE = re.compile(
    r"\{\(?[^{}]*\.std(?:err|out)\b[^{}]*?\)?(?:\.strip\(\)|\[[-\d]*:[-\d]*\])[^{}!]*\}")


def test_no_message_quotes_a_tool_s_output_raw():
    """Every message that quotes what a tool printed goes through
    _stderr_tail (#688; the review of rc4 found two decoder lines left)."""
    bad = []
    for name in ("jxl_photo_common.py", "jxl_tiff_encoder.py", "jxl_tiff_decoder.py",
                 "jxl_jpeg_transcoder.py", "jxl_recompressor.py"):
        text = (REPO / name).read_text(encoding="utf-8")
        for no, line in enumerate(text.splitlines(), 1):
            if _RAW_QUOTE.search(line):
                bad.append(f"{name}:{no}: {line.strip()}")
    assert not bad, "\n".join(bad)


def test_the_recap_keeps_the_start_and_the_cause():
    """The end-of-run recap shortens a long reason in the middle: its end is
    where the cause is (the review of rc4: it cut the end)."""
    import logging

    class G:
        pass

    g = G()
    g.logger = logging.getLogger("test_recap_keeps_the_cause")
    seen = []

    class Keep(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    g.logger.addHandler(Keep())
    g.logger.propagate = False
    reason = ("magick: unable to open image '" + LONG_PATH
              + "': No such file or directory @ error/blob.c/OpenBlob/3655.")
    common._log_failure_recap(g, [("DSC_0001 (2).jxl", reason)])
    line = [m for m in seen if "DSC_0001 (2).jxl" in m][0]
    assert line.startswith("  -> DSC_0001 (2).jxl: magick: unable to open image")
    assert line.endswith("No such file or directory @ error/blob.c/OpenBlob/3655.")
    assert len(line) <= len("  -> DSC_0001 (2).jxl: ") + 300


def test_bytes_str_none():
    raw = (ENC + "Getting pixel data failed.\r\n").encode()
    assert common._stderr_tail(raw) == common._stderr_tail(raw.decode()) == \
        "Getting pixel data failed."
    assert common._stderr_tail(None) == "" == common._stderr_tail(b"")


@pytest.mark.skipif(not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
                    reason="needs cjxl, djxl and exiftool on PATH")
def test_a_cjxl_failure_is_one_error_line(tmp_path):
    """The transcoder on a JPEG with 8 MB after its end (cjxl cannot keep it
    losslessly): the file's ERROR line holds the whole cause, and no line of
    the output continues it. Before #688: `cjxl:  be created. Possibly there
    is too much tail data.`, then two lines without a time."""
    src = tmp_path / HOSTILE / "JPG"
    src.mkdir(parents=True)
    jpg = src / "DSC_0001 (2).jpg"
    Image.fromarray(np.full((32, 48, 3), 120, np.uint8)).save(jpg, quality=90)
    jpg.write_bytes(jpg.read_bytes() + b"\0" * (8 * 1024 * 1024))
    r = subprocess.run([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), str(jpg),
                        str(tmp_path / HOSTILE / "JXL"), "--mode", "0", "--workers", "1"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=600,
                       env=dict(os.environ, JXLPHOTO_LOG_DIR=str(tmp_path / "logs"),
                                JXLPHOTO_SETTINGS=""))
    assert r.returncode != 0
    lines = r.stdout.splitlines()
    error = [ln for ln in lines if "| ERROR |" in ln and "DSC_0001 (2).jpg" in ln]
    assert len(error) == 1, r.stdout[-3000:]
    assert f"cjxl: {TAIL_DATA} | {TRY} | EncodeImageJXL() failed." in error[0]
    assert not [ln for ln in lines if ln.startswith(("Try using", "EncodeImageJXL"))]
