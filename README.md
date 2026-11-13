# jxl_photo — JXL Workflow Manager

Batch JPEG XL conversion tools with **full ICC color profile and EXIF metadata preservation**. Designed for photographers working with 16-bit TIFF files who want compact JXL archives without losing color accuracy or metadata. Tested with Capture One, Lightroom, NX Studio, Photoshop, and Fuji Hyper Utility exported 16-bit TIFFs.

**Current version: v2.30.0** (2026-11-13) · [What's new](#whats-new-in-v2300) · [Upgrading from an older version](docs/upgrading.md) · [All releases](docs/version_history.md)

**Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest) — unzip and double-click; cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included.

---

## Why JPEG XL?

Spectacular compression with no compromise on bit depth.

- Lossless 16-bit files much smaller than TIFF and TIFF with ZIP/Deflate
- Lossy 16-bit files — small files that retain full 16-bit tonal information, something no other common format achieves (JPEG is 8-bit, TIFF lossless is large)
- This is genuinely new: small lossy files, but with 16-bit color depth

Typical sizes in MB of ProPhoto exports from Capture One (median of 10–20 photos per resolution, ISO 64–4000). ZIP is the TIFF compression tiff-workflow writes. Detail and noise move the size more than the camera does — [every distance, with the spread across the photos](docs/file_sizes.md#every-distance-from-a-16-bit-and-an-8-bit-tiff):

| Format | 45 MP (Z7, Z8) | 24 MP (Zf) | 12 MP (D700) |
|--------|:--------------:|:----------:|:------------:|
| TIFF 16-bit / with ZIP | 273 / 227 | 146 / 120 | 72 / 60 |
| TIFF 8-bit / with ZIP | 136 / 61 | 73 / 30 | 36 / 16 |
| JXL lossless, from 16-bit | 173 | 89 | 43 |
| JXL lossless, from 8-bit | 43 | 21 | 10 |
| JXL `d=0.05` (finest lossy) | 42 | 21 | 9.9 |
| JXL `d=0.1` (near-lossless) | 31 | 15 | 7.2 |
| JXL `d=0.5` (high quality) | 11 | 4.9 | 2.4 |
| JXL `d=1.0` (visually lossless) | 5.5 | 2.7 | 1.2 |
| JXL `d=3.0` (web) | 1.8 | 0.9 | 0.4 |

`d=0.1` is the default and the recommendation for masters; film scans and 8-bit TIFFs are archived lossless on their own. The grain tests behind that, the measurements and the comparison with JPEG and ZIP-compressed TIFF: [docs/why_jxl.md](docs/why_jxl.md). Per-photo numbers: [docs/file_sizes.md](docs/file_sizes.md).

---

## Features

- **The original ICC profile comes back.** Plain cjxl/djxl give back a TIFF with a generic profile; the encoder stores the original profile in the JXL and the decoder puts it back byte for byte, even from a lossy file ([how](docs/commands_and_settings.md#how-the-icc-profile-is-preserved)).
- **TIFF → JXL masters**: 16-bit, lossless or near-lossless, EXIF/XMP visible in IrfanView and XnView MP; multi-page TIFFs and film scans (IR page included) split and rebuilt page for page.
- **JXL → TIFF**: the TIFF back in its original profile, to edit again.
- **JPEG ↔ JXL**: lossless, with the bit-exact JPEG back on demand.
- **Copies made from the masters**: lighter 16-bit JXLs and JPEG/PNG for sharing, with colour conversion, resize and output sharpening fitted to Capture One's presets.
- **The right distance for each file**: in one run, 8-bit TIFFs and film scans are archived lossless while the rest gets your distance.
- **Batch work**: folder modes for flat, recursive and Capture One / Lightroom export layouts, parallel workers, SSD staging, [manifests](docs/README_manifest.md) and presets that run unattended.
- **Archive and replace, safely**: `--delete-source` deletes a source only after its output is written, checked and decoded back to the same pixels; every output records which source made it.
- **Fast Explorer thumbnails in the right colours**, with [jxl-thumb](https://github.com/rsilvabr/jxl-thumb).

| Script | What it does |
|--------|--------------|
| [`jxl_photo.py`](jxl_photo.py) | The interactive wizard: guided jobs, Auto Mode, presets, manifests |
| [`jxl_tiff_encoder.py`](jxl_tiff_encoder.py) | TIFF → JXL masters |
| [`jxl_tiff_decoder.py`](jxl_tiff_decoder.py) | JXL → TIFF, original profile restored |
| [`jxl_jpeg_transcoder.py`](jxl_jpeg_transcoder.py) | JPEG ↔ JXL lossless; JXL → JPEG/PNG copies |
| [`jxl_recompressor.py`](jxl_recompressor.py) | JXL → JXL: smaller archives and 16-bit copies (sRGB, resized, sharpened) |

---

## Install

**On Windows, without Python:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest) — unzip and double-click.

**With Python 3.9+:**

```powershell
pip install tifffile numpy pillow rich imagecodecs
```

plus three tools on PATH: [cjxl / djxl / jxlinfo](https://github.com/libjxl/libjxl/releases) (the `jxl-x64-windows-static.zip`), [ExifTool](https://exiftool.org) and [ImageMagick](https://imagemagick.org). Then run `py jxl_photo.py`: its first line shows what it found. Step by step, with the PATH setup and troubleshooting: [docs/install.md](docs/install.md).

---

## Quick Start — Interactive Wrapper

The easiest way to use this toolkit. Run `py jxl_photo.py` and follow the guided menu:

```
╭───────────────────────────────────────────── JXL Tools Environment ────────────────────────────────────────────────╮
│ [✓] cjxl/djxl | [✓] exiftool | [✓] magick | [✓] tifffile | [✓] pillow | [✓] imagecodecs | [✓] rich                    │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭───────────────────────────────────────────────────── Main Menu ────────────────────────────────────────────────────╮
│     CONVERT                                                                                                        │
│  1  New job: archive, restore, export, recompress...                                                               │
│  2  Repeat last job                                                                                                │
│  3  Presets (2 saved)                                                                                              │
│  4  Folder list (manifest): run a CSV of folders                                                                   │
│     MAINTAIN                                                                                                       │
│  5  Renamed or moved folders: fix the masters' path records                                                        │
│  6  Explorer thumbnails for existing JXLs (jxl-thumb)                                                              │
│  7  JPEG archives (jbrd): audit, repair, EXIF for IrfanView                                                        │
│  8  Reset the encoder's ICC test cache                                                                             │
│     SETTINGS                                                                                                       │
│  9  Settings: wrapper defaults, script settings, reset, move                                                       │
│  D  Dependencies and new releases: check again                                                                     │
│  0  Exit                                                                                                           │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

A **New job** asks what to do, where, and how, then shows one review screen:

```
[1] New job
  Task      → Archive TIFFs as JXL masters
  Folder    → F:\Photos\2024
  Mode      → 7 (below _EXPORT, one subfolder) — or [A] to have the folder analysed
  Quality   → d=0.1, near-lossless (recommended); film scans stay lossless
  Review    → every option with its value and where it came from;
              [a] all options, [d] dry run, [r] run, [s] save as a preset
```

The value between `[brackets]` (blue in the menu) is the default: press `Enter` to accept it. Full wrapper guide: [docs/README_jxl_tools.md](docs/README_jxl_tools.md). Its settings live in `.jxl_tools_config.json`, next to `jxl_photo.py` or in your home folder (Settings, menu 9, moves it).

---

## Auto Mode and manifests

Instead of memorizing modes 0–8, press **[A]** in the mode step of a New job: the wizard reads your folder structure and *recommends* a mode — `_EXPORT` / `Export_*` folders → 6/7 (Capture One / Lightroom), deep subfolders → 3, several folders → 2, a single folder → 0 — and previews where each folder's output goes. It never runs anything you have not confirmed; **[N]** picks the mode yourself.

From there, **[P]** writes a **manifest**: a CSV with one Source → Destination row per folder, each with its own mode (and optionally its own colour space, size, sharpening, rename or export folder). Edit it in Excel, comment a row out with `#`, and run it again any time with **[M]**:

```csv
Source,Destination,Mode,Direction
F:\2025\Recife\_Export\TIFF,F:\2025\Recife\_Export\TIFF,6,tiff2jxl
F:\2025\São Paulo\_EXPORT\16bit,F:\2025\São Paulo\_EXPORT\16B_JXL,7,tiff2jxl
F:\2025\横浜\RAW,F:\2025\横浜\JXL,0,tiff2jxl
F:\2026\京都・嵐山 [FINAL]\_EXPORT\TIFF,F:\2026\京都・嵐山 [FINAL]\_EXPORT\TIFF,6,tiff2jxl
# F:\2026\Outtakes (later)\_EXPORT\TIFF,F:\2026\Outtakes (later)\_EXPORT\TIFF,6,tiff2jxl
```

The whole manifest is checked before anything runs: two rows writing the same file, or an invalid cell, refuse it up front. Every column, what can be left out, and recipes — among them a whole library from masters to phone JPEGs: [docs/README_manifest.md](docs/README_manifest.md#7-a-whole-library-end-to-end-masters-a-16-bit-srgb-copy-phone-jpegs).

---

## The scripts on their own

```
Capture One
    ↓ Export 16-bit TIFF (sRGB, AdobeRGB, ProPhoto RGB)
jxl_tiff_encoder.py      TIFF → JXL      the master: 16-bit, lossless or near-lossless
    ↓                                    (~31 MB at d=0.1, ~173 MB lossless, 45 MP)
    ├─ jxl_recompressor.py    JXL → JXL       lighter 16-bit copies: sRGB, resized, a higher distance
    ├─ jxl_jpeg_transcoder.py JXL → JPEG/PNG  for sharing or print: colour conversion, resize, sharpening
    └─ jxl_tiff_decoder.py    JXL → TIFF      the TIFF back, in its original profile, to edit again
```

```powershell
py jxl_tiff_encoder.py "F:\Photos\2024" --mode 7                   # TIFF → JXL masters (Capture One _EXPORT layout)
py jxl_tiff_decoder.py "photo.jxl"                                  # JXL → TIFF, original profile restored
py jxl_jpeg_transcoder.py "F:\Photos\2024"                          # JPEG → JXL, lossless
py jxl_jpeg_transcoder.py "F:\Photos\2024" --to-srgb --quality 95   # JXL → sRGB JPEG
py jxl_recompressor.py "F:\Photos\Archive" --mode 1 --distance 1.0 --dry-run   # simulate a smaller archive
```

`py jxl_photo.py encode …` (or `decode`, `recompress`, `transcode`) runs the same scripts, with the names of the Windows program; flags are written in full (`--workers`, not `--work`). The encoder only writes masters, by design: colour conversion, resize and sharpening live where copies are made, in the recompressor and the transcoder. More commands, the settings I recommend, what a re-run redoes and what to do after converting: [docs/commands_and_settings.md](docs/commands_and_settings.md).

---

## Good to know

One line each; the detail is in [docs/behavior_and_limitations.md](docs/behavior_and_limitations.md).

- **Lossy is the default** (`--distance 0.1`, near-lossless, about a tenth of the TIFF); use `--distance 0` for a bit-exact archive. [More](docs/behavior_and_limitations.md#lossy-is-the-default---distance-01)
- **cjxl 0.12 treats every distance up to 0.05 as 0.05**, so smaller values buy nothing there. [More](docs/behavior_and_limitations.md#the-distance-floor-depends-on-your-cjxl-005-on-libjxl-012-001-on-011)
- **Every page of a multi-page TIFF is converted by default**, embedded previews included, except Capture One's. [More](docs/behavior_and_limitations.md#multi-page-tiffs-every-page-is-converted-by-default)
- **The decoder never overwrites an original TIFF**, nor a decode you edited since. [More](docs/behavior_and_limitations.md#the-decoder-never-overwrites-an-original-tiff)
- **Re-runs differ per script:** the TIFF encoder/decoder sync by date, the JPEG transcoder skips existing outputs; a changed distance redoes the copies but not the masters ([table](docs/commands_and_settings.md#what-a-re-run-redoes)). [More](docs/behavior_and_limitations.md#default-re-run-behavior-differs-per-script)
- **Renaming folders:** a re-export after renaming its folder is refused by default; if you rename folders and never delete sources, turn the overwrite guard off. [More](docs/behavior_and_limitations.md#renamed-folders-and-the-overwrite-guard)
- **8-bit TIFFs** are archived lossless when the distance is 0.1 or less, where lossy saves little (`d=0.1`) or comes out larger (`d=0.05`); `--eight-bit-distance same` turns it off. [More](docs/README_jxl_tiff_encoder.md#8-bit-tiffs-are-archived-lossless-at-low-distances-eight_bit_distance)
- **Film scans** are archived lossless by default, and SilverFast recognises the IR channel and dust removal after the round trip; other scanner programs were not tested — test one file first. [More](docs/behavior_and_limitations.md#film-scanners-ir-channel--digital-ice)
- **Viewers:** Windows Explorer thumbnails are slow and not colour-managed ([jxl-thumb](https://github.com/rsilvabr/jxl-thumb) fixes both; the menu reminds you when no JXL thumbnail handler is installed); IrfanView reads the EXIF only from an uncompressed box placed first, which jxl-photo writes by default. [More](docs/behavior_and_limitations.md#viewer-quirks-not-data-loss)
- **New releases:** the menu checks GitHub at most once a day and adds a line when a newer release is out; nothing is downloaded, and `--run-preset` never checks. [More](docs/README_jxl_tools.md#new-release-notice)
- **What goes into your files:** your metadata is copied; the toolkit adds the original ICC, the encode record, a few `jxlphoto-*` markers and one `xmpMM:History` entry per write — no readable path or name. [More](docs/metadata_written.md)

---

## Documentation

| Document | Contents |
|----------|----------|
| [docs/install.md](docs/install.md) | Installing the Python version step by step: downloads, PATH, troubleshooting |
| [docs/README_jxl_tools.md](docs/README_jxl_tools.md) | The interactive wrapper |
| [docs/README_manifest.md](docs/README_manifest.md) | Manifests: every column, what can be omitted, recipes, and manifest → preset → Task Scheduler |
| [docs/README_jxl_tiff_encoder.md](docs/README_jxl_tiff_encoder.md) | TIFF → JXL encoding |
| [docs/README_jxl_tiff_decoder.md](docs/README_jxl_tiff_decoder.md) | JXL → TIFF decoding |
| [docs/README_jxl_jpeg_transcoder.md](docs/README_jxl_jpeg_transcoder.md) | JPEG ↔ JXL / JXL → PNG |
| [docs/README_jxl_recompressor.md](docs/README_jxl_recompressor.md) | JXL → JXL recompression |
| [docs/commands_and_settings.md](docs/commands_and_settings.md) | Everyday commands of each script, recommended settings, what a re-run redoes |
| [docs/behavior_and_limitations.md](docs/behavior_and_limitations.md) | Defaults, limits and viewer quirks worth knowing before a large batch |
| [docs/why_jxl.md](docs/why_jxl.md) | Why JPEG XL: sizes, the grain tests and the measurements behind the settings |
| [docs/file_sizes.md](docs/file_sizes.md) | File sizes per camera and for film scans, and what effort 9 buys |
| [docs/metadata_written.md](docs/metadata_written.md) | Every field the toolkit writes into your photos, and what it reveals |
| [docs/jxl_color_internals.md](docs/jxl_color_internals.md) | Deep dive: XYB, ICC blobs vs primaries, troubleshooting |
| [docs/related_projects.md](docs/related_projects.md) | The Windows program, Explorer thumbnails, the TIFF-only alternative, the backup script |
| [docs/whats_new.md](docs/whats_new.md) | What changed in each release, in a few lines |
| [docs/upgrading.md](docs/upgrading.md) | What to check when you upgrade from an older version |
| [docs/version_history.md](docs/version_history.md) | Every release in detail, and the full release table |
| [docs/bug_tracking_since_v1.0.md](docs/bug_tracking_since_v1.0.md) | Every bug fix since v1.0, numbered and dated |
| [docs/new_features_since_v1.0.md](docs/new_features_since_v1.0.md) | Every new feature since v1.0 |

**Testing.** `pytest tests/` runs the test suite (synthetic files, plus real-codec tests that skip when `cjxl`/`djxl`/`exiftool` are missing). [tools/real_photo_battery.py](tools/real_photo_battery.py) runs the four scripts end to end on **copies of your own photos** (`py tools/real_photo_battery.py --fixtures <folder>`; the fixture layout is in its docstring).

---

## What's new in v2.30.0

2026-11-13. The first stable release since v2.29.1; it includes the v2.30.0_beta1 pre-release.

- **Faster**: each file's metadata is written by one ExifTool process instead of several, and archiving a TIFF takes about half the time it did (decoding one back, 10–25% less).
- **Flags must be written in full**: a shortened flag such as `--work 4` is refused before anything runs. The wizard writes them in full; check your own `.bat` files ([upgrading](docs/upgrading.md)).
- **Each output's history names the libjxl version that made it** (`… with libjxl 0.12.0`), next to the toolkit's version.
- The decoder and the JPEG transcoder, in modes 6/7 with an output folder, put their outputs next to the library's own export folder even when a folder like `Lightroom_Export` sits above it ([upgrading](docs/upgrading.md)).
- Closes edge cases: a PNG named `.jpg` is refused by the lossless JPEG transcode; the wizard's delete preview and manifest check give the scripts' own answers; `--help` written to a file. `python jxl_photo.py encode|decode|recompress|transcode …` runs a script directly.

[Technical notes](docs/version_history.md#v2300) · [earlier releases, in short](docs/whats_new.md) · [every release](docs/version_history.md#release-history)

---

## About this project

I am sharing these scripts because getting all of this to work correctly was unexpectedly difficult: preserving 16-bit depth through the conversion pipeline, embedding EXIF so it is visible in IrfanView and other applications, handling the ICC profiles of Capture One exports (sRGB, AdobeRGB, ProPhoto RGB), fixing an XMP overwrite bug that destroyed original metadata and an EXIF extraction that produced corrupted data, a sync mode that reconverts only re-exported photos, and performance (RAM usage, parallelism, staging). Several of those bugs appear because of the specific combination of programs I use (Capture One, cjxl, exiftool, IrfanView); they and their fixes are documented in [`docs/bugs_fixes_explained.md`](docs/bugs_fixes_explained.md).

Related projects, all mine ([more on each](docs/related_projects.md)):

- [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe) — this toolkit as a Windows program, no Python needed. The scripts inside are byte for byte those of a release; the Python version here is the one I run every day and remains the more tested.
- [jxl-thumb](https://github.com/rsilvabr/jxl-thumb) — fast, colour-managed JXL thumbnails in Windows Explorer.
- [tiff-workflow](https://github.com/rsilvabr/tiff-workflow) — a simpler, TIFF-only alternative: lossless ZIP recompression, no libjxl needed, much less compression.
- [photo-backup](https://github.com/rsilvabr/photo-backup) — backs up what this toolkit leaves you with: the JXL masters, the originals of the exported photos and your edits.

---

## Disclaimer

These tools were made for my personal workflow.
Use at your own risk — I am not responsible for any issues you may encounter.

However, if you find any bugs, feel free to report them to me — I will gladly try my best to improve this project.

Always test with a small batch before processing important archives.

---

## License

MIT License — feel free to use, modify, and distribute.

---

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation  
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling  
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O  
- [Claude](https://www.anthropic.com/claude) (Anthropic) and [DeepSeek](https://www.deepseek.com), among other AI tools, for code assistance, reviews and technical discussion
