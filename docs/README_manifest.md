# Manifests — running a list of folders

A manifest is a CSV where **each row is one folder, processed as its own run** —
its own child process, its own log, its own failure domain. It is how you
convert `G:\2024`, `G:\2025` and `G:\2026` in one session, give each folder its
own mode, or (since v2.3.0/v2.4.0) its own output recipe.

The CSV is **re-read on every run**: add a row in Excel in the morning and that
night's scheduled run already picks it up. That is the main reason to schedule a
manifest instead of a plain folder.

Two ways to run one:

- **Interactively:** `[1] New workflow` → pick the direction → Auto Mode →
  `[M] Run from manifest`.
- **Unattended:** inside a preset (`--run-preset`), which is what Task Scheduler
  runs — see [Manifest → preset → Task Scheduler](#manifest--preset--task-scheduler-end-to-end).

* * *

## Creating a manifest

### Option A — let Auto Mode generate it (recommended)

`[1] New workflow` → direction → source folder → Auto Mode →
`[P] Generate manifest CSV` writes `manifests\manifest_YYYYMMDD_HHMMSS.csv`
next to `jxl_photo.py`, already with:

- one row per folder that holds origin files — modes 6/7 get one row per
  `_EXPORT` folder, and the recursive modes get only the *outermost* folders
  (each child already walks the whole tree, so subfolder rows would convert
  everything several times);
- the `Mode` and `Direction` cells filled;
- the optional columns the direction/modes accept, present and **empty** — you
  only fill what differs (see below);
- UTF-8 **with BOM**, so Excel opens non-ASCII folder names (Japanese, accents)
  correctly instead of as mojibake.

`[V] View manifest` shows the latest one as a table.

### Option B — write it by hand

A minimal manifest is four columns:

```csv
Source,Destination,Mode,Direction
G:\2024,,3,tiff2jxl
G:\2025,,3,tiff2jxl
G:\2026,,3,tiff2jxl
```

Rules that apply to the file itself:

- **Name and location:** to be runnable from the menu the file must be named
  `manifest_*.csv` and live in `manifests\` next to `jxl_photo.py` — the picker
  lists them newest first.
- **Encoding: UTF-8.** Generated files carry a BOM; hand-written ones load with
  or without it. In Excel, save as **`CSV UTF-8 (comma delimited)`** — plain
  `CSV` writes the system ANSI codepage, and the wrapper *refuses* such a file
  rather than guess an encoding and run against a wrongly-decoded path (these
  paths drive a converter that can delete sources).
- **Comments:** a row whose `Source` is empty or starts with `#` is ignored.
- **Relative paths** resolve against the CSV's own folder, not against wherever
  the wrapper happened to start (a scheduled run starts in
  `C:\Windows\System32`). `..` anywhere in a path refuses the whole manifest.
- **Header:** the first row is skipped as the header only when it names at
  least one of `Destination`/`Mode`/`Direction` — a data folder that happens to
  be called `source` is not eaten, and a deleted header does not silently lose
  entry #1.
- **Unknown column names after `Direction`** refuse the whole file: a typo like
  `Resise` must not be silently ignored, or you would believe the resize
  happened.

* * *

## The four base columns

| Column | Can it be empty? | Meaning |
|--------|------------------|---------|
| `Source` | **No** — an empty cell turns the row into a comment | Input folder (a file also works in the flat modes 0/1). |
| `Destination` | Yes — empty falls back to `Source` | Only honored by modes **0** and **2**; every other mode computes its own output location, and a value here is ignored (the wrapper warns). Mode 2 wants a real destination: empty ⇒ Destination = Source, which flattens the tree next to the originals (the recompressor refuses that, exit 2). |
| `Mode` | Yes — empty = auto-detect (legacy behaviour) | 0–8, per row; different folders can use different modes (see the [mode table](README_jxl_tools.md#step-4--organization-mode)). Excel's `7.0` is accepted; a fraction like `7.5` is refused, not truncated. |
| `Direction` | Yes, per row — but if *no* row fills it, the file is legacy and only assumed to match, with a warning | `tiff2jxl`, `jxl2tiff`, `jpeg2jxl`, `jxl2jpeg`, `jxl2png` or `jxl2jxl`. Every filled cell must equal the workflow you start — a TIFF→JXL manifest can never be replayed by a JXL→TIFF session, and one file never mixes two directions. |

* * *

## Optional columns: absent vs empty vs filled

All optional columns live **after `Direction`**, and all of them understand the
same three states — but what an **empty cell** means depends on the family:

| State | Derivative columns (`OutputICC`, `Resize`, `Sharpen`, `RenameFrom`, `RenameTo`) | Export columns (`ExportMarker`, `ExportSubfolder`, `ExportJxlFolder`) |
|-------|------|------|
| **Column absent** from the CSV | The wizard's/run's answer applies to every row | Same |
| **Cell empty** | The option is **not applied on that row** — it also *clears* the wizard's answer: the row runs plain | The row **keeps the run's value** (marker/subfolder/output folder) — filling one row never resets the others |
| **Cell filled** | Overrides the wizard's answer for that row only | Overrides the run's value for that row only |

Any invalid value refuses the **whole manifest before anything runs**, with the
row and column named — a typo must never become a silent "option not applied".
The confirmation preview lists each row's recipe next to it
(`sRGB · long:2048 · screen`).

### Derivative columns — `jxl2jxl`, `jxl2jpeg`, `jxl2png` only

A value in any other direction refuses the manifest, and the generator only
writes these columns in these three directions.

| Column | Values | Effect on that row |
|--------|--------|--------------------|
| `OutputICC` | `sRGB`, `AdobeRGB`, or a path to a `.icc`/`.icm` file (relative anchors to the CSV folder) | Colour-converted 16-bit derivative, converted from the source's own profile. |
| `Resize` | `long:2048`, `short:1024`, `50%` — add `+up` to allow upscaling (`long:3000+up`) | Resize the output, aspect ratio kept. |
| `Sharpen` | `none`, `screen`, `print` | Output sharpening after the resize. |
| `RenameFrom` / `RenameTo` | Literal text — no path characters, no `..` | The first occurrence of `RenameFrom` in the output **file name** becomes `RenameTo` (case-sensitive). `RenameTo` requires `RenameFrom` on the same row. Refused on a bit-exact `jxl2jpeg` recovery row (the recovered JPEG keeps the original file name). |

The safety rules of a direct run apply per row:

- A **resize/sharpen** row can never run in place (mode 8, or mode 0 with
  Destination = Source) and never deletes its source — combined with a delete
  answer, the whole manifest is refused up front. A bit-exact JPEG recovery
  refuses resize/sharpen entirely — and a `RenameFrom` on it is refused too,
  up front, for the same reason (the recovered JPEG keeps its original name).
  An `OutputICC` cell on such a row is **ignored with a warning** instead of
  refused: there is no pixel to convert, and the recovered JPEG keeps the
  original bytes, colour included. Pick the lossy JXL→JPEG conversion for
  those rows.
- An **`OutputICC`-only** row keeps its conversion but receives no
  `--delete-source`, while the plain rows keep it.
- In `jxl2jxl`, **rename** rows also cannot run in place.
- The duplicate-output guard sees the **renamed** names, so a rename that would
  land two rows on the same file aborts before anything is written.

### Export columns — marker, subfolder and output folder per row

| Column | Directions | Effect on that row |
|--------|------------|--------------------|
| `ExportMarker` | All six | Detect and scan that row under this export marker instead of the run's. |
| `ExportSubfolder` | All six (only mode 7 uses it) | Passed to the child as `--export-subfolder`. A filled cell on a row whose `Mode` is present and not 7 refuses the manifest (mode 6 processes every subfolder by design); a legacy row without a Mode cell is accepted. |
| `ExportJxlFolder` | `tiff2jxl`, `jxl2jxl` only | Names the modes-6/7 output folder for that row. The decoder and the transcoder have no such flag — a value in any other direction refuses the manifest. |

Values must be **one plain folder name** — no path separators, no `..`, not
blank. The wrapper validates the effective name (against the row's own marker
and input subfolder, with the child's default included) before any child
starts. To ask for the script's own default on a single row, write its name
(e.g. `16B_JXL`); to process every subfolder, put mode **6** on that row.

`ExportSubfolder` is also the fix for the mode-7 warning: a hand-written mode-7
row whose Source sits **above** the marker cannot derive its subfolder, and its
child would then process *every* subfolder of the marker — mode 6 wearing a
mode-7 label. Attended runs warn and can be declined; unattended presets are
refused. Filling the column on that row resolves it.

### `ExcludeFolders` — folder exclusions per row (`tiff2jxl`, `jxl2tiff` only)

| Column | Directions | Effect on that row |
|--------|------------|--------------------|
| `ExcludeFolders` | `tiff2jxl`, `jxl2tiff` only | The row's `';'`-separated folder NAMES, passed to the child as `--exclude-folders`. The encoder and the decoder are the only children with that flag — a **filled** cell in any other direction refuses the manifest. |

Its cell is a **hybrid** between the two families above: it follows the export
columns' "empty keeps the run's value" rule, and adds an explicit "no
exclusion" value, because the run's answer can legitimately be a list that
does not suit one row:

| State | Meaning on that row |
|-------|---------------------|
| **Column absent** from the CSV | The run's `--exclude-folders` answer applies |
| **Cell empty** | The row **keeps the run's value** (same convention as the Export columns) |
| **Cell `-` or `none`** | **No exclusion on that row** — the run's answer does not apply here |
| **Cell filled** | That row's own list, **overriding** the run's value |

Like the flag itself, the cell takes folder **names**, not paths: any `;`
entry containing `\` or `/` refuses the whole manifest. A cell holding only
`;` characters (with no name) reads as **empty** — it filters nothing in the
children, so the row keeps the run's value instead of silently erasing it.
(The generator writes the column, empty, for `tiff2jxl`/`jxl2tiff`
manifests only.)

The collision skip-check reasons per row: disjoint trees under different
markers still skip the full output scan, while two marker folders that nest
(one row's `_EXPORT` next to another row's `_EXPORT\SITE`) force it. The
wrapper's collision walk also applies each row's own `ExcludeFolders` value
before resolving outputs, so an excluded file — which the child would never
process — cannot abort the run as a phantom collision.

* * *

## What is per-row and what is run-wide

Only the CSV cells differ between folders. Everything the wizard asks after you
pick the manifest applies to **every** row: workers, distance/effort/quality,
multi-page policy, downgrade/regeneration policies, staging, expert flags — and
the delete behaviour, which is deliberately run-wide (see below). If two folders
need different *encoding settings*, that is two runs, not one manifest.

Per-row: `Source`, `Destination`, `Mode`, and the nine optional columns.

* * *

## Recipes

### 1. Nightly sync of the whole library (the auto-generated shape)

```csv
Source,Destination,Mode,Direction
G:\2024,,6,tiff2jxl
G:\2025,,6,tiff2jxl
G:\2026,,6,tiff2jxl
```

Mode 6 processes only what sits inside `_EXPORT` folders; Destination is
ignored. Disjoint Sources like these skip the collision scan, so the run starts
converting immediately instead of walking three libraries first.

### 2. Different modes per folder

```csv
Source,Destination,Mode,Direction
D:\scans\projeto_antigo,,3,tiff2jxl
E:\entregas\cliente_x,D:\cliente_x_jxl,2,tiff2jxl
G:\2026,,6,tiff2jxl
```

Row 1 keeps the tree (each subfolder gets its own output subfolder), row 2
flattens a whole tree into one output folder (Destination required — mode 2 is
one of the two modes that honor it), row 3 follows the marker.

### 3. Two folders under one `_EXPORT`, each writing its own output folder

```csv
Source,Destination,Mode,Direction,ExportJxlFolder
G:\fotos\_EXPORT\A,,6,tiff2jxl,PRINT_JXL
G:\fotos\_EXPORT\B,,6,tiff2jxl,SCREEN_JXL
```

Empty cells in the export family keep the run's value, so only the differing
column needs filling. (`ExportJxlFolder` exists only for `tiff2jxl`/`jxl2jxl`.)

### 4. Capture One tree: one row per output subfolder (mode 7)

```csv
Source,Destination,Mode,Direction,ExportSubfolder
E:\sessao\_EXPORT\16bit,,7,tiff2jxl,16bit
E:\sessao\_EXPORT\AdobeRGB,,7,tiff2jxl,AdobeRGB
```

Hand-written mode 7: bake the subfolder into the Source **and** name it in the
column. Either one alone works, but the explicit column protects the row if the
child's `EXPORT_*_SUBFOLDER` default is ever edited, and a Source above the
marker with neither degrades to mode 6 (see the warning above).

### 5. Web derivatives from JXL masters (sRGB, 2048 px, sharpened)

```csv
Source,Destination,Mode,Direction,OutputICC,Resize,Sharpen,RenameFrom,RenameTo
G:\2026\_EXPORT\site,G:\2026\_EXPORT\site_web,2,jxl2jpeg,sRGB,long:2048,screen,ProPhoto,sRGB
G:\2026\_EXPORT\blog,G:\2026\_EXPORT\blog_web,2,jxl2jpeg,sRGB,long:1024,screen,,
```

Row 2 leaves the rename columns empty: not applied on that row. Pick the
**lossy** JXL→JPEG conversion when the wizard asks — a bit-exact recovery
cannot be resized. Derivative rows never delete their sources; a manifest that
answers "delete originals" is refused up front while they are present.

### 6. Recompress the archive in place

```csv
Source,Destination,Mode,Direction
G:\2024,,8,jxl2jxl
G:\2025,,8,jxl2jxl
```

Mode 8 in `jxl2jxl` **replaces the masters** (nothing stays side by side): the
wizard charges the HHMM token before the run, and a preset carrying it is
refused unattended. To schedule a recompression, use a non-replacing mode
(1–7) instead.

* * *

## What is checked before anything runs

Each row runs as a **separate child process**, and a child can only see its own
entry — so everything that spans two rows is checked by the wrapper first:

**Load-time** (each refuses the whole manifest, naming the row/column):
encoding; unknown column names; Direction mismatch; Mode out of range or
fractional; `..` in a path; invalid option values; `ExportSubfolder` on an
explicit mode ≠ 7.

**Planning-time:**

- **Source overlaps** — equal or nested Sources re-process the same files as
  two child processes. Nesting only counts when the *outer* entry's mode
  recurses (modes 0/1 are flat), and identical Sources always count. Attended:
  loud warning, confirmation defaulting to No. Unattended preset: refused.
- **Duplicate outputs** — two rows whose files would land on the same output
  (the guard sees the *renamed* names) abort the run before anything is
  written. The scan that proves this is expensive, so it is skipped when a
  collision is impossible: rows that write inside their own Source tree (modes
  1/3/8, mode 0 in place, 6/7 whose marker sits below the Source) with
  non-overlapping Sources. Modes 2/4/5 always scan; mode 0 with a real
  Destination scans; 6/7 rows sharing a marker dir scan; nested per-row
  markers scan.
- **Output vs Source** — an entry whose Source *is* another entry's (future)
  output folder would process files the other entry just wrote: refused/warned
  like the other guards.
- **Derivative combinations** — the in-place/delete refusals listed with the
  derivative columns.
- **Mode 7 above the marker** — the warning/refusal listed with
  `ExportSubfolder`.

* * *

## Deleting originals from a manifest

A manifest containing **mode-8** rows is asked once whether to delete the
originals, and the answer applies to **every** row — deleting is a run-wide
setting, not a per-row one, and the question says so, naming the entry count
and the other modes whose originals go too. Answering yes then asks the same
gates the `[D]` menu asks for a single run:

- **round-trip verification** before each delete (TIFF→JXL only);
- whether to delete originals that were **already converted** (`SKIP`) — lossy
  directions charge a separate confirmation, default No;
- how an **existing output** is matched to the source about to replace it, when
  at least one row drops folder structure (`path` / `content` / `adopt` —
  TIFF→JXL only for `adopt`).

The `HHMM` token is still charged once, at execution time, and a dry run never
asks for it. Derivative (resize/sharpen) rows never delete: combined with a
delete answer the manifest is refused up front.

A preset saved with delete on is **refused unattended, in any mode** — that
confirmation is a typed token, and honouring it automatically would let a
scheduled task delete originals on its own.

* * *

## Reading the result

A manifest over a few folders can run for hours, and the per-folder totals
scroll away long before it ends. The run therefore closes with a block covering
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

Both list the file **paths**, not just a count: a number still leaves you
opening per-entry logs to find out which photo broke.

An entry whose child crashed, was killed, or was cancelled shows `(no summary -
failed)` instead of zeros, so a dead child is never mistaken for a clean
folder.

Ctrl+C cancels cleanly: the running child is killed, the summary block above is
still rendered (the interrupted entry marked `cancelled`, the rest
`not started`), and the wrapper exits `130` — the accounting of what *did*
complete survives the interruption.

**`Logs/jxl_photo/<timestamp>.log`** holds the same block plus the untruncated
folder paths (the table shortens them to fit the terminal) and the complete
failure lists (the screen shows the first 15). Each entry still writes its own
detailed log under `Logs/jxl_tiff_encoder/` etc., and the block lists those
paths too.

* * *

## Manifest → preset → Task Scheduler (end to end)

1. **Create the CSV** — generated (`[P]`) or by hand — and prove it once
   interactively: `[M] Run from manifest`, as a dry run first if you like.
2. **Save the preset.** Back at the main menu: `7` Presets → `[S]` snapshots
   the run you just did (mode 99 + the manifest path) under a name, e.g.
   `nightly-sync`.
3. **Check it without the menu:**
   ```powershell
   py jxl_photo.py --list-presets
   py jxl_photo.py --run-preset nightly-sync --dry-run
   ```
4. **Schedule it:**
   ```powershell
   schtasks /Create /TN "jxl-photo nightly" /SC DAILY /ST 03:00 ^
     /TR "cmd /c cd /d C:\tools\jxl-photo && py jxl_photo.py --run-preset nightly-sync" /RL LIMITED
   ```
   The `cd /d` matters — logs land in `Logs\` relative to it. In the task
   editor: **Program** `py`, **arguments** `jxl_photo.py --run-preset
   nightly-sync`, **Start in** the folder where `jxl_photo.py` lives (never
   blank).

What the scheduled run does: **sync** by default (reconvert only what is
newer); `--overwrite` redoes everything; `--dry-run` simulates — and neither is
ever inherited from the saved run. Exit codes: `0` ran, `1` failed or refused,
`2` no such preset. A preset that deletes sources is refused unattended, in
any mode. Full unattended reference:
[Running a preset unattended](README_jxl_tools.md#running-a-preset-unattended-task-scheduler--cron).

Because the CSV is re-read on every run, the scheduled job follows your Excel
edits with no preset changes — the preset points at the file, not at a
snapshot of its rows.

* * *

## Cheat sheet — what can be omitted

| Item | Omitted means |
|------|---------------|
| Header row | Tolerated only if the first row does not look like a header — but always write one |
| `Destination` cell | Falls back to `Source`; only modes 0/2 read it at all |
| `Mode` cell | Legacy auto-detection per folder (0, 6 or 7) |
| `Direction` cell | Tolerated per row; if *no* row fills it, the file is assumed to match the workflow, with a warning |
| Optional column (whole column) | The wizard's/run's answer applies to every row |
| Derivative cell empty | Option not applied on that row (clears the wizard's answer) |
| Export cell empty | Row keeps the run's marker/subfolder/output folder |
| `ExcludeFolders` cell empty | Row keeps the run's folder exclusions; a filled cell in this direction-restricted column overrides them (and `-` means "none" on that row) |
| BOM | Optional for reading; always written by the generator |
| Rows | Empty `Source` or a leading `#` = comment |
