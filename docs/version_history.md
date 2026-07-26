# Version history

Full "What's New" notes for releases before v1.8. The current release is
documented in the [main README](../README.md); per-release notes are published
as [GitHub Releases](https://github.com/rsilvabr/jxl-photo/releases).

For the complete list of individual fixes see
[bug_tracking_since_v1.0.md](bug_tracking_since_v1.0.md) and
[new_features_since_v1.0.md](new_features_since_v1.0.md).

---

## What's New in v1.7

### Multi-Page TIFF Support
TIFFs with more than one page are now handled explicitly instead of silently discarding extra pages.

**TIFF → JXL encoder:**
- `--multipage-mode ignore` — encode only page 0 (original behavior, default)
- `--multipage-mode skip` — skip files that have more than one "real" page
- `--multipage-mode split` — encode each real page to a separate JXL (`photo.jxl`, `photo_page2.jxl`, ...)
- `--multipage-mode split_all` — encode every page, including thumbnails

Thumbnails are detected via standard TIFF `SubfileType` flags (`is_reduced` / `is_subifd`). When splitting, thumbnails can be excluded or included with a configurable suffix (`_thumbnail` by default).

**JXL → TIFF decoder:**
- Reconstructs multi-page TIFFs from pages that carry the encoder's XMP group marker (`jxlphoto-mpg:` in `XMP-dc:Relation`). Grouping is marker-based, not name-based, so independently-named files such as `scan.jxl` + `scan_page2.jxl` are never merged unless they were split by this encoder.
- Preserves per-page ICC profiles: each page is restored with its own ICC tag; pages that inherited ICC from IFD0 are reconstructed without an ICC tag, matching the original TIFF structure.
- Preserves grayscale pages and `SubfileType` role: single-channel pages are reconstructed as 2D grayscale, and inherited RGB ICC is not forced onto them. Non-standard `SubfileType` values (e.g. scanner IR/mask pages) are restored as `PAGE` semantics.
- Per-page bit depth policy: main pages stay 16-bit while 8-bit thumbnails are restored as 8-bit by default (`--depth-policy preserve_thumbnails`). Use `force16` for all 16-bit output or `preserve_original` to keep every page at its original bit depth.
- `--thumbnail-handling ignore` — ignore `_thumbnail.jxl` files
- `--thumbnail-handling include` — include thumbnails in the reconstructed TIFF (default)
- `--thumbnail-handling generate` — not yet implemented; falls back to `include`
- `--no-reconstruct-multipage` — disable multi-page reconstruction entirely

```bash
# Photos with main image + thumbnail → split JXLs
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode split --thumbnail-mode include --distance 0

# Reconstruct the original multi-page TIFF
python jxl_tiff_decoder.py "E:\photos_jxl" "E:\photos_reconstructed" --mode 2 --thumbnail-handling include

# Film scanner workflow with IR/mask page (grayscale)
python jxl_tiff_encoder.py "E:\film_scans" "E:\film_scans_jxl" --mode 2 --multipage-mode split_all --thumbnail-mode include
python jxl_tiff_decoder.py "E:\film_scans_jxl" "E:\film_scans_tiff" --mode 2 --thumbnail-handling include
```

> **⚠️ IR channel / Digital ICE warning:** If your scanner software (e.g. SilverFast, VueScan) uses the IR page as a hidden channel for Digital ICE / dust & scratch removal, converting the TIFF to JXL and back may break that feature. Those programs often rely on vendor-specific tags and exact page ordering beyond the standard TIFF `SubfileType`. This tool preserves the page as a standard grayscale `PAGE`, but the original scanner software may no longer recognize it as an IR mask. Test with one file before batch-processing important film scans.

JPEG previews are automatically skipped when reconstructing multi-page TIFFs.

> **Scanner color profile note:** Scanner ICC profiles (e.g. SilverFast `SFprofT`) can cause `cjxl` to produce very dark images in lossy mode. The encoder works around this by not embedding the ICC in the intermediate PNG and restoring it into the reconstructed TIFF. The JXL file may therefore display with shifted colors in some viewers, but the TIFF round-trip is accurate. For scanner workflows, treat JXL as the backup container and the reconstructed TIFF as the final image.

### v1.7.1 / v1.7.2

- **Cautious ICC strategy (v1.7.1)** — the default `--icc-png-strategy cautious` round-trip-tests each unseen ICC profile through cjxl+djxl and caches the verdict, so scanner profiles that darken lossy encodes are skipped automatically.
- **Audit fixes (v1.7.1)** — a batch crash on JXLs without `jbrd` became a per-file error, lossy convert preserves EXIF/XMP/IPTC via exiftool, `.jfif`/`.jpe` support, `--multipage-mode skip` uses the detected real page.
- **v1.7.2** — the wrapper's `--delete-source` confirmation no longer gets stuck on the main wizard path, and lossy convert keeps Exif/XMP before the codestream after the metadata copy (IrfanView-compatible).

Full release notes: [v1.7.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.1) · [v1.7.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.2)

---

## What's New in v1.6

### Audit-Driven Fixes
This release fixes issues found during an independent audit of v1.5.3:

- **Encoder staging concurrency fixed** — failed outputs no longer get promoted when using multiple workers
- **Auto mode routing fixed** — JPEG folders are now encoded, not decoded; `--format jpeg` actually produces JPEGs
- **Manifest modes preserved** — generated manifests now include a `Mode` column, so modes 6 and 7 survive execution
- **Basic mode 16-bit fidelity** — PNG decode now preserves full 16-bit data
- **Matrix mode for libjxl v0.11.x** — uses the correct `--color_space` token
- **CMYK TIFFs rejected early** — no more silent RGBA mis-encoding
- **Wizard cleanup** — removed non-functional "skip" option; custom Target ICC asks for the real file path

### ICC Alias Cleanup
Only `sRGB` remains as a built-in ICC alias. `Adobe RGB` and `ProPhoto RGB` aliases were removed because Pillow cannot generate them on the fly; use actual `.icc` profile files instead.

---

## What's New in v1.5

### JXL → JPEG Auto-Detect Mode
**Smart per-file detection** for mixed JXL archives:
- Files **with jbrd box** → lossless transcoding (original JPEG recovered)
- Files **without jbrd** → lossy conversion with configurable quality
- Processes entire folders automatically, routing each file to the optimal method

### Optional JPEG Preview in TIFF Output
JXL → TIFF conversion now supports **disabling the embedded JPEG preview**:
```bash
python jxl_tiff_decoder.py folder/ --no-preview  # Smaller files, no preview
```
Default behavior unchanged (preview enabled for compatibility).

### Refined JXL → JPEG Options
Step 2 now offers three clear choices:
1. **JPEG Auto-Detect** — Recommended (auto-routes based on jbrd presence)
2. **JPEG Lossless** — Force lossless transcoding (requires jbrd)
3. **JPEG Lossy** — Force lossy conversion with quality/ICC control

---

## What's New in v1.3

### Auto Mode + Manifest System

> **Beta:** Auto Mode is functional but still being tested. If you encounter issues, use manual mode selection (options 0-8) which is fully stable.

**[A] Auto Mode** analyzes your folder structure and recommends the best organization mode automatically:
- Detects `_EXPORT`, `Export_Lightroom`, etc. (case-insensitive)
- Shows folder mapping preview before running
- Recommends mode with confidence level (high/medium/low)

**[P] Manifest CSV** — Generate, edit in Excel, then run:
```
[A] Auto Mode → [P] Generate manifest → Edit in Excel → [M] Run from manifest
```

- Edit paths, delete rows, reorder before running
- Comment out lines with `#` to skip temporarily
- Manifests saved in `manifests/` folder — rerun anytime
- Use with `--sync` to re-process only changed files

### Embedded JPEG Thumbnail in JXL (Optional)
Optional embedded 256px sRGB thumbnail in JXL files for fast preview in IrfanView, XnView, digiKam.
```bash
python jxl_tiff_encoder.py folder/ --embed-thumbnail
```
Adds ~20KB per file.

**Windows Explorer Note:** The current JXL WIC codec from Microsoft Store generates its own thumbnail and **ignores the embedded EXIF thumbnail**. Worse, it does so **without color management** — so if your image uses ProPhoto RGB or Adobe RGB, the thumbnail will show wrong/washed-out colors. This is a **Windows codec limitation, not a bug in this software**. Use IrfanView, XnView MP, or digiKam for accurate thumbnails.

**IrfanView Note:** EXIF display in JXL has limitations with this software:

| Source | JXL Type | EXIF in IrfanView | Why |
|--------|----------|-------------------|-----|
| **TIFF → JXL** | Lossless | ✅ Shows | Boxes reordered (Exif before codestream) |
| **JPEG → JXL** | Lossless | ❌ Hidden | Brotli compression (`brob` box) - IrfanView can't read |
| **JPEG → JXL** | Lossy | ❌ Hidden | Brotli compression (`brob` box) - IrfanView can't read |

For reliable EXIF viewing regardless of source, use **XnView MP** or **digiKam**.

**Wide-gamut Note:** On an Adobe RGB calibrated monitor, IrfanView and XnView MP may display wide-gamut JXL files (e.g. ProPhoto RGB) with a slightly muted appearance — comparable to the difference between an original Adobe RGB file and the same file properly converted to sRGB. The most vibrant colors that extend beyond sRGB may appear dulled. This is a **subtle viewer rendering limitation**, not data loss — the JXL file still holds the full gamut intact. If the image looks *heavily* desaturated, that is a real bug. You can verify preservation by decoding the JXL back to TIFF: the round-trip TIFF will show the original vibrant colors again in any color-managed editor. See [docs/jxl_color_internals.md](jxl_color_internals.md) for details.

---
