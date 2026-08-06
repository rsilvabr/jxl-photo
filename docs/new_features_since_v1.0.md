# New Features Since v1.0

## v1.10.0

Date: 2026-08-06

### Archive and replace: delete the originals from any mode

`--delete-source` used to work in mode 8 only, which meant "delete the master"
was locked to "keep the JXL next to it". The usual archival workflow — convert
into a separate tree and drop the originals — was impossible.

Deleting is now available in **every** mode (0-8) in all three scripts. Nothing
in the delete gates was ever mode-specific: they certify THIS run's output at
its FINAL path, which for modes 1-7 is the destination folder. A source is
removed only when

* every page of it encoded `ok`/`overwrite` **this run** (a skipped file blocks
  the delete — it cannot be certified);
* no page was dropped by the multi-page policy;
* the output exists at its final destination, and if staging was used, the move
  there actually succeeded (a stale file already sitting at that path does not
  count);
* it passes the integrity check **there**;
* and, with `--verify-roundtrip`, it decodes back to the source pixels.

In the modes that COLLAPSE folders — 2 flattens a tree, 5 merges siblings, 6/7
drop one level under the marker — what makes this safe is
`_abort_on_duplicate_outputs`, which runs in every mode before the first byte is
written: two inputs mapping to one output abort the run rather than costing two
originals.

```powershell
# archive a shoot into a sibling folder and drop the masters
py jxl_tiff_encoder.py "E:\shoot" --mode 5 --distance 0 --delete-source --verify-roundtrip
```

### `--verify-roundtrip`: the only gate that looks at pixels

The integrity check that always runs proves the file is a well-formed,
untruncated JXL container with a codestream. It does not prove the image came
back. This does, and it is opt-in because it costs one full decode per output.

* `--distance 0` → the decoded pixels must be **identical**, or the source stays.
* `--distance > 0` → a **sanity** check, not a quality check: mean brightness
  within a factor and PSNR above a permissive floor. Real photos land at 40-50 dB
  at d=1 and still 25-30 dB at d=15; a different image or a decode that turned to
  noise sits at 5-15 dB, so the gap is wide enough to reject catastrophes without
  ever second-guessing a legitimate lossy encode.

That lossy case is not hypothetical. Some large scanner ICC profiles make cjxl
emit near-black images (see `ICC_PNG_STRATEGY`), and such a file passes every
structural check there is. Measured on a real encode: the correct image scores
58.4 dB, a different image 8.5 dB, a black frame fails on brightness alone.

### `--delete-skipped`: finishing an interrupted archive

A source whose output already exists is reported as SKIP, and a skip blocks the
delete. That is the right default, but it means an archive interrupted between
the encode and the unlink — a locked file, antivirus, Ctrl+C — can never be
finished: the leftover is skipped on every later run, and the only way out was
`--overwrite`, i.e. re-encoding the whole library to delete a handful of files.

`--delete-skipped` covers those too. It is opt-in and gated harder than a normal
delete, because a SKIP proves much less than a conversion:

| | what the gate proves |
|---|---|
| converted | this run wrote the file **and** it passed the integrity check |
| SKIP | a file with that **name** exists and its mtime is not older than the source |

mtime is not content. It reads "newer" after a copy, a backup restore, a cloud
sync or a `touch`, and it says nothing about whether that JXL came from *this*
photo. So a skipped source is never deleted on the timestamp: the output must
still exist and pass `_verify_jxl_integrity` — the same structural floor a fresh
conversion gets — and with `--verify-roundtrip` it must decode back to the source
pixels. There is deliberately **no** "just delete it" mode.

Pair it with `--verify-roundtrip`. It matters *more* here than for a fresh
conversion: there is no "this run wrote it" to lean on, so the pixel comparison
is the only thing tying that JXL to that photo. Proven against the real files —
a JXL from a different photo placed under the right name with a newer timestamp,
and a re-edited version of the same photo at identical dimensions, are both
caught and the master is kept.

The cost is self-limiting: the check only runs for sources that still exist,
which is exactly the set pending deletion. After one clean pass that is empty.

`--dry-run` previews it (`would DELETE N already-archived source(s)`, with the
paths), using the cheap checks and saying so — a destructive option that could
not be previewed would be the wrong kind of opt-in.

### `--delete-skipped` in the decoder and the transcoder too

Same flag, same rule — never on the timestamp, the output must exist and pass
the integrity check — but the guarantee behind it differs by direction, and the
scripts, the docs and the wizard all now say which one you are in:

| Direction | What backs the delete |
|---|---|
| JPEG ↔ JXL (lossless transcode) | **Provenance PROVEN.** `checksums.md5` already stores the SOURCE's md5 keyed by the OUTPUT's name, so the source is re-hashed and compared. Stronger than any pixel comparison, and cheaper — no decode |
| TIFF → JXL | Structural check + the optional `--verify-roundtrip` pixel comparison |
| JXL → TIFF | Structural check only. The decoder deliberately has no round-trip verify: its output depends on `--depth`, `--depth-policy`, `--matrix/--basic/--none`, `--target-icc` and the appended preview page, so re-deriving it would reject good archives whenever the settings differ from the run that made them |
| Lossy (JXL → JPEG/PNG, lossy encodes) | ⚠️ Structural check only, and **nothing better is possible** — no checksum is stored and the output cannot reproduce the source |

The lossless directions refuse by default when no checksum exists
(`DELETE_SOURCE_REQUIRE_MD5`), rather than silently falling back to the weaker
check.

For the lossy directions the wizard's `[D]` charges a **separate confirmation**
(default No) before arming it, and both the run and the docs state plainly that
an unrelated file with the same name would pass.

Proven on real files: a JPEG archived to JXL, then swapped for a different photo
under the same name, is rejected on checksum mismatch; restored to the correct
source it is deleted and logged as already archived.

### `--provenance`: which source does this archive belong to?

The modes that **collapse folder structure** — 2 flattens a tree, 4 renames the
parent, 5 merges siblings, 6/7 drop one level under the marker — can send two
files with the same name, from different folders, to the same output.
`_abort_on_duplicate_outputs` catches that inside one run. Across runs it was
blind, and with `--delete-source` that destroyed photos:

```
run 1   root/A/foto.tif  →  root/JXL_16bits/foto.jxl   [source deleted]
        (later, root/B/foto.tif appears — a different photo, same name)
run 2   B is newer than the archive → reconverted OVER it → B deleted too
        "Done: 1 OK | 1 overwrites"
        → A's photo now exists nowhere.
```

Every encode now records **which source made it** — `jxlphoto-src:` (location)
and `jxlphoto-srcsum:` (image) — in the same `dc:Relation` bag as the multi-page
markers. Both are always written: the content id is hashed from the pixel array
already in memory, so recording it costs almost nothing and lets you change the
matching mode later without re-encoding the archive.

With `--delete-source` in a collapsing mode, an existing output whose marker
does not match the source about to replace it is **refused**: not converted, not
overwritten, nothing deleted.

| `--provenance` | Matches on | Re-export in place | Moved folder | Cost |
|---|---|---|---|---|
| `path` (default) | recorded location | ✅ allowed | ❌ refused (safely) | free |
| `content` | location **or** image | ✅ allowed | ✅ allowed | reads every source |

`content` is deliberately a **superset** of `path`: matching on content alone
would refuse a legitimately re-edited file and break the ordinary sync workflow.
The wizard asks for this right after you arm `[D]`, but only for the modes where
it means something, and pressing Enter gives the cheap, safe default. When
`path` refuses, the message names `--provenance content` as the way out.

All three scripts record and check it. The one exception is the **lossless
JXL → JPEG** path, whose output must stay byte-identical to the original and
therefore cannot carry a marker — there `checksums.md5` already holds the
original JPEG's hash keyed by the JXL, which is a stronger proof anyway.

### Deletions are now reported

The delete gates were correct but silent. `deleted` and `kept` were locals, so
the run summary had no count and the wrapper's manifest recap could not say a
word about the only irreversible thing the toolkit does — a 20-entry manifest
could remove 50 000 masters and look identical to one that removed none.

Every script now reports **sources deleted**, how many of those were *already
archived*, and — just as important — **sources KEPT by a refusing gate**. That
last number is the one that says something needs looking at: an integrity
failure, a failed round-trip, a checksum mismatch. The wrapper gives them their
own line, above the rest and not dimmed.

A **dry run** with `--delete-source` armed now says so, with the count and the
gates standing in front of it. Previously the flag that destroys originals was
the one thing the simulation never mentioned.

And the `[D]` confirmation counts with the **child's own finder** instead of by
file extension, so mode 6 no longer announces 23 files for a run that touches 3.
That count is what makes a wrong folder visible before the token is charged.

### `[D]` in the wizard

Deleting is not a layout, so it is not a mode. `[D]` charges a confirmation,
asks which layout (`0`-`8`), then shows **how many files, from which folder, to
where** before charging the HHMM token at execution. Three gates, each more
specific than the last — a repeated yes/no just trains you to answer it twice.

Mode 8 no longer implies deletion anywhere: it is "in-place recursive", and it
keeps your files.

**Safety note:** the wrapper's HHMM gates and the unattended `--run-preset`
refusal now key on `delete_source` rather than on mode 8, so a mode-3 delete is
confirmed (and, scheduled, refused) exactly like mode 8 always was.

## v1.8.4

Date: 2026-07-28

### `--run-preset`: presets without the menu

The companion to presets (v1.8.3): the same saved workflow, runnable from a
scheduled task.

```powershell
py jxl_photo.py --list-presets
py jxl_photo.py --run-preset nightly-sync
py jxl_photo.py --run-preset nightly-sync --dry-run
py jxl_photo.py --run-preset nightly-sync --overwrite
```

It runs once and exits, never touching stdin, so Task Scheduler (or cron) can
drive it. Exit codes: `0` ran, `1` failed or refused, `2` no such preset — with
the available names printed.

The two decisions that the interactive path always asks about are **flags, not
inheritance**: sync is the default (`--overwrite` to redo everything), and
dry-run is off unless requested. A stored simulation never makes the scheduled
job a simulation, and a stored real run never forces one. Passing either flag
without `--run-preset` is an error rather than a silent no-op, because a
`--dry-run` that quietly did nothing would mean a real conversion for someone
who believed they asked for a simulation.

A preset that deletes sources (mode 8 + `delete_source`) is **refused**
unattended: that confirmation is a typed token, and honouring it automatically
would let a scheduled task delete originals on its own. `--dry-run` still
simulates it.

## v1.8.3

Date: 2026-07-28

### Your own distance as a menu entry

Step 2 (TIFF → JXL) offered `d=0` / `d=0.1` / `d=1.0` / Custom. Anyone working at
another distance — 0.05 is a common choice for 45 MP masters — had to open
`[4] Custom` and retype the number on every run.

Option 4 (Edit default settings) now carries a **Distance (TIFF→JXL)** setting,
and entry `[2]` is built from it:

```
[1] d=0    - Lossless (exact replica)
[2] d=0.05 - Your default (change in Settings)
[3] d=1.0  - Visually lossless
[4] Custom - Enter any value 0-15
```

It ships as `0.1` and reads exactly as before until you change it. Two details
worth knowing:

- Setting it to **0** makes entry `[2]` run the *lossless* encoder — the same
  code path as entry `[1]`, not a `d=0` lossy run.
- `[4] Custom` now starts from the last distance you actually used, so repeating
  an experiment is two keystrokes.

### Repeatable manifest runs

A manifest run (mode 99) was never saved, so `Repeat last workflow` showed it
greyed out and a recurring "keep the library in sync" pass meant walking through
the whole wizard again.

The menu entry now reads `Repeat last workflow (manifest: manifest_2026.csv)`
and:

- **re-reads the CSV** instead of replaying a stored copy of its rows, so folders
  you added or removed in Excel between runs are honoured;
- skips the input-folder question (a manifest carries its own source and
  destination per row);
- still asks overwrite/sync (default: sync) and dry-run every time;
- applies the same `Direction` and path-traversal guards the wizard applies — a
  `tiff2jxl` manifest can never be replayed by a `jxl2tiff` session;
- is disabled only while the CSV itself is missing, and says so.

### Presets (main menu option 7)

`Repeat last workflow` remembers exactly one run. Presets are named snapshots of
the same thing, so several recurring jobs can coexist:

```
--- Presets ---
  1. nightly-sync
     TIFF->JXL | manifest: manifest_2026.csv | workers 12 | d=0.05
  2. shoot-inplace
     TIFF->JXL | mode 6 | G:\2026 | workers 8 | d=0.05
[number] run | [S] save last workflow as preset | [D] delete | [B] back
```

`[S]` snapshots whatever ran last — including a manifest run — under a name you
choose, and later runs no longer disturb it. Running a preset goes through the
*same* code as `Repeat last workflow`, so the two cannot drift apart: overwrite/
sync and dry-run are always asked, never inherited.

### Settings that reach the next run

Option 4 wrote `default_workers`/`default_quality`/`default_effort` while every
run read the `last_*` values saved by the previous session. Once you had run
anything, editing those settings had no visible effect — which read as "the
repeat resets my workers".

Now changing a value in option 4 also adopts it for the next run, repeats
included. Values you leave untouched still keep whatever the last run used, and
the screen tells you when that is happening:

```
Workers: 4  (last run used 12 — editing this adopts the new value)
```

### End-of-manifest summary
A manifest run now closes with a block covering the **whole** run instead of the
last entry's numbers:

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
---------------------------------------------------------------------------
  CORRUPT / UNREADABLE (1):
  These were NOT converted. The source files are damaged.
    [3] G:\2026\260512_Recife\_EXPORT\scan_099.tif
        -> no readable pages (corrupt or truncated TIFF)
===========================================================================
```

The header counts **entries**; the table counts **files**. Failed files are listed
with their paths — a count alone still leaves you hunting through per-entry logs
for which photo broke.

Entries that produced no summary (child crashed, killed, or cancelled) are shown
as `(no summary - failed)` rather than as zeros, so a dead child never reads as a
clean run.

### Combined wrapper log — `Logs/jxl_photo/<timestamp>.log`
Each manifest entry is a separate child process with its own log file, so nothing
on disk held the totals. The wrapper now writes its own: the block above, the
untruncated source paths (the on-screen table shortens them to fit 80 columns),
and the complete failure lists (the screen caps them at 15). This is what you open
hours later, once the scrollback is gone.

### `--summary-json` (all 3 scripts, internal)
Hidden from `--help`; the wrapper passes it to every manifest child. The child
prints one `##JXLSUM## {...}` line at the end — counts, per-script extras
(thumbnails excluded, D50 patched, MD5 failures), failed file paths, and its own
log path. The wrapper consumes the line and never displays it, so a direct human
run sees nothing new. Emitted *before* the non-zero exit, since a run with
failures is exactly the one the summary matters for.

Parsing the human-readable `Done:` line was the alternative and was rejected:
there are already three different final-line formats across the scripts (normal,
`SYNC done:`, and the transcoder's `reconverted`/`up to date` variants), so a
regex would have started with three cases and broken silently on the next wording
change.

### Corrupt files are counted apart from skipped files
`skipped` now means only what you asked to skip. A TIFF with no readable pages
gets its own `corrupt` count, its own summary section, and — on a direct run — its
own warning:

```
Done: 1 OK | 0 overwrites | 0 skipped | 0 errors
WARNING | Corrupt: 1 file(s) had no readable pages and were NOT converted:
WARNING |   -> G:\2026\260512_Recife\_EXPORT\scan_099.tif
```

Exit codes are unchanged: a damaged input is not a failed run.

## v1.8.1

Date: 2026-07 (the audit release)

### Output integrity verification on every conversion
Every successful output is validated before being reported OK — JXL: full box-chain walk to EOF requiring a codestream box (bare codestream refused); JPEG: SOI+EOI; PNG: signature+IEND; TIFF: tifffile open + forced last-pixel read. Previously only mode-8 delete gates checked anything, and only signatures.

### Direction-restriction flags
- `--from-jxl` (transcoder auto mode): only `.jxl` files are processed — used by the wrapper's "JXL → JPEG Auto" so folder JPEGs/PNGs are never transcoded into JXL.
- `--from-jpeg` (transcoder convert): only JPEGs are converted to JXL — used by "JPEG → JXL lossy" so folder PNGs are never converted or deleted.

### `--delete-confirm-off`
Skips the interactive delete confirmation for wrappers/automation that already asked the user (the wrapper passes it after its own HHMM gate). The wizard never leaves a hidden child prompt again.

### `--export-subfolder` (mode 7, all 3 scripts)
Mode 7 can finally filter a specific subfolder of the export marker from the CLI; the wizard asks for it (default from Auto Mode detection).

### Authoritative multipage page markers
New `jxlphoto-page:<N>` and `jxlphoto-thumb` XMP markers on split pages make reconstruction independent of filenames (sources named `*_page<N>` / `*_thumbnail` are now safe). Older JXLs keep the filename fallback.

### Manifest `Direction` column + picker
Manifests are bound to the workflow that generated them (`tiff2jxl`, `jxl2tiff`, ...) — replaying one from the wrong direction is refused. Multiple manifests get a file picker. Excel's `7.0` mode cells parse correctly.

### Idle-timeout subprocess runner (wrapper)
Healthy long batches are never killed by a wall-clock limit; a child stuck on a hidden prompt is detected via output silence and killed. Ctrl+C kills the child.

### Real sRGB conversion for delivery
`--to-srgb` converts via `magick -profile` with a Pillow-generated sRGB ICC (proper gamut mapping), replacing `-colorspace` reinterpretation.

### Gray+alpha (LA) end-to-end
Gray+alpha JXLs decode to single-channel TIFF pages with an alpha extrasample — with or without the encoder's markers — and the JPEG preview degrades to L instead of failing.

### `--output-suffix` (revived)
Convert mode 2: with an explicit output dir → flat into it; without → `<parent><suffix>/` sibling folder. Was dead code since v1.5.

---

## v1.8.0

Date: 2026-07-18

### libjxl v0.12 Support with Automatic Version Detection

The scripts query `cjxl`/`djxl --version` once per process (cached) and only pass v0.12-only flags when the binary supports them. Older libjxl (or undetectable versions) behave exactly as before — no v0.12 flag is ever appended.

- **`djxl --reconstruct_jpeg` on lossless recovery** — transcode decode (JXL → JPEG) now asks djxl ≥ 0.12 for an authoritative lossless reconstruction that fails cleanly if impossible. Second guard alongside the `jbrd` box check; failures become per-file errors and the batch continues.
- **`--buffering` option (opt-in)** — new `CJXL_BUFFERING` setting in the TIFF encoder and JPEG transcoder, plus a `--buffering 0|1|2|3` CLI flag on the encoder. Default `None` (flag not passed; cjxl uses its fast default). `0` restores pre-0.12 maximum compression, but measured on real 45 MP lossless TIFFs it is only ~1.2% smaller for ~6× slower encodes — see the [v1.8.0 benchmark](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.0).

---

## v1.5.1

Date: 2026-04-13

### Critical Bug Fix — 8-bit TIFF Conversion

**Location:** `jxl_tiff_encoder.py`

**The Bug:** When converting 8-bit TIFF files to JXL, images appeared completely black (~25 KB instead of ~25 MB). This was a critical data corruption bug affecting all 8-bit TIFF sources (NX Studio, GIMP, Lightroom 8-bit exports).

**Root Cause:** When converting 8→16 bit, pixel values were not scaled. Value 255 (white) became 255 in 0-65535 range = 0.39% brightness.

**Fix:** Proper scaling (multiply by 257 = 65535/255):
```python
if img.dtype == np.uint8:
    img = img.astype(np.uint16) * 257  # 0-255 → 0-65535
```

**Reported by:** WiseTomCat (NX Studio 8-bit LZW TIFFs)

---

## v1.5

Date: 2026-04-12

### Feature #8 — JXL→JPEG Auto Mode for Directories

**Location:** `jxl_jpeg_transcoder.py`, `jxl_photo.py`

**What changed:** The transcoder can now auto-detect per-file in batch mode:
- Files WITH jbrd box → lossless transcoding
- Files WITHOUT jbrd → lossy conversion

**CLI:**
```bash
python jxl_jpeg_transcoder.py folder/ --mode 8  # auto-detect per file
```

**Wizard:** Option [1] "JPEG Auto-Detect" now works for directories

---

### Feature #9 — Configurable JXL→TIFF Preview

**Location:** `jxl_tiff_decoder.py`

**What changed:** Users can now disable the embedded JPEG preview in output TIFF files.

**CLI:**
```bash
# With preview (default)
python jxl_tiff_decoder.py folder/ --mode 1

# Without preview (smaller files)
python jxl_tiff_decoder.py folder/ --mode 1 --no-preview
```

**Wizard:** Step 6 asks "Add JPEG preview?"

---

### Feature #10 — Complete Manifest System

**Location:** `jxl_photo.py`

**What changed:** Full workflow support via manifest CSV files:
- All flags supported (--staging, --embed-thumbnail, --delete-source, --no-preview, --encode-tag, --d50-patch)
- Consistent behavior between interactive and manifest modes
- Edit in Excel, comment lines with `#`

**Evolution from v1.3:** The manifest system in v1.3 was functional but missing many flags that existed in interactive mode. In v1.5, all missing flags were added, achieving full parity between interactive and manifest workflows. Previously, using a manifest would result in different behavior (missing thumbnails, different staging, etc.) — now both modes produce identical results.

---

### Feature #11 — D50 Patch Tracking in OFF Mode

**Location:** `jxl_tiff_encoder.py`

**What changed:** When D50 patch is disabled, the script now tracks statistics showing how many files were already correct vs would have needed patching.

---

## v1.4

Date: 2026-04-11

### Feature #7 — Embedded JPEG Thumbnail in JXL (Optional)

**Location:** `jxl_tiff_encoder.py`

**What changed:** Optional embedded JPEG thumbnail (256px, sRGB) in JXL files for fast preview in image viewers.

**CLI:**
```bash
python jxl_tiff_encoder.py folder/ --embed-thumbnail
```

---

### Improvements

- **Auto Mode improvements** — Better folder structure detection
- **Repeat workflow** — Now saves destination format correctly
- **Step 2 renumbering** — Fixed TIFF option numbering

---

## v1.3 (Legacy)

Date: 2026-04-11
Scripts: `jxl_photo.py` (formerly v2), `jxl_tiff_decoder.py`, `jxl_tiff_encoder.py`, `jxl_jpeg_transcoder.py`

---

### Feature #5 — Auto Mode + Manifest System (Beta)

**Location:** `jxl_photo.py`

> **Status:** Auto Mode is functional and works well for common folder structures, but is still being tested. For critical workflows, manual mode selection (0-8) remains the stable option.

**What changed:** Complete rebuild of the interactive wrapper with intelligent folder analysis.

**New Auto Mode:**
- Press `[A]` in Step 4 to analyze folder structure automatically
- Detects `_EXPORT`, `Export_*` folders (case-insensitive)
- Recommends best mode with confidence level (high/medium/low)
- Shows folder mapping preview (source → destination)

**New Manifest System:**
```
[A] Auto Mode → [P] Generate manifest → Edit in Excel → [M] Run from manifest
```
- Generate CSV manifest from folder analysis
- Edit paths, delete rows, reorder before running
- Comment out lines with `#` to skip temporarily
- Manifests saved in `manifests/` folder — rerun anytime
- Use with `--sync` to re-process only changed files

**Benefits:**
- No need to memorize modes 0-8
- Visual preview before execution
- Full control via Excel editing
- Safe workflow with manifest review

---

### Feature #6 — Capture One-Compatible TIFF Preview

**Location:** `jxl_tiff_decoder.py`

**What changed:** TIFF preview structure rebuilt to match Capture One behavior.

**Before (v1.0 - v1.2):**
- Page 0: Preview (1024px, ICC embedded)
- Page 1: Main 16-bit image
- Preview kept original color space (not sRGB)

**After (v1.3):**
- Page 0: Main 16-bit image (ICC embedded)
- Page 1: Preview (256px, sRGB, no ICC, thumbnail flag)
- Preview automatically converted to sRGB via LittleCMS

**Benefits:**
- Correct thumbnail colors in Windows Explorer
- Matches Capture One TIFF structure
- Smaller preview size (256px vs 1024px)
- ICC profile only on main image (standard behavior)

---

### Feature #7 — Embedded JPEG Thumbnail in JXL (Optional)

**Location:** `jxl_tiff_encoder.py`

**What changed:** Optional embedded JPEG thumbnail (256px, sRGB) in JXL files for fast preview in image viewers.

**How it works:**
- Generate 256px preview from source TIFF
- Convert to sRGB using LittleCMS (correct colors)
- Embed as EXIF ThumbnailImage via exiftool
- Adds ~15-30KB per file

**Enable:**
```python
# In jxl_tiff_encoder.py settings
EMBED_JPEG_THUMBNAIL = True
```

Or via CLI:
```bash
python jxl_tiff_encoder.py folder/ --embed-thumbnail
```

**Supported viewers:**
- ✅ IrfanView — shows thumbnail in file list (color fix reported, test with latest plugin)
- ✅ XnView MP — fast thumbnail preview
- ✅ digiKam — uses embedded thumbnail
- ✅ darktable — EXIF thumbnail support
- ❌ Windows Explorer — current WIC codec ignores embedded thumbnail and generates its own without color management

**Important — Windows Limitation:**
The Windows JXL WIC codec (from Microsoft Store) has two problems:
1. **Ignores the embedded EXIF thumbnail** — generates its own from scratch
2. **No color management** — converts ProPhoto/Adobe RGB to thumbnail without ICC profile, resulting in wrong/washed-out colors

This is a **Windows codec limitation**, not a bug in this software. The embedded thumbnail is correct (sRGB, properly converted), but Windows doesn't use it. For accurate thumbnails on Windows, use IrfanView, XnView MP, or digiKam.

**Note on EXIF in IrfanView:** While thumbnails display correctly with the latest plugin, EXIF visibility depends on JXL source:
- **TIFF → JXL:** EXIF visible ✅ (boxes reordered)
- **JPEG → JXL:** EXIF not visible ❌ (Brotli compression)

---

## v1.2

Date: 2026-04-05
Scripts: `jxl_tiff_decoder.py`, `jxl_photo.py`

---

### Feature #4 — Improved Basic Mode (ICC Preservation)

**Location:** `jxl_tiff_decoder.py`

**What changed:** The "Basic" decode mode now preserves the ICC profile generated by djxl, instead of discarding it entirely.

**Before (v1.0 - v1.1):**
- Basic mode decoded to PPM format (no ICC support)
- Output TIFF had no ICC profile attached
- Only useful for web/sRGB workflows

**After (v1.2):**
- Basic mode decodes to PNG format to capture ICC from djxl
- ICC profile generated by djxl is extracted and attached to output TIFF
- Makes more sense for most workflows where color accuracy matters

**New None Mode:**
The old "discard ICC" behavior is still available via `--none` flag (or `FORCE_NONE_MODE = True` in settings). Use this only if you specifically want no ICC profile.

**CLI Usage:**
```bash
# New Basic mode (preserves djxl ICC) — default when no XMP ICC
python jxl_tiff_decoder.py photo.jxl

# Force None mode (no ICC) — old behavior
python jxl_tiff_decoder.py photo.jxl --none

# All modes can be forced via flags
python jxl_tiff_decoder.py photo.jxl --basic   # Force Basic (djxl ICC)
python jxl_tiff_decoder.py photo.jxl --none    # Force None (no ICC)
python jxl_tiff_decoder.py photo.jxl --matrix  # Force Matrix (LittleCMS)
```

---

## v1.1

Date: 2026-04-04
Scripts: `jxl_photo.py`, `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

---

### Summary Table

| # | Feature | Scripts | Note |
|---|---------|---------|------|
| 1 | D50 Illuminant Patch (with modes) | encoder | v1.0 had basic patch; modes (auto/on/off) are new |
| 2 | Metadata Strip Mode | encoder | Did not exist in v1.0 |
| 3 | D50 Count in OFF Mode | encoder | New tracking when patch is disabled |

**All bug fixes from v1.0 are documented in `bug_tracking_since_v1.0.md`.**

---

### Feature #1 — D50 Illuminant Patch (with modes)

**Location:** `jxl_tiff_encoder.py`

**What changed since v1.0:** In v1.0, the D50 patch was always applied unconditionally to all files.

**New behavior:**
- `auto` (default): Detects Capture One exports via EXIF Software field and applies patch only when needed
- `on`: Always apply D50 patch
- `off`: Never apply D50 patch (but tracks correctness — see Feature #3)

**CLI Usage:**
```bash
python jxl_tiff_encoder.py folder/ --d50-patch auto
python jxl_tiff_encoder.py folder/ --d50-patch on
python jxl_tiff_encoder.py folder/ --d50-patch off
```

**Wizard:** Step 6 (Basic Parameters) asks for D50 patch mode when TIFF→JXL.

**Bug fixed:** D50 patch was unconditional in v1.0 — now respects modes and D50_PATCH_SOFTWARE_LIST.

---

### Feature #2 — Metadata Strip Mode

**Location:** `jxl_tiff_encoder.py`

**What changed since v1.0:** This feature did NOT exist in v1.0.

**Description:** Option to strip all metadata (EXIF, XMP) from output JXL files. Only encoding parameters are preserved in `dc:Description`.

**Use Cases:**
- Privacy: Remove GPS, camera info, timestamps
- Minimal file size: Strip all metadata for smallest possible JXL
- Clean archives: Only keep essential encoding info

**CLI Usage:**
```bash
python jxl_tiff_encoder.py folder/ --strip
```

**Wizard:** Step 6A (Advanced Options) → "Strip metadata?"

---

### Feature #3 — D50 Count in OFF Mode

**Location:** `jxl_tiff_encoder.py`

**What changed since v1.0:** When D50_PATCH_MODE="off", the script now tracks correctness even though no patching is applied.

**Description:** Users can see how many files were already correct vs would have needed patching, helping them decide if they should enable patching.

**Summary Output:**
```
# mode: off — shows what would have happened
D50 patch: 2 already correct | 8 would have needed (mode: off)
```

---

## Bug Fixes Summary

**All bugs from v1.0 and v1.1 are documented in `bug_tracking_since_v1.0.md`.**

**Code quality and compatibility notes are in `code_quality_refactoring.md`.**

Key fixes that improved robustness:
- Race conditions in staging directory (UUID added)
- Integer overflow in JXL box parser
- PPM truncation detection
- Deadlock in djxl+ImageMagick pipeline
- Distance parameter passed to cjxl correctly
- exiftool warning filtering in metadata
- lossless_jpeg=1 incompatible with distance>0
