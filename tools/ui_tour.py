#!/usr/bin/env python3
"""Walk every screen of the wizard (jxl_photo.py) and save what each shows.

    python tools/ui_tour.py [--out FOLDER] [SESSION ...]

For reviewing the interface by eye: each session feeds the wizard a fixed
sequence of answers on stdin (Rich rendering, 110 columns) and saves the
screen to FOLDER/screens/<session>.txt. A session that ends with rc 130 only
ran out of answers somewhere — read its file to see where.

Everything happens under FOLDER (default: a new folder in %TEMP%): a copy
of the five scripts (so the repo's own manifests/ folder and settings overlay,
which hold the owner's local files, never change what a screen shows), a home
folder with its own settings (a last job and one preset), an empty library
whose folder name has Japanese characters, [ ], a space and % (AGENTS.md),
and the logs. Only dry runs and audits run, on that empty library; nothing
outside FOLDER is read or written, and the update check is off.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = ("jxl_photo.py", "jxl_tiff_encoder.py", "jxl_tiff_decoder.py",
           "jxl_jpeg_transcoder.py", "jxl_recompressor.py")


def _sessions(lib: Path):
    return {
        "01_start_and_menu": "0\n",
        "02_presets": "3\nb\n0\n",
        "03_repeat_last": "2\n\n2\ny\nn\n0\n",
        "04_folder_list": "4\n1\nn\n0\n0\n",
        "05_renamed_folders": "5\n\n6\n\n\nn\n0\n",
        "06_thumbnails": "6\n\nn\n1\ny\n0\n",
        "07_jbrd": "7\n\ny\n0\n",
        "08_icc_cache": "8\nn\n0\n",
        "09_settings_submenu": "9\n0\n0\n",
        "10_settings_defaults": "9\n1\n" + "\n" * 9 + "0\n0\n",
        "11_script_settings": "9\n2\n1\n0\n0\n0\n",
        "12_dependencies": "D\n0\n",
        "13_job_archive_review": f"1\n1\n{lib}\n\n6\n\n\n\nc\nx\n\nq\n0\n",
        "14_job_all_options": f"1\n1\n{lib}\n\n6\n\n\n\na\n\nq\n0\n",
        "15_job_mode_details": f"1\n1\n{lib}\n\n?\n1\n\n3\nq\n0\n",
        "16_job_delete_gateway": f"1\n1\n{lib}\n\nD\ny\n6\n\n\nn\n1\n\n3\nq\n0\n",
        "17_job_auto": f"1\n1\n{lib}\n\nA\nN\n1\n\n3\nq\n0\n",
        "18_job_export_jpeg": f"1\n4\n{lib}\n\n6\n\n\n\n\nq\n0\n",
        "19_job_derive": f"1\n3\n{lib}\n\n6\n\n\n\n\nq\n0\n",
        "20_job_restore": f"1\n2\n{lib}\n\n6\n\n\nq\n0\n",
        "21_job_dry_run": f"1\n1\n{lib}\n\n6\n\n\n\nd\nq\n0\n",
        "22_job_save_preset": f"1\n1\n{lib}\n\n6\n\n\n\ns\nNEW [x] 5%\nq\n3\nb\n0\n",
    }


def _seed_config(app: Path, home: Path, lib: Path, env: dict) -> None:
    """A last job and one preset, written by the wrapper's own ConfigManager."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import jxl_photo as wp\n"
        "cfg = wp.ConfigManager(); c = cfg.config\n"
        "c.last_input_dir = sys.argv[2]\n"
        "c.last_origin_format, c.last_dest_format = 'tiff', 'jxl'\n"
        "c.last_conversion_type = 'jxl_tiff_encoder'; c.last_output_mode = '6'\n"
        "c.last_distance, c.last_effort, c.last_workers = 0.05, 7, 8\n"
        "cfg.save_config(); cfg.save_preset('MASTERS 2025 [x]', cfg.session_snapshot())\n")
    subprocess.run([sys.executable, "-c", code, str(app), str(lib)], env=env,
                   check=True, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", help="folder for the run (default: a new one in %%TEMP%%)")
    ap.add_argument("sessions", nargs="*", help="only these sessions (default: all)")
    args = ap.parse_args()

    root = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="jxlphoto_ui_tour_"))
    home, screens, app = root / "home", root / "screens", root / "app"
    lib = root / "lib" / "250118_吾妻山公園 [FINAL] 5%"
    for p in (home, screens, app / "manifests", lib / "01_Z7" / "_EXPORT" / "TIFF16"):
        p.mkdir(parents=True, exist_ok=True)
    for name in SCRIPTS:
        shutil.copy2(REPO / name, app / name)
    env = dict(os.environ, USERPROFILE=str(home), HOME=str(home),
               JXLPHOTO_LOG_DIR=str(root / "logs"), JXLPHOTO_NO_UPDATE_CHECK="1",
               PYTHONIOENCODING="utf-8", COLUMNS="110", JXLPHOTO_JXL_THUMB="0")
    env.pop("JXLPHOTO_SETTINGS", None)
    _seed_config(app, home, lib, env)

    sessions = _sessions(lib)
    unknown = [s for s in args.sessions if s not in sessions]
    if unknown:
        print(f"unknown session(s): {', '.join(unknown)}; known: {', '.join(sessions)}")
        return 2
    for name, keys in sessions.items():
        if args.sessions and name not in args.sessions:
            continue
        r = subprocess.run([sys.executable, str(app / "jxl_photo.py")], input=keys,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=str(app), timeout=300)
        text = r.stdout + ("\n--- stderr ---\n" + r.stderr if r.stderr.strip() else "")
        (screens / f"{name}.txt").write_text(text, encoding="utf-8")
        print(f"{name:26} rc {r.returncode:>3}  {len(r.stdout.splitlines()):>4} lines")
    print(f"screens: {screens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
