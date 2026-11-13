"""The wizard's plan of a run is the run's own — round 84, the independent
review of v2.30.0 (#663–#668).

The wrapper's file count (_count_origin_files) and manifest collision scan
(_manifest_output_collisions) ask each script which files a run takes and
where each output goes. Since v2.30.1 they ask with the run's own command line
— build_child_cmd's argv for that run, parsed by the script (_child_plan_args)
— so nothing the run gets can be left out of the plan:

* #663 — a JXL -> JPEG auto run with a resize and a rename: the plan never got
  the resize, so a JPEG archive (jbrd) "kept its name" while the run
  re-encoded it under the new one, and two rows writing one JPEG went unseen.
* #664 — the wizard's "Output folder name" (--output-name) and the expert
  flags that move outputs or pick the files reached the run, not the plan.
* #665 — the transcoder's count and scan were 1.7 to 7 times slower than in
  v2.30.0_beta1: a resolve() per file, each output resolved two or three times.
* #666 — a manifest row whose Source is a file, mode 2, empty Destination:
  every script read the file as its output folder and failed the row.
* #667 — a single file in modes 6/7, which the decoder and the transcoder
  refuse: the count said 1.
* #668 — the decoder read the libjxl version for the History in its workers.

Every test works in a folder with Japanese, [ ], a space and `100%off %d`.
"""
import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_jpeg_transcoder as tr  # noqa: E402
import jxl_photo as wp  # noqa: E402
import jxl_recompressor as rec  # noqa: E402
import jxl_tiff_decoder as dec  # noqa: E402

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"
_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))
real = pytest.mark.skipif(not _HAVE_TOOLS,
                          reason="needs cjxl, djxl, exiftool and magick on PATH")


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def root(tmp_path):
    r = tmp_path / HOSTILE
    r.mkdir()
    return r


def _touch(*paths, content=b"x"):
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _norm(p):
    return os.path.normcase(os.path.abspath(str(p)))


@pytest.fixture(scope="session")
def originals(tmp_path_factory):
    """One real file of each kind: a 16-bit TIFF, a JPEG, a PNG, a JXL of the
    PNG and a JPEG archive (jbrd) of the JPEG."""
    if not _HAVE_TOOLS:
        pytest.skip("needs cjxl, djxl, exiftool and magick on PATH")
    import numpy as np
    import tifffile
    from PIL import Image
    b = tmp_path_factory.mktemp("originals")
    arr = np.random.default_rng(7).integers(0, 255, (32, 48, 3), dtype=np.uint8)
    Image.fromarray(arr).save(b / "a.png")
    Image.fromarray(arr).save(b / "a.jpg", quality=90)
    tifffile.imwrite(b / "a.tif", arr.astype(np.uint16) * 257, photometric="rgb")
    # In a container, like every JXL the toolkit writes (a bare codestream
    # fails the recompressor's integrity check when it keeps the smaller file).
    subprocess.run(["cjxl", str(b / "a.png"), str(b / "plain.jxl"), "-d", "1",
                    "--container=1"], check=True, capture_output=True)
    subprocess.run(["cjxl", str(b / "a.jpg"), str(b / "jbrd.jxl")],
                   check=True, capture_output=True)
    return b


_KIND = {"tif": "a.tif", "jpg": "a.jpg", "png": "a.png", "jxl": "plain.jxl",
         "jbrd": "jbrd.jxl"}


@pytest.fixture
def library(root, originals):
    """A library below a 'Lightroom_Export' (#660's trap): loose files and a
    shoot with its export folder."""
    lib = root / "Lightroom_Export" / "lib [2025]"

    def put(rel, kind):
        p = lib / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(originals / _KIND[kind], p)

    put("IMG_ProPhoto_1.jxl", "jbrd")
    put("IMG_ProPhoto_2.jxl", "jxl")
    put("r0 (2).tif", "tif")
    put("r0j.jpg", "jpg")
    put("r0g.png", "png")
    e = "shoot A/2025_EXPORT"
    put(f"{e}/TIFF16/e1 [b].tif", "tif")
    put(f"{e}/JPG/e1j.jpg", "jpg")
    put(f"{e}/16B_JXL/IMG_ProPhoto_e.jxl", "jxl")
    put(f"{e}/JXL_jpeg/IMG_ProPhoto_eb.jxl", "jbrd")
    return lib


def _env(logs):
    return dict(os.environ, PYTHONIOENCODING="utf-8", JXLPHOTO_SETTINGS="",
                JXLPHOTO_LOG_DIR=str(logs), JXLPHOTO_JXL_THUMB="0")


def _row(origin, dest, conv, wf_extra=None, adv=None):
    wf = {"origin_format": origin, "dest_format": dest, "conversion_type": conv,
          "mode_config": {}}
    wf.update(wf_extra or {})
    adv = dict(adv or {})
    wf["advanced_options"] = adv
    return wf, adv


def _wrapper_plan(menu, origin, dest, source, dest_path, mode, wf, adv):
    """The wrapper's plan of one manifest row, the way its scan asks for it:
    the row's argv (build_child_cmd, quiet), parsed by the script, walked
    under the globals that parse gives. {source name: output}."""
    child = importlib.import_module(wp._child_module(origin, dest))
    cmd = menu.build_child_cmd(str(REPO / f"{child.__name__}.py"), origin, dest,
                               str(source), str(dest_path), mode, 1, wf, adv,
                               manifest=True, quiet=True)
    ns = wp._child_plan_args(child, cmd)
    assert ns is not None, cmd
    was = child.logger.disabled
    child.logger.disabled = True
    try:
        with wp._with_child_marker(child, *wp._child_plan_globals(
                ns, wp._direction(origin, dest))):
            pairs = child._planned_pairs(ns, read_markers=False)
    finally:
        child.logger.disabled = was
    return {f.name: _norm(o) for f, o in pairs}


_ENC_DRY = re.compile(r"^(?P<name>.+?) page(?P<page>\d+)(?: \[[a-z]+\])* > (?P<out>.+)$")


def _dry_run_plan(cmd, logs):
    """The script's own plan: its --dry-run lines, {source name: output}, and
    the names it lists without a place (skipped, refused)."""
    r = subprocess.run(cmd + ["--dry-run"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=_env(logs),
                       stdin=subprocess.DEVNULL, timeout=300)
    text = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, text[-3000:]
    module = Path(cmd[1]).stem
    plan, unplaced = {}, set()
    for ln in text.splitlines():
        if " DRY | " not in ln:
            continue
        body = ln.split(" DRY | ", 1)[1]
        if module == "jxl_tiff_encoder":
            m = _ENC_DRY.match(body)
            if m and m.group("page") == "0":
                plan[m.group("name")] = _norm(m.group("out").strip())
            continue
        if " -> " not in body:
            parts = [p.strip() for p in body.split(" | ")]
            if len(parts) >= 2:
                unplaced.add(parts[1])
            continue
        head, _sep, tail = body.partition(" -> ")
        if module == "jxl_tiff_decoder":
            plan[head.strip()] = _norm(tail.rsplit(" | ", 1)[0].strip())
        elif module == "jxl_recompressor":
            m = re.match(r"^(.*?\.jxl)(?: \(.*)?$", tail.strip(), re.I)
            plan[head.split(" | ", 1)[1].strip()] = _norm(m.group(1) if m else tail.strip())
        else:
            plan[head.strip()] = _norm(tail.split(" [DERIVE")[0].strip())
    return plan, unplaced


# (id, origin, dest, conversion, mode, source below the library, destination,
#  workflow extras, advanced options)
_RENAME = {"rename_from": "ProPhoto", "rename_to": "sRGB"}
SCENARIOS = [
    # #663: a resize (or sharpening) re-encodes the JPEG archive too, renamed.
    ("auto-resize-rename-2", "jxl", "jpeg", "jxl_to_jpeg_auto", 2, "", "out", {},
     dict(_RENAME, resize_mode="long", resize_value=16)),
    ("auto-sharpen-rename-6", "jxl", "jpeg", "jxl_to_jpeg_auto", 6, "", "out", {},
     dict(_RENAME, sharpen="screen")),
    ("auto-rename-2", "jxl", "jpeg", "jxl_to_jpeg_auto", 2, "", "out", {}, _RENAME),
    # The conversion and format flags (the review's M11/M12).
    ("png-rename-1", "jxl", "png", "jxl_to_png", 1, "", "same", {}, _RENAME),
    ("force-rename-3", "jxl", "jpeg", "jxl_to_jpeg_force", 3, "", "same", {}, _RENAME),
    # #664: the wizard's Output folder name, and expert flags that move outputs.
    ("output-name-3", "jpeg", "jxl", "convert_lossy", 3, "", "same",
     {"extra_options": {"output_name": "custom out [c]"}}, {}),
    ("expert-output-name-1", "jpeg", "jxl", "convert_lossy", 1, "", "same",
     {"expert_flags": "--output-name perito"}, {}),
    ("expert-export-jxl-folder-6", "tiff", "jxl", None, 6, "", "same",
     {"expert_flags": "--export-jxl-folder JXL_perito"}, {}),
    ("expert-export-jpeg-folder-6", "jxl", "jpeg", "jxl_to_jpeg_force", 6, "", "same",
     {"expert_flags": "--export-jpeg-folder JPG_perito"}, {}),
    ("expert-export-subfolder-7", "jxl", "tiff", None, 7, "", "same",
     {"mode_config": {"export_subfolder": "JXL_jpeg"},
      "expert_flags": "--export-subfolder 16B_JXL"}, {}),
    # #660's class in all four scripts: an explicit Destination in mode 6.
    ("encoder-6-out", "tiff", "jxl", None, 6, "", "out", {}, {}),
    ("decoder-6-out", "jxl", "tiff", None, 6, "", "out", {}, {}),
    ("recompressor-rename-6-out", "jxl", "jxl", "jxl_recompress", 6, "", "out", {}, _RENAME),
    ("transcoder-rename-6-out", "jxl", "jpeg", "jxl_to_jpeg_auto", 6, "", "out", {}, _RENAME),
    # An auto run with --decode is the lossless recovery: names kept (M28).
    ("auto-decode-rename-1", "jxl", "jpeg", "jxl_to_jpeg_auto", 1, "", "same",
     {"expert_flags": "--decode"}, _RENAME),
    # #666: a file row, mode 2, empty Destination (the loader puts the file there).
    ("file-mode2-encoder", "tiff", "jxl", None, 2, "r0 (2).tif", "file", {}, {}),
    ("file-mode2-decoder", "jxl", "tiff", None, 2, "IMG_ProPhoto_2.jxl", "file", {}, {}),
    ("file-mode2-recompressor", "jxl", "jxl", "jxl_recompress", 2, "IMG_ProPhoto_2.jxl",
     "file", {}, {}),
    ("file-mode2-transcoder", "jpeg", "jxl", "transcode_lossless", 2, "r0j.jpg", "file",
     {}, {}),
]


@real
@pytest.mark.parametrize("case", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_the_wrappers_plan_is_the_runs_dry_run(menu, library, tmp_path, case):
    """For one manifest row, the wrapper's plan (the row's argv parsed by the
    script, _planned_pairs) names the outputs the script's own --dry-run of
    that very argv names, file by file."""
    _id, origin, dest, conv, mode, rel, where, wf_extra, adv = case
    source = library / rel if rel else library
    dest_path = {"out": library.parent.parent / "out [x]", "same": source,
                 "file": source}[where]
    wf, adv = _row(origin, dest, conv, wf_extra, adv)
    planned = _wrapper_plan(menu, origin, dest, source, dest_path, mode, wf, adv)
    child = wp._child_module(origin, dest)
    cmd = [str(c) for c in menu.build_child_cmd(
        str(REPO / f"{child}.py"), origin, dest, str(source), str(dest_path), mode, 1,
        wf, adv, manifest=True)]
    ran, unplaced = _dry_run_plan(cmd, tmp_path / "logs")
    assert ran, f"the scenario writes nothing: {cmd}"
    assert {n: o for n, o in planned.items() if n not in unplaced} == ran, cmd


@real
def test_the_scan_sees_a_resized_jpeg_archive_renamed_onto_another_rows_output(
        menu, root, originals):
    """#663, the review's case: row A holds a JPEG archive (jbrd), row B a
    plain JXL named like A's renamed output; both rows decode to one folder
    with a resize. The resize re-encodes A's archive under the new name, so
    both rows write IMG_sRGB_001.jpg: the second was skipped as existing, the
    photo of row B left without its JPEG and nothing said so."""
    a, b, out = root / "sub A", root / "sub B", root / "out [x]"
    a.mkdir()
    b.mkdir()
    shutil.copyfile(originals / "jbrd.jxl", a / "IMG_ProPhoto_001.jxl")
    shutil.copyfile(originals / "plain.jxl", b / "IMG_sRGB_001.jxl")
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    analyzer = wp.FolderAnalyzer(Path("."), "jxl", "jpeg", "_EXPORT")

    def scan(adv):
        wf, adv = _row("jxl", "jpeg", "jxl_to_jpeg_auto", None, adv)
        return menu._manifest_step_output_collisions(
            wf, "jxl", "jpeg", entries, analyzer, "_EXPORT", [("_EXPORT", None, None)] * 2,
            [(wf, adv)] * 2, {})

    assert scan(dict(_RENAME, resize_mode="long", resize_value=16)) is False
    assert scan(dict(_RENAME, sharpen="screen")) is False
    # The control: without the resize the archive is recovered with its own
    # name — no collision.
    assert scan(dict(_RENAME)) is True


def test_the_scan_follows_the_output_folder_name_and_the_expert_flags(menu, root):
    """#664: the wizard's 'Output folder name' (modes 1/3 of a lossy
    conversion) and an expert --output-name reached the run, not the plan:
    the cross-row check got recovered_jpeg/converted_jxl instead."""
    shoot = root / "shoot [a]"
    _touch(shoot / "DSC 0001 (2).jpg")
    for extra, name in (({"extra_options": {"output_name": "custom out [c]"}},
                         "custom out [c]"),
                        ({"expert_flags": "--output-name perito"}, "perito")):
        wf, adv = _row("jpeg", "jxl", "convert_lossy", extra)
        sink = {}
        menu._manifest_output_collisions(
            [(str(shoot), str(shoot), 3)], {".jpg", ".jpeg"}, origin="jpeg", dest="jxl",
            export_marker="_EXPORT", conversion_type="convert_lossy", cross_sink=sink,
            row_effs=[(wf, adv)])
        assert sink == {0: {_norm(shoot / name)}}, (extra, sink)


def test_the_scan_follows_an_expert_export_folder(menu, root):
    """#664: --export-jxl-folder in the expert flags moves a mode-6 run's
    masters; the plan kept the setting's folder."""
    lib = root / "lib [2025]"
    _touch(lib / "2025_EXPORT" / "TIFF16" / "DSC 0001 (2).tif")
    wf, adv = _row("tiff", "jxl", None, {"expert_flags": "--export-jxl-folder JXL_perito"})
    sink = {}
    menu._manifest_output_collisions(
        [(str(lib), str(lib), 6)], {".tif", ".tiff"}, origin="tiff", dest="jxl",
        export_marker="_EXPORT", cross_sink=sink, row_effs=[(wf, adv)])
    assert sink == {0: {_norm(lib / "2025_EXPORT" / "JXL_perito")}}, sink


def test_the_plan_builds_the_runs_argv_without_its_notes(menu, root, capsys):
    """The plan's argv is the run's, built quietly: the notes some rules
    print belong to the run. A manifest prints the 'exclusion not supported'
    warning once per run — a plan that printed it would also have used up
    that once, and the run's own line would never show."""
    shoot = root / "shoot [a]"
    _touch(shoot / "DSC 0001 (2).jxl")
    wf, adv = _row("jxl", "jxl", "jxl_recompress", {"exclude_folders": "OLD_TIFFs"},
                   {"provenance": "adopt"})
    menu._manifest_output_collisions(
        [(str(shoot), str(root / "out [x]"), 2)], {".jxl"}, origin="jxl", dest="jxl",
        export_marker="_EXPORT", row_effs=[(wf, adv)], cross_sink={})
    out = capsys.readouterr().out
    assert "not supported" not in out and "adopt" not in out, out
    assert not menu.__dict__.get('_exclude_folder_warned'), "the run's warning was used up"
    cmd = menu._build_manifest_entry_cmd("x.py", str(shoot), str(root / "out [x]"), 2,
                                         "jxl", "jxl", 1, wf, adv)
    out = capsys.readouterr().out
    assert "not supported" in out and "adopt" in out, out
    assert cmd == menu.build_child_cmd("x.py", "jxl", "jxl", str(shoot),
                                       str(root / "out [x]"), 2, 1, wf, adv,
                                       manifest=True, quiet=True)


def test_the_count_follows_expert_flags_that_pick_the_files(menu, root):
    """#664: the delete preview counted the files of the wizard's subfolder
    and exclusion while expert flags (applied last) picked others."""
    for sub, n in (("TIFF16", 1), ("TIFF_X", 3)):
        for i in range(n):
            _touch(root / "2025_EXPORT" / sub / f"DSC {i} [b].tif")
    _touch(root / "skipme" / "DSC 9.tif", root / "keep" / "DSC 8.tif")
    wf = {"origin_format": "tiff", "dest_format": "jxl", "input_dir": str(root),
          "mode_config": {"export_subfolder": "TIFF16"},
          "expert_flags": "--export-subfolder TIFF_X"}
    assert menu._count_origin_files(wf, 7) == 3
    wf = {"origin_format": "tiff", "dest_format": "jxl", "input_dir": str(root),
          "expert_flags": "--exclude-folders skipme"}
    assert menu._count_origin_files(wf, 2) == 5          # all but skipme/


@pytest.mark.parametrize("origin,dest,conversion,script,name", [
    ("jxl", "tiff", None, "jxl_tiff_decoder.py", "DSC 0001 (2).jxl"),
    ("jxl", "jpeg", "jxl_to_jpeg_auto", "jxl_jpeg_transcoder.py", "DSC 0001 (2).jxl"),
    ("jpeg", "jxl", "transcode_lossless", "jxl_jpeg_transcoder.py", "DSC 0001 (2).jpg"),
])
def test_one_file_inside_the_marker_counts_zero_in_a_marker_mode(menu, root, tmp_path,
                                                                 origin, dest, conversion,
                                                                 script, name):
    """#667 (#656's other half): the decoder and the transcoder refuse a FILE
    in modes 6/7 (exit 2); the count said 1 for one inside an export folder."""
    f = root / "2025_EXPORT" / "sub [b]" / name
    _touch(f)
    wf = {"origin_format": origin, "dest_format": dest, "input_dir": str(f),
          "conversion_type": conversion}
    assert menu._count_origin_files(wf, 6) == 0
    assert menu._count_origin_files(wf, 0) == 1          # the run takes it there
    r = subprocess.run([sys.executable, str(REPO / script), str(f), "--mode", "6",
                        "--dry-run"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=_env(tmp_path / "logs"),
                       stdin=subprocess.DEVNULL, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


# ── #666 ─────────────────────────────────────────────────────────────────────

@real
@pytest.mark.parametrize("origin,dest,conv,name", [
    ("tiff", "jxl", None, "r0 (2).tif"),
    ("jxl", "tiff", None, "IMG_ProPhoto_2.jxl"),
    ("jxl", "jxl", "jxl_recompress", "IMG_ProPhoto_2.jxl"),
    ("jpeg", "jxl", "transcode_lossless", "r0j.jpg"),
])
def test_a_file_row_in_mode_2_converts_where_the_plan_says(menu, library, tmp_path,
                                                          origin, dest, conv, name):
    """A manifest row with a FILE Source, mode 2 and an empty Destination (the
    loader puts the file itself there): the file was sent as the output
    positional and every script failed the row ('1 errors', nothing
    written). It now writes where that script's mode 2 puts a single file,
    which is where the scan said."""
    src = library / name
    # A JXL without an encode record: re-encode it (the owner's setting may
    # say skip, which writes nothing to look for).
    wf, adv = _row(origin, dest, conv, None,
                   {"on_unknown": "convert"} if (origin, dest) == ("jxl", "jxl") else {})
    planned = _wrapper_plan(menu, origin, dest, src, src, 2, wf, adv)
    assert list(planned) == [name], planned
    cmd = menu._build_manifest_entry_cmd(
        str(REPO / f"{wp._child_module(origin, dest)}.py"), str(src), str(src), 2,
        origin, dest, 1, wf, adv)
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=_env(tmp_path / "logs"),
                       stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert Path(planned[name]).is_file(), planned


# ── #665 ─────────────────────────────────────────────────────────────────────

def _counting(monkeypatch, mod, name, calls):
    real_fn = getattr(mod, name)

    def counted(*a, **k):
        calls.append(name)
        return real_fn(*a, **k)

    monkeypatch.setattr(mod, name, counted)


def test_the_count_places_no_file_outside_the_marker_modes(root, monkeypatch):
    """Only modes 6/7 leave a file out, so only there does the count need
    each file's place: it resolved every output of every mode."""
    for i in range(5):
        _touch(root / f"sub {i} [b]" / f"IMG_{i}.jxl")
    calls = []
    for name in ("resolve_output_convert", "resolve_output_transcode"):
        _counting(monkeypatch, tr, name, calls)
    ns = tr.build_parser().parse_args([str(root), "--mode", "2", "--from-jxl"])
    assert len(tr._planned_sources(ns, read_markers=False)) == 5
    assert calls == []
    ns = tr.build_parser().parse_args([str(root), "--mode", "6", "--from-jxl"])
    assert tr._planned_sources(ns, read_markers=False) == []       # no marker
    assert len(calls) == 5


def test_the_scan_routes_once_and_places_each_file_once(menu, root, monkeypatch):
    """The scan asked _planned_sources (one place per file) and then
    _planned_output (main()'s routing again, and the place again) for every
    file: twice the work, plus a determine_command per file."""
    for i in range(4):
        _touch(root / "sub [b]" / f"IMG {i}.jxl")
    calls = []
    for name in ("resolve_output_convert", "resolve_output_transcode", "determine_command"):
        _counting(monkeypatch, tr, name, calls)
    menu._manifest_output_collisions(
        [(str(root), str(root / "out [x]"), 2)], {".jxl"}, origin="jxl", dest="jpeg",
        export_marker="_EXPORT", conversion_type="jxl_to_jpeg_force")
    assert calls.count("determine_command") == 1, calls
    assert calls.count("resolve_output_convert") == 4, calls
    assert "resolve_output_transcode" not in calls, calls


def test_the_finders_resolve_each_folder_not_each_file(root, monkeypatch):
    """resolve() opened every file twice (GetFinalPathNameByHandle): three
    quarters of a 40 000-file count. The real path of a plain file is its
    folder's, resolved once, plus its name."""
    for d in range(3):
        for i in range(4):
            _touch(root / f"shoot {d} [b]" / f"IMG {i}.jxl")
    calls = []
    real_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve",
                        lambda self, *a, **k: calls.append(self) or real_resolve(self, *a, **k))
    files = tr.find_jxls_recursive(root)
    monkeypatch.undo()
    assert len(files) == 12
    assert len(calls) <= 3, calls


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows junction")
def test_a_folder_reached_through_a_junction_is_taken_once(root):
    """The dedup the resolve() gave, kept: a shoot reached a second time
    through a junction inside the tree is not converted twice."""
    import _winapi
    shoot = root / "shoot [a]"
    _touch(shoot / "IMG 1.jxl", shoot / "IMG 2.jpg")
    try:
        _winapi.CreateJunction(str(shoot), str(root / "link to shoot"))
    except OSError as e:
        pytest.skip(f"cannot create a junction here: {e}")
    assert [f.name for f in tr.find_jxls_recursive(root)] == ["IMG 1.jxl"]
    assert [f.name for f in tr.find_jpegs_recursive(root)] == ["IMG 2.jpg"]


# ── #668 ─────────────────────────────────────────────────────────────────────

def test_the_decoder_probes_djxl_once_and_the_history_reads_that(monkeypatch):
    """The decoder's version check ran `djxl --version` itself, so the first
    History entry asked again inside a worker (a probe that timed out there
    left the run's History without its libjxl version). One probe now, before
    the workers, through _tool_version's cache."""
    probes = []
    real_run = subprocess.run

    def fake_run(cmd, *a, **k):
        if list(cmd)[1:] == ["--version"]:
            probes.append(cmd[0])
            return SimpleNamespace(stdout="djxl v0.12.0 [AVX2]", stderr="", returncode=0)
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", fake_run)
    dec._warn_if_libjxl_too_old("djxl")
    line = dec._history_args("converted", "a.jxl", "b.tif", codec="djxl")[0]
    assert probes == ["djxl"], probes
    assert "with libjxl 0.12.0" in line, line


# ── the scan and the count read no marker ────────────────────────────────────

@pytest.mark.parametrize("origin,dest,conversion", [
    ("jxl", "jpeg", "jxl_to_jpeg_auto"),
    ("jxl", "jpeg", "jxl_to_jpeg_force"),
    ("jxl", "jxl", "jxl_recompress"),
])
def test_the_collision_scan_reads_no_marker_where_nothing_collides(menu, root, monkeypatch,
                                                                  origin, dest,
                                                                  conversion):
    """The scan reads the derivative markers of the files that COLLIDE only
    (_without_derivative_collisions); a scan that read every JXL's markers in
    modes 6/7 would run exiftool over the whole library on every manifest
    (the review's M17)."""
    for name in ("DSC_0001 (2).jxl", "DSC_0002 [b].jxl"):
        _touch(root / "2025_EXPORT" / "16B_JXL" / name)
    reads = []
    for mod in (tr, rec):
        monkeypatch.setattr(mod, "_read_derived_markers_batch",
                            lambda paths: reads.append(list(paths)) or {})
    sink = {}
    cols = menu._manifest_output_collisions(
        [(str(root), str(root), 6)], {".jxl"}, origin=origin, dest=dest,
        export_marker="_EXPORT", conversion_type=conversion, cross_sink=sink)
    assert cols == [] and sink.get(0), sink
    assert reads == [], "the scan read the derivative markers"
