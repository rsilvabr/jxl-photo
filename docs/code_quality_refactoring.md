# Code Quality & Refactoring Log

This document tracks changes that improve code maintainability, compatibility, and clarity **without changing user-facing behavior**. These are not bugs or features — they are internal improvements, cleanups, and backports.

---

## Python Compatibility

### Python 3.8+ Backports

**Items:** #18, #24, #34  
**Files:** `jxl_photo.py`, `jxl_photo_v2.py`, `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

**Changes:**
- Replaced `Path.is_relative_to()` with a `_is_relative_to()` backport function (Python 3.8 compatible).
- Replaced `str | None` type-hint syntax with `Optional[str]` (Python 3.8 compatible).

**Why:** The project supports Python 3.8+, but some modern syntax was introduced inadvertently. These backports ensure compatibility without changing runtime behavior.

---

## Code Style & Safety

### Bare Except Clauses

**Item:** #21  
**Files:** All Python scripts

**Change:** Replaced 12 bare `except:` clauses with `except Exception:` or more specific exception types.

**Why:** Bare `except:` catches `KeyboardInterrupt` and `SystemExit`, making it impossible to cancel operations with Ctrl+C. This is a code-quality fix — no functional bug was caused by the old code under normal operation.

---

### Boolean Comparison Style

**Item:** #50  
**Files:** `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`

**Change:** Replaced `if OVERWRITE == False:` with `if OVERWRITE is False:`.

**Why:** `== False` could match `0` or other falsy values. Using `is False` is semantically correct for explicit boolean flags.

---

## Cleanup & Dead Code

### Redundant Import

**Item:** #32  
**File:** `jxl_photo.py`

**Change:** Removed redundant `from pathlib import Path` inside `_execute_manifest_workflow()`. The function already had access to the module-level `Path` import.

---

### Dead Counter in D50 Patch

**Item:** #66  
**File:** `jxl_tiff_encoder.py`

**Change:** Documented that `_d50_patch_count["skipped"]` is initialized and read but never incremented. The actual tracking uses `skipped_needed` and `already_correct` instead.

**Why:** This is dead code. It does not affect output (the value is always 0), but it should be noted for future cleanup.

---

## Documentation & Comments

### Misplaced ICC Comments

**Item:** #48  
**File:** `jxl_tiff_encoder.py`

**Change:** Moved ICC-related comments from the `D50_PATCH_SOFTWARE_LIST` block to the correct `EMBED_ICC_IN_JXL` block.

**Why:** Pure documentation fix — no code behavior changed.

---

### Docstring Correction

**Item:** #49  
**File:** `jxl_tiff_decoder.py`

**Change:** Corrected the module docstring from "JPEG as page 0, 16-bit as page 1" to "16-bit as page 0 (primary), JPEG preview as page 1 (thumbnail flag)".

**Why:** The docstring did not match the actual code behavior. This is a documentation fix.

---

## External Tool Compatibility

### djxl 0.11.x Compatibility

**Item:** #78  
**File:** `jxl_jpeg_transcoder.py`

**Change:** Removed `--output_format` flag from djxl calls because it is not supported in djxl 0.11.x.

**Why:** Adaptation to the specific libjxl version. The code behaves identically; it simply avoids passing an unsupported flag to the external binary.

---

## Summary

| # | Change | Type |
|---|--------|------|
| 18 | `is_relative_to` backport (`jxl_photo_v2.py`) | Compatibility |
| 21 | Bare except → `except Exception` | Style/Safety |
| 24 | `is_relative_to` backport (all scripts) | Compatibility |
| 32 | Remove redundant `import Path` | Cleanup |
| 34 | `str \| None` → `Optional[str]` | Compatibility |
| 48 | Move ICC comments to correct block | Documentation |
| 49 | Fix TIFF docstring order | Documentation |
| 50 | `== False` → `is False` | Style |
| 66 | Note dead code in D50 counter | Cleanup |
| 78 | Remove unsupported `--output_format` | Tool Compatibility |
