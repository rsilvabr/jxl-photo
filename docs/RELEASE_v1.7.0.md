# v1.7.0 — Multi-Page TIFF Support

**Release date:** 2026-07-11  
**Status:** Released

This release adds explicit handling for multi-page TIFFs. Previous versions read only the first page (`tif.series[0]`) and silently discarded additional pages. v1.7.0 detects real pages vs thumbnails and gives the user full control over splitting, skipping, or reconstructing multi-page files.

> **Reconstruction is marker-based, not name-based.** When the encoder splits a
> multi-page TIFF it writes a group marker into each page's XMP (`XMP-dc:Relation`,
> prefix `jxlphoto-mpg:`). The decoder only rejoins files that carry a matching
> marker. Independently-named files such as `scan.jxl` + `scan_page2.jxl` are
> therefore **never silently merged** — they decode to separate TIFFs. Pass
> `--no-reconstruct-multipage` to disable rejoining entirely.

Each split page also carries its own effective ICC profile (read from the page's own
ICC tag 34675; if absent, page N > 0 inherits IFD0's ICC for color interpretation).
Pages that inherit the ICC are flagged with `jxlphoto-icc:inherited` in `dc:Relation`,
so the decoder restores them **without an ICC tag**, matching the original TIFF
structure byte-for-byte.

---

## ✨ New Features

### Multi-Page TIFF → JXL

`jxl_tiff_encoder.py` now supports four behaviors:

- `--multipage-mode ignore` — encode only page 0 (original behavior, default)
- `--multipage-mode skip` — skip files that have more than one real page
- `--multipage-mode split` — encode each real page to a separate JXL
- `--multipage-mode split_all` — encode every page, including thumbnails

Thumbnail pages are detected via standard TIFF `SubfileType` flags (`is_reduced` / `is_subifd`). When splitting:

- Page 0 → `photo.jxl`
- Page N → `photo_pageN.jxl`
- Thumbnail page N → `photo_pageN_thumbnail.jxl`

Thumbnail inclusion is controlled with `--thumbnail-mode {exclude,include}` and `--thumbnail-suffix`.

### JXL → Multi-Page TIFF

`jxl_tiff_decoder.py` reconstructs multi-page TIFFs from pages that carry the encoder's XMP group marker (`jxlphoto-mpg:` in `XMP-dc:Relation`). Grouping is marker-based, not name-based, so independently-named files such as `scan.jxl` + `scan_page2.jxl` are never merged.

- `--thumbnail-handling ignore` — ignore `_thumbnail.jxl` files
- `--thumbnail-handling include` — include thumbnails in the reconstructed TIFF (default)
- `--thumbnail-handling generate` — reserved for future use; currently falls back to `include`
- `--no-reconstruct-multipage` — disable multi-page reconstruction entirely

JPEG previews are automatically skipped when reconstructing multi-page TIFFs.

### Per-Page ICC Preservation

When a multi-page TIFF is split, each page keeps its own ICC profile through the round trip. Pages without an own ICC tag inherit IFD0's profile for color interpretation, but are reconstructed without an ICC tag, matching the original TIFF structure. This is tracked via an `XMP-dc:Relation` flag (`jxlphoto-icc:inherited`).

### Grayscale and SubfileType Preservation

Split pages preserve their original channel count and `SubfileType` role:

- Single-channel pages are encoded as grayscale and reconstructed as 2D TIFF pages (`samplesperpixel=1`).
- Inherited RGB ICC is not applied to grayscale pages, avoiding libpng iCCP errors on scanner IR/mask pages.
- Non-zero `SubfileType` values (e.g. `PAGE`/`MASK`) are recorded and restored. `SubfileType=4` (MASK) is mapped to `PAGE` (`2`) on reconstruction because tifffile does not accept `MASK` on normal image pages, but the "additional page" semantics are preserved.

### Wrapper Integration

`jxl_photo.py` exposes the new options in the advanced-options step:

- TIFF → JXL: choose multi-page mode, thumbnail mode, and thumbnail suffix
- JXL → TIFF: choose how `_thumbnail.jxl` files are handled

New persistent settings: `last_multipage_mode`, `last_thumbnail_mode`, `last_thumbnail_suffix`, `last_thumbnail_handling`.

---

## 🧪 Tests

- Regression test: `tests/test_multipage.py` (runs from any working directory)
  - Creates a synthetic 3-page TIFF (real, thumbnail, real)
  - Verifies encoder ignore / skip / split-exclude / split-include modes
  - Verifies decoder reconstruction with ignore / include thumbnail handling
  - **Single-page metadata roundtrip** — asserts Make/Software survive decode
    (guards against the preview step wiping EXIF)
  - **Independent-file safety** — asserts `scan.tif` + `scan_page2.tif` decode
    to two separate TIFFs and are never merged
  - **Per-page ICC preservation** — asserts each page is restored with its own
    ICC tag, and inherited pages are restored without one

---

## 🔧 Pre-release audit fixes

The first multi-page implementation passed its own structural tests but an
independent audit found several regressions in the shared single-page path.
All fixed before release:

- **Single-page metadata loss (critical):** the refactor ran `add_jpeg_preview`
  (which recreates the TIFF) *after* `copy_metadata`, wiping all EXIF/XMP on the
  default decode path. Order restored: preview first, metadata second.
- **Concurrent mode-8 delete (critical):** the decoder's delete loop paired
  `zip(tasks, results)` positionally while results arrive in completion order,
  so under `--workers > 1` a failed conversion could delete its source. Now
  keyed explicitly by main-JXL path (same fix pattern as v1.6.0 #105).
- **Corrupt TIFF aborted the batch (critical):** planning opened every TIFF with
  no error handling. Now wrapped per-file; ignore mode short-circuits without
  opening the file at all, so one bad file logs a single error and the run
  continues.
- **Filename-based grouping (high):** replaced with the XMP marker mechanism
  described above, so independent files are never merged.
- **Encoder skip-status key mismatch (high):** skipped pages returned a
  different key shape than ok/error, producing false "KEEP in staging (error)"
  warnings on sync re-runs. Unified.
- **None-mode regression / tifffile tags (medium):** None mode no longer gets a
  JPEG preview (restores the 1-page v1.6.0 contract), and the leftover
  `Software: tifffile.py` / shaped-JSON `ImageDescription` tags are now cleared.
- Dead code removed (`convert_one`, `write_tiff` in the decoder;
  `_count_outputs_for_tiff` in the encoder).

---

## 📈 Stats

- **Full changelog:** [`docs/bug_tracking_since_v1.0.md`](bug_tracking_since_v1.0.md)

---

## Release History

| Version | Date | Highlights |
|---------|------|------------|
| **v1.7.0** | 2026-07-11 | Multi-page TIFF support: split/skip/ignore modes, thumbnail handling, marker-based decoder reconstruction, per-page ICC preservation; audit-fixed single-page path |
| v1.6.0 | 2026-07-05 | Audit-driven fixes: staging concurrency, wrapper routing, manifest Mode column, CMYK rejection, ICC alias cleanup (103 fixes) |
| v1.5.3 | 2026-04-14 | Documentation & minor fixes: OVERWRITE log accuracy, PIL pixel limit, 8-bit PNG scaling, exiftool(-k).exe detection |
| v1.5.2 | 2026-04-13 | Critical fix: 8-bit TIFF → JXL black images |
| v1.5.1 | 2026-04-12 | Refinements and stability: cmd_auto handling, _EXPORT matching, MD5 staging, Matrix ICC parsing |
| v1.5 | 2026-04-11 | Stability & reliability: full AUTO mode, PNG bit depth, ICC with --no-ram, Mode 1 dirs, EXIF preservation |
| v1.4 | 2026-04-11 | JXL→JPEG workflow: lossy/lossless conversion modes, repeat workflow fixes |
| v1.3 | 2026-04-11 | Auto Mode (Beta), Manifest System, improved TIFF preview, Python 3.8+ support |
| v1.2 | 2026-04-05 | Basic mode preserves djxl ICC, None mode, ICC mode selector, full English codebase |
| v1.1 | 2026-04-05 | D50 patch modes, metadata strip, race condition / deadlock / PPM truncation fixes |
| v1.0 | 2026-04-02 | First stable release — TIFF ↔ JXL and JPEG ↔ JXL with ICC preservation |
