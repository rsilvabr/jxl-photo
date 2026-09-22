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
| 6 | Capture One `_EXPORT` workflow: files under marker folders → `<_EXPORT>/16B_JXL_small/` |
| 7 | Mode 6 restricted to one export subfolder (`--export-subfolder`) |
| 8 | Recursive **in place**: each JXL is replaced next to itself (confirmation required) |

Recursive scans skip this tool's **own output folders** below the input root
(`recompressed_jxl`, `JXL_recompressed`, `16B_JXL_small`, `JXL_small`), so a
re-run never eats its own output. Pointing the input **at** such a folder to
compress it again is legitimate and works — only descendants are filtered.

Mode 2 is recursive and writes **flat by name**: an output folder that
equals the input folder is refused at startup (exit 2) — root files would be
replaced in place while files from subfolders were flattened into the root,
mixed with the originals they came from. Use mode 0 (flat) or 8 (recursive)
for a true in-place run. Mode 0 accepts its own folder as output: it is flat,
so that simply IS the in-place run. Modes 1/3/4/5/6/7 compute their own
folders and ignore the output positional.

## Deleting the originals

`--delete-source` removes each source JXL after its output is:

1. written and verified at its **final** path (container box-chain walk), and
2. for verbatim copies, **MD5-matched** against the source, and
3. in collapsing modes, provenance-matched (`--provenance path|content` via
   the `jxlphoto-src:`/`jxlphoto-srcsum:` markers).

`--verify-roundtrip` additionally decodes both sides and compares pixels
before deleting — pixel-exact for lossless→lossless, brightness/PSNR sanity
floors when a lossy step is involved (`VERIFY_LOSSY_MIN_MEAN_RATIO`,
`VERIFY_LOSSY_MIN_PSNR`). `--delete-skipped` widens the deletion to sources
whose output already existed (finishing an interrupted archive) — the
existing output must pass every gate. Armed **without** `--delete-source` it
does nothing (with a warning): it used to delete already-archived sources
with no confirmation and no provenance check, and a same-named output from a
different photo was enough to destroy the only copy of a source.

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
in staging under a UUID name.

Three interactive confirmations guard the deletion unless
`--delete-confirm-off` is passed — the wrapper (`jxl_photo.py`) charges its
own confirmation and passes this flag so the child never prompts on an
invisible stdin.

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
CJXL_BUFFERING = None        # [libjxl >= 0.12] --buffering for cjxl
OVERWRITE = "smart"          # False | "smart" (source newer) | True
ON_DOWNGRADE = "ask"         # ask/copy/skip/convert
ON_REGENERATION = "ask"      # ask/copy/skip/convert (gen >= 2 + lossy request)
ON_UNKNOWN = "convert"       # ask/copy/skip/convert
JBRD_POLICY = "copy"         # copy/skip/convert
KEEP_SMALLER = True          # Verbatim copy when the re-encode is not smaller
ENCODE_TAG_MODE = "xmp"      # xmp/software/off
VERIFY_ROUNDTRIP = False     # Pixel comparison before any delete
DELETE_SOURCE = False        # Delete sources after verification
DELETE_SKIPPED = False       # Also delete already-archived sources
PROVENANCE_CHECK = "path"    # path | content
DELETE_CONFIRM = True        # Interactive confirmations before deleting

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
