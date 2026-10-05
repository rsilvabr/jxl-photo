# jxl_recompressor.py

Batch **JXL → JXL recompressor**: shrink an existing JPEG XL archive by
re-encoding it at a new distance/effort, keeping everything this toolkit
stamps into the files — the base64 ICC in XMP CreatorTool, EXIF/XMP/IPTC
metadata, provenance markers (`jxlphoto-*`) and multi-page group markers.

The typical workflow: photos were archived at near-lossless (`d=0.05–0.1`)
when disk was cheap; years later storage runs short and the archive is
recompressed to `d=1.0` ("visually lossless") or `d=2.0` — paying **one**
generation of lossy re-encode, not several.

## Requirements

- **cjxl / djxl** — [libjxl releases](https://github.com/libjxl/libjxl/releases), on PATH
- **exiftool** — [exiftool.org](https://exiftool.org), on PATH (metadata never round-trips through cjxl; it is copied with exiftool)
- **numpy + imagecodecs** — only needed for `--verify-roundtrip`
- **ImageMagick + Pillow** — only needed for derivatives (`--output-icc`, `--resize-long/-short/-percent`, `--sharpen`)

## Quick start

```powershell
# The easy way — a folder, outputs in recompressed_jxl/ next to the sources
py jxl_recompressor.py D:\Photos\Archive --mode 1 --distance 1.0

# Single file to a specific output folder
py jxl_recompressor.py photo.jxl D:\Smaller --mode 0 --distance 2.0

# Recursive, mirroring the tree: each subfolder gets its own JXL_recompressed/
py jxl_recompressor.py D:\Photos\Archive --mode 3 --distance 1.0

# Recursive into a flat folder (mode 2 writes everything into the output root)
py jxl_recompressor.py D:\Photos\Archive D:\Photos\Archive_small --mode 2 --distance 1.0

# Sync an interrupted run — only recompress sources newer than their output
py jxl_recompressor.py D:\Photos\Archive --mode 1 --distance 1.0 --sync

# See what would happen, touching nothing
py jxl_recompressor.py D:\Photos\Archive --mode 1 --distance 1.0 --dry-run
```

## How it decides what to do with each file

Every JXL written by this toolkit records its encoding parameters as an
append-only lineage chain (`gen=N | cjxl d=X e=Y | cjxl d=... e=...` in
XMP-dc:Description, or in EXIF Software when the encoder ran with
`--encode-tag software`). `gen=N` is the number of **lossy** generations the
file has been through (a `d=0` entry is recorded but does not count). The
recompressor reads that record and compares it against the request:

| Situation | Category | Default action |
|---|---|---|
| Source lossless (`d=0`), request lossy | **ok** — first lossy generation, best possible quality at that distance | recompress |
| Source lossless, request lossless with **higher** effort | **ok** — same pixels, smaller file | recompress |
| Source lossy at `d_old`, request `d_new > d_old` | **ok** — a genuinely smaller target | recompress |
| Request `d_new == d_old` (any effort) | **downgrade** — buys nothing but a generation of loss | `ask` |
| Request `d_new < d_old` | **downgrade** — quality cannot be recovered; the file only grows | `ask` |
| Lossless → lossless with effort ≤ recorded | **downgrade** — same pixels, no gain | `ask` |
| No `cjxl d=/e=` record found | **unknown** | `convert` |
| **`gen >= 2` and the request is lossy** | **regeneration** — a REPEATED lossy re-encode, on top of generations the file already carries | `ask` |

The **regeneration** row is independent of the others and fires *in addition*
to them. The threshold is `gen >= 2`, not `>= 1`: every lossy JXL this
toolkit's own encoder produces is born at `gen=1`, so guarding at 1 would turn
the recompressor's main use case — taking the encoder's `d=0.1` previews to
the final `d=1.0` archive — into an `ask` that silently skips everything on
unattended runs. The first recompression of an encoder output is expected;
the guard exists for the **second** lossy re-encode onwards. Measured on real
files, each extra lossy generation costs ~0.2–0.6 dB of PSNR on top of what
the byte reduction alone costs (at a fixed file size, and growing with the
number of generations), and the recorded nominal `d` stops describing the
result: a 19-generation chain of small steps landed 9 dB below a single
direct encode at the same file size (nominal d≈1.5, perceptual quality of
d≈4–7). `d_new > d_old` cannot see this — it compares
one step at a time, so a slow drip of `d=0.1 → 1.0 → 1.5 → 2.0` runs years
apart passes every check while the image degrades. The `gen=` count is what
closes that hole. A lossless request (`d=0`) does not fire it: a lossless
step adds no loss, so there is no new generation to warn about. When
regeneration and downgrade both apply, the more conservative action wins
(skip > copy > ask > convert).

The policies are configurable (`--on-downgrade`, `--on-regeneration`,
`--on-unknown`, and the matching settings in the script header). Each
accepts:

- **ask** — one batch prompt before the run (interactive only; unattended
  runs treat it as *skip* — fail closed)
- **copy** — copy the original JXL **verbatim** (zero generation loss)
- **skip** — leave the file out of the run
- **convert** — re-encode anyway

### Planning takes a while on a hard disk

All of this is decided **before** the first file is converted. The run reads
each source's record with exiftool, walks its boxes for a `jbrd`, and checks
the existing outputs. Every file is opened at least twice. On a hard disk that
costs roughly 0.15–0.25 s per file: a folder of 3 327 JXLs once planned for
13 minutes before the first `[1/N]` line. The log says so as it starts, and its
last planning line says where the time went. Measured on a 20 TB SATA hard disk:

```text
Planning 1355 file(s): reading each one's encode record and checking for a jbrd box — on a hard disk this can take several minutes...
Planned in 3m58s (encode records 3m58s, jbrd check 0s, output checks 0s)
```

Practically all of it is the exiftool read of each file's record. The `jbrd`
walk that follows finds the same files already in the system's cache.

### JXLs transcoded from JPEG (jbrd box)

A JXL produced by `jxl_jpeg_transcoder.py` carries a **jbrd box**: the
original JPEG is bit-exact recoverable from it (`djxl --reconstruct_jpeg`),
and `checksums.md5` binds those bytes for the transcoder's delete gates.
Recompressing destroys both, so these files default to **copy** verbatim,
whatever distance you asked for. `--jbrd-policy convert` overrides this —
logged per file, because the JPEG recovery is gone for good.

### Keep-smaller safety net

After every re-encode the sizes are compared. If the new file is **not
smaller** than the source (already well-compressed content, very high
effort request, …), the output is replaced by a verbatim copy of the
source — same bytes, zero generation loss. In in-place runs the original
is simply kept. Disable with `--no-keep-smaller`.

## Metadata

cjxl cannot be trusted to carry metadata across a JXL→JXL re-encode (older
builds drop it; cjxl 0.12 carries Exif/XMP, but Brotli-compressed),
so the script copies it explicitly with exiftool (`-tagsfromfile`, EXIF/XMP/IPTC),
**appends** the new `cjxl d=/e=` parameters to the lineage chain (the old
entries stay — the chain is the history the regeneration guard reads), and
preserves the provenance markers (`jxlphoto-src:`, `jxlphoto-srcsum:`,
multi-page `jxlphoto-mpg:`, …) verbatim — an archive made by
`jxl_tiff_encoder.py` stays provable as-is.

The metadata is written as **plain** `Exif`/`xml ` boxes placed **before** the
codestream — the same layout the encoder produces. cjxl 0.12 would otherwise
leave them Brotli-compressed (`brob`) between the codestream parts, which
IrfanView cannot read (see *Viewer quirks* in the main README).

The `gen=` token at the head of the chain is **derived, never incremented**:
every write recounts the lossy (`d>0`) entries and keeps
`max(stored gen, count)`, so a hand-edited field self-corrects on the next
pass and the guard never undercounts. Any user text in the field (a caption)
stays first. Verbatim-copy paths (policy copies, jbrd copies, the
keep-smaller fallback) carry the field over byte-for-byte — no re-encode
happened, so nothing is recomputed.

`--encode-tag` controls where the new record goes: `xmp` (default),
`software` (EXIF Software field) or `off` (record nothing — and any previous
record, `gen=` and chain together, is **stripped**, so a later run never
trusts stale parameters; it is the only way to deliberately discard the
lineage).

When a file carries the record in the OTHER field (it was written with a
different `--encode-tag`), the chain is **migrated**, never dropped, and the
generation count is read over both fields. Repeated entries are real history
(`cjxl d=0.1 e=7 | cjxl d=0.1 e=7` is two generations — a decode and a
re-encode at the same settings) and are never collapsed. Only when both
fields hold the same chain (or one extends the other) is it treated as one
history; otherwise both are kept, `dc:Description` first.

## Output modes

Same semantics as the other scripts in the toolkit:

| Mode | What it does |
|---|---|
| 0 | Flat: file→file/folder. **Without an output argument — or with the source's own folder as output (what a manifest row sends) — replaces the source in place** (confirmation required) |
| 1 | Flat folder → `recompressed_jxl/` subfolder inside it |
| 2 | Flat folder → explicit output folder (default: `<input>/recompressed_jxl`) |
| 3 | Recursive → mirror tree, `JXL_recompressed/` inside each source folder |
| 4 | Recursive → folder token replace (`16B_JXL` → `16B_JXL_small`; `_JXL_small` appended if no token) |
| 5 | Recursive → sibling `JXL_recompressed/` next to each source folder |
| 6 | Capture One `_EXPORT` workflow: files under marker folders → `<_EXPORT>/16B_JXL_small/` (folder name configurable with `--export-jxl-folder`) |
| 7 | Mode 6 restricted to one export subfolder (`--export-subfolder`) |
| 8 | Recursive **in place**: each JXL is replaced next to itself (confirmation required) |

Recursive scans skip this tool's **own output folders** below the input root
(`recompressed_jxl`, `JXL_recompressed`, `16B_JXL_small`, `JXL_small`) — plus
the modes 6/7 output folder configured via `EXPORT_JXL_FOLDER` /
`--export-jxl-folder` (e.g. a derivative folder such as `16B_JXL_sRGB`), so a
re-run never eats its own output. Pointing the input **at** such a folder to
compress it again is legitimate and works — only descendants are filtered.

Mode 2 is recursive and writes **flat by name**: an output folder that
equals the input folder is refused at startup (exit 2) — root files would be
replaced in place while files from subfolders were flattened into the root,
mixed with the originals they came from. Use mode 0 (flat) or 8 (recursive)
for a true in-place run. Mode 0 accepts its own folder as output: it is flat,
so that simply IS the in-place run. Modes 1/3/4/5/6/7 compute their own
folders and ignore the output positional.

**Overwriting a foreign output is loudly warned, never blocked.** In the
folder-preserving modes (1 and 3) a sync/`--overwrite` run re-encodes over an
existing output with no provenance gate — the source is authoritative on
that path, and re-running is what regenerates it. If that output carries
`jxlphoto-src:`/`jxlphoto-srcsum:` markers naming a DIFFERENT origin, the
run logs a loud warning before replacing it. A markerless or unreadable
output — the common case — says nothing: there is no cheap proof to
arbitrate an arbitrary pair, so the check is advisory by design.

## Deleting the originals

`--delete-source` removes each source JXL after its output is:

1. written and verified at its **final** path (container box-chain walk), and
2. for verbatim copies, **MD5-matched** against the source, and
3. matched to its source by provenance in the folder-collapsing modes
   (`--provenance path|content` via the `jxlphoto-src:`/`jxlphoto-srcsum:`
   markers).

`--verify-roundtrip` additionally decodes both sides and compares pixels
before deleting — pixel-exact for lossless→lossless, brightness/PSNR sanity
floors when a lossy step is involved (`VERIFY_LOSSY_MIN_MEAN_RATIO`,
`VERIFY_LOSSY_MIN_PSNR`). `--delete-skipped` widens the deletion to sources
whose output already existed (finishing an interrupted archive). The existing
output must pass every gate — and for a skipped **re-encode** that includes the
provenance proof, in **every** mode: its `jxlphoto-src`/`jxlphoto-srcsum`
markers must match the source's. The folder-preserving modes (1/3) used to
delete on the structural check alone, so a valid, newer, same-named output
written by a different photo could destroy the only copy of the source. The
recompressor copies the markers verbatim from the source, so a genuine previous
re-encode passes for free; a missing or unreadable marker fails **CLOSED** and
the source is KEPT (`KEEP (existing output carries no matching provenance
marker)`). Armed **without** `--delete-source` it does nothing (with a
warning): it used to delete already-archived sources with no confirmation and
no provenance check, and a same-named output from a different photo was enough
to destroy the only copy of a source.

Multi-page documents delete as a **group**: every page sharing a
`jxlphoto-mpg:` id in the same folder must pass its own gates, otherwise no
page of the group is deleted — a half-deleted group would be spread across
two folders with a dangling master page. A page that did not make it this run
at all (its conversion failed, a policy skipped it, provenance refused it)
counts as a failed page: it keeps its siblings too.

In-place runs (mode 0/8, and staging promotions) replace the source only
after the re-encode passed every gate, via a temp file in the destination
folder and a same-volume atomic `os.replace` — a cross-volume move failure
can no longer leave the original destroyed with the only good copy stranded
in staging under a UUID name. Multi-page documents replaced in place are
held together as well: pages of one `jxlphoto-mpg:` id are swapped only
after every page of the group has settled, and a page that failed — or one
whose siblings did not all take part this run — vetoes the replacement of
the whole group (`GROUP HELD IN PLACE`, the re-encodes discarded). The
all-or-nothing rule the delete gate applies to deletion therefore extends
to the replacement itself, and no document is ever left spread across two
generations.

Three interactive confirmations guard the deletion unless
`--delete-confirm-off` is passed — the wrapper (`jxl_photo.py`) charges its
own confirmation and passes this flag so the child never prompts on an
invisible stdin.

## Derivatives: colour conversion, resize and sharpening

A **derivative** is any run that shapes output pixels instead of plainly
recompressing the master: `--output-icc` (colour conversion), `--resize-long/-short/-percent`
(Lanczos resize) and/or `--sharpen` (output sharpening). The recipes combine,
and every guarantee below applies to all of them.

`--output-icc sRGB|AdobeRGB|<path to .icc>` converts the master: it is decoded
at 16 bits, converted with ImageMagick from its own profile (fixed **relative
colorimetric + black point compensation**) and re-encoded at the requested
`--distance` — still **16 bits** (8-bit lossy JXL was measured to be no
smaller, so there is no 8-bit option). Use it to make light delivery copies
from the masters, e.g. a ~4 MB sRGB file to replace a JPEG:

```
Capture One  ->  _EXPORT/TIFF16/*.tif   (16-bit ProPhoto)

# master (as usual)
py jxl_tiff_encoder.py "F:\Fotos\2025" --mode 7 --export-subfolder TIFF16 --distance 0.05
    ->  _EXPORT/16B_JXL/*.jxl

# light 16-bit sRGB derivative (replaces the JPEG), profile swapped in the name
py jxl_recompressor.py "F:\Fotos\2025" --mode 7 --export-subfolder 16B_JXL `
     --export-jxl-folder 16B_JXL_sRGB --output-icc sRGB --distance 1.0 --sync `
     --rename-from ProPhoto-g22 --rename-to sRGB
    ->  _EXPORT/16B_JXL_sRGB/_DSC0013_sRGB_v1.jxl

# light JXL derivative, 4000 px on the long edge, with screen sharpening
py jxl_recompressor.py "<pasta>" --mode 7 --export-subfolder 16B_JXL `
     --export-jxl-folder 16B_JXL_4k --output-icc sRGB --resize-long 4000 `
     --sharpen screen --distance 1.0
```

| Target | What it is |
|---|---|
| `sRGB` | built-in sRGB (Pillow/LittleCMS); needs Pillow |
| `AdobeRGB` (also `adobe`, `adobergb1998`) | built-in Adobe RGB (1998)-compatible matrix/TRC profile (verified: 0 difference from Adobe's own profile in 8 bits at intents 0 and 1, and cjxl records D65 / 0.64, 0.33 · 0.21, 0.71 · 0.15, 0.06) |
| any path to `.icc`/`.icm` | a real **RGB** profile; CMYK/grey files are refused before the batch starts |

The derivative is built **from the master JXL**, not from the TIFF: measured
on a 16 MP ProPhoto photo, master d=0.05 → sRGB d=1.0 gave 38.086 dB against a
direct encode of the TIFF at 38.102 dB — a 0.017 dB difference, at the same
size. "Keep the original colour space" is simply not passing `--output-icc`.

Guarantees, all fail-closed:

- **Never in place.** Modes 8 and 0-without-output are refused (exit 2): the
  converted file would replace the master. Pick a mode that writes to another
  folder (1–7).
- **Never deletes.** `--delete-source`/`--delete-skipped` with a derivative are
  refused (exit 2), and `--verify-roundtrip` too (it compares against the
  source, which a converted derivative no longer matches).
- **Never overwrites what is not its own derivative**, even with
  `--overwrite`: an existing file at the destination without a
  `jxlphoto-derived:` token (or whose markers cannot be read) is REFUSED, so
  pointing `--export-jxl-folder` at the master folder cannot destroy the
  masters.
- **Re-derives when the recipe changes.** Each output records
  `jxlphoto-derived:<recipe>` (`sRGB` / `AdobeRGB` / `icc-<md5[:12]>` / `keep`
  for no conversion, plus `@long2048`/`@short1080`/`@pct50` (`+up` when an
  upscale was allowed) and `+screen`/`+print`), followed by the encode record
  `/d<distance>e<effort>` (e.g. `sRGB/d4e9`). A run with another recipe
  re-derives instead of trusting (and skipping) the old file. An identical
  re-run with `--sync` SKIPs normally.
  A distance below this cjxl's floor is recorded AS the floor (cjxl clamps it
  to the same output, so 0.01 and 0.05 are the same encode). With
  `REDERIVE_ON_ENCODE_CHANGE` (default True) a `--sync` run also re-derives
  when the recorded distance/effort differ from this run's — changing the
  preset's `--distance`/`--effort` refreshes existing derivatives. Labels
  written before v2.7.0 carry no encode record and are NEVER re-derived for
  distance/effort (the run logs how many it saw and suggests one
  `--overwrite` pass to refresh them); use
  `--no-rederive-on-encode-change` to apply a new distance/effort to new files
  only.
- **Removes the archive provenance**: `jxlphoto-src:`/`jxlphoto-srcsum:` are
  dropped (a derivative must never prove the original TIFF is archived — the
  encoder would otherwise accept it under `--delete-skipped`), and the
  `ICC:<base64>` blob in `CreatorTool` is replaced by the target profile, so
  decoding the derivative to TIFF labels it with the colour space its pixels
  are really in.
- `copy` policies (downgrade/regeneration/jbrd) become **skip** and
  keep-smaller is turned **off**: a verbatim copy would carry the source
  colour space into a folder that promises the target's.

Costs and notes:

- Memory: see [Memory and --workers](#memory-and---workers) — the worker count
  is capped automatically. The intermediates live in `TEMP_DIR` and are
  removed per file.
- A derivative needs `djxl` and `magick` on PATH, plus Pillow (even for
  AdobeRGB: the sRGB profile assigned to sources that decode without one comes
  from Pillow).
- The usual classification still applies: a derivative at the **same**
  distance as the master (master d=0.05 → sRGB d=0.05) reads as a
  "downgrade" and the default policy would skip it — pass
  `--on-downgrade convert` for that.
- To decode only the masters to TIFF later, use the decoder in mode 7 with
  `--export-subfolder 16B_JXL` (mode 6 would also pick the derivative folder).

### Resize and sharpening

`--resize-long PX`, `--resize-short PX` and `--resize-percent P` (mutually
exclusive) write the derivative at another size; the aspect ratio is always
kept. Without `--allow-upscale` an image already smaller than the target is
**never enlarged** — it keeps its size and the run logs
`Already smaller than the target — kept at W×H`; `--resize-percent` above 100
is an error without the flag, and when an upscale is allowed the label records
it (`+up`).

`--sharpen none|screen|print` (default `none`) applies output sharpening after
the resize. Colour images are sharpened on the Lab **L** channel only (no
colour fringes); a grey image is sharpened directly. The preset numbers live
in one table (`SHARPEN_PRESETS`), in **output pixels** so one preset works at
any size, calibrated against Capture One:

| Preset | sigma (px) | gain | threshold |
|---|---|---|---|
| `screen` | 0.80 | 0.60 | 0 |
| `print` | 3.24 | 0.77 | 0 |

Calibrated 2026-09-25/26 against Capture One's **default** "screen" and "print"
output-sharpening presets: the same 4 photos (Nikon Z7, 16-bit) exported with
sharpening off, screen and print at 1000, 2000 and 3000 px long edge and at full
resolution (8256 px), and a grid search of this operation on the "off" export.
Capture One keeps the **radius** nearly constant in output pixels (print ≈ 3 px
at every size); only the screen **amount** falls as the image grows. So:

| Preset | Distance from Capture One's per-size best (1000 / 2000 / 3000 / 8256 px) |
|---|---|
| `print` 3.24 · 0.77 | ≤ 0.1 dB at every size (visually the same halos) |
| `screen` 0.80 · 0.60 | −1.3 / −0.7 / −0.2 / −1.6 dB — the best single value |

The `--sharpen-sigma`/`--sharpen-gain`/
`--sharpen-threshold` expert overrides are deliberately **outside** the recipe
label: after changing one, re-derive with `--overwrite` (the sync would
otherwise see the old label as current). Without `--sharpen screen|print` they
are inert (a warning is printed). The Lanczos resize runs in the image's own
gamma-encoded space (the ImageMagick default), not in linear light.

Resize and sharpening work with or without `--output-icc`: without it the
source colour space is kept — the source profile is assigned explicitly before
the shaping pass and re-assigned after the Lab sharpening drops it, so the
output's `CreatorTool` ICC still describes its pixels. A greyscale image is
never colour-converted (an RGB profile on a single channel is invalid), but it
is still resized and sharpened.

## Renaming the outputs (`--rename-from` / `--rename-to`)

`--rename-from TEXT --rename-to TEXT` swaps a piece of the output **file name**
at planning time — the semantics are exactly the transcoder's: literal,
case-sensitive, only the **first** occurrence, only in the stem (the extension
never changes). A name without the token is kept, with a warning naming how
many files were left alone. The rename applies to any run, with or without
`--output-icc` — this is how the profile in the name follows the derivative:

```
py jxl_recompressor.py "F:\Fotos\2025" --mode 7 --export-subfolder 16B_JXL `
     --export-jxl-folder 16B_JXL_sRGB --output-icc sRGB --distance 1.0 --sync `
     --rename-from ProPhoto-g22 --rename-to sRGB
    master:    _EXPORT/16B_JXL/_DSC0013_ProPhoto-g22_v1.jxl
    derivative: _EXPORT/16B_JXL_sRGB/_DSC0013_sRGB_v1.jxl
```

The name is changed **before** the run plans, so the sync finds the renamed
output on the next run, the non-derivative refusal and the duplicate-output
abort see the real destination, and two sources collapsing onto one renamed
name abort the run (exit 2) instead of overwriting each other.

In-place runs (mode 8, or mode 0 without an output folder) are **refused**
(exit 2): a renamed "replacement" is a new file beside its source, which the
next recursive scan would pick up as a fresh input while the source is never
replaced.

## Memory and --workers

Most re-encodes **stream**: `cjxl` holds a little more than one row of blocks at
a time, so the worker count is limited by CPU, not memory. Four settings make it
encode the **whole image at once**, and then each worker's peak jumps 2.5–8×:

- **effort ≥ 10**, any distance;
- **effort 8–9** and **distance > 0.5**;
- **effort 7** and **distance ≥ 3**;
- **`--buffering 0`** (any effort).

Everything else streams. Measured on 45 MP 16-bit photos, libjxl 0.12.0:

| Encode | Peak per worker |
|---|---|
| streaming (the common case) | ~1.5 GB |
| whole image, effort 7 | ~3.6 GB |
| whole image, effort 8+ | ~12.4 GB |

**The 2026-10-04 incident.** The scheduled MOBILE preset recompressed 45 MP
photos at **d=3, effort 7** with `--workers 30` — the whole-image path. 30 ×
3.6 GB ≈ **107 GB**, far above the 76.6 GB commit limit of the 64 GB machine
it ran on: 314 of 681
files failed with `JxlEncoderProcessOutput failed` / `WinError 1455`, and one
worker hung forever when the MemoryError landed in a subprocess reader thread.

**The run caps `--workers` for you.** Before the pool starts it takes the
**largest** file in the batch, estimates the per-worker peak from the distance,
effort and buffering, and lowers the count so the jobs fit in the memory the
system can still commit (RAM + pagefile) times `WORKER_MEMORY_FRACTION` (0.8).
It logs `Memory: ~N GB per worker (...) | M GB available | workers K` and warns
`--workers N reduced to K` with the `--buffering 1` remedy on the whole-image
path. The worker count the pool really uses is the capped one; the startup
`Mode: ... | workers:` line still shows what you asked for.

The budget is the memory the system can still **commit** when the run starts,
not the RAM that looks free: programs left open (a raw editor can hold 15 GB of
commit while using 3 GB of RAM) shrink it, so the same preset can get fewer
workers on a night when they are open. Closing them — or `--buffering 1` —
gives the workers back.

**If files still fail for lack of memory**, the summary says so:
`N error(s) look like the system ran out of memory — lower --workers or
WORKER_MEMORY_FRACTION, or use --buffering 1`. The failed files have no output,
so the next run retries them.

**More than ~8 workers buy almost nothing.** Measured throughput (24 files,
JXL → d=3 e=7):

| Workers | 1 | 4 | 8 | 8 + `--buffering 1` | 16 + `--buffering 1` |
|---|---|---|---|---|---|
| files/s | 0.23 | 0.50 | 0.61 | 0.68 | 0.73 |

`--buffering 1` (or 2/3) forces the streaming path at any distance/effort for
files only ~2 % larger — a good way to keep many workers safe by hand.

## All flags

```
py jxl_recompressor.py <input> [output] [flags]

input / output        Input JXL file or folder / optional output (modes 0 and 2;
                      other modes warn that the output positional is ignored)
--mode 0-8            Output folder mode (default 0)
--workers N           Parallel workers (default: min(CPU, 16))
--overwrite           Always recompress, even if the output exists
--sync                Recompress only when the source JXL is newer than the output
--distance 0-15       Target JXL distance (default: CJXL_DISTANCE = 1.0)
--effort 1-10         cjxl effort (default: CJXL_EFFORT = 7)
--buffering 0-3       [libjxl >= 0.12] encoder buffering level
--on-downgrade POL    ask/copy/skip/convert for requests that cannot gain (default: ask)
--on-regeneration POL ask/copy/skip/convert on a REPEATED lossy re-encode
                      (gen >= 2) and the request adds another (default: ask)
--on-unknown POL      ask/copy/skip/convert for files with no d=/e= record (default: convert)
--jbrd-policy POL     copy/skip/convert for JPEG-recoverable JXLs (default: copy)
--no-keep-smaller     Keep the re-encoded file even when it is not smaller
--rederive-on-encode-change
--no-rederive-on-encode-change
                      Derivatives only: re-derive an existing output whose
                      recorded distance/effort differ from this run's
                      (--rederive-on-encode-change: default,
                      REDERIVE_ON_ENCODE_CHANGE = True), or only when the
                      colour/resize/sharpening recipe changes
                      (--no-rederive-on-encode-change). Ignored, with a
                      warning, on plain recompressions
--output-icc TARGET   sRGB / AdobeRGB / path to an RGB .icc: write a
                      16-bit colour-converted derivative instead of a plain
                      recompression. Never in place, never with --delete-source.
                      See "Derivatives: colour conversion, resize and
                      sharpening" above
--resize-long PX      Output long edge in pixels (derivative; aspect kept).
--resize-short PX     Output short edge in pixels. Mutually exclusive with the
--resize-percent P    other two; P is a percentage. Without --allow-upscale the
                      image is never enlarged
--allow-upscale       Let --resize-long/-short/-percent enlarge an image smaller than the target
                      (recorded in the recipe as +up)
--sharpen MODE        none (default) / screen / print: output sharpening after
                      the resize (Lab L channel only)
--sharpen-sigma N     Expert overrides of the sharpening preset (px, gain,
--sharpen-gain N      0-1 threshold). Only with --sharpen screen|print; NOT in
--sharpen-threshold N the recipe label — re-derive with --overwrite after
                      changing one
--rename-from TEXT    Replace this text in each output file name (literal,
                      case-sensitive, first occurrence, extension untouched;
                      same semantics as the transcoder's flag)
--rename-to TEXT      Replacement for --rename-from (may be empty)
--encode-tag MODE     xmp/software/off — where to record the new d=/e= (default: xmp)
--delete-source       Delete each source JXL after its output is verified
--delete-skipped      Also delete sources whose output already existed
--verify-roundtrip    Decode and compare pixels before deleting
--delete-confirm-off  Skip the interactive delete confirmation (wrapper passes this)
--provenance MODE     path/content — how an existing output is matched to its source
--staging DIR         Staging directory on a fast SSD (overrides TEMP2_DIR)
--clean-staging       Sweep staging leftovers older than 1h before the run
--no-preflight        Skip the advisory free-space check
--export-marker NAME  Marker folder name for modes 6/7 (default: _EXPORT)
--export-subfolder N  Mode 7: only files under EXPORT_MARKER/<name>
--export-jxl-folder NAME
                      [Modes 6/7] Output folder under the marker (default:
                      EXPORT_JXL_FOLDER, '16B_JXL_small'). Must be one plain
                      folder name that is not the marker and not the input
                      subfolder. The configured folder is skipped as a source
                      by later recursive scans
--dry-run             Simulate: nothing written, copied, replaced or deleted
--summary-json        Machine-readable ##JXLSUM## line consumed by jxl_photo.py
                      (hidden from the help text; not a user-facing flag)
```

## Settings

Same model as the other scripts: defaults live at the top of
`jxl_recompressor.py`, CLI flags override them per run.

```python
CJXL_DISTANCE = 1.0          # Target distance (0-15)
CJXL_EFFORT = 7              # Effort: size/encode-time, NOT quality
CJXL_BUFFERING = None        # [libjxl >= 0.12] --buffering for cjxl. None = cjxl
                             # chooses (streaming, EXCEPT the whole-image cases
                             # in "Memory and --workers"); 1-3 = always stream
WORKER_MEMORY_FRACTION = 0.8 # Cap --workers so parallel cjxl processes fit in
                             # memory; 0 disables the cap
OVERWRITE = "smart"          # False | "smart" (source newer) | True
ON_DOWNGRADE = "ask"         # ask/copy/skip/convert
ON_REGENERATION = "ask"      # ask/copy/skip/convert (gen >= 2 + lossy request)
ON_UNKNOWN = "convert"       # ask/copy/skip/convert
JBRD_POLICY = "copy"         # copy/skip/convert
KEEP_SMALLER = True          # Verbatim copy when the re-encode is not smaller
REDERIVE_ON_ENCODE_CHANGE = True
                             # Derivatives: a sync run re-derives when the
                             # recorded distance/effort differ (also
                             # --rederive-on-encode-change /
                             # --no-rederive-on-encode-change)
ENCODE_TAG_MODE = "xmp"      # xmp/software/off
VERIFY_ROUNDTRIP = False     # Pixel comparison before any delete
DELETE_SOURCE = False        # Delete sources after verification
DELETE_SKIPPED = False       # Also delete already-archived sources
PROVENANCE_CHECK = "path"    # path | content
DELETE_CONFIRM = True        # Interactive confirmations before deleting

RESIZE_MODE = None           # None | "long" | "short" | "percent" (--resize-long/-short/-percent)
RESIZE_VALUE = None          # px, or percent for RESIZE_MODE == "percent"
ALLOW_UPSCALE = False        # Let --resize-long/-short/-percent enlarge smaller images
SHARPEN = "none"             # none | screen | print (--sharpen)
SHARPEN_SIGMA = None         # Expert overrides, OUTSIDE the recipe label
SHARPEN_GAIN = None
SHARPEN_THRESHOLD = None

CONVERTED_JXL_FOLDER = "recompressed_jxl"   # mode 1/2 default
JXL_FOLDER_NAME = "JXL_recompressed"        # modes 3/5
JXL_SUFFIX_TO_REPLACE = "JXL"               # mode 4
JXL_SUFFIX_REPLACE = "JXL_small"            # mode 4
EXPORT_MARKER = "_EXPORT"                   # modes 6/7
EXPORT_JXL_FOLDER = "16B_JXL_small"         # modes 6/7
EXPORT_JXL_SUBFOLDER = ""                   # mode 7
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Done, no errors (skips are not errors) |
| 1 | One or more files failed / pre-flight refused the run |
| 2 | Usage error (argparse) or the run aborted early (disk full) |
| 3 | Aborted by the user at the delete confirmation |
| 130 | Interrupted with Ctrl+C (summary still printed with partial results) |

## Notes

- A **bare codestream** JXL (no container boxes) never passes the delete
  gate — every output of this toolkit is a container, so bare means
  something went wrong mid-write.
- In-place modes never "skip existing" — output **is** input; the
  keep-smaller check is what protects them.
- `--summary-json` is the contract `jxl_photo.py` parses after each child
  run; the human log goes to `Logs/jxl_recompressor/`.

## Version history

Feature and fix history: [version_history.md](./version_history.md) · [bug_tracking_since_v1.0.md](./bug_tracking_since_v1.0.md) · [new_features_since_v1.0.md](./new_features_since_v1.0.md)

## License

MIT License — feel free to use, modify, and distribute.

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling
- [Kimi](https://www.kimi.com) (Moonshot AI) and [Claude](https://www.anthropic.com/claude) (Anthropic) for code assistance and technical discussion
