"""A convert result that matches no planned pair is reported as KEEP and
counted, in both lossy delete gates of the transcoder (#650) -- as the
transcode gate (process_group_transcode) already did. Before, a bare
`continue` made it vanish from the report: fail-closed (nothing deleted),
but invisible."""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr  # noqa: E402

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="main() checks the tools before a real (not dry) run")


def _png(path: Path) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (200, 100, 50)).save(path)


@pytest.mark.parametrize("route", ["convert", "auto"])
def test_an_unpaired_result_is_kept_and_reported(tmp_path, monkeypatch, caplog, route):
    src_dir = tmp_path / "250118_吾妻山 [FINAL] 100%off"
    _png(src_dir / "shot (1).png")
    ghost = src_dir / "not planned [x].png"
    calls = []

    def fake_convert(pairs, *a, **k):
        calls.append(len(pairs))
        # the shape encode_to_jxl/decode_to_image return: (src, status, detail, md5)
        return [(str(ghost), "ok", "", None)], set()
    monkeypatch.setattr(tr, "process_group_convert", fake_convert)
    tr._delete_stats.update(deleted=0, kept=0, deleted_archived=0)
    argv = ["jxl_jpeg_transcoder.py", str(src_dir), str(tmp_path / "out"),
            "--delete-source", "--delete-confirm-off"]
    if route == "convert":
        argv.append("--force-convert")
    monkeypatch.setattr(sys, "argv", argv)
    caplog.set_level("WARNING")
    try:
        tr.main()
    except SystemExit:
        pass
    assert calls, "the lossy convert path was not reached: the test proves nothing"
    assert tr._delete_stats["kept"] == 1, tr._delete_stats
    assert any("KEEP (final output missing)" in r.getMessage() and "not planned [x].png" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]
    assert (src_dir / "shot (1).png").exists()
