# jxl_photo.py

Interactive wrapper for the JPEG XL processing toolkit. Provides a **wizard-style menu interface** that guides you through all conversion options — no need to remember CLI flags for each script.

Handles TIFF → JXL, JXL → TIFF, JXL → JXL (smaller or colour-converted copies), JPEG → JXL, JXL → JPEG and JXL → PNG conversions through one menu, with settings that persist between runs.

* * *

## Requirements

```
Python 3.9+
rich            (optional — enables fancy UI, auto-detected)
tifffile        (for TIFF workflows)
numpy           (tifffile dependency)
imagecodecs     (for LZW/ZIP compressed TIFFs)
pillow          (JPEG preview in decoded TIFFs, derivatives)
cjxl / djxl →  https://github.com/libjxl/libjxl/releases
exiftool    →  https://exiftool.org
ImageMagick →  https://imagemagick.org  (for JXL -> JPEG/PNG ICC conversion)
```

If `rich` is not installed, the tool falls back to plain text mode automatically.

Download, PATH and the checks: [Requirements & Installation](../README.md#requirements--installation)
in the main README. The menu's status bar then shows what it found.

* * *

## Quick start

```powershell
# Just run — dependency check happens automatically
py jxl_photo.py
```

The tool shows a status bar with all detected dependencies (`cjxl/djxl` turns into a warning when `jxlinfo`, which ships beside them, is missing), then presents the main menu:

```
╭───────────────────────────────────────────── JXL Tools Environment ────────────────────────────────────────────────╮
│ [✓] cjxl/djxl | [✓] exiftool | [✓] magick | [✓] tifffile | [✓] pillow | [✓] imagecodecs | [✓] rich                    │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭───────────────────────────────────────────────────── Main Menu ────────────────────────────────────────────────────╮
│     CONVERT                                                                                                        │
│  1  New job: archive, restore, export, recompress...                                                               │
│  2  Repeat last job (manifest: manifest_library.csv)                                                               │
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

**A typical job:**
```
[1] New job
  Task      → 1  Archive TIFFs as JXL masters
  Folder    → F:\Photos\2024
  Mode      → 7 (below _EXPORT, one subfolder)  [A] analyses the folder and suggests one
  Quality   → 3  d=0.1 — near-lossless (recommended)   (Enter = your default distance)
  Review    → every option with its value and where it came from;
              [r] runs it, [d] runs it dry first and comes back
```

> **Tip:** Values shown in **blue/cyan** (Rich) or between `[brackets]`/`(parentheses)` are defaults. Just press `Enter` to accept!

* * *

## Main menu options

| # | Option | Description |
|---|--------|-------------|
| `1` | New job | Pick a task (archive TIFFs, restore TIFFs, smaller/resized JXL copies, export JPEGs or PNGs, archive JPEGs bit-exact, get the original JPEGs back, re-encode JPEGs lossy), a folder, a mode and a quality, then the [review](#the-review-screen) |
| `2` | Repeat last job | Re-run the previous job with the same settings. **Manifest runs are repeatable too**: the entry reads `Repeat last job (manifest: <file>.csv)`, skips the input-folder question and re-reads the CSV, so edits you made in Excel between runs are picked up. It is disabled only while that CSV is missing. You are still asked overwrite/sync (default: sync) and dry-run every time |
| `3` | Presets | Save a job under a name and re-run it later (also `[s]` on the review). See below |
| `4` | Folder list (manifest) | Pick a task, then a manifest CSV (one folder per row), then the review. A manifest is generated from a folder with `[A]` then `[P]` in the mode step of a New job — see [Running a list of folders](#running-a-list-of-folders-manifest) |
| `5` | Renamed or moved folders | After a folder of TIFFs was renamed or moved, the masters' path record names the old place and the overwrite guard refuses to re-encode them. This rewrites the record of every master whose TIFF is unchanged — nothing is re-encoded, file dates are kept, masters whose TIFF changed are listed and left alone. Dry run first, then asks. Runs `jxl_tiff_encoder.py <folder> --mode N --restamp-moved` ([details](behavior_and_limitations.md#renamed-folders-and-the-overwrite-guard)) |
| `6` | Explorer thumbnails for existing JXLs | Stores or renews the small thumbnail [jxl-thumb](https://github.com/rsilvabr/jxl-thumb) shows in Explorer, in JXLs that already exist (a folder, with or without subfolders): automatic (recommended: every file when jxl-thumb is installed, lossless files only otherwise), every file, or remove them. Re-encodes nothing; each file keeps its modified date, so syncs are not fooled. Runs `jxl_recompressor.py --refresh-explorer-thumbnails` ([details](README_jxl_recompressor.md#explorer-thumbnails-for-existing-files---refresh-explorer-thumbnails)) |
| `7` | JPEG archives (jbrd) | Audit/repair lossless JPEG → JXL files: those whose bit-exact JPEG recovery broke (v2.0.0–v2.0.3 wrote XMP markers into jbrd containers), and those whose EXIF IrfanView cannot read (compressed, as the transcoder wrote it before v2.19.2). Audit (dry run) is the default; every repair works on a copy and only replaces a file when the copy provably rebuilds the same JPEG |
| `8` | Reset the ICC test cache | The encoder's cautious ICC strategy remembers, per profile and cjxl version, whether a profile survives a lossy encode; clearing it makes the next lossy run test each profile again (after a libjxl update, or to re-check a profile). Runs `jxl_tiff_encoder.py --clear-icc-cache` |
| `9` | Settings | `1` the wrapper's defaults ([below](#settings-menu-9)), `2` the scripts' own settings ([Script settings](#script-settings-menu-9-option-2)), `3` reset all settings (delete the config and start fresh), `4` move the settings file (script folder ↔ user profile) |
| `D` | Dependencies | Re-scan all tools and libraries |
| `0` | Exit | Quit |

Letters are taken in either case (`d`, `D`). The letters of v2.22.0_beta1's
menus still work: `R` = 5, `C` = 8, `S` = the script settings.

The stored workflow is validated before a repeat or a preset replays it: the
config is plain JSON you can hand-edit, so not just the numbers (mode, workers,
distance, ...) but also the enumerated values (bit depth, provenance,
multi-page policy, compression, depth policy, conversion type, ICC profile)
are checked. A corrupt value is **refused with the reason**, not defaulted and
not passed to a child as a command line nobody typed. That includes the two
fields that pick WHICH script runs (`last_origin_format`/`last_dest_format`):
an unknown value there is refused instead of routing the run to the wrong
script while the panel still shows the direction you expected.

### Presets (menu 3)

`Repeat last job` only ever remembers **one** run. Presets let several
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
  `Repeat last job`: overwrite/sync (default sync) and dry-run are always
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

The other command-line flags are `--version` (print the toolkit version and
exit) and `--recheck`, which re-runs the dependency detection
(cjxl/djxl/exiftool/ImageMagick) and refreshes what the status line at the top
of the menu reports — use it after installing or moving a tool, instead of
wondering why the menu still shows it as missing.

At startup the wrapper also warns, without blocking, when a script in its folder
carries a different `TOOLKIT_VERSION` than itself — a copy forgotten during an
update. Each output records the version of the script that wrote it in its
`xmpMM:History`, so such a mix would otherwise only show up in the files.

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

## New job (menu 1)

### Task

| Task | Converts | What it is for |
|---|---|---|
| Archive TIFFs as JXL masters | TIFF → JXL | The masters, lossless or near-lossless, with the original ICC and every marker the delete gates read |
| Restore TIFFs from JXL masters | JXL → TIFF | The TIFF back, in the original profile (Roundtrip), with an optional JPEG preview |
| Make smaller or resized JXL copies | JXL → JXL | `jxl_recompressor.py`: a new distance/effort, or a colour/resize/sharpening derivative |
| Export JPEGs for sharing (lossy) | JXL → JPEG | Quality, colour conversion, resize, sharpening; the review can switch it to *auto* (bit-exact when the JXL holds a JPEG, else lossy) |
| Export PNGs | JXL → PNG | 8 or 16 bits |
| Archive JPEGs losslessly (bit-exact) | JPEG → JXL | About 20% smaller, the original JPEG comes back byte for byte |
| Get the original JPEGs back (bit-exact) | JXL → JPEG | The bit-exact recovery of JPEG archives; the review can switch it to *auto* |
| Re-encode JPEGs lossy | JPEG → JXL | A lossy re-encode at a chosen distance |

A task the PC cannot run is shown with what it needs (`needs tifffile`, `needs
cjxl`) and refused.

The quality comes right after the mode, and only where a task has one.
Archiving TIFFs shows a named list with the typical file size of each
distance at 45, 24 and 12 MP:

```
                                                             JXL size from a 16-bit TIFF (typical)
                                                               45 MP    24 MP    12 MP
  1  d=0    — lossless                                        173 MB    89 MB    43 MB
  2  d=0.05 — finest lossy available                           42 MB    21 MB   9.9 MB
  3  d=0.1  — near-lossless (recommended)                      31 MB    15 MB   7.2 MB
  4  d=0.5  — high-quality lossy (headroom for later edits)    11 MB   4.9 MB   2.4 MB
  5  d=0.7  — visually lossless (keeps the grain texture)     7.8 MB   3.7 MB   1.7 MB
  6  d=1    — visually lossless (JPEG XL's definition)        5.5 MB   2.7 MB   1.2 MB
  7  your distance (0-25) — shows every distance's size
  A photo can be half or twice these, depending on its detail and noise.
  From an 8-bit TIFF: lossless is 43 / 21 / 10 MB (about the size of d=0.05), lossy about the same as above.
```

`Enter` takes your default distance (0.1 out of the box; Settings → wrapper
defaults changes it). "Your distance" first shows every distance from 0 to
25 with its size, then asks for the number, pre-filled with the last one you
used. Below the list, a note says when film scans or 8-bit TIFFs will get
another distance in this run (lossless, by default).

Making smaller JXL copies shows the same kind of list for copies: d=0.5,
0.7 and 1 as above, then d=2 (about quality 79: phones, sharing), d=3 (about
quality 68: web and social media) and d=5 (about quality 46: screens only).
Nothing higher: from d=10 libjxl halves the resolution by itself, so a
smaller copy is better made with a resize.
JPEG → JXL lossy asks the distance; a JPEG export asks the quality.
Everything else is on the review.

**Make smaller or resized JXL copies** runs `jxl_recompressor.py`: ICC,
metadata and provenance markers carried over. Requests that cannot gain
anything (same or lower distance than the source's recorded `cjxl d=/e=`)
fall back to a verbatim copy or are skipped, files that were already
lossy-recompressed once (`gen≥2` in the record — every lossy encoder output is
born at `gen=1`, so its first recompression is the normal case) trigger the
regeneration policy (`--on-regeneration`), and a re-encode that comes out
*larger* keeps the original bytes. JPEG-recoverable JXLs (jbrd) are copied
verbatim by default. Derivatives (a colour/resize/sharpening recipe) record
their distance/effort too; the review's options screen sets whether an
existing derivative is re-derived when those change (untouched: the
recompressor's `REDERIVE_ON_ENCODE_CHANGE`) and whether a LOWER effort at the
same distance re-derives too (`REDERIVE_ON_LOWER_EFFORT`).

**Modes 0 and 8 REPLACE the source JXLs** there (the recompressor writes the
new file over the old one; nothing stays "side by side"). The mode list says
so and the HHMM token is charged before the run, exactly like a delete;
`--run-preset` refuses such a preset unattended. In a manifest, a mode-0 row
whose Destination is empty or equal to its Source is the same in-place run and
is gated the same way (mode 8 always is). A mode-2 row whose Destination equals
its Source is refused by the recompressor (exit 2): mode 2 would flatten the
subfolders into the root, next to the originals.

### Folder
Enter the folder path containing the files (surrounding quotes are stripped, so Explorer's "Copy as path" works).

A New job takes **one** folder here. To run a **list of folders** in a single
session — `G:\2024`, `G:\2025`, `G:\2026` — use a manifest (see below).

TIFF→JXL and JXL→TIFF runs — and only those; the flag lives only in the
TIFF encoder and decoder — get one more question right after the folder:

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

Then: menu **4 (Folder list)** → pick the task → the CSV (or `[M]` in the mode
step of a New job). The encoding settings you pick on the review
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

### Mode
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

Items shown in **green** (like `_EXPORT`) are configurable in **Settings (menu 9 → 1)**.

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
| TIFF → JXL | Provenance marker naming THIS source + structural check, plus the round-trip pixel comparison (on by default since v2.18.0: `VERIFY_ROUNDTRIP` in `jxl_tiff_encoder.py`; `[D]` then says so instead of asking) |
| JXL → TIFF | Provenance marker naming THIS source + structural check |
| Any lossy direction | ⚠️ Provenance marker naming THIS source + structural check — no checksum, no pixel comparison |

> ### ⚠️ Lossy directions
>
> A lossy conversion stores no checksum and its output cannot reproduce the
> source. What ties the existing file to the original you are about to delete
> is the provenance marker every toolkit output carries (an unrelated
> same-named file has none, or another one, and keeps the original — the
> transcoder requires it since the 2026-10-08 audit). The marker names the
> source FILE, though, not its pixels, so `[D]` still charges a **separate
> confirmation** (default **No**) before arming it there.

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
it is the only gate that looks at pixels rather than at file structure.

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
The wizard asks for the subfolder name in the mode settings (passed to the scripts as `--export-subfolder`). Empty (default) = processes all subfolders, same as mode 6.

```
_EXPORT/
├── JXL\               ← PROCESSED ✓ (matches subfolder filter)
│   └── img.jxl
├── AdobeRGB\          ← IGNORED ✗ (doesn't match subfolder filter)
│   └── img.jxl
└── sRGB\              ← IGNORED ✗ (doesn't match subfolder filter)
    └── img.jxl
```

### Mode settings
- Modes 6/7: Confirm or change the `_EXPORT` marker name
- Modes 6/7, TIFF→JXL and JXL→JXL: the **output folder name under the marker**
  (default `16B_JXL` for TIFF→JXL, `16B_JXL_small` for JXL→JXL; passed as
  `--export-jxl-folder`). This is what lets one preset write masters to
  `_EXPORT/16B_JXL` and another write derivatives to `_EXPORT/16B_JXL_sRGB`.
  The child validates the name (one plain component, not the marker, not the
  input subfolder) and exits 2 with a clear message if it is unusable
- Mode 2: Specify the output directory for merged files

Folder exclusions are not asked again here: the question (and its default)
comes with the folder, and the manifest's `ExcludeFolders` column overrides it per
row ([manifest reference](README_manifest.md)).

### The review screen

After the quality, every job ends on one screen:

```
Review — Archive TIFFs as JXL masters
Folder    F:\Photos\2024\250118_吾妻山公園 [FINAL]\01_Z7
Mode      6 — below the export marker
Multi-page TIFFs: split — one JXL per page (thumbnail pages excluded)
  [ 1] PNG in RAM               yes                     last job
  [ 2] Workers                  16                      last job
  [ 3] Distance                 0.1                     you
  [ 4] Effort                   7                       settings
  [ 5] Staging folder           E:\staging_jxl          last job
  [ 6] Existing outputs         sync (only newer)
  [ 7] Delete the sources       no ([b] back to the mode step for [D])   (fixed)
  [ 8] Overwrite guard          on                      script
  [ 9] D50 patch                auto                    script
[number] change  [a] all options  [/text] search  [c] command line  [x] expert flags
[d] dry run  [r] RUN  [s] save as preset  [b] back  [q] cancel
```

- **The rows** are the job's essential options, per direction: workers, RAM
  (TIFF), distance and effort, the recompressor's policies (*already good
  enough*, *recompressed before*, *film scans*: copy / skip / convert), the
  JXL → JXL derivative (output colour space with its rename, resize and
  sharpening), staging, JPEG quality, sRGB conversion and resize/sharpening
  for JPEG/PNG exports, PNG bit depth, TIFF compression / bit depth / preview /
  decode mode, existing outputs (sync or overwrite all), the overwrite guard
  and the D50 patch. Each starts at the value the old wizard gave when every
  question was answered with Enter — a job started with `[r]` right away runs
  exactly that (a test compares the command lines of every task).
- **The `from` column:** `you` (changed here or in the quality step), `last
  job`, `settings` (Settings → wrapper defaults), `script` (the script's own
  setting, or the script settings of menu 9 → 2); empty for a fixed default.
- **What is not offered** says why: no derivative (colour, sRGB,
  resize/sharpen) in place (modes 0/8) or with `[D]` — a derivative never
  deletes its source, so the run could only be refused later. Resize and
  sharpening start at `none` every time they are opened.
- **`[a]` all options / `[/text]` search:** every option the job's script
  takes, for this direction only, grouped (existing outputs, deletion,
  multi-page, colour, metadata, derivative, files, performance), with its
  current value and flag: everything the old Step 6A asked (strip, encode
  record, modular, multi-page and thumbnail pages, film-scan distance, decode
  modes, ICC cleanup, the recompressor's unknown/jbrd/re-derive policies,
  MD5, validation, jbrd repair, output suffix) and the flags no question
  asked: `--buffering`, `--no-preflight`, `--clean-staging` (with staging),
  `--icc-png-strategy`, `--scan-scope`, `--warn-thumbnail-discard`,
  `--restamp-then-sync`, `--explorer-thumbnail`, the recompressor's
  `--encode-tag`, `--allow-incomplete-groups`, `--compress-metadata`,
  `--jpeg-orientation` (JXL → JPEG), `--exiftool-ignore-minor` (JXL → TIFF),
  `--output-name` (modes 1/3 of a JPEG/PNG conversion) and the sharpening
  overrides. A number changes one; a choice made of numbers is typed as the
  number (`1` = `--buffering 1`, `-` = the script's value). The delete rules
  are shown but chosen with `[D]` in the mode step, where their checks are. An
  option that relaxes a safety check (`--allow-incomplete-groups`) asks first.
  The review then lists what you set (`Options: --buffering 1, --scan-scope
  raw`), and the values are saved with the last job and the presets.
- **`[c]`** prints the exact command line the run will start (a manifest
  prints each row's when it runs). **`[x]`** sets the expert flags, applied
  last: they are checked against the script's own command line at once — a
  typo, another script's flag or a value outside its choices is refused with
  the reason, and so is a `--mode` (the mode is chosen in the mode step or the
  manifest's Mode column, and every check before the run uses that one).
- **`[d]`** runs the job as a dry run and comes back to the review; **`[r]`**
  runs it for real. Both save it as the last job. A run that deletes, or a
  JXL → JXL run in place, still asks for the current time (HHMM) first; a
  dry run never does. **`[s]`** saves the job as a preset (and as the last
  job) without running it. **`[b]`** goes back to the mode step (for `[D]`,
  another mode or a manifest); **`[q]`** cancels.

Options that cannot go together are refused on the options screen and again
before the run, before any HHMM confirmation: `--restamp-then-sync` never with
a delete or Overwrite, `--allow-incomplete-groups` only with Delete originals.

* * *

## Settings (menu 9)

Menu 9 → `1` (wrapper defaults):

Persistent defaults saved to `~/.jxl_tools_config.json`.

> Changing **workers**, **quality**, **effort** or **distance** here also updates
> what the next run uses, including *Repeat last job*. Values you leave
> untouched keep whatever the last run used, and the screen tells you when a
> saved session is currently overriding a default.

- **Staging directory** — output SSD path
- **Workers** — default thread count
- **Quality** — JPEG quality for lossy workflows
- **Effort** — cjxl effort level (1-10)
- **Distance (TIFF→JXL)** — your preferred cjxl distance (default: `0.1`). It is
  what `Enter` picks in the quality list of *Archive TIFFs*, so if you always
  work at `0.05`, set it once here and accept it with `Enter` on every run. A
  value that is not in the list (`0.2`, say) is offered as "your distance",
  pre-filled.
- **Confirm deletes** — safety confirmation before destructive operations
- **Export marker** — the folder name anchor for modes 6/7 (default: `_EXPORT`)
- **Explorer thumbnail tip** — whether the menu shows the
  [thumbnail tip](#explorer-thumbnail-tip-windows). Asked only while the tip
  applies (no JXL thumbnail handler on this PC) or after you turned it off.

**Set in the file only (for now):** `child_idle_timeout_minutes` (default
`60`, at least `5`) — how long a script may print nothing before the wrapper
stops it as hung ("No output for N min"). The scripts print a line per
finished file and, during planning, one per slow exiftool batch
("Provenance markers: 1200/8000 read …"); raise it only for a single file or
batch slower than that (a huge drum scan at effort 10, a very slow network
share). Edit the value in the config file while the wrapper is closed.

Alongside these defaults the config keeps the **last-run answers** the wizard
offers back as defaults — among them `last_exclude_folders`, the raw
`';'`-separated string of the last TIFF↔JXL run's folder-exclusion answer
(`-`/empty stored as no exclusion). It is not editable here; the wizard's
folder question offers it as the default, and `Repeat last job`/
snapshots replay it as-is — but only in the TIFF↔JXL directions, where the
flag actually exists; every other direction runs without it.

* * *

## Settings file location

The config file is stored at:
- **Script folder** — `jxl_photo/.jxl_tools_config.json` (if existing there)
- **User Profile** — `~/.jxl_tools_config.json` (portable, follows the user)

Use **Settings (menu 9) → 4, move the settings file** to toggle between the two locations.

* * *

## What can be configured in the wizard vs scripts

Some options are available directly in the wizard, others must be edited in the script files themselves.

### ✅ Available in a New job (the review and its options screen)

| Option | Location | Notes |
|--------|----------|-------|
| Workers | Review | All workflows |
| Quality / Distance | Review | Context-aware |
| Effort | Review | All workflows |
| Staging directory | Review | TIFF→JXL, JXL→TIFF |
| Existing outputs | Review | sync (default) or overwrite all |
| ICC conversion (sRGB) | Review | JXL→JPEG/PNG |
| TIFF compression | Review | zip/lzw/none |
| Bit depth | Review | 8 or 16 for TIFF output |
| JPEG Preview | Review | JXL→TIFF (default: yes) |
| Dry run | Review `[d]` | All jobs: runs dry, then back to the review |
| Strip metadata | Review `[a]` | TIFF→JXL |
| D50 patch mode | Review | auto / on / off |
| Encode record, visible copy | Review `[a]` | history (none, default) / comment / title / software / off |
| Force Modular encoder (lossy) | Review `[a]` | TIFF→JXL; NOT for photos — screenshots/graphics only (default off: VarDCT) |
| ICC matrix mode | Review `[a]` | JXL→TIFF |
| Target ICC profile | Review `[a]` | JXL→TIFF |
| Skip ICC cleanup | Review `[a]` | JXL→TIFF |
| Skip MD5 verification | Review `[a]` | JPEG↔JXL |
| Auto-repair broken JPEG recovery | Review `[a]` | JXL→JPEG only; repairs a copy and decodes from it, the JXL is never modified (default: off) |
| Skip validation | Review `[a]` | JPEG↔JXL (risky) |
| Output suffix | Review `[a]` | JPEG↔JXL |
| Downgrade policy | Review | JXL→JXL: ask/copy/skip/convert |
| Re-derive when distance/effort change | Review `[a]` | JXL→JXL derivatives only (colour/resize/sharpen runs): default = the recompressor's `REDERIVE_ON_ENCODE_CHANGE` setting |
| ...also on a lower effort (same distance) | Review `[a]` | Offered after a yes above: default = the recompressor's `REDERIVE_ON_LOWER_EFFORT` setting (no) |
| Output folder under the marker | Mode settings | Modes 6/7, TIFF→JXL and JXL→JXL (`--export-jxl-folder`) |
| Output colour space (derivative) | Review | JXL→JXL: keep/sRGB/AdobeRGB/.icc — 16-bit, never in place, never deletes (`--output-icc`) |
| Rename in output file names | Review | JXL→JXL with an output colour space: e.g. ProPhoto→sRGB (`--rename-from`/`--rename-to`) |
| Resize (derivative) | Review | JXL→JXL (modes 1–7) and JXL→JPEG/PNG: none/long/short/percent (`--resize-long`/`--resize-short`/`--resize-percent`, `--allow-upscale`) |
| Output sharpening | Review | Same directions: none/screen/print after the resize (`--sharpen`); sigma/gain/threshold on the options screen (`a`) |
| Expert flags | Review `[x]` | Any flag of the job's script, checked at once |

### ⚙️ Available in Settings (menu 9 → 1)

| Option | Notes |
|--------|-------|
| Staging directory | Persisted across sessions |
| Default workers | Persisted |
| Default quality | Persisted |
| Default effort | Persisted |
| Default distance (TIFF→JXL) | Drives the second quality choice of *Archive TIFFs*. Default: `0.1` |
| Confirm deletes | Safety toggle |
| Export marker | Default: `_EXPORT` |
| Explorer thumbnail tip | On by default; asked only when it applies |

### 🔧 The scripts' own settings (menu 9 → 2, or the script file)

These are global variables at the top of each script. Change them in
[Settings → script settings](#script-settings-menu-9-option-2) (an overlay file, the `.py` files stay as
they are), or open the script and edit the variable at the top. The safety
settings (`DELETE_CONFIRM`, `OVERWRITE_GUARD`, the verification thresholds,
...) are not offered there: they are per-run choices, or a deliberate edit of the
script.

The wizard reads them too: its default for every option it passes to a script
(multi-page and thumbnail mode, thumbnail suffix, D50 patch, encode tag, RAM
for the PNG intermediate, TIFF compression, bit depth, JPEG preview, depth
policy, multi-page reconstruction, provenance check, the recompressor's
distance) is that script's setting, so an edit here shows up as the wizard's
default. An answer the wizard remembers from an earlier run, or a preset's
stored value, still comes first. The wrapper's own settings above (workers,
quality, effort, the TIFF → JXL distance, the export marker) are the
exception: they are set in Settings (menu 9 → 1).

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
| `ENCODE_TAG_MODE` | `"history"` | The d=/e= record is always in xmpMM:History; a visible copy: `comment`, `title`, `software` (also `--encode-tag`; `xmp` = `history`) |
| `CJXL_MODULAR` | `False` | Force Modular encoder for lossy (`--modular=1`) |
| `CJXL_BUFFERING` | `None` | [libjxl ≥ 0.12] `--buffering` for pixel encodes (also `--buffering` CLI); `None` = use cjxl default (fast); `0` = best compression, ~6× slower on large lossless TIFFs ([benchmark](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.0)) |
| `USE_RAM_FOR_PNG` | `True` | Keep PNG intermediate in RAM |
| `WORKER_MEMORY_FRACTION` | `1.0` | Caps `--workers` so the parallel cjxl processes fit in this share of the memory budget; `0` = no cap |
| `WORKER_MEMORY_LIMIT` | `"both"` | The cap's budget: the smaller of commit (RAM + pagefile) and free physical RAM; `"commit"` or `"physical"` = only that one |
| `DELETE_CONFIRM` | `True` | Require HHMM confirmation before deleting (`--delete-source` works in every mode) |
| `OVERWRITE_GUARD` | `True` | `False` = overwrite an output whose provenance marker names another source path (renamed/moved folders; also `--no-overwrite-guard`); never combined with `--delete-source` (that run exits 2). [More](behavior_and_limitations.md#renamed-folders-and-the-overwrite-guard) |

**Encoder flags without a wizard question (the review's options screen, `a`):** `--icc-png-strategy` (scanner-profile workaround for lossy encodes), `--buffering` (libjxl ≥ 0.12: `--buffering 1` keeps a heavy setting on the fast, low-memory streaming path — see the recompressor note below; `--buffering 0` is the opposite, best compression at a large cost in RAM and time). `--clear-icc-cache` (reset the cautious ICC cache) is menu 8. Expert flags still work and are appended LAST, so they override earlier wizard choices.

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
| `OVERWRITE_GUARD` | `True` | `False` = overwrite a TIFF whose provenance marker names different JXLs (renamed/moved folders; also `--no-overwrite-guard`); never combined with `--delete-source` (that run exits 2); an original master is still kept. [More](behavior_and_limitations.md#renamed-folders-and-the-overwrite-guard) |

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
| `OVERWRITE_GUARD` | `True` | `False` = overwrite an output whose provenance marker names another source path (renamed/moved folders; also `--no-overwrite-guard`); never combined with `--delete-source` (that run exits 2); a jbrd archive of another JPEG is still refused. [More](behavior_and_limitations.md#renamed-folders-and-the-overwrite-guard) |

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
| `ENCODE_TAG_MODE` | `"history"` | The new d=/e= is always in xmpMM:History; a visible copy: `comment`, `title`, `software` (`xmp` = `history`) |
| `WORKER_MEMORY_FRACTION` | `1.0` | Caps how many cjxl run at once (and `--workers`) so the run fits in this share of the memory budget; extra workers prepare the next files with what is left; `0` = no cap |
| `WORKER_MEMORY_LIMIT` | `"both"` | The cap's budget: the smaller of commit (RAM + pagefile) and free physical RAM; `"commit"` or `"physical"` = only that one |
| `DELETE_CONFIRM` | `True` | Require HHMM confirmation before deleting |
| `OVERWRITE_GUARD` | `True` | `False` = overwrite an output whose provenance markers name a different origin (renamed/moved folders; also `--no-overwrite-guard`); never combined with `--delete-source` (that run exits 2). [More](behavior_and_limitations.md#renamed-folders-and-the-overwrite-guard) |

**Heavy settings and `--buffering 1` (encoder and recompressor).** At
**effort 7 with distance ≥ 3**, **effort 8–9 with distance > 0.5** and
**effort 10**, cjxl (libjxl 0.12) stops streaming and encodes the whole image at
once. Each worker then needs ~3.6 GB (effort 7) or ~12.4 GB (effort 8+) on a
45 MP photo instead of ~1.5 GB, and the run lowers `--workers` to fit in memory
— an e9 preset can drop to a handful of workers. Setting **`--buffering 1`** on the
review's options screen (`a`, or in Expert flags) keeps those settings on the
streaming path, with the memory and worker count of a light setting; it is
saved with the preset.
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

### Script settings (menu 9, option 2)

**Settings (menu 9) → 2** edits the settings at the top of the four scripts without touching
the `.py` files: pick a script, then a setting (`/text` filters), and type the
new value; `-` puts it back to the script's own value. The list shows each
setting's value, `(overlay)` where it was changed here, and the comment the
script has under it.

The values go to a JSON file the scripts read when they start:

1. the path in the `JXLPHOTO_SETTINGS` environment variable, if it is set
   (set but **empty** turns the overlay off — the test suite and the
   real-photo battery do this);
2. else `.jxl_script_settings.json` next to the scripts (portable install,
   like this program's own settings file);
3. else `.jxl_script_settings.json` in your user profile (`%USERPROFILE%`).

A `JXLPHOTO_SETTINGS` that names a file that does not exist is reported in the
run's log (`Script settings overlay: JXLPHOTO_SETTINGS names a file that does
not exist …`) and no setting changes.

Each script checks every value again (type, range, the allowed choices,
folder names without path separators) and takes only the settings this
menu offers; anything else in the file is ignored with a warning in the run's
log. Every run logs the file and the settings it changed, e.g.
`Script settings overlay: C:\Users\me\.jxl_script_settings.json: 1 value(s): EXPLORER_THUMBNAIL_SIZE`.
**Never** taken from it, in any script: deletion (`DELETE_SOURCE`,
`DELETE_SKIPPED`, `DELETE_CONFIRM`, `DELETE_SOURCE_REQUIRE_MD5`,
`STORE_MD5`), the guards and checks (`OVERWRITE_GUARD`, `PROVENANCE_CHECK`,
`ADOPT_SCAN`, `ALLOW_INCOMPLETE_GROUPS`, `VERIFY_ROUNDTRIP` and its
thresholds), `OVERWRITE`, `STRIP_METADATA`, `AUTO_REPAIR_JBRD` and the
encoder's copy of the original profile (`EMBED_ICC_IN_JXL`). Those stay
per-run choices (the wizard, the flags) or a deliberate edit of the script.

A change applies to the next run of that script. Jobs started from this
wizard send their own workers, effort, JPEG quality, export marker and
TIFF → JXL distance (Settings → wrapper defaults, or the job's review): for
those, the script's value counts when the script is run directly. The
recompressor's `ON_DOWNGRADE`, `ON_REGENERATION` and `ON_SCAN` are followed
by the wizard (only `ask`, a silent skip on its pipe, becomes the wizard's
own default). The wizard's other defaults, read from the scripts, show a
change the next time this program starts.
The file is plain JSON, one object per script:

```json
{"jxl_tiff_encoder": {"EXPLORER_THUMBNAIL_SIZE": 512, "SCANNER_SOFTWARE": ["SilverFast", "VueScan"]},
 "jxl_recompressor": {"ON_DOWNGRADE": "skip"}}
```

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
| `pillow` | JPEG preview embedding in TIFF, derivatives |
| `imagecodecs` | Reading LZW/ZIP-compressed TIFFs; the round-trip check |
| `rich` | Fancy UI with colors/panels |

If `rich` is missing, the tool runs in plain-text mode with the same functionality.

### Explorer thumbnail tip (Windows)

Windows Explorer shows no thumbnail for a JXL file unless a thumbnail handler
for `.jxl` is installed ([jxl-thumb](https://github.com/rsilvabr/jxl-thumb),
or Microsoft's JPEG XL extension). When there is none, the menu prints one
line under the status bar:

```
Tip: Windows Explorer shows no thumbnails for JXL files on this PC. jxl-thumb adds them: https://github.com/rsilvabr/jxl-thumb
     (Hide this tip: 9 - Settings, then 1.)
```

It is shown on every start, not once: someone new to JXL usually meets the
problem only later, in Explorer. It is never a question, it goes away by
itself once any handler is installed, and Settings (menu 9 → 1) turns it off. It is never
printed in `--run-preset` or `--list-presets` runs. The check is one registry
read; nothing is installed or written. The environment variable
`JXLPHOTO_THUMBNAIL_TIP` replaces the text (a packaged build names its own
install command) and an empty value turns the tip off.

* * *

### New release notice

When the menu starts, it asks GitHub whether a newer release is out — at most
once a day (a day counts once GitHub answered), with a 3-second limit; without
a network it says nothing and the next run asks again. The
menu does not wait for the answer (half a second at most): the lookup runs in
the background and keeps its answer in the settings file as soon as it is in,
so a run that goes straight into a job still leaves it for the NEXT run, which
shows it at once with its date (`Checked by an earlier run on 2026-11-06; D in
the menu checks again.`). `D` in the menu checks the dependencies and asks
GitHub again right away. A newer
one adds a line at the top (`A new release is out: v2.23.0 (this copy is
v2.22.0)`) and a `U` entry in the menu that opens the release page. Nothing is
downloaded or installed. Pre-releases (betas) are ignored. The Windows program
follows the jxl-photo-exe releases instead. `--run-preset` and
`--list-presets` never ask. The request is a plain anonymous GitHub API call
(`api.github.com/repos/rsilvabr/jxl-photo/releases/latest`). Turn it off with
`"check_for_updates": false` in `.jxl_tools_config.json`, or the environment
variable `JXLPHOTO_NO_UPDATE_CHECK=1`.

When [jxl-thumb](https://github.com/rsilvabr/jxl-thumb) is installed, the same
daily ask also looks up its latest release and compares it with the installed
DLL's version (read from the file, without loading it): an older one adds a
line (`jxl-thumb 0.2.1 is installed; 0.2.2 is out`) and `T` opens its releases
page. The same settings turn it off.

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
