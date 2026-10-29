# jxl_tiff_encoder.py

Batch TIFF 16-bit → JPEG XL converter. Encodes TIFF files to JXL format with 
configurable quality (lossless or lossy), preserves full EXIF/XMP metadata, 
and **embeds the original ICC color profile as XMP metadata** for perfect 
round-trip preservation.

Works with any 16-bit TIFF — Capture One exports, NX Studio, Photoshop, or 
standard uncompressed TIFFs from various sources.

**Key feature:** ICC profile embedding. 
```
When paired with `jxl_tiff_decoder.py`, the exact original ICC profile is 
preserved even for **lossy JXL files**, which would otherwise lose 
the detailed ICC information (gamma curves, copyright, etc.).
```

---

## Requirements

```
Python 3.9+
pip install tifffile numpy imagecodecs
cjxl  →  https://github.com/libjxl/libjxl/releases
exiftool  →  https://exiftool.org
```

Both `cjxl.exe` and `exiftool.exe` must be on your PATH.

### Download the Correct Files

| Tool | Download | What to Get |
|------|----------|-------------|
| **cjxl** | https://github.com/libjxl/libjxl/releases | `jxl-x64-windows-static.zip`  **(NOT `jxl-x64-windows.zip` which has only DLLs)** |
| **exiftool** | https://exiftool.org | `exiftool-XX.XX_64.zip`  **(Windows .zip, NOT .tar.gz source)** |

### exiftool Setup

> **Note:** The scripts automatically detect both `exiftool.exe` and `exiftool(-k).exe`. **Renaming is no longer required**, but still works if you prefer.

The Windows download comes as `exiftool(-k).exe`. If you want to rename it anyway:

```powershell
# Option A: Rename
Rename-Item "C:\tools\exiftool\exiftool(-k).exe" "exiftool.exe"

# Option B: Duplicate and rename (keeps original)
Copy-Item "C:\tools\exiftool\exiftool(-k).exe" "C:\tools\exiftool\exiftool.exe"
```

### Add to PATH

**Replace with YOUR actual paths:**

```powershell
$myPaths = @(
    "C:\tools\libjxl\bin",    # where cjxl.exe is
    "C:\tools\exiftool"        # where exiftool.exe / exiftool(-k).exe is
)
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", ($myPaths -join ";") + ";$p", "User")
# Restart PowerShell after this!
```

### Verify
```powershell
cjxl --version      # JPEG XL encoder v0.11.x
exiftool -ver       # 13.xx
```

---

## Quick start

```powershell
# ── The easy way — mode 0, no flags needed ──────────────────────
# Single file, in-place
py jxl_tiff_encoder.py "F:\Photos\photo.tif"

# Single file → specific output folder
py jxl_tiff_encoder.py "F:\Photos\photo.tif" "F:\output"

# Whole folder, in-place (flat — subfolders not touched)
py jxl_tiff_encoder.py "F:\Photos"

# Whole folder → specific output folder (flat)
py jxl_tiff_encoder.py "F:\Photos" "F:\output"

# ── Other modes ──────────────────────────────────────────────────
# Capture One _EXPORT workflow (mode 7) — most common for C1 users
py jxl_tiff_encoder.py "F:\2024" --mode 7

# Sync — only reconvert TIFFs newer than existing JXL
py jxl_tiff_encoder.py "F:\2024" --mode 7 --sync

# 16 parallel workers
py jxl_tiff_encoder.py "F:\2024" --mode 7 --workers 16

# Mode 8 — in-place recursive: JXL next to each TIFF, all subfolders
py jxl_tiff_encoder.py "F:\2024" --mode 8
```

---

## Key settings

Edit at the top of the script:

```python
CJXL_DISTANCE = 0.1
# 0   = mathematically lossless (pixel-perfect, ~173MB for 45MP)
# 0.05 = near-lossless (~47MB, imperceptible difference) ⭐ RECOMMENDED for archive
# 0.1 = near-lossless (~34MB, imperceptible difference)  ⭐ ALSO RECOMMENDED for archive
# 0.5 = high quality lossy (~13MB) — recommended starting point (libjxl authors)
# 1.0 = "visually lossless" per libjxl documentation (~8MB)

CJXL_EFFORT = 7
# Compression effort (1-10). Controls file size, NOT quality.
# 7 is the sweet spot for camera photos.
# Effort 8-10 is much slower and can produce larger files for high-ISO images.

CJXL_BUFFERING = None
# [libjxl >= 0.12 only] Encoder buffering level passed to cjxl (also --buffering CLI).
# None = do not pass the flag; cjxl chooses: a streaming encode (the fast,
#        low-memory path) EXCEPT at effort 7 with distance >= 3, effort 8-9
#        with distance > 0.5 and effort 10+, where it encodes the whole image
#        at once (2.5-8x the RAM; see "RAM per worker" below).
# 0    = best compression, but ~6x slower on large lossless TIFFs for only
#        ~1.2% smaller files (see the v1.8.0 release notes benchmark on GitHub).
# 1-3  = always stream.
# Ignored automatically when cjxl is < 0.12 (flag doesn't exist there).

WORKER_MEMORY_FRACTION = 0.8
# Caps --workers so the parallel cjxl processes fit in memory. The peak of each
# worker is estimated from the largest page in the batch and the encode
# settings (see "RAM per worker"); the run then uses at most this fraction of
# the budget WORKER_MEMORY_LIMIT picks. 0 disables the cap.

WORKER_MEMORY_LIMIT = "both"
# "both" = the smaller of what the system can still commit (RAM + pagefile)
# and the physical RAM still available; "commit" or "physical" = only that
# one. See "Memory and --workers" in README_jxl_recompressor.md.

EMBED_ICC_IN_JXL = True
# Embeds the original ICC profile as metadata in the JXL file.
# The ICC is NOT used by the JXL decoder (JXL uses native primaries),
# but is preserved for round-trip conversion back to TIFF/JPEG.
# This ensures the exact original ICC (with TRC curves, copyright, etc.)
# is available when converting JXL → TIFF, even for lossy JXLs.
# True  → embed ICC profile in JXL metadata (recommended, default)
# False → do not embed ICC (smaller file, but lossy JXLs will use generic ICC on decode)

ENCODE_TAG_MODE = "xmp"
# Records encoding parameters in the JXL metadata as an append-only lineage
# chain: "gen=N | cjxl d=X e=Y | cjxl d=... e=..." (gen = number of LOSSY
# generations, reconciled from the chain on every write, never incremented)
# "software" → appends to the EXIF Software field
# "xmp"      → writes as XMP metadata (default); any user caption stays FIRST
# "off"      → records nothing AND strips any gen=/cjxl record the source
#              TIFF carries — the only way to deliberately discard the lineage
# Can also be set via --encode-tag CLI argument (xmp/software/off)
# NOTE: When EMBED_ICC_IN_JXL is True, the ICC goes to XMP:CreatorTool
# and encoding params are concatenated to dc:Description.

EXPLORER_THUMBNAIL = "auto"
# A 768 px sRGB JPEG of the image stored inside the output (its own box, not
# the EXIF), shown by jxl-thumb in Windows Explorer: "auto" (default) = every
# output when jxl-thumb is installed, lossless outputs only otherwise; "on" =
# every output; "off" = none. Also --explorer-thumbnail. See "Explorer
# thumbnails" below.

CJXL_MODULAR = False
# False (default) — lossy uses VarDCT encoder + XYB colorspace.
# True  — forces Modular encoder for lossy (--modular=1).
#   Less efficient for photos, but good for screenshots/UI art.
#   Use only if you need non-XYB encoding for compatibility reasons.
#
# Measured (2026-09, 7 real images — Nikon Zf/Z8 ProPhoto 16-bit TIFFs,
# medium-format film scan, IR dust-channel scan, negative scan; cjxl e=7,
# d=0.05 and d=0.10, scored with SSIMULACRA2 against the sRGB reference):
#   Quality: a wash — modular won 5 of 14 head-to-heads, VarDCT won 9,
#     every margin ≤ 0.39 (indistinguishable).
#   Size:   VarDCT smaller in 14 of 14 — modular up to +33% larger.
#   Speed:  VarDCT 20-100x faster (1-6s vs 40-599s per file).
# Conclusion: there is no photo use case for lossy modular; the False
# default is correct. This setting exists for graphics/screenshot batches.
# Can also be set via --modular CLI argument (on/off).

D50_PATCH_MODE = "auto"
# Normalises the ICC header's illuminant field to the exact bytes of the ICC
# spec. Kodak's ProPhoto.icm (the ProPhoto RGB Capture One exports with) rounds
# it slightly differently; no failure was ever traced to that. See
# "D50 Illuminant Patch" below.
# "on"   → Always apply the patch
# "off"  → Never apply the patch (use original ICC values)
# "auto" → Only a near-miss illuminant (e.g. Kodak's ProPhoto.icm, Z 0xD32B);
#          decided from the ICC profile itself, no extra exiftool call
# Can also be set via --d50-patch CLI argument (on/off/auto)

CLEANUP_XMP_ICC_MARKER = False
# Remove legacy ICC markers from XMP if present.
# True  → clears xmp-icc:all and xmp-photoshop:ICCProfile tags that might conflict
# False → keeps existing ICC markers (default)

USE_RAM_FOR_PNG = True
# True  → PNG intermediate stays entirely in RAM (faster; RAM scales with
#         megapixels, not with a fixed per-worker figure — see Performance)
# False → PNG is written to disk in TEMP_DIR (useful if RAM is limited)

PIL_MAX_IMAGE_PIXELS = None
# PIL's decompression bomb protection limit (prevents DOS attacks with malicious images).
# None  → Disable the limit completely (recommended for trusted local files/panoramas)
# N     → Maximum number of pixels (e.g., 500_000_000 for ~500MP limit)
# The default PIL limit (~89MP) is too low for large panoramas. Set to None for photography workflows.

CJXL_TIMEOUT = 900
# Timeout (seconds) for each cjxl invocation; a timeout is a per-file error.

EXIFTOOL_TIMEOUT = CJXL_TIMEOUT
# Timeout for each per-file exiftool call (metadata copy, markers, thumbnail).
# Same patience as the codec: many workers on a busy hard disk can stall a
# metadata rewrite for minutes. The planning-time batch reads use the two
# settings below.

EXIFTOOL_BATCH_STALL = 180
# A planning-time batch read (up to 400 files per exiftool call) is stopped only
# after this many seconds with no output from exiftool (a hang).
EXIFTOOL_BATCH_TIMEOUT = 1800
# … or after this many seconds in all. A slow disk that keeps printing is left
# to finish (#537).

TEMP2_DIR = None
# Staging SSD for output JXLs. Separates read I/O (HDD with TIFFs) from write I/O.
# None (default) → write directly to the final destination.
# Example: r"E:\staging"
# Files are moved to their final destination after each folder group completes.
# Can also be set via --staging CLI argument (overrides this variable).

OVERWRITE = "smart"
# False   → skip if JXL already exists (safe for resuming)
# True    → always overwrite
# "smart" → same as --sync: reconvert only if TIFF is newer than JXL

DELETE_SOURCE = False
# Whether to delete the source TIFF after successful encode. Works in EVERY
# mode (0-8): the mode decides where the JXL lands, this decides whether the
# TIFF survives it. The source only goes once its JXL exists at the FINAL
# destination (after the staging move, and that move must have succeeded),
# passes the integrity check there, and — with --verify-roundtrip — decodes
# back to the source pixels.
# WARNING: irreversible. Only enable after testing on a small batch first.

# — Safety (DELETE_SOURCE, any mode) —
DELETE_CONFIRM = True
# True  → require interactive confirmation before deleting source files
# False → skip confirmation (for automation only)
```

#### Safety confirmation (DELETE_SOURCE)

> **Changed in v2.0.0 — read this if you have a saved command.** `--delete-source`
> used to be honoured **in mode 8 only**: outside it the flag was accepted and
> silently ignored. It now works in **every** mode, so a stored command or
> scheduled task using it with any other mode deleted nothing before and deletes
> the originals now. Deleting is a separate opt-in from the output layout, which
> is what makes "convert into a separate tree and drop the masters" possible at
> all — but it means the flag has to be read as written.
```
When `DELETE_SOURCE = True` and `DELETE_CONFIRM = True`:
- **Lossless:** type `yes` to confirm
- **Lossy:** type the current time in `HHMM` format (forces conscious decision)
```

The confirmation is only charged when the plan can actually delete something: a
re-run of an already-archived folder produces only skips, deletes nothing and
exits `0` without a prompt.



---

## Explorer thumbnails (`EXPLORER_THUMBNAIL`, `--explorer-thumbnail`)

Windows Explorer has to decode a JXL to show its thumbnail. A **lossy** JXL
carries a 1:8 preview of itself that [jxl-thumb](https://github.com/rsilvabr/jxl-thumb)
reads in ~75 ms; a **lossless** one has none, so its thumbnail means decoding
the whole file: about **1.8 s per 45 MP photo on a hard disk**.

So the encoder stores a small JPEG of the image inside its outputs — every
output when jxl-thumb is installed, lossless ones otherwise:
**768 px** on the long side (the largest size Explorer asks for its icon
views), **converted to sRGB** from the photo's own profile. jxl-thumb 0.2.0+
shows it in **~2 ms** instead of decoding the photo.

**Where it is.** In a box of the JXL container of its own — an ISO BMFF
`uuid` box, the format's way to carry a program's private data — right
before the image data. Other programs skip a box they do not know: viewers,
djxl, exiftool edits (which keep it), Lightroom-style metadata writers.
It is not in the EXIF on purpose: a 768 px JPEG does not fit the 64 KB EXIF
segment of a JPEG, and djxl, which copies a JXL's EXIF into the JPEG it
writes, fails on such a file ("Bogus marker length").

**It cannot go stale unnoticed.** The box also records a fingerprint of the
image data it was made from (`jxlphoto-thumbnail:v1:<bytes>:<hash>`: the
codestream's length and a hash of its first 64 KiB). jxl-thumb shows the
thumbnail only while that still matches the file; a program that re-encodes
the pixels and somehow keeps the box leaves a stale one, which is ignored.
Metadata edits never touch the image data, so they keep it valid.

| `EXPLORER_THUMBNAIL` / `--explorer-thumbnail` | Stores one in |
|---|---|
| `auto` (default) | **every** output when jxl-thumb is installed for your Windows user (lossy thumbnails get ~25x faster too: ~50 → 2 ms); **lossless** outputs (`--distance 0`) only when it is not — they gain the most, and are ready if you install it later |
| `on` | every output, whatever is installed |
| `off` | none |

`auto` reads the registry once per run: is jxl-thumb Explorer's thumbnail
handler for `.jxl`? The log says so when it covers lossy outputs. The
environment variable `JXLPHOTO_JXL_THUMB=1` or `0` overrides the check (the
test suite pins it to `0`; a run making files for another PC can use it).

Measured on 100 photos of 45 MP on a hard disk, cold (first opening of the
folder): a lossless folder 176 s → under 2 s; on an SSD, warm, per file:
lossless 1100 → 1.7 ms, lossy 52–76 → 1.7 ms. Cost: about 90 KB per file
(0.05% of a lossless 45 MP JXL, 1% of a d=1 one) and about 2 s per 45 MP
lossless encode, about 1.2 s per 45 MP lossy one (djxl + ImageMagick). Needs `djxl` and ImageMagick; without
them the run says so once and stores none. `--strip` never stores one.
`EXPLORER_THUMBNAIL_SIZE` (default 768) sets the size.

**Small files get none**, even with `on`: an image no larger than the
thumbnail, or a file where the thumbnail would be more than
`EXPLORER_THUMBNAIL_MAX_SHARE` (default 10%) of its size — an 800 px d=3 JXL
of a few dozen KB, a 2048 px derivative. Those decode fast anyway.

Files that already exist get theirs from the recompressor's
`--refresh-explorer-thumbnails` (or menu option 9 of `jxl_photo.py`), which
changes nothing but the thumbnail and keeps each file's modified date — see
[README_jxl_recompressor.md](README_jxl_recompressor.md#explorer-thumbnails-for-existing-files---refresh-explorer-thumbnails).

## Modes 6 and 7 — ONLY files inside `_EXPORT`

**Modes 6 and 7 ONLY process files inside folders whose names start with or end with `_EXPORT` (case-insensitive). Everything outside is IGNORED.**

```
E:\sessao\
├── foto1.tif          ← NOT processed (outside _EXPORT)
├── foto2.tif          ← NOT processed (outside _EXPORT)
└── _EXPORT\
    ├── folder1\
    │   └── img.tif    ← PROCESSED ✓
    ├── folder2\
    │   └── img.tif    ← PROCESSED ✓
    └── folder3\sub\
        └── img.tif    ← PROCESSED ✓
```

**Mode 6** — processes ALL TIFFs under ALL `_EXPORT` folders.

**Mode 7** — only TIFFs inside a specific subfolder of `_EXPORT` (configurable via `EXPORT_TIFF_SUBFOLDER`; default is `""`, which processes all subfolders inside `_EXPORT`).

```
Mode 7 example with EXPORT_TIFF_SUBFOLDER = "16B_TIFF":
session/_EXPORT/16B_TIFF/photo.tif → session/_EXPORT/16B_JXL/photo.jxl  ✓
session/_EXPORT/AdobeRGB/photo.tif → ignored
```

Both modes also skip TIFFs sitting in a **decoder output folder** (`16B_TIFF`,
`TIFF_16bits`, `converted_tiff`) below the marker — re-encoding a decoded TIFF
at `d>0` is generational loss. Mode 7 exempts the one subfolder explicitly
requested via `EXPORT_TIFF_SUBFOLDER`; mode 6 has no requested subfolder, so it
skips **all** of them — a leftover `EXPORT_TIFF_SUBFOLDER` value does not
exempt one there.

The output folder is `EXPORT_MARKER/EXPORT_JXL_FOLDER` (default `_EXPORT/16B_JXL`).
`--export-jxl-folder` overrides it per run, so different presets can write to
different folders — e.g. a separate "Print" TIFF export encoded at a lower
distance, with its own output folder:

```
py jxl_tiff_encoder.py "F:\Photos\2025" --mode 7 --export-subfolder TIFF16 --distance 0.05
py jxl_tiff_encoder.py "F:\Photos\2025" --mode 7 --export-subfolder Print --export-jxl-folder PRINT_JXL --distance 0.5
```

---

## Excluding folders from discovery (`--exclude-folders`)

`--exclude-folders` takes a `';'`-separated list of folder **names**, not
paths (entries containing `\` or `/` are rejected at startup). Matching is by
**segment**, case-insensitive — `_EXPORT` never matches `My_EXPORT_photos` —
and is evaluated **relative to the input folder**: only folders **below** the
input count, so the input folder's own name and its ancestors never exclude,
and pointing the input AT a folder named `_EXPORT` still processes it. The
run logs `Excluded N file(s) under: ...` whenever something is excluded, so an
exclusion is never invisible. The flag is accepted in every mode (0–8), but
the flat modes 0/1 and single-file inputs never have anything below the root
to exclude — like the export marker, it only really bites in the recursive
modes:

```powershell
# Everything under the root EXCEPT the _EXPORT trees, in place
py jxl_tiff_encoder.py "F:\2024" --mode 8 --exclude-folders _EXPORT
```

---

## Output modes

| Mode | Input | How it finds files | Output location | Example |
|------|-------|-------------------|----------------|---------|
| `0` | File or directory | Flat (non-recursive) — only files in the given folder | In-place (flat, non-recursive) | `photo.jxl` |
| `1` | File or directory | Flat (non-recursive) — only files in the given folder | `converted_jxl/` subfolder next to source | `.../converted_jxl/photo.jxl` |
| `2` | Directory | Recursive — all subfolders | Flat → output_dir (recursive) | `output_dir/photo.jxl` |
| `3` | Directory | Recursive — all subfolders | `JXL_16bits/` inside each TIFF folder | `.../TIFF/JXL_16bits/photo.jxl` |
| `4` | Directory | Recursive — all subfolders | Rename folder `TIFF` → `JXL` | `.../Export_JXL/photo.jxl` |
| `5` | Directory | Recursive — all subfolders | Sibling folder `JXL_16bits/` | `.../JXL_16bits/photo.jxl` |
| `6` | Directory | Recursive, **only inside `EXPORT_MARKER`** (default: `_EXPORT`) | ONLY TIFFs INSIDE `EXPORT_MARKER` — ignores everything outside. Marker name configurable. | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `7` | Directory | Recursive, **only inside specific `EXPORT_MARKER` subfolder** (configurable) | Like mode 6 but only specific `EXPORT_MARKER` subfolder. Both marker and subfolder are configurable. | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `8` | File or directory | Recursive — walks all subfolders | In-place **recursive** — JXL next to each TIFF | `.../session/photo.jxl` |

> **Mode 2 note:** with a directory input, output goes flat to `output_dir` (or the input folder itself if no output is given). With a **single file** input and no output argument, mode 2 writes to `converted_jxl/` next to the file (same as mode 1).

---

## CLI reference

```
py jxl_tiff_encoder.py <input> [output] [options]

Arguments:
  input           Input root folder or file
  output          Output folder (modes 0 and 2; the other modes derive the
                  destination from each source's own folder and ignore it)

Options:
  --version       Print the toolkit version and exit
  --mode 0-8      Output folder mode (default: 0)
  --workers N     Parallel threads (tested up to 32 on a Ryzen 9 5950X).
                  RAM is the real ceiling — see "RAM per worker" in Performance
  --overwrite     Always overwrite existing JXLs
  --sync          Reconvert only TIFFs newer than their JXL
                  (wins over --overwrite if both are given)
  --distance N    JXL distance (0=lossless, 0.1=near-lossless, default: from script)
  --effort 1-10  Compression effort (default: from script setting)
  --buffering 0-3 [libjxl >= 0.12] cjxl buffering level (default: off = use cjxl default;
                  0 = best compression, much slower on large lossless images,
                  and ~2.4x the RAM per worker)
  --ram           Keep PNG intermediate in RAM (faster, more memory)
  --no-ram        Write PNG intermediate to disk (slower, less memory)
  --delete-source Delete source TIFFs after their JXL is written to the mode's
                  destination and verified. Works in EVERY mode. IRREVERSIBLE.
                  A TIFF changed on disk since this run read it (re-exported
                  during a long batch) is KEPT: its new version was never archived
  --provenance path|content|adopt
                  [with --delete-source, modes 2/4/5/6/7] Those modes DROP folder
                  structure, so two files with the same name in different folders
                  land on the same output. Before overwriting an output that already
                  exists -- and deleting the file that made it -- the run checks the
                  archive really came from this source, using markers every encode
                  writes (jxlphoto-src = location, jxlphoto-srcsum = image). The
                  same proof also backs --delete-skipped in EVERY mode: a skipped
                  source is only deleted when its pre-existing output carries the
                  markers this source proves.
                  EVERY run (any mode, with or without --delete-source) also refuses
                  to OVERWRITE an existing output whose marker names a DIFFERENT
                  source, or that is a lossless JPEG archive (jbrd) written by the
                  transcoder: the overwrite itself is the loss when an earlier run
                  already deleted that other photo's source (foto.tif and foto.tiff
                  both write foto.jxl, even in mode 8). A refusal is an error (exit
                  1, listed in the failures); a markerless output is overwritten as
                  before.
                  path (default): compares the LOCATION. Free. Handles re-exporting a
                  file in place, but a MOVED folder reads as a different file.
                  content: also accepts a matching IMAGE, so it survives moved
                  folders -- slower, it reads and hashes every source. A mismatch
                  always fails closed: not converted, nothing overwritten or deleted
                  adopt: for an archive built BEFORE these markers existed, which has
                  no record at all. Each unmarked output is DECODED and compared
                  against the source that would overwrite it, then STAMPED -- a
                  one-time healing pass, after which the strict check applies again.
                  A MISMATCHING marker is still refused: adopt relaxes "I cannot
                  tell", never "I can tell it is wrong". This script is the only one
                  with adopt -- the decoder and the transcoder have no way to prove
                  the pairing after the fact
  --no-overwrite-guard
                  Turn OFF the "marker names another source" refusal: overwrite the
                  output anyway, with a log line per file. For libraries whose sources
                  are never deleted and whose folders get renamed (the marker records
                  the source's full PATH, so a renamed folder reads as "another
                  source"). Refused together with --delete-source (exit 2), which is
                  the combination that can lose a photo. Still refused: a lossless
                  JPEG archive (jbrd) of another JPEG. Same as OVERWRITE_GUARD = False.
                  Details: behavior_and_limitations.md, "Renamed folders and the
                  overwrite guard" (#renamed-folders-and-the-overwrite-guard)
  --restamp-moved
                  Fix ONLY the provenance record of masters whose folder was renamed
                  or moved: for each existing output whose jxlphoto-src names another
                  path but whose jxlphoto-srcsum matches this TIFF's bytes, rewrite
                  jxlphoto-src to the new path. Nothing is encoded, the pixels and the
                  file date are kept (so derivative --sync runs do not redo them), and
                  no xmpMM:History entry is written. Outputs whose content no longer
                  matches (renamed AND edited since) are listed and left alone: delete
                  those old JXLs by hand and sync again. Refused with --delete-source,
                  --delete-skipped or --overwrite. See "Renamed folders and the
                  overwrite guard" in behavior_and_limitations.md
                  To restamp and sync in one run, use --restamp-then-sync.
  --restamp-then-sync
                  The --restamp-moved rewrite, then the run continues as --sync in the
                  same pass: unchanged photos stay up to date, a TIFF newer than its
                  JXL is re-encoded. The restamp runs before the overwrite guard, so a
                  touched-but-unchanged TIFF is not refused. Moved AND edited outputs
                  are left to the guard, as in a plain sync. It reads the record of
                  every existing master (one batched exiftool read, a few minutes on a
                  large library from a hard disk; the guard then reads them again), so
                  a plain sync that reads none for up-to-date files costs more here.
                  Opt-in: a sync without the flag never rewrites a record. Refused with
                  --delete-source, --delete-skipped, --overwrite and --restamp-moved
                  (exit 2): a record rewritten in this run must never decide a deletion.
  --no-adopt-scan [with --provenance adopt] Skip the decode-and-compare and TRUST the
                  existing pairing, warning per file. The scan is what makes adoption
                  safe rather than a rubber stamp; only defensible when you know the
                  archive was never built with a mode that merges folders (2/4/5/6/7)
  --delete-skipped
                  [with --delete-source] Also delete sources whose output ALREADY
                  EXISTS -- the files reported as SKIP. Without it an archive
                  interrupted between the encode and the unlink can never be
                  finished: the leftover is skipped on every later run, and a skip
                  blocks the delete. NEVER acts on the timestamp alone -- the
                  output must exist, pass the integrity check, AND carry a
                  provenance marker MATCHING this source (jxlphoto-src/srcsum --
                  the same check the folder-collapsing modes run before an
                  overwrite), AND the TIFF's bytes must still match the
                  jxlphoto-srcsum recorded in it: a TIFF replaced since that
                  encode (a restored backup with an older date, which sync
                  skips) is KEPT -- rebuild its archive with --overwrite.
                  A markerless (a pre-marker legacy archive) or
                  foreign output is KEPT, with a log line saying how to heal it:
                  re-encode, or stamp it once with --provenance adopt in a
                  folder-collapsing mode. Pair it with --verify-roundtrip as
                  well: the marker proves the pairing, the round-trip proves
                  the pixels
  --verify-roundtrip
                  Before deleting a source, decode its JXL and compare it with
                  the source page. --distance 0: the pixels must match exactly.
                  Lossy: a brightness + PSNR sanity check that catches a black or
                  scrambled encode -- NOT a quality check, since quality loss is
                  what you asked for. Roughly doubles the run. Needs djxl and
                  imagecodecs. Only affects --delete-source
  --delete-confirm-off  Skip the interactive delete confirmation (for wrappers/
                   automation that already asked the user)
  --export-subfolder NAME
                   [Mode 7] Only process TIFFs inside this subfolder of the
                   export marker (default: script setting, empty = all)
  --staging DIR   Staging directory for output JXLs (reduces HDD seek contention).
                  Each finished file is moved to a temp in the destination folder
                  and then renamed over the final name (os.replace): an existing
                  output is never half-overwritten by an interrupted move
  --encode-tag      Where to record encoding params: xmp (default), software, off
                    ('off' also strips any gen=/cjxl record the source TIFF
                    carries — the only way to deliberately discard the lineage)
  --d50-patch       D50 illuminant patch: on (always), off (never), auto (only a near-D50 illuminant, e.g. Kodak's ProPhoto.icm)
  --modular         on/off — force the Modular encoder for LOSSY output (default off:
                    cjxl's VarDCT). Screenshots/graphics only — measured on real
                    photos: equal quality, bigger files, 20-100x slower (see
                    CJXL_MODULAR in the settings section). Lossless always uses
                    Modular regardless
  --icc-png-strategy cautious|heuristic|always|skip
                    How to embed ICC in the PNG intermediate for lossy encoding (default: cautious)
  --strip           Strip all metadata from output (no EXIF/XMP preservation;
                    the encode record is still written to dc:Description unless
                    --encode-tag off, which writes nothing; also disables the
                    multi-page reconstruction and grayscale markers, so split
                    pages decode as standalone files and faithful multipage/
                    grayscale reconstruction is not possible)
  --scan-distance D Distance for film scans (SCANNER_SOFTWARE, an IR page, or no
                    Software tag and no camera EXIF):
                    0 = lossless (default, SCAN_DISTANCE), 'same' = the run's
                    --distance. See "Film scans are archived lossless"
  --scan-scope all|raw
                    Which film scans --scan-distance covers: all (default) or
                    raw ones only (SCAN_SCOPE)
  --explorer-thumbnail auto|on|off
                    Thumbnail stored for jxl-thumb (EXPLORER_THUMBNAIL): auto =
                    every output when jxl-thumb is installed, lossless only
                    otherwise (default); on = every output; off = none. See
                    "Explorer thumbnails"
  --embed-thumbnail Deprecated since v2.12.0 and ignored; --explorer-thumbnail replaces it
  --dry-run         Preview operations without converting
  --summary-json  Emit ONE machine-readable line per run:
                  `##JXLSUM## {"ok": N, "errors": N, "failures": [...], ...}`
                  on stdout, in addition to the normal log. jxl_photo.py
                  passes this to every manifest child and consumes the line
                  to total a multi-entry run; it is never printed to the
                  user. Safe to use in your own scripts -- the prefix is
                  stable and the payload is JSON.
  --export-marker M
                  Folder name marker for modes 6/7 (default: the EXPORT_MARKER
                  setting in the script). Matched case-insensitively on
                  folder names that START or END with it.
  --export-jxl-folder NAME
                  [Modes 6/7] Output folder created under the export marker
                  (default: the EXPORT_JXL_FOLDER setting, '16B_JXL'). Overrides
                  EXPORT_JXL_FOLDER. Must be ONE plain folder name: not empty,
                  not '.'/'..', no path characters, must not itself match the
                  export marker (it would become a second anchor) and must not
                  equal the --export-subfolder (outputs would land among the
                  sources). Ignored, with a warning, outside modes 6/7.
  --exclude-folders NAMES
                  ';'-separated folder NAMES skipped during discovery, in every
                  mode (see "Excluding folders" above). Entries containing
                  '\' or '/' are rejected at startup
  --icc-cache-dir D  Where the ICC round-trip test caches its verdicts (default:
                  the ICC_CACHE_DIR_OVERRIDE setting, else a folder beside the
                  script). One entry per profile; see "ICC strategy" below
  --clear-icc-cache  Delete those cached verdicts and exit. Use it after
                  replacing a profile that the cautious strategy has already
                  judged
  --no-preflight  Skip the pre-run space estimate. The estimate encodes three
                  small crops of your own files to measure this batch's real
                  compression, then projects it against the free space on the
                  staging and destination drives. It only warns, never blocks,
                  and is skipped automatically below 5 GB of input. It counts
                  only the TIFFs this run will encode: in a sync, up-to-date
                  masters are not counted, so a quiet night skips it
  --clean-staging Before converting, delete staging leftovers from EARLIER runs
                  (failed outputs kept for inspection). Only files this tool
                  wrote, never subfolders, and nothing touched in the last hour
                  (a concurrent run may still own it). Sweeps the EFFECTIVE
                  staging dir, so it works with TEMP2_DIR set in the script and
                  no --staging flag. Never runs under --dry-run
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success (all files converted or legitimately skipped) |
| `1` | One or more files failed |
| `2` | Aborted (e.g. duplicate output destinations, invalid arguments) |
| `3` | User declined the delete-source confirmation |
| `130` | Interrupted with Ctrl+C (cancelled) |

**D50 Patch option:**
```powershell
# Force D50 patch on all files
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --d50-patch on

# Disable D50 patch entirely
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --d50-patch off

# Auto-detect based on EXIF Software (default behavior)
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --d50-patch auto
```

---

## ICC Profile Preservation

### Why This Matters

When converting TIFF → **lossy JXL** (`d>0`), the `cjxl` encoder by default:
1. Converts the ICC color profile to **native primaries** (for efficient encoding)
2. The original ICC detail (TRC curves, copyright, etc.) is **discarded** in favor of compact primaries

The JXL format itself supports full ICC in lossy mode, but the reference encoder 
optimizes for size unless explicitly configured otherwise.

#### Without ICC embedding:
```
TIFF (ProPhoto ICC) → JXL (lossy, primaries only) → TIFF (generic ICC from primaries)
```

#### With ICC embedding (`EMBED_ICC_IN_JXL = True`, default):
```
TIFF (ProPhoto ICC) → JXL (lossy + XMP with base64 ICC) → TIFF (original ProPhoto ICC restored)
```
**OBS: If all you need is small file sizes, disable this funcition: it leads to bigger files.**

---

### How It Works

1. **Extract ICC** from source TIFF using exiftool (original ICC for XMP, patched for PNG)
2. **Base64-encode** the ICC profile
3. **Embed in XMP** metadata (`xmp:CreatorTool` field with `ICC:` prefix)
4. **Encoding params** go to `dc:description` as an append-only lineage
   chain (`gen=1 | cjxl d=0.1 e=7`, visible in Windows Properties); any
   existing user text (a caption) stays first. Every encode **appends** one
   entry — re-encoding a TIFF produced by the decoder at identical d/e adds
   a second entry, because decode-then-re-encode is exactly where a
   generation of loss happens. `gen=N` counts the **lossy** (`d>0`) entries
   and is reconciled from the chain on every write, never incremented; the
   recompressor's `--on-regeneration` guard reads it.

When converting back with `jxl_tiff_decoder.py`:
1. Extract ICC from XMP metadata
2. Apply to output TIFF
3. Clean up XMP (remove base64 data, keep encoding params)

---

### Technical Details

The embedded ICC is stored as:
```xml
<xmp:CreatorTool>ICC:AAADrEtDTVMCEAAAbW50clJHQiBYWVogB84A...</xmp:CreatorTool>
<dc:description>
  <rdf:Alt>
    <rdf:li xml:lang="x-default">gen=1 | cjxl d=0.1 e=7</rdf:li>
  </rdf:Alt>
</dc:description>
```

The base64 string is not human-readable but preserves the **exact binary ICC data**.

---

### Important: ICC Extraction Strategy

The script uses **two separate ICC extractions**:

1. **ICC for PNG (cjxl encoding)**: with the header illuminant normalised (D50 patch, below)
2. **ICC for XMP (preservation)**: Original unmodified ICC for perfect round-trip

This ensures:
- cjxl receives a spec-exact ICC header
- The exact original ICC is preserved for restoration

→ See [JXL Color Internals](jxl_color_internals.md) for more technical details.

---

## D50 Illuminant Patch

### What is it?

Every ICC profile has an *illuminant* field in its header (bytes 68–79). The ICC
specification requires D50 there in **every** profile — sRGB and Adobe RGB
included; it is the reference of the conversion space, not the white point of
the colour space (that one is the `wtpt` tag, which the patch never touches).
Stored as 1/65536 fixed-point numbers, D50 is `0000F6D6 00010000 0000D32D`
(X = 0.964202, Y = 1.000000, Z = 0.824905).

Kodak's `ProPhoto.icm` — the ProPhoto RGB profile that ships with Windows and
that Capture One embeds in a ProPhoto RGB export — stores Z as `0000D32B`
(0.824875): 2/65536 off. The patch rewrites those 12 bytes to the exact value.

**What it changes in practice: nothing visible.** The difference is about
0.004 % in a field colour engines barely use, and no `cjxl` failure or warning
was ever traced to it (older versions of this page said otherwise — that was
wrong, as was calling it a Capture One bug: the profile is Kodak's, and Capture
One embeds it unchanged). Profiles that already carry the exact bytes — Adobe
RGB, sRGB, eciRGB and the Elle ProPhoto-class profiles among them — come out
byte-identical, and the summary counts them as `already correct`.

### How the patch works

The script **normalises the illuminant bytes** of the ICC profile before passing it to cjxl:

```
Original ICC → Patch D50 bytes → cjxl encoding
      ↓                              ↓
  XMP storage                    JXL file
```

### Configuration

**Three modes available:**

| Mode | Behavior |
|------|----------|
| `on` | Always apply D50 patch (forces correction on all files) |
| `off` | Never apply D50 patch (use original ICC values) |
| `auto` | Only a near-miss illuminant (each of X, Y, Z within 4/65536 of the spec bytes, e.g. Kodak's ProPhoto.icm) — decided from the profile itself, no extra exiftool call (default) |

A profile whose illuminant is far from D50 (Capture One's
`PhaseOne RGB(Trinitron G18 D50).icm`, for one) is left as it is by `auto` and
counted as "other illuminant"; `on` still overwrites it. Until v2.14.0, `auto`
decided by the EXIF Software tag instead; that list is gone (see
[upgrading](upgrading.md)).

### Summary output

After conversion completes, the script shows D50 patch statistics:

```
Done: 42 OK | 0 overwrites | 0 skipped | 0 errors
D50 patch: 0 applied | 0 skipped (already correct) | 0 needed patch, 42 already correct (mode: auto)
```

When a run meets profiles `auto` leaves alone, the line gains
`| N other illuminant (left as is)`.

This helps you verify that the auto-detection is working correctly for your files.

#### `skipped` vs `corrupt`

`skipped` counts **only** files you asked to skip — `--multipage-mode skip` on a
multi-page TIFF, a thumbnail-only file under `--thumbnail-mode exclude`, or a file
outside the export marker in modes 6/7.

A file that simply cannot be read is reported apart, because no setting asked for
it to be dropped:

```
Done: 41 OK | 0 overwrites | 0 skipped | 0 errors
WARNING | Corrupt: 1 file(s) had no readable pages and were NOT converted:
WARNING |   -> G:\2026\260512_Recife\_EXPORT\scan_099.tif
```

The test is that the page analyzer found neither a real page nor a thumbnail — a
healthy TIFF always has at least one page, so there is no guessing involved. The
classification is the same in **every** multipage mode: a header that opens but
yields no pages is `corrupt` under `split`/`split_all`/`skip`/`ignore` alike
(not a per-file error), so the exit code keeps its meaning everywhere.

**Corrupt files do not change the exit code.** A damaged input is not a failed
run: exit `1` still means the conversion failed on a file, so automation reading
exit codes does not start firing on broken photos. Run a manifest and the same
files appear under `CORRUPT / UNREADABLE` in the end-of-run block (see
[README_jxl_tools.md](README_jxl_tools.md)).

---

## Scanner ICC / Lossy Encoding Workaround

Some scanner ICC profiles (e.g. SilverFast `SFprofT`, VueScan) are very large (`> 50 KB`) and contain huge LUTs. When `cjxl` encodes a lossy JXL (`d > 0`) it must convert the pixels to XYB through the ICC profile. With these scanner profiles the conversion can produce extremely dark or almost-black images, even though the same profile works fine in Photoshop / Lightroom / IrfanView.

The workaround is to **not embed the ICC profile in the intermediate PNG's `iCCP` chunk** during lossy encoding. The pixel data is then treated as plain RGB, the JXL encodes correctly, and the original ICC is still preserved as base64 metadata in the JXL (`XMP-xmp:CreatorTool`). The decoder re-attaches the original ICC when it restores the TIFF, so the round-trip file is visually identical to the original.

| Strategy | Behavior | When to use |
|---|---|---|
| `cautious` (default) | Test each unseen ICC with a small 8/16-bit round-trip and cache the result. Avoids false positives from the size heuristic. | Mixed camera + scanner workflows; recommended default. |
| `heuristic` | Skip `iCCP` for profiles ≥ 50 KB **or** with ICC class `scnr` (scanner). Otherwise embed normally. | Mixed camera + scanner workflows when you need a faster rule-based fallback. |
| `always` | Always embed the ICC in the PNG. | Normal camera images where you want the JXL to display correctly in viewers. |
| `skip` | Never embed the ICC in the PNG. | Maximum safety for any source; JXL colors may look wrong until decoded. |

> **Note:** the `skip` strategy here means "do not embed the ICC in the PNG intermediate". It does **not** mean "skip the JXL conversion". A future release may add a separate option to copy the original TIFF instead of converting when the ICC is problematic.

> **Note:** the JXL file itself may display with shifted colors in some viewers when the ICC is skipped, because the JXL falls back to native/sRGB primaries. For scanner workflows the JXL is treated as a long-term backup container; the reconstructed TIFF is the final image.

### Example CLI usage

```powershell
# Heuristic: skip iCCP for large/scanner profiles (default)
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --distance 0.1 --icc-png-strategy heuristic

# Always embed the ICC (best for normal camera images; JXL displays correctly in viewers)
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --distance 0.1 --icc-png-strategy always

# Never embed the ICC (safest for any source, but JXL colors may look wrong until decoded)
py jxl_tiff_encoder.py "F:\Scanner_Archive" --mode 7 --distance 0.1 --icc-png-strategy skip

# Cautious: round-trip test each unseen ICC and cache the result (recommended for mixed libraries)
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --distance 0.1 --icc-png-strategy cautious
```

For more details, see the v1.7.0 release notes and `docs/bugs_fixes_explained.md`.

---

## Relationship with jxl_tiff_decoder.py

These scripts are designed to work as a pair:

```powershell
# Encode: TIFF → JXL (with ICC embedding)
py jxl_tiff_encoder.py "photo.tif" --mode 0

# Decode: JXL → TIFF (ICC restored)
py jxl_tiff_decoder.py "photo.jxl" --mode 0
```

**For best results:**
- Use `EMBED_ICC_IN_JXL = True` (default) in `jxl_tiff_encoder.py`
- Both scripts detect and handle the embedded ICC automatically

---

## Why the encoder never converts colour or resizes (design decision)

The encoder has **no** colour-space conversion, resize or output sharpening,
and it should not get any. It writes the **master**: a faithful JXL of the TIFF,
same pixels (up to the chosen distance), same ICC, same metadata. Two things
depend on that:

1. **The delete gates.** Every output carries `jxlphoto-src`/`jxlphoto-srcsum`,
   the proof that "this JXL IS this TIFF". `--delete-source`, `--delete-skipped`
   and `--provenance` unlink a TIFF on the strength of that proof. An encoder
   that could also convert or shrink would write JXLs that carry the proof but
   are *not* the TIFF — a 2048 px sRGB file could then justify deleting the
   16-bit ProPhoto original.
2. **The round trip.** `jxl_tiff_decoder.py` restores the original TIFF (ICC,
   bit depth, page structure) from the master. That only holds if the master is
   the TIFF.

Light copies are **derivatives**, and derivatives are made **from the master**,
by the scripts built for them:

| You want | Use |
|---|---|
| A lighter JXL in another colour space / size | `jxl_recompressor.py --output-icc … --resize-long … --sharpen …` |
| A JPEG/PNG to share or print | `jxl_jpeg_transcoder.py --icc-profile … --resize-long … --sharpen …` |
| A TIFF in another colour space | `jxl_tiff_decoder.py --matrix --target-icc …` (8-bit internal precision) |

Derivatives never carry the provenance proof, never delete anything and never
run in place. Going through the master costs nothing measurable: a d=1.0 sRGB
derivative made from a d=0.05 master landed **0.017 dB** from a direct encode of
the TIFF (real 16 MP ProPhoto export, 2026-09-24).

---

## XMP Preservation

Metadata is written with **targeted XMP updates**:
- `-xmp-dc:Description=` for the encode record (`gen=N | cjxl d=/e=` chain appended after any existing dc:description)
- `-xmp-xmp:CreatorTool=` for ICC data (base64-encoded ICC profile)
- All other XMP tags preserved via `-tagsfromfile`

Original ratings, keywords and descriptions coexist with the encoding info and the ICC.

---

## Performance

With `USE_RAM_FOR_PNG = True` (default), the PNG intermediate lives entirely
in RAM. Disk I/O per file = read TIFF + write JXL.

With `--staging` (or `TEMP2_DIR`) set to a separate SSD, JXLs are written to fast storage during conversion
and moved in bulk at the end — eliminates random write contention on HDD collections.

### RAM per worker

Measured with libjxl **v0.12.0**: peak RSS of the whole process tree (Python +
`cjxl`) with `--workers 1`, lossless (`--distance 0`), `USE_RAM_FOR_PNG = True`,
on real 16-bit ProPhoto exports. Divide your RAM budget by these to pick
`--workers`.

| Source | Effort | Buffering | RAM per worker | Time | JXL size |
|---|---|---|---|---|---|
| 24 MP (6048×4032) | 7 | default | 0.87 GB | 19.7 s | 68.8 % |
| 24 MP | 9 | default | 0.99 GB | 79.9 s | 68.6 % |
| 24 MP | 7 | `0` | 2.31 GB | 141.5 s | 67.8 % |
| 24 MP | 9 | `0` | 3.14 GB | 351.0 s | 67.0 % |
| 45 MP (8256×5504) | 7 | default | 1.73 GB | 36.8 s | 66.3 % |
| 45 MP | 9 | default | 1.55 GB | 168.7 s | 66.8 % |
| 45 MP | 7 | `0` | 4.24 GB | 278.6 s | 65.4 % |
| 45 MP | 9 | `0` | 5.05 GB | 632.9 s | 65.4 % |
| 93 MP scan (11170×8355) | 7 | default | 3.19 GB | 75.4 s | — |
| 93 MP scan | 9 | default | 3.32 GB | 415.8 s | — |

JXL size is a percentage of the source TIFF. Peak-RSS sampling has a few
percent of noise: the 45 MP effort-9 figure landing just under effort 7 is
noise, not a real inversion.

**Megapixels drive RAM, not effort.** Effort 7 → 9 barely moves memory but
costs 2–5× the time. Budget roughly **35–40 MB of RAM per megapixel, per
worker**.

#### Exception: the whole-image threshold

"Megapixels drive RAM" holds only on the **streaming** path, which is what the
default (`CJXL_BUFFERING = None`) uses. `cjxl` encodes the whole image at once —
2.5–8× the RAM — when **effort ≥ 10**, when **effort 8–9 and distance > 0.5**,
when **effort 7 and distance ≥ 3**, or with **`--buffering 0`**. Measured on
45 MP 16-bit photos: 4 workers at d=1 e=9 used **47.6 GB (≈11.6 GB/worker)**,
against about **1.3 GB/worker** at d=0.05 e=9. The run estimates this from the
largest page and the settings and **caps `--workers` automatically** so the jobs
fit in the smaller of the commit limit and the free physical RAM
(`WORKER_MEMORY_LIMIT`) × `WORKER_MEMORY_FRACTION`; it logs `Memory: ...` and
warns `--workers N reduced to K`. Use `--buffering 1` to force streaming when
you need the lower peak: at effort 7 the files come out ~1.5 % larger with the
same quality, but at effort 8–9 streaming gives effort 7's result — measured in
[Streaming vs whole-image](README_jxl_recompressor.md#streaming-vs-whole-image-what---buffering-1-costs-measured)
(cjxl 0.12.0). Masters at d ≤ 0.5 stream at effort 8–9 anyway.

**The safe worker count depends on image size, not just on the machine.** On
64 GB, at effort 9 with default buffering:

| Source | 30 workers | Comfortable ceiling |
|---|---|---|
| 24 MP | ≈ 30 GB | 30+ |
| 45 MP | ≈ 47 GB — tight | ~25 |
| 93 MP scan | ≈ 100 GB — pages badly | ~15 |

**`--buffering 0` does not pay for itself.** It costs 2.5–3.3× the RAM and
3.8–7.6× the time to return **1–1.6 percentage points** of file size. At 45 MP
it gives the same 65.4 % at effort 7 and effort 9, so pairing it with effort 9
buys nothing at all. This is why it is off by default.

### Lossy (`--distance 0.05`): same RAM, a fraction of the time

Same photos, same measurement method, at the near-lossless distance most
delivery workflows use:

| Source | Effort | Buffering | RAM per worker | Time | JXL size |
|---|---|---|---|---|---|
| 24 MP | 7 | default | 0.86 GB | 8.1 s | 19.4 % |
| 24 MP | 9 | default | 0.92 GB | 9.4 s | 19.4 % |
| 24 MP | 9 | `0` | 6.53 GB | 42.3 s | 20.6 % |
| 45 MP | 7 | default | 1.56 GB | 13.1 s | 17.4 % |
| 45 MP | 9 | default | 1.56 GB | 15.4 s | 17.4 % |
| 45 MP | 9 | `0` | 12.09 GB | 74.5 s | 18.7 % |

**RAM is the same as lossless**, so size `--workers` from either table. What
collapses is time: effort 9 goes from 79.9 s to 9.4 s at 24 MP and from 168.7 s
to 15.4 s at 45 MP (8–11× faster). Effort is nearly free here — 7 → 9 costs
about a second and changes nothing in the output. Same RAM only **below the
threshold**: above distance 0.5 at effort 8–9 (from 3 at effort 7) RAM jumps 2.5–8× —
see the exception above.

> **Never combine `--buffering 0` with lossy.** At effort 9 it costs 7–8× the
> RAM and ~4.7× the time **and produces a larger file** (20.6 % vs 19.4 % at
> 24 MP; 18.7 % vs 17.4 % at 45 MP). It loses on all three axes. At 12 GB per
> worker, five workers would already need 60 GB. At effort 7 it is merely
> pointless: double the RAM for the same time and the same size.

### 8-bit sources: much faster, barely lighter

The table above is all **16-bit** input. Running the same photos converted to
8 bits (identical dimensions and ICC, pixels shifted down by 8):

| Source | Effort | RAM per worker | vs 16-bit | Time | vs 16-bit |
|---|---|---|---|---|---|
| 24 MP 8-bit | 7 | 0.69 GB | −21 % | 6.4 s | 3.1× faster |
| 24 MP 8-bit | 9 | 0.95 GB | −4 % | 13.2 s | 6.1× faster |
| 45 MP 8-bit | 7 | 1.40 GB | −19 % | 11.8 s | 3.1× faster |
| 45 MP 8-bit | 9 | 1.36 GB | −12 % | 24.7 s | 6.8× faster |

**Do not raise `--workers` for 8-bit work expecting the memory to halve.** Bit
depth is a speed lever, not a memory one: encoding is 3–7× faster, but RAM
falls only 4–21 %, because `cjxl` works in an internal representation that does
not shrink with input depth. Only the PNG intermediate halves. Size the worker
count from the 16-bit figures either way.

Every figure on this page is measured on **libjxl v0.12.0**. Older libjxl is
not characterised here — if you are on one, upgrading is the answer.

---

## Logs

```
<script_folder>/Logs/jxl_tiff_encoder/YYYYMMDD_HHMMSS_<pid>.log
```

Opening line shows all active settings:
```
Mode: 7 | Effort: 7 | Distance: 0.1 | RAM PNG: False | D50: auto | Workers: 16
```

Final summary includes D50 patch statistics (when applicable):
```
Done: 42 OK | 0 overwrites | 0 skipped | 0 errors
D50 patch: 0 applied | 0 skipped (already correct) | 0 needed patch, 42 already correct (mode: auto)
```


---

## How to verify output

```powershell
# Check JXL has embedded ICC (ICC lives in XMP CreatorTool)
exiftool -XMP-xmp:CreatorTool photo.jxl | findstr "ICC:"

# Check encoding params (they live in dc:Description)
exiftool -XMP-dc:Description photo.jxl

# Check EXIF Software (informational; the D50 patch no longer reads it)
exiftool -Software photo.jxl

# Full JXL info
jxlinfo -v photo.jxl
```


---

## Known behaviors

### IrfanView and color-calibrated monitors (reported & fixed)

**Update:** This issue was reported to the IrfanView developer and an updated plugin DLL with proper ICC profile support was received. It is recommended to download the latest JXL plugin from the IrfanView website to test if the fix has been publicly released.

*Previous behavior (old plugin):*
```
JXL lossless files embed the ICC color profile as a blob. Most software handles this
correctly — GIMP, XnView MP, Darktable, Firefox, Waterfox, and `jxl_to_jpeg.py` all
display correct colors.

IrfanView's behavior with lossless JXL appeared to depend on the system display profile
installed on the machine. The issue was specific to IrfanView on calibrated systems.

**The files themselves are correct.** Any conformant JXL decoder will display the colors
accurately. If using an old IrfanView plugin where colors look wrong, use lossy at `d=0.1` 
(imperceptible difference), or open the files in any of the other viewers listed above.
```

*For detailed technical information about JXL color management, XYB vs non-XYB, 
ICC blobs vs native primaries, and primary coordinates reference tables, 
see [JXL Color Internals](jxl_color_internals.md).*

### IrfanView EXIF display limitations

**TIFF → JXL (this tool):** EXIF is visible in IrfanView ✅  
*Reason: Boxes are reordered so Exif comes before codestream.*

**JPEG → JXL:** EXIF is **not visible** in IrfanView ❌  
*Reason: Uses Brotli compression (`brob` box) which IrfanView cannot read.*

**Recommendation:** Use **XnView MP** or **digiKam** for reliable EXIF viewing regardless of source format.

### XnView MP color profile display for lossy JXL
```
XnView MP shows `Color Profile: sRGB` in the properties panel for lossy JXL files,
even when the actual colorspace is ProPhoto RGB or AdobeRGB.

**This is not a conversion to sRGB.** It is a display error in XnView MP's metadata panel.

Lossy JXL encodes colorspace information as compact numeric primaries (CICP-style),
not as an embedded ICC blob. XnView reads the ICC blob field, finds nothing, and
falls back to showing "sRGB" as a default label.

The actual colorspace is correctly preserved and correctly rendered — as confirmed
by `jxlinfo` and by every other viewer (GIMP, Darktable, browsers, etc.).

→ See [JXL Color Internals](jxl_color_internals.md) for full details.
```
---

## Multi-Page TIFF Support (v1.7.0+)

Multi-page TIFFs are handled explicitly instead of silently discarding extra pages:

- `--multipage-mode split` — **default**. Encode each real page to a separate JXL
  (`photo.jxl`, `photo_page1.jxl`, ...). A TIFF with one real page yields exactly
  `photo.jxl`, identical to the old `ignore` default. Thumbnail pages follow
  `--thumbnail-mode` (`auto` default, `scans`, `exclude` or `include` — see
  [Thumbnail pages](#thumbnail-pages---thumbnail-mode-v2170) below).
- `--multipage-mode ignore` — encode only page 0, drop the rest (the pre-v1.8.2
  default). Not silent: files whose extra pages are dropped are logged as warnings
  (capped at 20 lines per run) and the run summary repeats the total
  (`Multi-page: DISCARDED N page(s) from M TIFF(s)`). Many TIFFs are multi-page
  without looking like it — Capture One and most scanners append an embedded
  preview page, and film scanners add an IR/mask page.
- `--multipage-mode skip` — skip files that have more than one "real" page
- `--multipage-mode split_all` — encode every page, **always including thumbnails**.
  Equivalent to `--multipage-mode split --thumbnail-mode include`; `--thumbnail-mode`
  is ignored in this mode.

**Images in SubIFDs are not encoded — and their source is never deleted.** Only
the main IFD chain is read. A TIFF/EP- or DNG-style file that keeps a small
preview in IFD0 and the full image in a SubIFD would otherwise have its preview
archived and the real image deleted with `--delete-source`; such a file is
logged (`SubIFD image(s) NOT encoded`) and kept by every delete gate.

**Lossy encodes keep the colour under a 4th channel.** A 2nd/4th channel (alpha,
or the IR channel of an RGB+IR scan saved as 4 channels) reaches cjxl as alpha,
and lossy cjxl would otherwise rewrite the colour wherever that channel is 0.
The encoder turns on cjxl's `keep_invisible` option for such pages (density
cost only). The
channel's ExtraSamples type is still written back by the decoder as unassociated
alpha.

Thumbnail pages are detected via standard TIFF `SubfileType` flags (`is_reduced` / `is_subifd`). When splitting, a kept thumbnail gets a configurable suffix (`--thumbnail-suffix`, default `_thumbnail`). The suffix must be a plain filename suffix — an empty value, a path separator, or `..` is rejected at startup (a thumbnail name must never point outside the destination).

### Thumbnail pages (`--thumbnail-mode`, v2.17.0)

| Mode | Keeps the thumbnail page of |
|---|---|
| `auto` (default) | every TIFF, except those whose Software tag contains a name in `THUMBNAIL_DROP_SOFTWARE` (default: `Capture One`, which adds a tiny preview to every export) |
| `scans` | scans only: the Software tag contains a name in `SCANNER_SOFTWARE` (SilverFast, VueScan, Epson Scan, Nikon Scan, FlexColor, Frontier, EZ Controller, ScanGear, Plustek), or the TIFF has an IR channel, or neither a Software tag nor camera EXIF — the film-scan rule of [`SCAN_DISTANCE`](#film-scans-are-archived-lossless-scan_distance-v2170) |
| `exclude` | none (the default before v2.17.0) |
| `include` | every TIFF |

Both lists sit at the top of the script; the match is a case-insensitive
"contains". Why keep a reduced copy at all: scanner software can depend on it.
SilverFast shows "Loading..." in its browser instead of the picture for a scan
that lost its thumbnail page.

Two things `auto`/`scans` never do:

- **Add a thumbnail to an archive this run does not rewrite.** The group id
  covers the set of pages a split writes, so a sync run that skipped a scan's
  existing pages and wrote only its new thumbnail would leave a lone page in a
  second group. A TIFF whose main JXL is skipped (sync: up to date, or no
  `--overwrite`) keeps the layout it was archived with — with or without the
  thumbnail. To add the thumbnails to scans archived before v2.17.0, run once
  with `--overwrite` on those folders.
- **Fail on a thumbnail they cannot encode.** The decoder appends a JPEG (YCbCr)
  preview to every single-page TIFF it writes; re-archiving such a TIFF leaves
  that preview out instead of reporting an error. `include` keeps the old
  behaviour (every thumbnail page, an error for one that cannot be encoded).

**Pages a TIFF no longer has (v2.17.1).** When a run rewrites every page of a
TIFF and the new layout has fewer pages than the archive it replaces — a
re-archive with `--thumbnail-mode exclude --overwrite`, a photo re-exported
from Capture One whose thumbnail `auto` now drops, a scan saved again without
its IR page — the old pages would stay beside the new ones under the old group
id, and the decoder would report an incomplete group. After the run, a leftover
**thumbnail** page is removed (`Removed a thumbnail page left by an earlier
encode of …`): only a file named like one of that TIFF's pages, carrying that
TIFF's `jxlphoto-src` and a group id the new pages do not have, and only while
the TIFF still exists. A leftover **real** page is never deleted: the run names
it with a warning (`… KEPT; check it`) and counts it in the summary.

**Dropped thumbnails are not warned per file.** A mode that leaves a thumbnail
out does what was asked, so it produces a single summary line
(`Thumbnails: excluded N thumbnail page(s) from M TIFF(s)`, naming the reason)
instead of one WARNING per file — a library where every export carries a preview
page would otherwise bury the real errors. Pass `--warn-thumbnail-discard` when
you want the file names.

Split pages carry a group marker in `XMP-dc:Relation` (`jxlphoto-mpg:<id>`), so `jxl_tiff_decoder.py` can reconstruct the original multi-page TIFF only from genuinely split files. Independently-named files such as `scan.jxl` + `scan_page2.jxl` are never merged.

Since v2.0.2 that id identifies **this split**, not just its source: it covers
the set of pages the split produces. Re-encoding a file whose page structure did
not change keeps the same id (the outputs simply overwrite each other, which is
what a sync run is for), while a run that writes a *different* set of names gets
a new one. That matters after a decode-and-re-archive cycle — excluding a
thumbnail does not renumber the real pages, so a `[real, thumb, IR]` scan
archives as `{0, 2}`, and the reconstructed `[real, IR]` re-archives as
`{0, 1}`, leaving `scan_page2.jxl` behind. With a source-only id those leftovers
joined the new group and the next decode rebuilt a TIFF with a duplicated page.

One exception keeps pre-v2.0.2 archives healable: those carry the *old* id (the
source path alone). If a page of such an archive is lost and re-encoded while
its siblings are skipped by sync/no-overwrite, stamping it with the new id
would split the archive into two groups the decoder cannot merge. So when every
existing sibling page in the destination unanimously carries this source's
legacy id, the new pages are stamped with that same id (logged as an INFO
line), and the archive heals into one group. Mixed or missing sibling markers
keep the new formula — the id is adopted from proof, never guessed.

The encoder also names such leftovers when it finds them in the destination:

```
WARNING | 1 JXL(s) in the destination look like pages of a PREVIOUS split of the
          same source and are not part of this run's output. Nothing was
          deleted — check them and remove them ...
WARNING |     E:\archive\scan_page2.jxl
```

Nothing is removed automatically: `<name>_page1.jxl` can legitimately be an
independent file. See "Groups with MORE pages than the split recorded" in the
decoder README for what happens if they are left in place.

Since v1.8.1, split pages also carry `jxlphoto-page:<N>` (the TIFF page index) and `jxlphoto-thumb` (thumbnail role) in `dc:Relation`. The decoder treats these as **authoritative** — the filename is only a fallback for JXLs encoded before v1.8.1, and under `--no-reconstruct-multipage` only a file that carries `jxlphoto-page`/`jxlphoto-group` has its `_thumbnail` suffix trusted at all. This makes reconstruction safe even when the source TIFF itself is named like a page (`scan_page3.tif`, `holiday_thumbnail.tif`): a single-page photo whose stem ends in `_thumbnail` carries only `jxlphoto-depth` and decodes as an ordinary full-resolution photo, never as a reduced-resolution thumbnail.

Since v2.0.0, every page also carries `jxlphoto-pages:<N>` — **how many JXLs the split produced**. It is what lets the decoder tell a complete split from a truncated one: pages `{0,1}` of a three-page scan otherwise look exactly like a two-page scan, and a decode of that writes a valid short TIFF whose sources `--delete-source` would then destroy. The count is the number of JXLs **written**, not the pages the source TIFF had — a thumbnail dropped by `--thumbnail-mode exclude` is not part of the group, so the decoder must not go looking for it. See "Incomplete splits" in the decoder README.

### Where the first master came from (`jxlphoto-origin`)

Every JXL this script writes also records what the **first master** of the photo
was made from, in `XMP-dc:Relation` as `jxlphoto-origin:<value>` — `tiff8` or
`tiff16` for the TIFF it read. The transcoder adds `jpeg`/`png8`/`png16` when
it re-encodes JPEG/PNG pixels, and the recompressor adds `jxl-lossy` or
`jxl-lossless` for a JXL no script of the toolkit wrote — what `jxlinfo` reads
in its header; `jxl` when it cannot tell, and in files written by v2.10.0 — or
`jpeg` when it converts a `jbrd` JXL. The rule is
**written once, then carried forward unchanged**: an input that already names
an origin keeps that value (a decoded TIFF carries the JXL's origin, so a
re-encode inherits it), a clean input gets the value above, and an input the
toolkit touched before this marker existed — it carries any other `jxlphoto-*`
token or a `cjxl d=` record — stays **unknown**: absent means unknown, never
guessed. `--strip` drops it like the rest of the metadata. The only thing that
reads it is the recompressor's `--on-regeneration` guard (see the recompressor
README); no delete gate does.

### Processing history (`xmpMM:History`)

Every output this script (and the other three) writes also gains ONE entry in
`xmpMM:History` — `Action` (`converted`, or `derived` for a derivative), `When`,
`SoftwareAgent` = `jxl-photo <version> (<repo link>)` and `Parameters` = the
script and the conversion. Earlier entries the file already had (Capture One, a
previous run of the toolkit) are kept; nothing in the toolkit ever reads it back
— no gate, no policy. `--strip` writes none. The exact format, the per-script
differences and how to read it yourself:
[What jxl-photo writes into your photos](metadata_written.md#processing-history-xmpmmhistory).

### Per-Page ICC Preservation (v1.7.0)

When splitting, each page is encoded with its own effective ICC profile (read from the page's own ICC tag 34675; if absent, page N > 0 inherits IFD0's profile for color interpretation). Pages that inherit the ICC are flagged with `jxlphoto-icc:inherited` in `dc:Relation`, so the decoder can reconstruct them without an ICC tag when the original page also had none.

### Grayscale and SubfileType Preservation (v1.7.0)

Single-channel pages are encoded as grayscale and flagged with `jxlphoto-grayscale` in `dc:Relation`. The original `SubfileType` value (e.g. `2` for PAGE, `4` for MASK) is also recorded so the decoder can restore the page's role. Inherited RGB ICC is not applied to grayscale pages, which prevents libpng iCCP errors on scanner IR/mask pages.

A page with a 2nd or 4th **channel** (grey + extra, RGB + extra — e.g. VueScan's RGBI layout) reaches cjxl as alpha. Its TIFF `ExtraSamples` value is recorded as `jxlphoto-extrasamples:<v>` (`0` unspecified, e.g. IR; `1` associated alpha; `2` unassociated alpha) so the decoder gives the channel back its role (v2.8.1); lossy encodes keep the colour under it with cjxl's `keep_invisible` option (v2.8.0).

### Film scans and their scanner software (v2.17.0)

A SilverFast scan (image, 1:8 thumbnail, IR page) archived with the defaults
and decoded back opens in SilverFast with its IR channel and dust removal
(iSRD) recognised and its browser icon, as the original did — checked on a
Provia slide and a Fuji Pro 160 negative, also after the master was
recompressed. What that takes:

- the IR page keeps its role (`SubfileType` 4, `jxlphoto-subfiletype`);
- the thumbnail page is kept (`--thumbnail-mode auto`, above);
- **the XMP travels as one block.** SilverFast keeps its IR/iSRD settings in
  an XMP namespace of its own, which exiftool can read but not write; the
  field-by-field copy used before v2.17.0 dropped it, and SilverFast then no
  longer saw an IR channel. The encoder, the decoder and the recompressor now
  copy the source's XMP whole;
- **private TIFF tags** (code 32768 and up) that exiftool cannot copy ride in
  a `jxlphoto-tifftag` marker and are written back on the same page —
  SilverFast's 35243/35244, for instance. Only byte-sized types, up to 4 KB
  each; a page with a larger one (Affinity Photo stores a 230 MB document in
  one) keeps none of them, and the log says so.

Other scanner programs were not tested. If yours relies on something else,
test one file before converting a whole archive.

### Film scans are archived lossless (`SCAN_DISTANCE`, v2.17.0)

A film scan gets its own distance, in the same run as every other TIFF:
`SCAN_DISTANCE = 0` (lossless) by default, `--scan-distance` per run. A scan
is a TIFF whose Software tag names a program in `SCANNER_SOFTWARE`, or that
has an IR channel (an IR/mask page, or a 2nd/4th channel that is not alpha),
or that has **neither a Software tag nor camera EXIF** (an exposure, an
aperture or a focal length). The last one is what lab scans (Noritsu,
Frontier) and Epson Scan look like: on a library of 15 000 TIFFs, the ~4 000
without a Software tag were scans and scanned documents, save ~90 that carry
a camera's EXIF and 455 camera TIFFs rewritten without any tag (Fuji S5 Pro,
Nikon E990). Those still carry the 160×120 thumbnail page cameras write, which
none of the ~2 300 scans had: a TIFF without either tag that has such a page
is taken for a camera TIFF, not a scan, and is marked `jxlphoto-scan:no` so
the recompressor does not take its master for an old lab scan.

Any other TIFF without either tag is still a guess, and the run says so.
Before the first encode (counting only the TIFFs it will write, so a sync run
over an archived library stays quiet) and again in the summary, it counts the
scans at the scan distance by reason — scanner software, an IR page, guessed
— and lists the folders of the guessed ones with how to opt out
(`--scan-distance same`); the wizard's end-of-run recap shows the counts too.
A wrong guess costs space, not quality.

Why lossless: a scan can only be redone by scanning again, a "final" scan is
often the only copy left (its raw is gone, like a TIFF whose RAW is), and many
are 48-bit scans meant to be corrected later. A raw negative also gets heavy
processing after the archive (inversion, curves), which magnifies a loss
that is invisible in the file: the same negative recompressed to `d=0.3`
showed it clearly after that processing, while `d=0.05` and `d=0.1` encoded
directly looked fine to the owner. Sizes for that 91 MP negative (image +
thumbnail + IR): TIFF 739 MB, lossless JXL ~440 MB, `d=0.05` 107 MB,
`d=0.1` 72 MB.

- `--scan-distance same` (or `SCAN_DISTANCE = None`): scans follow
  `--distance` like any TIFF.
- `--scan-scope raw` (`SCAN_SCOPE = "raw"`): only **raw** scans keep the scan
  distance — the scanner's own ICC profile (class `scnr`), no profile at 16
  bits, or an IR channel; a **final** scan (converted to an output profile
  such as sRGB or Adobe RGB, or 8-bit) follows `--distance`. Measured on a
  library of 2 970 scanner TIFFs, doubtful cases fall on the raw side, where
  a mistake costs space, not quality.

Every page of a scan carries `jxlphoto-scan:raw` or `jxlphoto-scan:final`,
which the recompressor reads: it never re-encodes a scan lossy unless asked
(`--on-scan`, see its README). The delete gate and `--verify-roundtrip` check each output
at the distance it was encoded at (bit-exact for a lossless scan), and the
space preflight counts the scans at their full size.

### Examples

```bash
# Old behavior: encode only page 0, ignore extra pages
# (mode 8 will then REFUSE to delete any source whose pages were dropped)
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode ignore

# Skip any TIFF that has more than one real page
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode skip

# Split pages and exclude thumbnails
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode split --thumbnail-mode exclude

# Keep thumbnail pages for scans only
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --thumbnail-mode scans

# Split every page, including thumbnails, with a custom suffix
# (split_all already includes thumbnails; --thumbnail-mode has no effect here)
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode split_all --thumbnail-suffix "_thumb"

# Split, drop thumbnails, but list every file whose thumbnail was dropped
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode split --thumbnail-mode exclude --warn-thumbnail-discard

# Film scanner workflow: encode main + preview + IR/mask pages
python jxl_tiff_encoder.py "E:\film_scans" "E:\film_scans_jxl" --mode 2 --multipage-mode split_all --thumbnail-mode include
```

---

## Disclaimer

These tools were made for my personal workflow. 
Use at your own risk — I am not responsible for any issues you may encounter.

However, If you find any bugs, feel free to report to me - I will gladly try my best to improve this project.

Always test with a small batch before processing important archives.

---

## Version history

Feature and fix history: [version_history.md](./version_history.md) · [bug_tracking_since_v1.0.md](./bug_tracking_since_v1.0.md) · [new_features_since_v1.0.md](./new_features_since_v1.0.md)

---

## License

MIT License — feel free to use, modify, and distribute.

---

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation  
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling  
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O  
- [Claude](https://www.anthropic.com/claude) (Anthropic) and [DeepSeek](https://www.deepseek.com), among other AI tools, for code assistance, reviews and technical discussion
