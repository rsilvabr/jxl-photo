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
| `6` | Marker _EXPORT (full) | ONLY files INSIDE `_EXPORT` — ignores everything outside |
| `7` | Marker _EXPORT (subfolder) | Like mode 6 but only specific `_EXPORT` subfolder (e.g. `_EXPORT/JXL`) |
| `8` | DELETE originals ⚠️ | Same as mode 0 but deletes source files — IRREVERSIBLE |

Items shown in **green** (like `_EXPORT`) are configurable in **option 4 (Edit default settings)**. Other folder names (like `converted_jxl`, `16B_JXL`, `16B_TIFF`) must be edited directly in the scripts.

### Modes 6 and 7

**These modes ONLY process files inside `_EXPORT` folders. Everything outside is IGNORED.**

```
E:\sessao\
├── foto1.jpg          ← NOT processed (outside _EXPORT)
├── foto2.jpg          ← NOT processed (outside _EXPORT)
└── _EXPORT\
    ├── folder1\
    │   └── img.tif    ← PROCESSED ✓
    ├── folder2\
    │   └── img.tif    ← PROCESSED ✓
    └── folder3\sub\
        └── img.tif    ← PROCESSED ✓
```

**Mode 6** — processes ALL files under ALL `_EXPORT` folders found recursively.

**Mode 7** — like mode 6, but only files inside a SPECIFIC subfolder of `_EXPORT`.
Default: `_EXPORT/JXL` (configurable via `EXPORT_JXL_SUBFOLDER` / `EXPORT_JPEG_SUBFOLDER`).

```
_EXPORT/
├── JXL\               ← PROCESSED ✓ (matches subfolder filter)
│   └── img.jxl
├── AdobeRGB\          ← IGNORED ✗ (doesn't match subfolder filter)
│   └── img.jxl
└── sRGB\              ← IGNORED ✗ (doesn't match subfolder filter)
    └── img.jxl
```

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

## What can be configured in the wizard vs scripts

Some options are available directly in the wizard, others must be edited in the script files themselves.

### ✅ Available in the wizard (Step 6 / 6A)

| Option | Location | Notes |
|--------|----------|-------|
| Workers | Step 6 | All workflows |
| Quality / Distance | Step 6 | Context-aware |
| Effort | Step 6 | All workflows |
| Staging directory | Step 6 | TIFF→JXL, JXL→TIFF |
| Overwrite mode (0/1/2) | Step 6 | Always asked |
| ICC conversion (sRGB) | Step 6 | JXL→JPEG/PNG |
| TIFF compression | Step 6 | zip/lzw/none |
| Bit depth | Step 6 | 8 or 16 for TIFF output |
| Dry run | Step 6 | All workflows |
| Strip metadata | 6A | TIFF→JXL |
| D50 patch mode | 6A | auto / on / off |
| Encode tag location | 6A | xmp / software / off |
| ICC matrix mode | 6A | JXL→TIFF |
| Target ICC profile | 6A | JXL→TIFF |
| Skip ICC cleanup | 6A | JXL→TIFF |
| Skip MD5 verification | 6A | JPEG↔JXL |
| Skip validation | 6A | JPEG↔JXL (risky) |
| Output suffix | 6A | JPEG↔JXL |
| Expert flags | 6B | Custom CLI args |

### ⚙️ Available in option 4 (Edit default settings)

| Option | Notes |
|--------|-------|
| Staging directory | Persisted across sessions |
| Default workers | Persisted |
| Default quality | Persisted |
| Default effort | Persisted |
| Confirm deletes | Safety toggle |
| Export marker | Default: `_EXPORT` |

### 🔧 Must be edited directly in the scripts

These are hardcoded global variables at the top of each script. To change them, open the script file and edit the variable at the top.

#### jxl_tiff_encoder.py
| Variable | Default | What it does |
|----------|---------|--------------|
| `CONVERTED_JXL_FOLDER` | `"converted_jxl"` | Mode 1 subfolder name |
| `JXL_FOLDER_NAME` | `"JXL_16bits"` | Mode 3/5 sibling folder |
| `EXPORT_MARKER` | `"_EXPORT"` | Path anchor for modes 6/7 |
| `EXPORT_JXL_FOLDER` | `"16B_JXL"` | Mode 6/7 output folder |
| `TIFF_SUFFIX_TO_REPLACE` | `"TIFF"` | Mode 4 suffix match |
| `JXL_SUFFIX_REPLACE` | `"JXL"` | Mode 4 suffix replacement |
| `EMBED_ICC_IN_JXL` | `True` | Embed ICC in JXL metadata |
| `ENCODE_TAG_MODE` | `"xmp"` | Where to record d=/e= (now also via `--encode-tag`) |
| `USE_RAM_FOR_PNG` | `True` | Keep PNG intermediate in RAM |
| `DELETE_CONFIRM` | `True` | Require HHMMSS confirmation for mode 8 delete |

#### jxl_tiff_decoder.py
| Variable | Default | What it does |
|----------|---------|--------------|
| `CONVERTED_TIFF_FOLDER` | `"converted_tiff"` | Mode 1 subfolder name |
| `TIFF_FOLDER_NAME` | `"TIFF_16bits"` | Mode 3/5 sibling folder |
| `EXPORT_MARKER` | `"_EXPORT"` | Path anchor for modes 6/7 |
| `EXPORT_TIFF_FOLDER` | `"16B_TIFF"` | Mode 6/7 output folder |
| `JXL_SUFFIX_TO_REPLACE` | `"JXL"` | Mode 4 suffix match |
| `TIFF_SUFFIX_REPLACE` | `"TIFF"` | Mode 4 suffix replacement |
| `ADD_JPEG_PREVIEW` | `True` | Embed JPEG preview in output TIFF |
| `JPEG_PREVIEW_SIZE` | `1024` | Max preview dimension |
| `USE_MATRIX_MODE` | `False` | Force ICC matrix conversion |
| `CLEANUP_XMP_ICC_MARKER` | `True` | Remove ICC base64 from XMP after extraction |

#### jxl_jpeg_transcoder.py
| Variable | Default | What it does |
|----------|---------|--------------|
| `CONVERTED_JXL_FOLDER` | `"converted_jxl"` | Mode 1 subfolder name |
| `EXPORT_MARKER` | `"_EXPORT"` | Path anchor for modes 6/7 |
| `EXPORT_JXL_FOLDER` | `"JXL_jpeg"` | Mode 6/7 output folder for JXL |
| `EXPORT_JPEG_FOLDER` | `"JPEG_recovered"` | Mode 6/7 output folder for JPEG |
| `JPEG_DEFAULT_QUALITY` | `95` | Default JPEG quality |
| `PNG_DEFAULT_BIT_DEPTH` | `16` | Default PNG bit depth |
| `STORE_MD5` | `True` | Store MD5 for losslessness verification |
| `DELETE_CONFIRM` | `True` | Require confirmation for mode 8 delete |
| `FORCE_CONTAINER_FOR_LOSSY` | `True` | Always pass `--container=1` for lossy |

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

## Changes since v1.0

### New Features
- **D50 patch option** — wizard now asks for D50 patch mode (auto/on/off) in Step 6A for TIFF→JXL workflows
- **Lossy JPEG→JXL** — fixed cjxl 0.11.2 incompatibility: added `--lossless_jpeg=0` when distance>0

### Bug Fixes
- Race condition in staging (UUID-based filenames in all scripts)
- Distance not passed to cjxl for PNG→JXL encoding
- Wrong delete confirmation for lossy operations (HHMM vs yes)
- Deadlock in djxl+ImageMagick pipeline (threaded stderr reader)
- PPM truncation validation
- Integer overflow in JXL box parser (size limits)
- Missing UUID in process_group_transcode staging
- D50 patch not preserved when repeating last workflow
- Invalid --resize option removed (not supported by any script)

Full tracking: [bug_tracking_since_v1.0.md](./bug_tracking_since_v1.0.md)

* * *

## License

MIT License — feel free to use, modify, and distribute.

* * *

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O
- [MiniMax](https://www.minimax.io/) (MiniMax AI) and [Kimi](https://www.kimi.com) (Moonshot AI) for code assistance and technical discussion
