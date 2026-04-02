# jxl_photo.py

Interactive wrapper for the JPEG XL processing toolkit. Provides a **wizard-style menu interface** that guides you through all conversion options — no need to remember CLI flags for each script.

Handles TIFF → JXL, JXL → TIFF, JPEG → JXL, JXL → JPEG, and JXL → PNG conversions through a unified menu system with persistent configuration.

* * *

## Requirements

```
Python 3.10+
rich            (optional — enables fancy UI, auto-detected)
tifffile        (for TIFF workflows)
numpy           (tifffile dependency)
cjxl / djxl →  https://github.com/libjxl/libjxl/releases
exiftool    →  https://exiftool.org
ImageMagick →  https://imagemagick.org  (for JXL -> JPEG/PNG ICC conversion)
```

Quick setup (PowerShell, then reopen terminal):
```powershell
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", "$p;C:\tools\libjxl\bin;C:\tools\exiftool;C:\Program Files\ImageMagick-7.1.1-Q16-HDRI", "User")
```

If `rich` is not installed, the tool falls back to plain text mode automatically.

Verify:
```powershell
cjxl --version
djxl --version
exiftool -ver
magick --version
```

* * *

## Quick start

```powershell
# Just run — dependency check happens automatically
py jxl_photo.py
```

The tool shows a status bar with all detected dependencies, then presents the main menu:

```
╭───────────────────────────────────────────── JXL Tools Environment ────────────────────────────────────────────────╮
│ [✓] cjxl/djxl | [✓] exiftool | [✓] magick | [✓] tifffile | [✓] pillow | [✓] rich                                 │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭───────────────────────────────────────────────────── Main Menu ────────────────────────────────────────────────────╮
│  1  New workflow                                                                                                   │
│  2  Repeat last workflow (unknown)                                                                                 │
│  3  Check dependencies again                                                                                       │
│  4  Edit default settings                                                                                          │
│  5  Reset all settings                                                                                             │
│  6  Move settings file                                                                                             │
│  0  Exit                                                                                                           │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

**Typical session:**
```
[1] New workflow
  Step 1: Source Format   → TIFF
  Step 2: Destination     → JXL d=0.1
  Step 3: Directory       → F:\Photos\2024
  Step 4: Mode            → 7 (Marker _EXPORT, subfolder)
  Step 5: Confirmation    → OK
  Step 6: Parameters      → Workers: 8, Distance: 0.1, Effort: 7
  Step 7: Summary         → Review and type YES to confirm
  → Executes the underlying script with all options
```

* * *

## Main menu options

| # | Option | Description |
|---|--------|-------------|
| `1` | New workflow | Start the conversion wizard |
| `2` | Repeat last workflow | Re-run the previous conversion with same settings |
| `3` | Check dependencies again | Re-scan all tools and libraries |
| `4` | Edit default settings | Change workers, quality, effort, export marker |
| `5` | Reset all settings | Delete config and start fresh |
| `6` | Move settings file | Toggle between script folder and User Profile |
| `0` | Exit | Quit |

* * *

## Workflow wizard steps

### Step 1 — Source Format
Choose what type of files to convert:
- **JPEG** — lossless transcoding to JXL
- **TIFF** — encoding to JXL with ICC preservation
- **JXL** — decoding to JPEG, PNG, or TIFF

Unavailable formats (missing dependencies) are shown with `✗` and cannot be selected.

### Step 2 — Destination
Choose the output format based on the source:
- JPEG → JXL Lossless (reversible) / JXL Lossy (smaller)
- TIFF → JXL d=0.1 (near-lossless) / JXL d=0 (lossless)
- JXL → JPEG / PNG / TIFF

### Step 3 — Source Directory
Enter the folder path containing the files. Shows a preview of found files before proceeding.

### Step 4 — Organization Mode
How output files are organized. Press `?` for detailed explanations with visual examples.

| Mode | Name | Description |
|------|------|-------------|
| `0` | In-place | Same folder as source (non-recursive) |
| `1` | Subfolder | Creates `converted_jxl/` or `converted_tiff/` subfolder |
| `2` | Flat -> output folder | All files merged to single output folder (recursive) |
| `3` | Recursive subfolders | Each subfolder gets its own output subfolder |
| `4` | Sibling folder (rename) | Renames folder: `JXL_raw/` → `JXL_processed/` |
| `5` | Folder suffix | Appends suffix: `Raw/` → `Raw_JXL/` |
| `6` | Marker _EXPORT (full) | Processes all files under `_EXPORT` marker |
| `7` | Marker _EXPORT (subfolder) | Only files inside specific `_EXPORT` subfolder |
| `8` | DELETE originals ⚠️ | Same as mode 0 but deletes source files — IRREVERSIBLE |

Items shown in **green** (like `_EXPORT`, `converted_jxl`) are configurable — change them in **option 4 (Edit default settings)** before running.

### Step 5 — Mode-specific configuration
- Modes 6/7: Confirm or change the `_EXPORT` marker name
- Mode 2: Specify the output directory for merged files

### Step 6 — Parameters
Basic parameters always shown:
- **Workers** — parallel threads (default: 4)
- **Quality / Distance / Effort** — context-aware based on conversion type
- **Staging directory** — SSD staging for HDD collections
- **ICC conversion** — for JXL → JPEG/PNG (with ImageMagick)
- **TIFF compression** — zip / lzw / none
- **Bit depth** — 8 or 16
- **Dry run** — simulate without converting

Optional advanced and expert flags follow.

### Step 7 — Summary
Full review of all settings before execution. Type `YES` to confirm.

* * *

## Edit default settings (option 4)

Persistent defaults saved to `~/.jxl_tools_config.json`:
- **Staging directory** — output SSD path
- **Workers** — default thread count
- **Quality** — JPEG quality for lossy workflows
- **Effort** — cjxl effort level (1-10)
- **Confirm deletes** — safety confirmation before destructive operations
- **Export marker** — the folder name anchor for modes 6/7 (default: `_EXPORT`)

* * *

## Settings file location

The config file is stored at:
- **Script folder** — `jxl_photo/.jxl_tools_config.json` (if existing there)
- **User Profile** — `~/.jxl_tools_config.json` (portable, follows the user)

Use **option 6 (Move settings file)** to toggle between the two locations.

* * *

## Relationship with other scripts

`jxl_photo.py` is a **wrapper** — it invokes the individual scripts with the options you select:

| Script | Purpose | Called when... |
|--------|---------|----------------|
| `jxl_tiff_encoder.py` | TIFF → JXL | Source = TIFF |
| `jxl_tiff_decoder.py` | JXL → TIFF | Source = JXL, Dest = TIFF |
| `jxl_jpeg_transcoder.py` | JPEG ↔ JXL / JXL → JPEG/PNG | Source = JPEG, or Source = JXL + Dest = JPEG/PNG |

You can also run any of those scripts directly — `jxl_photo.py` is optional convenience.

* * *

## Dependency status bar

The top bar shows which tools and libraries are available:

| Item | Enables |
|------|---------|
| `cjxl/djxl` | All JXL encoding/decoding |
| `exiftool` | Metadata preservation |
| `magick` | ICC color conversion (JXL → JPEG/PNG) |
| `tifffile` | TIFF workflows (TIFF ↔ JXL) |
| `pillow` | JPEG preview embedding in TIFF |
| `rich` | Fancy UI with colors/panels |

If `rich` is missing, the tool runs in plain-text mode with the same functionality.

* * *

## Logs

Each underlying script writes its own log:
```
<script_folder>/Logs/<script_name>/YYYYMMDD_HHMMSS.log
```

`jxl_photo.py` itself does not write a log — it streams the selected script's output in real-time.

* * *

## Disclaimer

These tools were made for my personal workflow.
Use at your own risk — I am not responsible for any issues you may encounter.

However, if you find any bugs, feel free to report to me — I will gladly try my best to improve this project.

Always test with a small batch before processing important archives.

* * *

## License

MIT License — feel free to use, modify, and distribute.

* * *

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O
- [MiniMax](https://www.minimax.io/) (MiniMax AI) and [Kimi](https://www.kimi.com) (Moonshot AI) for code assistance and technical discussion
