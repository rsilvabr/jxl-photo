#!/usr/bin/env python3
"""X-A of the 2026-11-03 audit: an exiftool marker batch that FAILS must not
read as "no marker".

`_read_source_markers_batch` (parity-pinned in the four backends) pre-fills
every path with {'src': None, 'srcsum': None} and left it that way when its
batch failed, stalled or returned no entry for a file. Every non-strict
overwrite guard then took "cannot tell" for a legacy, markerless archive and
let the overwrite through — onto what may be the only copy of another photo
(an earlier run deleted its source). The reader now adds 'unread': True, and
every guard refuses such an output (a warning instead with the guard off,
like the marker refusal); the strict guards name the real reason.

U1-U5 exercise the reader with a faked `_run_captured` (no exiftool needed);
G1-G9 the guards of each backend; R1-R2 run the real exiftool/cjxl (skipped
when absent). Every fixture path is hostile: Japanese, `[ ]`, a space, `%`.

Pre-fix proof (must fail on the base commit's scripts):

    mkdir <tmp>/xapre
    git show HEAD:jxl_tiff_encoder.py > <tmp>/xapre/jxl_tiff_encoder.py
    (same for jxl_tiff_decoder, jxl_jpeg_transcoder, jxl_recompressor)
    JXL_SCRIPTS_UNDER_TEST=<tmp>/xapre python -m pytest tests/test_unread_markers.py -q
"""

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
sys.path.insert(0, str(Path(_OVERRIDE).resolve()) if _OVERRIDE else str(_REPO))

import jxl_jpeg_transcoder as tra  # noqa: E402
import jxl_recompressor as rec  # noqa: E402
import jxl_tiff_decoder as dec  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402

SCRIPTS = ["jxl_tiff_encoder", "jxl_tiff_decoder", "jxl_jpeg_transcoder",
           "jxl_recompressor"]
HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"
_JXL_SIG = b"\x00\x00\x00\x0cJXL \r\n\x87\n"

_HAVE_EXIFTOOL = shutil.which("exiftool") is not None
_HAVE_CODECS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real_exiftool = pytest.mark.skipif(not _HAVE_EXIFTOOL, reason="needs exiftool on PATH")
real_codecs = pytest.mark.skipif(not _HAVE_CODECS,
                                 reason="needs cjxl, djxl and exiftool on PATH")

# Run-scoped globals an in-process main() can arm; restored around each test.
_RUN_FLAGS = ("OVERWRITE", "DELETE_SOURCE", "DELETE_CONFIRM", "DELETE_SKIPPED",
              "VERIFY_ROUNDTRIP", "PROVENANCE_CHECK", "ADOPT_SCAN",
              "OVERWRITE_GUARD", "TEMP2_DIR", "STORE_MD5")


@pytest.fixture(autouse=True)
def _restore_run_flags():
    saved = {(m, n): getattr(m, n) for m in (enc, dec, tra, rec)
             for n in _RUN_FLAGS if hasattr(m, n)}
    yield
    for (m, n), v in saved.items():
        setattr(m, n, v)


@pytest.fixture(params=SCRIPTS)
def mod(request):
    return importlib.import_module(request.param)


class _Run:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class _FakeLogger:
    def __init__(self):
        self.infos, self.warnings, self.errors = [], [], []

    def info(self, m, *a, **k): self.infos.append(str(m))
    def warning(self, m, *a, **k): self.warnings.append(str(m))
    def error(self, m, *a, **k): self.errors.append(str(m))
    def debug(self, m, *a, **k): pass
    def critical(self, m, *a, **k): self.errors.append(str(m))


def _is_marker_batch(cmd):
    """The marker reader's call: exiftool -@ <argfile> with -XMP-dc:Relation."""
    if "-@" not in cmd:
        return False
    try:
        lines = Path(cmd[cmd.index("-@") + 1]).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return lines[:4] == ["-j", "-s", "-s", "-XMP-dc:Relation"]


def _failing_batches(module, monkeypatch, stdout="", returncode=1):
    """Every marker batch fails (rc!=0, no stdout); other calls go through."""
    real = module._run_captured

    def fake(cmd, *a, **k):
        if _is_marker_batch(cmd):
            return _Run(stdout=stdout, returncode=returncode)
        return real(cmd, *a, **k)
    monkeypatch.setattr(module, "_run_captured", fake)


def _hostile(tmp_path) -> Path:
    d = tmp_path / HOSTILE / "01_Z7 (2)"
    d.mkdir(parents=True)
    return d


def _stub(path: Path, payload: bytes = b"\x00" * 32) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_JXL_SIG + payload)
    return path


def _tiff(path: Path, value: int = 1000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((32, 48, 3), value, np.uint16),
                     photometric="rgb")
    return path


def _make_newer(target: Path, than: Path, delta: int = 100):
    stamp = than.stat().st_mtime + delta
    os.utime(target, (stamp, stamp))


# ===========================================================================
# U — the reader
# ===========================================================================

def test_u1_failed_batch_marks_every_file_unread(mod, tmp_path, monkeypatch):
    d = _hostile(tmp_path)
    files = [_stub(d / "DSC_0001 (2).jxl"), _stub(d / "50%off.jxl")]
    _failing_batches(mod, monkeypatch)
    marks = mod._read_source_markers_batch(files)
    for f in files:
        info = marks[str(f)]
        assert info.get("src") is None and info.get("srcsum") is None
        assert info.get("unread") is True, f"{mod.__name__}: {info}"


def test_u2_unparseable_output_marks_the_batch_unread(mod, tmp_path, monkeypatch):
    d = _hostile(tmp_path)
    f = _stub(d / "DSC_0001.jxl")
    _failing_batches(mod, monkeypatch, stdout="Warning: not JSON", returncode=0)
    assert mod._read_source_markers_batch([f])[str(f)].get("unread") is True


def test_u3_partial_batch_marks_only_the_missing_file(mod, tmp_path, monkeypatch):
    """exiftool exits non-zero on ONE bad file but prints the rest."""
    d = _hostile(tmp_path)
    marked, missing = _stub(d / "DSC_0001 (2).jxl"), _stub(d / "DSC_0002 [b].jxl")
    payload = json.dumps([{"SourceFile": marked.as_posix(),
                           "Relation": [f"{mod.SRC_PREFIX}abc123"]}])
    _failing_batches(mod, monkeypatch, stdout=payload, returncode=1)
    marks = mod._read_source_markers_batch([marked, missing])
    assert marks[str(marked)].get("src") == "abc123"
    assert not marks[str(marked)].get("unread"), marks[str(marked)]
    assert marks[str(missing)].get("unread") is True, marks[str(missing)]


def test_u4_error_entry_is_unread(mod, tmp_path, monkeypatch):
    d = _hostile(tmp_path)
    f = _stub(d / "DSC_0001.jxl")
    payload = json.dumps([{"SourceFile": f.as_posix(), "Error": "File is busy"}])
    _failing_batches(mod, monkeypatch, stdout=payload, returncode=1)
    assert mod._read_source_markers_batch([f])[str(f)].get("unread") is True


def test_u5_answered_without_relation_stays_a_plain_markerless_file(
        mod, tmp_path, monkeypatch):
    """Control: a legacy archive exiftool DID read (no Relation at all) keeps
    the old 'markerless' meaning — the guards must not start refusing it."""
    d = _hostile(tmp_path)
    f = _stub(d / "DSC_0001.jxl")
    payload = json.dumps([{"SourceFile": f.as_posix()}])
    _failing_batches(mod, monkeypatch, stdout=payload, returncode=0)
    info = mod._read_source_markers_batch([f])[str(f)]
    assert info == {"src": None, "srcsum": None}, info


# ===========================================================================
# G — the guards
# ===========================================================================

def test_g1_encoder_non_strict_guard_refuses_an_unread_output(tmp_path, monkeypatch):
    d = _hostile(tmp_path)
    t, j = _tiff(d / "DSC_0001 (2).tif"), _stub(d / "DSC_0001 (2).jxl")
    _failing_batches(enc, monkeypatch)
    monkeypatch.setattr(enc, "OVERWRITE_GUARD", True)
    info = enc._read_source_markers_batch([j])[str(j)]
    why = enc._foreign_overwrite_reason(info, t, j)
    assert why and "could not be read" in why, why


def test_g2_encoder_guard_off_overwrites_an_unread_output_with_a_warning(
        tmp_path, monkeypatch):
    d = _hostile(tmp_path)
    t, j = _tiff(d / "DSC_0001.tif"), _stub(d / "DSC_0001.jxl")
    fake = _FakeLogger()
    monkeypatch.setattr(enc, "logger", fake)
    monkeypatch.setattr(enc, "OVERWRITE_GUARD", False)
    info = {"src": None, "srcsum": None, "unread": True}
    assert enc._foreign_overwrite_reason(info, t, j) is None
    assert any("could not be read" in w for w in fake.warnings), fake.warnings


def _enc_main_plumbing(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(enc, "setup_logger", lambda: tmp_path / "fake.log")
    monkeypatch.setattr(enc, "logger", fake)
    monkeypatch.setattr(enc, "_min_effective_distance", lambda exe: 0.05)
    monkeypatch.setattr(enc, "_get_cjxl_cmd", lambda: "cjxl")
    monkeypatch.setattr(enc, "_get_exiftool_cmd", lambda: "exiftool")
    monkeypatch.setattr(enc, "_warn_if_libjxl_too_old", lambda *a, **k: None)
    monkeypatch.setattr(enc, "process_group", lambda items, workers, mode=0: [])
    captured = []
    monkeypatch.setattr(enc, "emit_summary_json",
                        lambda enabled, **kw: captured.append(kw))
    _failing_batches(enc, monkeypatch)
    return captured


def _enc_tree(tmp_path):
    src = _hostile(tmp_path)
    t = _tiff(src / "DSC_0001 (2).tif")
    os.utime(t, (1000, 1000))
    out = tmp_path / "出力 [JXL] 5%"
    j = _stub(out / "DSC_0001 (2).jxl")
    return src, t, out, j


def test_g3_encoder_main_overwrite_refuses_an_unread_output(tmp_path, monkeypatch):
    """--overwrite into a flat output folder (mode 0 + output = collapsing),
    no deletion: the non-strict guard in main()."""
    src, t, out, j = _enc_tree(tmp_path)
    fake = _FakeLogger()
    captured = _enc_main_plumbing(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(src), str(out), "--mode", "0",
                         "--overwrite", "--dry-run", "--summary-json",
                         "--no-preflight"])
    try:
        enc.main()
    except SystemExit:
        pass
    fails = (captured[-1].get("failures") if captured else None) or []
    assert any(str(t) in f[0] and "could not be read" in f[1] for f in fails), \
        f"the unread output was not refused: {fails} / {fake.errors}"


def test_g4_encoder_strict_guard_never_adopts_an_unread_output(tmp_path, monkeypatch):
    """--delete-source in a collapsing run with --provenance adopt: an unread
    output must be refused, never counted as a markerless archive to adopt."""
    src, t, out, j = _enc_tree(tmp_path)
    fake = _FakeLogger()
    captured = _enc_main_plumbing(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(src), str(out), "--mode", "0",
                         "--overwrite", "--delete-source", "--delete-confirm-off",
                         "--provenance", "adopt", "--dry-run", "--summary-json",
                         "--no-preflight"])
    try:
        enc.main()
    except SystemExit:
        pass
    fails = (captured[-1].get("failures") if captured else None) or []
    assert any(str(t) in f[0] and "could not be read" in f[1] for f in fails), \
        f"the unread output was not refused: {fails} / {fake.errors}"
    assert not any("carry no provenance record" in m for m in fake.infos), \
        f"an unread output was counted as adoptable: {fake.infos}"


def _dec_main(monkeypatch, tmp_path, extra):
    src_dir = _hostile(tmp_path)
    src = _stub(src_dir / "DSC_0001 (2).jxl")
    out_dir = tmp_path / "出力 [TIFF] 5%"
    out_dir.mkdir()
    final = out_dir / "DSC_0001 (2).tif"
    final.write_bytes(b"another photo's only copy")
    captured = {}
    fake = _FakeLogger()
    monkeypatch.setattr(dec, "setup_logger", lambda *a, **k: tmp_path / "fake.log")
    monkeypatch.setattr(dec, "logger", fake)
    monkeypatch.setattr(dec, "emit_summary_json", lambda *a, **k: captured.update(k))
    monkeypatch.setattr(dec, "collect_multipage_groups",
                        lambda jxls: {src: [(src, 0, False, False, 0, False, None)]})
    monkeypatch.setattr(dec, "_group_conflicts", [])
    monkeypatch.setattr(dec, "_check_external_tools", lambda *a, **k: None)
    monkeypatch.setattr(dec, "_warn_if_libjxl_too_old", lambda *a, **k: None)
    _failing_batches(dec, monkeypatch)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(src_dir), str(out_dir),
                         "--mode", "2", "--dry-run", "--summary-json"] + extra)
    try:
        dec.main()
    except SystemExit:
        pass
    return src, final, captured, fake


def test_g5_decoder_overwrite_refuses_an_unread_tiff(tmp_path, monkeypatch):
    src, final, captured, fake = _dec_main(monkeypatch, tmp_path, ["--overwrite"])
    fails = captured.get("failures") or []
    assert any(str(src) in f[0] and "could not be read" in f[1] for f in fails), \
        f"the unread TIFF was not refused: {fails} / {fake.warnings}"


def test_g6_decoder_strict_guard_names_the_unread_reason(tmp_path, monkeypatch):
    src, final, captured, fake = _dec_main(
        monkeypatch, tmp_path, ["--overwrite", "--delete-source", "--delete-confirm-off"])
    fails = captured.get("failures") or []
    assert any(str(src) in f[0] and "could not be read" in f[1] for f in fails), \
        f"the strict refusal blamed an older version instead: {fails}"


def test_g7_transcoder_non_strict_guard_refuses_an_unread_lossy_output(
        tmp_path, monkeypatch):
    d = _hostile(tmp_path)
    src = _stub(d / "DSC_0001 (2).jxl")
    out = d / "converted" / "DSC_0001 (2).jpg"
    out.parent.mkdir()
    out.write_bytes(b"\xff\xd8another photo's JPEG\xff\xd9")
    monkeypatch.setattr(tra, "logger", _FakeLogger())
    monkeypatch.setattr(tra, "OVERWRITE_GUARD", True)
    _failing_batches(tra, monkeypatch)
    kept, refused = tra._foreign_overwrite_filter([(src, out)], False,
                                                  lambda s, o: True)
    assert kept == [] and len(refused) == 1, (kept, refused)
    assert "could not be read" in refused[0][2], refused


def test_g8_transcoder_strict_guard_names_the_unread_reason(tmp_path, monkeypatch):
    d = _hostile(tmp_path)
    src = _stub(d / "DSC_0001 (2).jxl")
    out_dir = tmp_path / "出力 [JPEG] 5%"
    out = out_dir / "DSC_0001 (2).jpg"
    out_dir.mkdir()
    out.write_bytes(b"\xff\xd8another photo's JPEG\xff\xd9")
    monkeypatch.setattr(tra, "logger", _FakeLogger())
    monkeypatch.setattr(tra, "DELETE_SOURCE", True)
    _failing_batches(tra, monkeypatch)
    kept, refused = tra._provenance_filter([(src, out)], 2, False,
                                           output_arg=out_dir, source_root=d)
    assert kept == [] and len(refused) == 1, (kept, refused)
    assert "could not be read" in refused[0][2], refused


def _rec_item(tmp_path):
    d = _hostile(tmp_path)
    src = _stub(d / "DSC_0001 (2).jxl")
    final = _stub(d / "recompressed_jxl" / "DSC_0001 (2).jxl", b"\x01" * 32)
    return {"src": src, "final": final, "in_place": False, "action": "convert"}


def test_g9_recompressor_guard_refuses_an_unread_output(tmp_path, monkeypatch):
    it = _rec_item(tmp_path)
    monkeypatch.setattr(rec, "logger", _FakeLogger())
    monkeypatch.setattr(rec, "OVERWRITE", True)
    monkeypatch.setattr(rec, "OVERWRITE_GUARD", True)
    _failing_batches(rec, monkeypatch)
    refused = rec._foreign_overwrite_refusals([it])
    assert len(refused) == 1 and "could not be read" in refused[0][1], refused


def test_g10_recompressor_guard_names_an_unread_source(tmp_path, monkeypatch):
    """The output carries markers, the SOURCE's batch answer is missing: the
    refusal says so instead of 'names a different origin'."""
    it = _rec_item(tmp_path)
    monkeypatch.setattr(rec, "logger", _FakeLogger())
    monkeypatch.setattr(rec, "OVERWRITE", True)
    monkeypatch.setattr(rec, "OVERWRITE_GUARD", True)
    payload = json.dumps([{"SourceFile": it["final"].as_posix(),
                           "Relation": [f"{rec.SRC_PREFIX}abc123"]}])
    _failing_batches(rec, monkeypatch, stdout=payload, returncode=1)
    refused = rec._foreign_overwrite_refusals([it])
    assert len(refused) == 1, refused
    assert "source's provenance markers could not be read" in refused[0][1], refused


def test_g11_recompressor_guard_off_overwrites_with_a_warning(tmp_path, monkeypatch):
    it = _rec_item(tmp_path)
    fake = _FakeLogger()
    monkeypatch.setattr(rec, "logger", fake)
    monkeypatch.setattr(rec, "OVERWRITE", True)
    monkeypatch.setattr(rec, "OVERWRITE_GUARD", False)
    _failing_batches(rec, monkeypatch)
    assert rec._foreign_overwrite_refusals([it]) == []
    assert any("could not be read" in w for w in fake.warnings), fake.warnings


# ===========================================================================
# R — real exiftool / codecs
# ===========================================================================

@real_exiftool
def test_r1_real_exiftool_reads_a_markerless_file_and_flags_a_missing_one(
        mod, tmp_path):
    """A plain TIFF exiftool reads (no Relation) is markerless, not unread; a
    path that vanished between planning and the batch is unread."""
    d = _hostile(tmp_path)
    plain = _tiff(d / "DSC_0001 (2) 5%.tif")
    gone = d / "DSC_0002 [gone].tif"
    marks = mod._read_source_markers_batch([plain, gone])
    assert marks[str(plain)] == {"src": None, "srcsum": None}, marks[str(plain)]
    assert marks[str(gone)].get("unread") is True, marks[str(gone)]


@real_codecs
def test_r2_real_encoder_overwrite_keeps_another_photos_archive(tmp_path, monkeypatch):
    """End to end: photo A archived into a flat folder; photo B (same name,
    other folder) arrives with --overwrite while the marker batch fails. B must
    be refused and A's archive left byte-identical."""
    out = tmp_path / "出力 [JXL] 5%"
    a = _tiff(tmp_path / HOSTILE / "A" / "DSC_0001 (2).tif", 1000)
    r = subprocess.run([sys.executable, str(Path(enc.__file__)), str(a.parent),
                        str(out), "--mode", "0", "--distance", "0", "--effort", "3",
                        "--no-preflight"],
                       capture_output=True, text=True, timeout=600,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    archive = out / "DSC_0001 (2).jxl"
    before = archive.read_bytes()

    b = _tiff(tmp_path / HOSTILE / "B" / "DSC_0001 (2).tif", 60000)
    fake = _FakeLogger()
    monkeypatch.setattr(enc, "setup_logger", lambda: tmp_path / "fake.log")
    monkeypatch.setattr(enc, "logger", fake)
    captured = []
    monkeypatch.setattr(enc, "emit_summary_json",
                        lambda enabled, **kw: captured.append(kw))
    _failing_batches(enc, monkeypatch)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(b.parent), str(out), "--mode", "0",
                         "--overwrite", "--distance", "0", "--effort", "3",
                         "--summary-json", "--no-preflight"])
    try:
        enc.main()
    except SystemExit:
        pass
    assert archive.read_bytes() == before, "A's archive was overwritten by B"
    fails = (captured[-1].get("failures") if captured else None) or []
    assert any("could not be read" in f[1] for f in fails), fails
