"""The scripts' debug lines reach the log FILE, and only the file (#651).

Each setup_logger gave the file handler the DEBUG level but left the logger
itself at INFO, which filters a record before any handler sees it: the 57
logger.debug lines of the four scripts (why an adopt stamp failed, why a
delete kept a source, which ICC was used) were written nowhere. The console
-- what a person and the wizard read -- stays at INFO (the decoder's handlers
had no level of their own at all).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr  # noqa: E402
import jxl_recompressor as rec  # noqa: E402
import jxl_tiff_decoder as dec  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402


@pytest.mark.parametrize("mod", [enc, dec, tr, rec],
                         ids=["encoder", "decoder", "transcoder", "recompressor"])
def test_a_debug_line_reaches_the_file_not_the_console(mod, capsys):
    log_file = Path(mod.setup_logger())
    mod.logger.debug("PROBE debug line | 吾妻山 [x] 100%off")
    mod.logger.info("PROBE info line")
    for handler in mod.logger.handlers:
        handler.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "PROBE debug line | 吾妻山 [x] 100%off" in text
    assert "PROBE info line" in text
    console = capsys.readouterr().out
    assert "PROBE info line" in console
    assert "PROBE debug line" not in console
