# jxl_photo.py

Interactive wrapper for the JPEG XL processing toolkit. Provides a **wizard-style menu interface** that guides you through all conversion options — no need to remember CLI flags for each script.

Handles TIFF → JXL, JXL → TIFF, JPEG → JXL, JXL → JPEG, and JXL → PNG conversions through a unified menu system with persistent configuration.

* * *

## Requirements

```
Python 3.9+
rich            (optional — enables fancy UI, auto-detected)
tifffile        (for TIFF workflows)
numpy           (tifffile dependency)
imagecodecs     (for LZW/ZIP compressed TIFFs)
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

### Download the Correct Files

| Tool | Download | What to Get |
|------|----------|-------------|
| **cjxl / djxl** | https://github.com/libjxl/libjxl/releases | `jxl-x64-windows-static.zip`  **(NOT `jxl-x64-windows.zip` which has only DLLs)** |
| **exiftool** | https://exiftool.org | `exiftool-XX.XX_64.zip`  **(Windows .zip, NOT .tar.gz source)** |
| **ImageMagick** | https://imagemagick.org | Installer `.exe` (Q16-HDRI x64) |

### exiftool Setup

> **Note:** The scripts (including the wrapper `jxl_photo.py`) automatically detect both `exiftool.exe` and `exiftool(-k).exe`. **Renaming is no longer required**, but still works if you prefer.

The Windows download comes as `exiftool(-k).exe`. If you want to rename it anyway:

```powershell
# Option A: Rename
Rename-Item "C:\tools\exiftool\exiftool(-k).exe" "exiftool.exe"

# Option B: Duplicate and rename (keeps original)
Copy-Item "C:\tools\exiftool\exiftool(-k).exe" "C:\tools\exiftool\exiftool.exe"
```

### Verify
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
│ [✓] cjxl/djxl | [✓] exiftool | [✓] magick | [✓] tifffile | [✓] pillow | [✓] imagecodecs | [✓] rich                    │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭───────────────────────────────────────────────────── Main Menu ────────────────────────────────────────────────────╮
│  1  New workflow                                                                                                   │
│  2  Repeat last workflow (unknown)                                                                                 │
│  3  Check dependencies again                                                                                       │
│  4  Edit default settings                                                                                          │
│  5  Reset all settings                                                                                             │
│  6  Move settings file                                                                                             │
│  7  Presets (2 saved)                                                                                              │
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

> **Tip:** Values shown in **blue/cyan** (Rich) or between `[brackets]`/`(parentheses)` are defaults. Just press `Enter` to accept!
>
> Example:
> ```
> Workers [4]:          ← Press Enter to use 4
> Distance [0.1]:       ← Press Enter to use 0.1
> Execute? [y/n] (y):   ← Press Enter to accept 'y' (yes)
> ```

* * *

## Main menu options

| # | Option | Description |
|---|--------|-------------|
| `1` | New workflow | Start the conversion wizard |
| `2` | Repeat last workflow | Re-run the previous conversion with same settings. **Manifest runs are repeatable too**: the entry reads `Repeat last workflow (manifest: <file>.csv)`, skips the input-folder question and re-reads the CSV, so edits you made in Excel between runs are picked up. It is disabled only while that CSV is missing. You are still asked overwrite/sync (default: sync) and dry-run every time |
| `3` | Check dependencies again | Re-scan all tools and libraries |
| `4` | Edit default settings | Change workers, quality, effort, export marker |
| `5` | Reset all settings | Delete config and start fresh |
| `6` | Move settings file | Toggle between script folder and User Profile |
| `7` | Presets | Save the last workflow under a name and re-run it later. See below |
| `0` | Exit | Quit |

### Presets (option 7)

`Repeat last workflow` only ever remembers **one** run. Presets let several
recurring jobs coexist — a nightly manifest sync, a per-shoot conversion — each
under its own name:

```
--- Presets ---
  1. nightly-sync
     TIFF->JXL | manifest: manifest_2026.csv | workers 12 | d=0.05
  2. shoot-inplace
     TIFF->JXL | mode 6 | G:\2026 | workers 8 | d=0.05
[number] run | [S] save last workflow as preset | [D] delete | [B] back
```

- **`[S]`** snapshots whatever ran last (including a manifest run) under a name
  you choose, so it stops being affected by later runs.
- **`[number]`** runs a preset. It goes through exactly the same questions as
  `Repeat last workflow`: overwrite/sync (default sync) and dry-run are always
  asked, never inherited.
- Presets live in the same `~/.jxl_tools_config.json` as the rest of the settings.

#### Running a preset unattended (Task Scheduler / cron)

```powershell
py jxl_photo.py --list-presets                    # names + what each one does
py jxl_photo.py --run-preset nightly-sync         # runs it, no menu, no prompts
py jxl_photo.py --run-preset nightly-sync --dry-run
py jxl_photo.py --run-preset nightly-sync --overwrite
```

The only other command-line flag is `--recheck`, which re-runs the dependency
detection (cjxl/djxl/exiftool/ImageMagick) and refreshes what the status line at
the top of the menu reports — use it after installing or moving a tool, instead
of wondering why the menu still shows it as missing.

`--run-preset` runs once and exits — safe to point a scheduled task at, since it
never waits on stdin.

| | |
|---|---|
| **Default** | sync (reconvert only what is newer). `--overwrite` redoes everything |
| **Dry-run** | off unless you pass `--dry-run`. It is **never** inherited from the stored run — a saved simulation does not silently make the scheduled job a simulation, nor the other way round |
| **Exit code** | `0` ran, `1` failed or refused, `2` no such preset (available names are printed) |

`--overwrite` and `--dry-run` are rejected without `--run-preset`, rather than
silently ignored — a stray `--dry-run` that did nothing would mean a real
conversion for someone who asked for a simulation.

> **Presets that delete sources cannot run unattended — in any mode.** A preset
> with `delete_source` on is refused with an explanation: that confirmation is a
> typed token, and honouring it automatically would let a scheduled task delete
> originals on its own. Run it from the menu, or add `--dry-run` to simulate it.
> (This used to be keyed on mode 8; deleting is available in every mode now, and
> so is the refusal.)

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

**JPEG source:**
- JXL Lossless — Reversible transcoding, ~20% smaller
- JXL Lossy — Smaller files, configurable distance

**TIFF source:**
- JXL d=0 — Lossless (exact replica)
- JXL d=0.1 — Near-lossless (recommended, much smaller). This entry shows **your**
  distance when you change it in option 4 (e.g. `d=0.05 — Your default`)
- JXL d=1.0 — Visually lossless (smallest)
- Custom distance — Any value 0-15 (pre-filled with the last distance you used)

**JXL source:**
- **JPEG Auto-Detect** — Recommended: lossless if jbrd present, else lossy
- JPEG Lossless — Force lossless transcoding (requires jbrd box)
- JPEG Lossy — Force lossy conversion with quality/ICC control
- PNG — With transparency support
- TIFF — Lossless master with optional JPEG preview

### Step 3 — Source Directory
Enter the folder path containing the files (surrounding quotes are stripped, so Explorer's "Copy as path" works).

The wizard takes **one** folder here. To run a **list of folders** in a single
session — `G:\2024`, `G:\2025`, `G:\2026` — use a manifest (see below).

* * *

## Running a list of folders (manifest)

A manifest is a CSV where each row is one folder, processed as its own run. Auto
Mode can generate one for you (`[P] Generate manifest CSV`), but you can also
write it by hand and drop it in `manifests/` next to `jxl_photo.py`:

```csv
Source,Destination,Mode,Direction
G:\2024,,3,tiff2jxl
G:\2025,,3,tiff2jxl
G:\2026,,3,tiff2jxl
```

| Column | Meaning |
|--------|---------|
| `Source` | Input folder. Rows starting with `#` are comments. |
| `Destination` | Only used by modes 0 and 2. Leave **empty** for every other mode — they compute their own output location, and a value there is ignored (the wrapper warns). |
| `Mode` | 0–8, per row. Different folders can use different modes. |
| `Direction` | `tiff2jxl`, `jpeg2jxl`, `jxl2tiff`, ... Must match the workflow you start, so a TIFF→JXL manifest can never be replayed by a JXL→TIFF session. |

Then: `[1] New workflow` → pick the same direction → at Auto Mode choose
**`[M] Run from manifest`**. The encoding settings you pick in Step 6/6A
(distance, effort, multi-page policy, ...) apply to every entry.

**Editing a manifest in Excel:** generated manifests are UTF-8 **with BOM**, so
Excel opens non-ASCII folder names (Japanese, accents) correctly instead of as
mojibake. When you save, keep **`CSV UTF-8 (comma delimited)`** — plain `CSV`
writes the system ANSI codepage, and the wrapper then refuses the file rather
than guess an encoding and run against a wrongly-decoded path.

Each row runs as a **separate child process**, so a failure in one folder does not
kill the rest. Before anything runs, the wrapper checks for files from *different*
rows that would land on the same output file and refuses the whole manifest if it
finds any — a child process can only see its own entry, so that check has to live
here. That scan walks every Source, so it is **skipped when a collision is
impossible**: rows that write inside their own Source tree (modes 1/3/8, mode 0
in place, and 6/7 whose export marker sits below the Source) cannot reach each
other as long as their Sources do not overlap. The common
`G:\2024` / `G:\2025` / `G:\2026` manifest in mode 6 therefore starts converting
immediately instead of walking all three libraries first.

**Deleting originals from a manifest.** A manifest containing **mode-8** rows is
asked once whether to delete the originals, and the answer applies to *every*
row — deleting is a run-wide setting, not a per-row one, and the question says
so. Answering yes then asks the same gates the `[D]` menu asks for a single run:

- **round-trip verification** before each delete (TIFF→JXL only);
- whether to delete originals that were **already converted** (`SKIP`);
- how an **existing output** is matched to the source about to replace it, when
  at least one row uses a mode that drops folder structure (2/4/5/6/7).

The `HHMM` token is still charged once, at execution time, and a dry run never
asks for it.

### Reading the result

A manifest over a few folders can run for hours, and the per-folder totals scroll
away long before it ends. The run therefore closes with a block covering
everything:

```
===========================================================================
Manifest complete: 3 entries - 2 ok, 1 with failures, 0 cancelled
---------------------------------------------------------------------------
  #  mode folder                           OK    ovw   skip corrupt    err
  1  6    D:\2026\260318_Rio             2003      0      0       0      2
  2  6    E:\2026\260425_Nara            3001      2      4       0      0
  3  6    G:\2026\260512_Recife           758      0      0       1      0
---------------------------------------------------------------------------
  TOTAL files                            5762      2      4       1      2
---------------------------------------------------------------------------
  D50 patched: 12  |  Thumbnails excluded: 5762
---------------------------------------------------------------------------
  FAILURES (2):
    [1] D:\2026\260318_Rio\_EXPORT\IMG_0412.tif
        -> cjxl exit 1
    [1] D:\2026\260318_Rio\_EXPORT\IMG_0587.tif
        -> ICC profile rejected
---------------------------------------------------------------------------
  CORRUPT / UNREADABLE (1):
  These were NOT converted. The source files are damaged.
    [3] G:\2026\260512_Recife\_EXPORT\scan_099.tif
        -> no readable pages (corrupt or truncated TIFF)
===========================================================================
```

The first line counts **entries**; the table counts **files**. The two failure
sections are deliberately separate:

| Section | Meaning |
|---------|---------|
| `FAILURES` | The conversion failed on that file — this is what makes the run exit non-zero. |
| `CORRUPT / UNREADABLE` | The source file is damaged and was not converted. The run itself is fine, so exit codes are unaffected. |

Both list the file **paths**, not just a count: a number still leaves you opening
per-entry logs to find out which photo broke.

An entry whose child crashed, was killed, or was cancelled shows
`(no summary - failed)` instead of zeros, so a dead child is never mistaken for a
clean folder.

**`Logs/jxl_photo/<timestamp>.log`** holds the same block plus the untruncated
folder paths (the table shortens them to fit the terminal) and the complete
failure lists (the screen shows the first 15). Each entry still writes its own
detailed log under `Logs/jxl_tiff_encoder/` etc., and the block lists those paths
too.

If you just want a shell loop instead, the encoder takes one folder per call:

```powershell
foreach ($y in "G:\2024","G:\2025","G:\2026") { py jxl_tiff_encoder.py $y --mode 3 }
```

### Step 4 — Organization Mode
How output files are organized. Press `?` for detailed explanations with visual examples.

| Mode | Input | How it finds files | Name | Description |
|------|-------|-------------------|------|-------------|
| `0` | File or folder | Flat (non-recursive) — only files in the given folder | In-place (flat) | Same folder as source. If folder: non-recursive (flat) |
| `1` | File or folder | Flat (non-recursive) — only files in the given folder | Subfolder | Creates `converted_jxl/` or `converted_tiff/` subfolder next to source |
| `2` | Directory | Recursive — all subfolders | Flat → output folder | All files merged to single output folder (recursive) |
| `3` | Directory | Recursive — all subfolders | Recursive subfolders | Each subfolder gets its own output subfolder |
| `4` | Directory | Recursive — all subfolders | Folder rename (suffix swap) | Renames folder: `JXL_raw/` → `TIFF_raw/` (or appends `_TIFF`) |
| `5` | Directory | Recursive — all subfolders | Sibling folder | Creates sibling: `JXL_16bits/`, `TIFF_16bits/` (by direction) |
| `6` | Directory | Recursive, **only inside `EXPORT_MARKER`** (default: `_EXPORT`) | Marker _EXPORT (full) | ONLY files INSIDE `EXPORT_MARKER` — ignores everything outside. Marker name is configurable. |
| `7` | Directory | Recursive, **only inside specific `EXPORT_MARKER` subfolder** (configurable) | Marker _EXPORT (subfolder) | Like mode 6 but only a specific subfolder of `EXPORT_MARKER`. Both the marker name and the subfolder are configurable. |
| `8` | File or folder | Recursive — walks all subfolders | In-place recursive | Same folder as source, walks subfolders. Originals are KEPT — use `[D]` to delete them |
| `D` | — | (follows the layout you pick) | **Convert and DELETE originals** ⚠️ | Not a layout: pick any mode `0`-`8`, then the sources are removed once each output is written to that mode's destination and verified — IRREVERSIBLE |

Items shown in **green** (like `_EXPORT`) are configurable in **option 4 (Edit default settings)**.

### `[D]` — convert, verify, then delete the originals

Deleting is **not a layout**, so it is not a mode. `[D]` asks which layout you want
(`0`-`8`) and arms the deletion alongside it — on the command line that is simply
`--mode 3 --delete-source`. Mode `8` on its own is just "in-place recursive" and
keeps your files.

A source is removed only after its output **exists at the mode's destination**
(after the staging move, and that move must have succeeded), and **passes the
integrity check there**. If two inputs would map to the same output — mode 5 merges
sibling folders, modes 6/7 drop one level under the marker, mode 2 flattens a whole
tree — the run **aborts before writing anything** rather than spending two originals
on one file.

Three confirmations, each more specific than the last (rather than the same question
twice, which trains you to answer it twice):

1. a plain yes/no, so a mis-keyed `D` costs nothing;
2. the concrete consequence — **how many files, from which folder, to where**. This
   is the one that catches a wrong folder, because the count is visible;
3. the **HHMM token** at execution time. A dry run never asks for it.

**Already-converted originals** (offered inside `[D]`): sources whose JXL already
exists are reported as SKIP and normally **kept**, which means an archive
interrupted between the encode and the delete can never be finished without
re-encoding everything. Turning this on deletes them too — but never on the
timestamp: the output must exist and be a valid, complete JXL, and with the
round-trip check on, it must decode back to the source. That check matters *more*
here than for a fresh conversion, because there is no "this run wrote it" to fall
back on — it is the only thing tying that JXL to that photo.

What backs that up depends on the direction, and `[D]` says which one you are in:

| Direction | What backs a delete of an already-converted original |
|---|---|
| JPEG ↔ JXL lossless | **Provenance PROVEN** — `checksums.md5` holds the source's hash; it must match |
| TIFF → JXL | Structural check, plus the round-trip pixel comparison below if you enable it |
| JXL → TIFF | Structural check only |
| Any lossy direction | ⚠️ Structural check only, **and nothing better is possible** |

> ### ⚠️ Lossy directions
>
> A lossy conversion stores no checksum and its output cannot reproduce the
> source, so nothing can tie the existing file to the original you are about to
> delete — an unrelated file with the same name would pass. `[D]` charges a
> **separate confirmation** (default **No**) before arming it there.

**Matching an existing output to its source** (asked inside `[D]` for modes
2/4/5/6/7): those modes drop folder structure, so two files with the same name in
different folders land on the same output. Before overwriting an output that
already exists — and deleting the file that made it — the run checks that the
archive really came from this source:

| Answer | What it compares | Cost | Available for |
|---|---|---|---|
| `path` (default) | the recorded **location** of the source | free | every direction |
| `content` | also accepts matching source **bytes**, so it survives folders you MOVED since archiving | reads and hashes every source | every direction |
| `adopt` | for an archive built **before this check existed**, which has no record at all: each unmarked output is decoded, compared against its source, and then stamped — a one-time healing pass | a full decode per unmarked output | **TIFF → JXL only** |

A mismatch always fails closed: not converted, nothing overwritten, nothing
deleted. `adopt` relaxes only "I cannot tell" — an output whose marker names a
*different* source is still refused.

> `adopt` is offered only for TIFF → JXL because only the encoder can prove the
> pairing before trusting it. For JXL → TIFF and the JPEG↔JXL directions an
> archive written before the markers existed has no migration path: use a
> structure-preserving mode (0/1/3/8) for those folders.

**Round-trip verification** (TIFF → JXL only, offered inside `[D]`): decodes each
JXL and compares it with the source before deleting. With `--distance 0` the pixels
must match **exactly**; on a lossy run it is a brightness + PSNR **sanity** check
that catches a black or scrambled encode — not a quality check, since quality loss
is what you asked for. It roughly doubles the run, which is why it is opt-in — but
it is the only gate that looks at pixels rather than at file structure. Other folder names (like `converted_jxl`, `16B_JXL`, `16B_TIFF`) must be edited directly in the scripts.

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
The wizard asks for the subfolder name in Step 5 (passed to the scripts as `--export-subfolder`). Empty (default) = processes all subfolders, same as mode 6.

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

> **Tip:** The value shown in **blue** (or between `[brackets]`/`(parentheses)`) is the default. Just press `Enter` to accept!
>
> Example:
> ```
> Workers [4]:          ← Press Enter to use 4
> Distance [0.1]:       ← Press Enter to use 0.1
> Execute? [y/n] (y):   ← Press Enter to accept 'y' (yes)
> ```

### Step 7 — Summary
Full review of all settings before execution. Type `YES` to confirm.

* * *

## Edit default settings (option 4)

Persistent defaults saved to `~/.jxl_tools_config.json`.

> Changing **workers**, **quality**, **effort** or **distance** here also updates
> what the next run uses, including *Repeat last workflow*. Values you leave
> untouched keep whatever the last run used, and the screen tells you when a
> saved session is currently overriding a default.

- **Staging directory** — output SSD path
- **Workers** — default thread count
- **Quality** — JPEG quality for lossy workflows
- **Effort** — cjxl effort level (1-10)
- **Distance (TIFF→JXL)** — your preferred cjxl distance (default: `0.1`). This is
  the value offered as entry `[2]` in Step 2, so if you always work at `0.05`,
  set it once here and pick it with a single keystroke instead of going through
  `[4] Custom` on every run. Setting it to `0` makes entry `[2]` run the
  **lossless** encoder, exactly like entry `[1]`.
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
| Overwrite mode (1/2) | Step 6 | Always asked (1=overwrite, 2=sync) |
| ICC conversion (sRGB) | Step 6 | JXL→JPEG/PNG |
| TIFF compression | Step 6 | zip/lzw/none |
| Bit depth | Step 6 | 8 or 16 for TIFF output |
| JPEG Preview | Step 6 | JXL→TIFF (default: yes) |
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
| Default distance (TIFF→JXL) | Drives entry `[2]` of Step 2. Default: `0.1` |
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
| `EMBED_JPEG_THUMBNAIL` | `False` | Embed 256px JPEG thumbnail in JXL (also `--embed-thumbnail`) |
| `CJXL_MODULAR` | `False` | Force Modular encoder for lossy (`--modular=1`) |
| `CJXL_BUFFERING` | `None` | [libjxl ≥ 0.12] `--buffering` for pixel encodes (also `--buffering` CLI); `None` = use cjxl default (fast); `0` = best compression, ~6× slower on large lossless TIFFs ([benchmark](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.0)) |
| `USE_RAM_FOR_PNG` | `True` | Keep PNG intermediate in RAM |
| `DELETE_CONFIRM` | `True` | Require HHMM confirmation for mode 8 delete |

**CLI-only encoder flags (no wizard question — pass via Expert flags in Step 6B):** `--icc-png-strategy` (scanner-profile workaround for lossy encodes), `--buffering` (max compression on libjxl ≥ 0.12), `--clear-icc-cache` (reset the cautious ICC cache). Expert flags are appended LAST, so they override earlier wizard choices.

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
| `JPEG_PREVIEW_SIZE` | `256` | Max preview dimension |
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
| `FORCE_CONTAINER_FOR_LOSSY` | `True` | Always pass `--container=1` for lossy encode |
| `CJXL_BUFFERING` | `None` | [libjxl ≥ 0.12] `--buffering` for lossy pixel encodes (setting only, no CLI flag); `None` = use cjxl default (fast); `0` = best compression, slower |

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

`jxl_photo.py` streams the selected script's output in real-time and only writes
a log of its own for **manifest runs** — a combined summary across all entries:
```
Logs/jxl_photo/YYYYMMDD_HHMMSS.log
```

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

Full tracking: [bug_tracking_since_v1.0.md](./bug_tracking_since_v1.0.md) | [new_features_since_v1.0.md](./new_features_since_v1.0.md) | [code_quality_refactoring.md](./code_quality_refactoring.md)

* * *

## License

MIT License — feel free to use, modify, and distribute.

* * *

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O
- [Kimi](https://www.kimi.com) (Moonshot AI) and [Claude](https://www.anthropic.com/claude) (Anthropic) for code assistance and technical discussion
