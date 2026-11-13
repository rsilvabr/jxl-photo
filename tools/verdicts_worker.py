"""One version's verdicts (run by verdicts.py, one process per version):

    python tools/verdicts_worker.py <code folder> <trees folder> <out.json>

Imports jxl_photo.py from <code folder> and answers, for every count input
(the trees and a few single files), direction, conversion type, mode and
exclusion: _count_origin_files; and for every manifest the collisions and the
cross_sink of _manifest_output_collisions."""
import json
import os
import sys
import tempfile
from pathlib import Path

code, trees, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(code))
os.environ["JXLPHOTO_SETTINGS"] = ""
os.environ["JXLPHOTO_JXL_THUMB"] = "0"
os.environ.setdefault("JXLPHOTO_LOG_DIR", tempfile.mkdtemp(prefix="f8_logs_"))
os.chdir(tempfile.mkdtemp(prefix="f8_cwd_"))
import jxl_photo as wp  # noqa: E402

cfg_path = Path(tempfile.mkdtemp(prefix="f8_cfg_")) / ".jxl_tools_config.json"
wp.ConfigManager._get_config_path = lambda self: cfg_path
cfg = wp.ConfigManager()
menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
wp.RICH_AVAILABLE = False
wp.console = None

PLANS = [("tiff", "jxl", "jxl_tiff_encoder"), ("jxl", "tiff", "jxl_tiff_decoder"),
         ("jxl", "jxl", "jxl_recompress"),
         ("jpeg", "jxl", "transcode_lossless"), ("jpeg", "jxl", "convert_lossy"),
         ("jpeg", "jxl", "unknown_type"),
         ("jxl", "jpeg", "jxl_to_jpeg_auto"), ("jxl", "jpeg", "jxl_to_jpeg_lossless"),
         ("jxl", "jpeg", "jxl_to_jpeg_force"), ("jxl", "png", "jxl_to_png")]
EXCLUDES = [None, "OLD_TIFFs"]


def rel(p):
    try:
        return str(Path(p).relative_to(trees))
    except ValueError:
        return str(p)


result = {"counts": {}, "collisions": {}}
inputs = json.loads((trees / "count_inputs.json").read_text(encoding="utf-8"))
for item in inputs:
    for origin, dest, conv in PLANS:
        for mode in range(9):
            for exc in EXCLUDES:
                for sub in ((None, "TIFF16") if mode == 7 else (None,)):
                    wf = {"origin_format": origin, "dest_format": dest,
                          "input_dir": str(trees / item),
                          "mode_config": {"export_subfolder": sub} if sub else {},
                          "exclude_folders": exc, "conversion_type": conv}
                    key = f"{item}|{origin}->{dest} {conv}|mode {mode}|sub {sub}|exclude {exc}"
                    try:
                        result["counts"][key] = menu._count_origin_files(wf, mode)
                    except Exception as e:
                        result["counts"][key] = f"RAISED {type(e).__name__}: {e}"

manifests = json.loads((trees / "manifests.json").read_text(encoding="utf-8"))
for name, m in sorted(manifests.items()):
    origin, dest = m["origin"], m["dest"]
    entries = [(str(trees / s), str(trees / d) if d else "", mode) for s, d, mode in m["rows"]]
    exts = wp.FolderAnalyzer(Path("."), origin, dest)._get_extensions(origin)
    for exc in EXCLUDES:
        sink = {}
        key = f"{name} | exclude {exc}"
        try:
            cols = menu._manifest_output_collisions(
                entries, exts, origin=origin, dest=dest, export_marker="_EXPORT",
                export_subfolder=m.get("subfolder"),
                export_jxl_folder=m.get("export_jxl_folder"),
                rename_from=m.get("rename_from", ""), rename_to=m.get("rename_to", ""),
                row_renames=None, row_export=None, row_excludes=[exc] * len(entries),
                cross_sink=sink, conversion_type=m["conv"], page_options={})
            result["collisions"][key] = {
                "collisions": sorted([rel(a), rel(b), rel(d)] for a, b, d in cols),
                "cross_sink": {str(k): sorted(rel(x) for x in v)
                               for k, v in sorted(sink.items())}}
        except Exception as e:
            result["collisions"][key] = f"RAISED {type(e).__name__}: {e}"

out.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
print(f"{len(result['counts'])} counts, {len(result['collisions'])} manifests")
