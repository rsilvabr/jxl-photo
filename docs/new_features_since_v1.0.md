# New Features Since v1.0

Date: 2026-04-04
Scripts: `jxl_photo.py`, `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

---

## Summary Table

| # | Feature | Scripts | Note |
|---|---------|---------|------|
| 1 | D50 Illuminant Patch (with modes) | encoder | v1.0 had basic patch; modes (auto/on/off) are new |
| 2 | Metadata Strip Mode | encoder | Did not exist in v1.0 |
| 3 | D50 Count in OFF Mode | encoder | New tracking when patch is disabled |

**All bug fixes are documented in `bug_tracking_since_v1.0.md`.**

---

## Detailed Feature Descriptions

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

**All bugs from v1.0 are documented in `bug_tracking_since_v1.0.md`.**

Key fixes that improved robustness:
- Race conditions in staging directory (UUID added)
- Integer overflow in JXL box parser
- PPM truncation detection
- Deadlock in djxl+ImageMagick pipeline
- Distance parameter passed to cjxl correctly
- exiftool warning filtering in metadata
- lossless_jpeg=1 incompatible with distance>0
