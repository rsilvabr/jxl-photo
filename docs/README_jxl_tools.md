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
│  8  Repair JPEG recovery (jbrd audit/repair)                                                                       │
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
| `4` | Edit default settings | Change workers, quality, effort, export marker. The marker set here is **always** passed to every script (`--export-marker`), so the wrapper and the script anchor on the same folders even if a script's own `EXPORT_MARKER` setting was edited. The modes-6/7 output folder names shown in the delete panel are read from each script's own `EXPORT_*_FOLDER` setting |
| `5` | Reset all settings | Delete config and start fresh |
| `6` | Move settings file | Toggle between script folder and User Profile |
| `7` | Presets | Save the last workflow under a name and re-run it later. See below |
| `8` | Repair JPEG recovery (jbrd) | Audit/repair JXLs whose bit-exact JPEG recovery broke (v2.0.0–v2.0.3 wrote XMP markers into jbrd containers). Audit (dry run) is the default; the repair works on a copy and only replaces a file when the copy provably reconstructs |
| `0` | Exit | Quit |

The stored workflow is validated before a repeat or a preset replays it: the
config is plain JSON you can hand-edit, so not just the numbers (mode, workers,
distance, ...) but also the enumerated values (bit depth, provenance,
multi-page policy, compression, depth policy, conversion type, ICC profile)
are checked. A corrupt value is **refused with the reason**, not defaulted and
not passed to a child as a command line nobody typed. That includes the two
fields that pick WHICH script runs (`last_origin_format`/`last_dest_format`):
an unknown value there is refused instead of routing the run to the wrong
script while the panel still shows the direction you expected.

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

`--list-presets` is read-only and works even with no codecs installed — useful
for checking what a config carries before copying it to another machine.

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

##### Scheduling on Windows (Task Scheduler)

One line — it works the same in `cmd` and in PowerShell (a `^` line
continuation does **not** work in PowerShell, and a long line pasted from a
narrow window can arrive broken in two, so keep it on one line):

```text
schtasks /Create /TN "jxl-photo nightly" /SC DAILY /ST 03:00 /RL LIMITED /TR "cmd /k cd /d C:\tools\jxl-photo && py jxl_photo.py --run-preset nightly-sync"
```

`cmd /k` keeps the window open when the run ends, so you see the result and any
error the next morning — see [Keep the window open](#keep-the-window-open--or-a-failed-run-goes-unseen)
for why that matters. The `cd /d` matters too — see *Start in* below. Or use the
task editor: *Create Task → Actions → New → Start a program*:

- **Program/script:** `cmd`
- **Add arguments:** `/k py jxl_photo.py --run-preset "nightly-sync"` — quote a
  name that contains spaces (`--run-preset "SYNC PHOTOS"`)
- **Start in:** the folder where `jxl_photo.py` lives. **Do not leave this
  blank**: `py jxl_photo.py` is looked up in it, so a blank field (which
  starts the task in `C:\Windows\System32`) makes every run fail at once with
  `can't open file ... jxl_photo.py` and exit code 2 — the same code as
  "no such preset". The logs themselves always land in `Logs\` next to the
  scripts.

##### Several presets in one scheduled run (a `.cmd` file)

One scheduled task can run any number of presets, in order, from a small batch
file. That is how a library with several workflows stays in sync with one
task — e.g. the TIFF masters, then light copies made FROM those masters (they
must run after the masters of the same night), then a JPEG folder. Save this as
`run_scheduled_presets.cmd` in the folder where `jxl_photo.py` lives:

```bat
@echo off
rem One block per preset, in the order they must run.
cd /d "%~dp0"

echo === %date% %time%  SYNC PHOTOS
py jxl_photo.py --run-preset "SYNC PHOTOS"
echo === exit code %errorlevel%

echo === %date% %time%  MOBILE
py jxl_photo.py --run-preset "MOBILE"
echo === exit code %errorlevel%

rem py jxl_photo.py --run-preset "OUTTAKES JPEG"
echo === %date% %time%  done
```

- **The `echo` lines are the morning report.** With the window kept open
  (`cmd /k`, below), each preset ends in one line — `exit code 0` ran,
  `1` something failed or was refused, `2` no such preset — so a glance at the
  bottom of the window tells you whether to scroll up. They are optional; the
  two `py` lines alone work the same.
- **Order matters** when one preset reads what another writes: masters first,
  derivatives (`--output-icc`/resize/a lighter distance) after.
- **Each line runs even if the one before failed** — every preset is its own
  run with its own log, so one bad folder does not cancel the others. (Join
  lines with `&&` only if a failure must stop everything after it.)
- **`rem` disables a line** without deleting it — handy for a preset you only
  want after a backup.
- `cd /d "%~dp0"` makes the batch work from wherever it is started (logs land
  in `Logs\` next to the scripts), so the task needs no *Start in*.
- **Do not list presets that delete sources**: they are refused unattended
  (exit 1) and only clutter the log. Run those from the menu.

Then point one task at the batch file — again one line, `cmd` or PowerShell
(the example runs every Saturday at 23:30; `/SC DAILY` for every night):

```text
schtasks /Create /TN "jxl-photo" /SC WEEKLY /D SAT /ST 23:30 /RL LIMITED /TR "cmd /k C:\tools\jxl-photo\run_scheduled_presets.cmd"
```

- **A task you already have**: `schtasks /Change /TN "jxl-photo" /TR "cmd /k
  C:\tools\jxl-photo\run_scheduled_presets.cmd"` replaces only what it runs
  (the schedule stays). If it asks for the *run as* password, just press
  Enter — the task stays "run only when logged on". *Access denied* means the
  task was created by an administrator: run the console as administrator — or
  save that one line as a `.cmd` file and use *right-click → Run as
  administrator*.
- **Keep the path free of spaces** (`C:\tools\jxl-photo`), or the quoting
  inside `/TR` gets fiddly; the task editor (*Actions → Edit*) takes any path.
- **`/k`, not `/c`** — see the next section.
- **Leave "random delay" off** in the task's trigger: with *Delay task for up
  to 1 day* the run can start any time in the 24 hours after the time you set.

##### Keep the window open — or a failed run goes unseen

A scheduled run has nobody watching it. With `cmd /c` — or with **Program**
`py` in the task editor — the console window closes the moment the run ends,
**including when it failed**. Everything is still written to `Logs\`, but
nobody opens the logs after every run: a preset that starts failing one night
(a moved or unplugged drive, a full disk, a safety abort, a batch of
out-of-memory errors) keeps failing quietly, night after night, until you
happen to look for a file that was never written.

That is why every example on this page uses **`cmd /k`**: the window **stays
open** after the run, with the run summary and every error on screen, until you
close it. The next morning the window on your desktop *is* the report — a
failure is in front of you instead of buried in a log.

Setting it up:

- **New task, one line:** `/TR "cmd /k ..."`, as in the examples above.
- **A task you already have:** `schtasks /Change /TN "<task name>" /TR "cmd /k
  C:\tools\jxl-photo\run_scheduled_presets.cmd"` changes only what it runs —
  or in the task editor, *Actions → Edit*: **Program/script** `cmd`, **Add
  arguments** `/k ` followed by what it ran before.
- **In a `.cmd` file**, the `echo === exit code %errorlevel%` line after each
  preset (example above) gives one verdict per preset at the bottom of the
  window.

Three things to know:

- **The window only appears when the task runs as you, while you are logged
  on** — *General → Run only when user is logged on*, which is what
  `schtasks /Create` without `/RU` sets up. A task set to *Run whether user is
  logged on or not* runs in a hidden session: no window at all, whatever `/k`
  says. On nights it runs, lock the screen instead of signing out.
- **Close the window once you have read it.** While it is open the task counts
  as *Running*, and Task Scheduler's default (*Settings → If the task is
  already running: Do not start a new instance*) skips the next run. That is
  as much a safeguard as a catch: a failure nobody has looked at holds the next
  run back instead of piling up behind it. (The same tab's *Stop the task if it
  runs longer than 3 days* closes a forgotten window eventually.)
- **`/c` is for a machine nobody reads** — a server, an account nobody logs on
  to. Then check the exit code in Task Scheduler's *History* (`0x1` = failed,
  refused or aborted) and the [logs](#logs): every run writes the child's own
  log, plus a combined wrapper log for manifest runs, and an abort names its
  culprits there.

##### Run it now, without waiting for the schedule

To test a new task, or to catch up after a night it skipped, start the **task
itself**. It runs exactly what the schedule would, with the same program, the
same folder and the same `cmd /k` window:

```text
schtasks /Run /TN "jxl-photo"
```

You can also do it from Task Scheduler: right-click the task → **Run**.

- **Close the window of the previous run first.** While that window is open the
  task still counts as *Running*, and Task Scheduler silently ignores the new
  start (*Do not start a new instance*, above). `schtasks /Query /TN
  "jxl-photo"` shows `Running` or `Ready`.
- **`SUCCESS: Attempted to run` only means the task was started.** The result
  is in the window it opens, and in the logs.

To run without the task, or to watch a run live, start it in a terminal
yourself. The window is yours and stays open:

```text
cd /d C:\tools\jxl-photo
run_scheduled_presets.cmd
```

To run a single preset instead, use `py jxl_photo.py --run-preset nightly-sync`.
Add `--dry-run` to that line to see what the preset would convert without
writing anything.

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
- **JXL** — decoding to JPEG, PNG, or TIFF, or recompressing to a smaller JXL

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
- **JXL (smaller)** — Recompress the archive to a new distance/effort
  (`jxl_recompressor.py`): ICC, metadata and provenance markers carried over.
  Requests that cannot gain anything (same or lower distance than the source's
  recorded `cjxl d=/e=`) fall back to a verbatim copy or are skipped, files
  that were already lossy-recompressed once (`gen≥2` in the record — every
  lossy encoder output is born at `gen=1`, so its first recompression is the
  normal case) trigger the regeneration policy (`--on-regeneration`), and a
  re-encode that comes out *larger* keeps the original bytes. JPEG-recoverable
  JXLs (jbrd) are copied verbatim by default. Derivatives (a colour/resize/
  sharpening recipe) record their distance/effort too; Step 6A asks whether an
  existing derivative must be re-derived when those change (default: the
  recompressor's `REDERIVE_ON_ENCODE_CHANGE` setting) and, after a yes, whether
  a LOWER effort at the same distance re-derives too (default: no — the
  existing file is the smaller one; `REDERIVE_ON_LOWER_EFFORT`).

  **Modes 0 and 8 REPLACE the source JXLs** here (the recompressor writes the
  new file over the old one; nothing stays "side by side"). The wizard says so
  in the mode list and charges the HHMM token before the run, exactly like a
  delete; `--run-preset` refuses such a preset unattended. In a manifest, a
  mode-0 row whose Destination is empty or equal to its Source is the same
  in-place run and is gated the same way (mode 8 always is). A mode-2 row
  whose Destination equals its Source is refused by the recompressor (exit 2):
  mode 2 would flatten the subfolders into the root, next to the originals.

### Step 3 — Source Directory
Enter the folder path containing the files (surrounding quotes are stripped, so Explorer's "Copy as path" works).

The wizard takes **one** folder here. To run a **list of folders** in a single
session — `G:\2024`, `G:\2025`, `G:\2026` — use a manifest (see below).

TIFF→JXL and JXL→TIFF runs — and only those; the flag lives only in the
TIFF encoder and decoder — get one more question at the end of Step 3:

```
Exclude folders? (';'-separated folder names, '-' = none; only affects recursive modes)
```

`-`, `none` or an empty answer means no exclusion; anything else is passed to
the child as `--exclude-folders` (folder NAMES, relative to the input root —
see the encoder/decoder READMEs). The default is your **last answer**, and
the answer is asked here — before the mode is known and before the `[D]`
delete panel counts the files — so that count always matches what the child
will actually process. The answer is also only **persisted and replayed for
these two directions**: a stale `last_exclude_folders` left over from a TIFF
run is not carried into a JPEG/JXL workflow — no child there reads the flag,
so the stale value can neither shrink a run nor raise the spammy "IGNORED"
warning on a direction that never asked.

* * *

## Running a list of folders (manifest)

A manifest is a CSV where each row is one folder, processed as its own run. Auto
Mode can generate one for you (`[P] Generate manifest CSV`), but you can also
write it by hand and drop it in `manifests/` next to `jxl_photo.py` (name it
`manifest_*.csv` so the picker lists it):

```csv
Source,Destination,Mode,Direction
G:\2024,,3,tiff2jxl
G:\2025,,3,tiff2jxl
G:\2026,,3,tiff2jxl
```

Then: `[1] New workflow` → pick the same direction → at Auto Mode choose
**`[M] Run from manifest`**. The encoding settings you pick in Step 6/6A
(distance, effort, multi-page policy, ...) apply to every entry; the CSV
carries what differs per folder. `Destination` is only honored by modes 0 and
2, and an empty `Mode` cell auto-detects.

Beyond the four base columns, a manifest can carry **per-row optional
columns** — the derivative recipe (`OutputICC`, `Resize`, `Sharpen`,
`RenameFrom`/`RenameTo`, on `jxl2jxl`/`jxl2jpeg`/`jxl2png`), the export
overrides (`ExportMarker`, `ExportSubfolder`, `ExportJxlFolder`), and the
per-row folder exclusions (`ExcludeFolders`, `tiff2jxl`/`jxl2tiff` only) —
each with rules for what may be omitted and what an empty cell means.

→ **Full manifest reference: [README_manifest.md](README_manifest.md)** —
every column, what can be omitted, the pre-run guards, deletion, the
end-of-run recap, a recipe gallery, and the manifest → preset → Task
Scheduler walkthrough.

Each row runs as a **separate child process**, so a failure in one folder does
not kill the rest; before anything runs, the wrapper refuses the whole manifest
if files from *different* rows would land on the same output (a child can only
see its own entry, so that check has to live here). A manifest containing
**mode-8** rows is asked once whether to delete the originals — the answer
applies to *every* row — and the `HHMM` token is still charged at execution
time.

**Editing a manifest in Excel:** generated manifests are UTF-8 **with BOM**, so
Excel opens non-ASCII folder names (Japanese, accents) correctly instead of as
mojibake. When you save, keep **`CSV UTF-8 (comma delimited)`** — plain `CSV`
writes the system ANSI codepage, and the wrapper then refuses the file rather
than guess an encoding and run against a wrongly-decoded path.

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
- Modes 6/7, TIFF→JXL and JXL→JXL: the **output folder name under the marker**
  (default `16B_JXL` for TIFF→JXL, `16B_JXL_small` for JXL→JXL; passed as
  `--export-jxl-folder`). This is what lets one preset write masters to
  `_EXPORT/16B_JXL` and another write derivatives to `_EXPORT/16B_JXL_sRGB`.
  The child validates the name (one plain component, not the marker, not the
  input subfolder) and exits 2 with a clear message if it is unusable
- Mode 2: Specify the output directory for merged files

Folder exclusions are not asked again here: the question (and its default)
is in Step 3, and the manifest's `ExcludeFolders` column overrides it per
row ([manifest reference](README_manifest.md)).

### Step 6 — Parameters
Basic parameters always shown:
- **Workers** — parallel threads (default: 4)
- **Quality / Distance / Effort** — context-aware based on conversion type
- **Downgrade policy** — JXL→JXL only: what to do when the request cannot gain
  anything (same or lower distance than the file already is): ask / copy / skip /
  convert. The wizard decides it up front and passes `--on-downgrade` to the
  child, which never prompts on an invisible stdin
- **Output colour space** — JXL→JXL only: `keep` (a normal recompression),
  `sRGB`, `AdobeRGB`, or a path to an `.icc` file. Anything but `keep` writes a
  **colour-converted 16-bit derivative** (passed as `--output-icc`), and then
  asks for the **rename text** (`--rename-from`/`--rename-to`, e.g.
  `ProPhoto-g22` → `sRGB`) so the file name follows the colour space. Not
  offered in modes 0/8: a derivative can never replace its own master, and the
  child refuses those runs. Answering `keep` (or picking mode 0/8) also clears
  a rename answered on an earlier pass through the step. A **manifest** run
  with a colour space or a rename is refused up front, before any child
  starts, when a row would run in place (mode 8, or mode 0 with Destination =
  Source) — the rows are listed
- **Resize** — JXL→JXL (modes 1–7) and JXL→JPEG/PNG: `none` (default), `long
  edge`, `short edge` or `percent`, then the value and "Allow upscale?"
  (`--resize-long`/`--resize-short`/`--resize-percent`, `--allow-upscale`).
  The value is validated before it becomes a flag: a percentage must be
  finite and above zero, an edge a positive integer — `nan`, `inf`, `0` and
  negatives are re-prompted instead of handed to the child to fail per file
  (the same domain the manifest's `Resize` column enforces). A resized
  output is a **derivative** of its own: not offered in modes 0/8
  for JXL→JXL, and not offered for the bit-exact JPEG recovery (which has no
  pixels to shape). `none` clears an answer from an earlier pass
- **Output sharpening** — the same directions: `none` (default), `screen` or
  `print` (`--sharpen`), applied after the resize on the output pixels' size.
  The expert overrides (`--sharpen-sigma`/`--sharpen-gain`/
  `--sharpen-threshold`) have no wizard question — use Expert flags in Step 6B.
  A run with resize and/or sharpening plus a delete option is refused up front
  (a derivative never deletes its source), and the Step 7 summary shows a
  `Resize:` and a `Sharpening:` line
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

Alongside these defaults the config keeps the **last-run answers** the wizard
offers back as defaults — among them `last_exclude_folders`, the raw
`';'`-separated string of the last TIFF↔JXL run's folder-exclusion answer
(`-`/empty stored as no exclusion). It is not editable here; the wizard's
Step 3 question offers it as the default, and `Repeat last workflow`/
snapshots replay it as-is — but only in the TIFF↔JXL directions, where the
flag actually exists; every other direction runs without it.

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
| Force Modular encoder (lossy) | 6A | TIFF→JXL; NOT for photos — screenshots/graphics only (default off: VarDCT) |
| ICC matrix mode | 6A | JXL→TIFF |
| Target ICC profile | 6A | JXL→TIFF |
| Skip ICC cleanup | 6A | JXL→TIFF |
| Skip MD5 verification | 6A | JPEG↔JXL |
| Auto-repair broken JPEG recovery | 6A | JXL→JPEG only; repairs a copy and decodes from it, the JXL is never modified (default: off) |
| Skip validation | 6A | JPEG↔JXL (risky) |
| Output suffix | 6A | JPEG↔JXL |
| Downgrade policy | Step 6 | JXL→JXL: ask/copy/skip/convert |
| Re-derive when distance/effort change | 6A | JXL→JXL derivatives only (colour/resize/sharpen runs): default = the recompressor's `REDERIVE_ON_ENCODE_CHANGE` setting |
| ...also on a lower effort (same distance) | 6A | Asked only after a yes above: default = the recompressor's `REDERIVE_ON_LOWER_EFFORT` setting (no) |
| Output folder under the marker | Step 5 | Modes 6/7, TIFF→JXL and JXL→JXL (`--export-jxl-folder`) |
| Output colour space (derivative) | Step 6 | JXL→JXL: keep/sRGB/AdobeRGB/.icc — 16-bit, never in place, never deletes (`--output-icc`) |
| Rename in output file names | Step 6 | JXL→JXL with an output colour space: e.g. ProPhoto→sRGB (`--rename-from`/`--rename-to`) |
| Resize (derivative) | Step 6 | JXL→JXL (modes 1–7) and JXL→JPEG/PNG: none/long/short/percent (`--resize-long`/`--resize-short`/`--resize-percent`, `--allow-upscale`) |
| Output sharpening | Step 6 | Same directions: none/screen/print after the resize (`--sharpen`); sigma/gain/threshold only via Expert flags |
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
| `WORKER_MEMORY_FRACTION` | `0.8` | Caps `--workers` so the parallel cjxl processes fit in this share of the memory budget; `0` = no cap |
| `WORKER_MEMORY_LIMIT` | `"both"` | The cap's budget: the smaller of commit (RAM + pagefile) and free physical RAM; `"commit"` or `"physical"` = only that one |
| `DELETE_CONFIRM` | `True` | Require HHMM confirmation before deleting (`--delete-source` works in every mode) |

**CLI-only encoder flags (no wizard question — pass via Expert flags in Step 6B):** `--icc-png-strategy` (scanner-profile workaround for lossy encodes), `--buffering` (libjxl ≥ 0.12: `--buffering 1` keeps a heavy setting on the fast, low-memory streaming path — see the recompressor note below; `--buffering 0` is the opposite, best compression at a large cost in RAM and time), `--clear-icc-cache` (reset the cautious ICC cache). Expert flags are appended LAST, so they override earlier wizard choices.

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
| `DELETE_CONFIRM` | `True` | Require confirmation before deleting (`--delete-source` works in every mode) |
| `FORCE_CONTAINER_FOR_LOSSY` | `True` | Always pass `--container=1` for lossy encode |
| `CJXL_BUFFERING` | `None` | [libjxl ≥ 0.12] `--buffering` for lossy pixel encodes (setting only, no CLI flag); `None` = use cjxl default (fast); `0` = best compression, slower |

#### jxl_recompressor.py
| Variable | Default | What it does |
|----------|---------|--------------|
| `CJXL_DISTANCE` | `1.0` | Target distance for the new files |
| `CONVERTED_JXL_FOLDER` | `"recompressed_jxl"` | Mode 1/2 default output folder |
| `JXL_FOLDER_NAME` | `"JXL_recompressed"` | Mode 3/5 sibling folder |
| `JXL_SUFFIX_TO_REPLACE` / `JXL_SUFFIX_REPLACE` | `"JXL"` / `"JXL_small"` | Mode 4 token replace |
| `EXPORT_MARKER` | `"_EXPORT"` | Path anchor for modes 6/7 |
| `EXPORT_JXL_FOLDER` | `"16B_JXL_small"` | Mode 6/7 output folder |
| `ON_DOWNGRADE` | `"ask"` | Policy when the request cannot gain (also wizard/CLI) |
| `ON_REGENERATION` | `"ask"` | Policy when the file already carries a lossy generation (`gen≥2` — files are born at `gen=1`, so a first recompression never fires the guard) and the request adds another (also wizard/CLI) |
| `ON_UNKNOWN` | `"convert"` | Policy for files with no `cjxl d=/e=` record |
| `JBRD_POLICY` | `"copy"` | Policy for JPEG-recoverable JXLs (jbrd box) |
| `KEEP_SMALLER` | `True` | Verbatim copy when the re-encode is not smaller |
| `REDERIVE_ON_ENCODE_CHANGE` | `True` | Derivatives: re-derive when the recorded distance/effort differ (also `--rederive-on-encode-change` / `--no-rederive-on-encode-change`) |
| `REDERIVE_ON_LOWER_EFFORT` | `False` | Derivatives: at the same distance only a higher effort re-derives; `True` also re-derives on a lower one (also `--rederive-on-lower-effort` / `--no-rederive-on-lower-effort`; the wizard asks right after the question above) |
| `ENCODE_TAG_MODE` | `"xmp"` | Where to record the new d=/e= |
| `WORKER_MEMORY_FRACTION` | `0.8` | Caps `--workers` so the parallel cjxl processes fit in this share of the memory budget; `0` = no cap |
| `WORKER_MEMORY_LIMIT` | `"both"` | The cap's budget: the smaller of commit (RAM + pagefile) and free physical RAM; `"commit"` or `"physical"` = only that one |
| `DELETE_CONFIRM` | `True` | Require HHMM confirmation before deleting |

**Heavy settings and `--buffering 1` (encoder and recompressor).** At
**effort 7 with distance ≥ 3**, **effort 8–9 with distance > 0.5** and
**effort 10**, cjxl (libjxl 0.12) stops streaming and encodes the whole image at
once. Each worker then needs ~3.6 GB (effort 7) or ~12.4 GB (effort 8+) on a
45 MP photo instead of ~1.5 GB, and the run lowers `--workers` to fit in memory
— an e9 preset can drop to a handful of workers. Putting **`--buffering 1`** in
**Expert flags** (Step 6B) keeps those settings on the streaming path, with the
memory and worker count of a light setting; the flag is saved with the preset.
What it costs depends on the effort (measured with cjxl 0.12.0 on 45 MP
photos, d=3):

- **effort 7**: files ~1.5 % larger, the same image (identical SSIMULACRA2 and
  Butteraugli) — a good trade whenever the memory cap lowers your workers;
- **effort 8–9**: the file effort 7 would have produced — the extra effort is
  simply not used while streaming. Pick **effort 7** for a light preset, or keep
  effort 9 **without** `--buffering 1` for ~8 % smaller files at ~3× the CPU
  and ~11.5 GB per worker.

Measured throughput at effort 7 and d=3: 0.61 files/s with 8 workers, 0.68
with 8 + `--buffering 1`, 0.73 with 16 + `--buffering 1`. Details:
[Streaming vs whole-image](README_jxl_recompressor.md#streaming-vs-whole-image-what---buffering-1-costs-measured),
[Memory and --workers](README_jxl_recompressor.md#memory-and---workers) and
[the whole-image threshold](README_jxl_tiff_encoder.md#exception-the-whole-image-threshold).

* * *

## Relationship with other scripts

`jxl_photo.py` is a **wrapper** — it invokes the individual scripts with the options you select:

| Script | Purpose | Called when... |
|--------|---------|----------------|
| `jxl_tiff_encoder.py` | TIFF → JXL | Source = TIFF |
| `jxl_tiff_decoder.py` | JXL → TIFF | Source = JXL, Dest = TIFF |
| `jxl_jpeg_transcoder.py` | JPEG ↔ JXL / JXL → JPEG/PNG | Source = JPEG, or Source = JXL + Dest = JPEG/PNG |
| `jxl_recompressor.py` | JXL → JXL (smaller) | Source = JXL, Dest = JXL (smaller) |

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
<script_folder>/Logs/<script_name>/YYYYMMDD_HHMMSS_<pid>.log
```

`jxl_photo.py` streams the selected script's output in real-time and only writes
a log of its own for **manifest runs** — a combined summary across all entries:
```
Logs/jxl_photo/YYYYMMDD_HHMMSS_<pid>.log
```

Set the environment variable `JXLPHOTO_LOG_DIR` to move every log folder (the
wrapper's and each script's, including `rejected_files.log`) elsewhere; the
test suite and the real-photo battery use it so their runs never land in
`Logs\`.

* * *

## Disclaimer

These tools were made for my personal workflow.
Use at your own risk — I am not responsible for any issues you may encounter.

However, if you find any bugs, feel free to report to me — I will gladly try my best to improve this project.

Always test with a small batch before processing important archives.

* * *

## Version history

Feature and fix history: [version_history.md](./version_history.md) · [bug_tracking_since_v1.0.md](./bug_tracking_since_v1.0.md) · [new_features_since_v1.0.md](./new_features_since_v1.0.md)

* * *

## License

MIT License — feel free to use, modify, and distribute.

* * *

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O
- [Claude](https://www.anthropic.com/claude) (Anthropic) and [DeepSeek](https://www.deepseek.com), among other AI tools, for code assistance, reviews and technical discussion
