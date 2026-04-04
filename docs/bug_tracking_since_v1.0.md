# Bug Tracking Since v1.0

Date: 2026-04-04
Scripts: `jxl_photo.py`, `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

---

## Summary Table

| # | Bug | Scripts | Status |
|---|-----|---------|--------|
| 1 | Race condition in staging (UUID) | encoder, decoder, transcoder | ✅ FIXED |
| 2 | Distance not passed to cjxl | transcoder, photo | ✅ FIXED |
| 3 | Wrong delete confirmation for lossy ops | transcoder | ✅ FIXED |
| 4 | Deadlock in djxl+ImageMagick pipeline | transcoder | ✅ FIXED |
| 5 | PPM truncation / buffer overflow | decoder | ✅ FIXED |
| 6 | Integer overflow in JXL box parser | encoder, transcoder | ✅ FIXED |
| 7 | Description capturing exiftool warnings | encoder | ✅ FIXED |
| 8 | Missing UUID in process_group_transcode | transcoder | ✅ FIXED |
| 9 | D50 patch not preserved in repeat workflow | photo | ✅ FIXED |
| 10 | Invalid --resize option in wizard | photo | ✅ REMOVED |
| 11 | cjxl --lossless_jpeg=1 incompatible with distance>0 | transcoder | ✅ FIXED |
| 12 | Strip flag not implemented | encoder | ✅ FIXED |
| 13 | Status string case inconsistency | decoder | ✅ FIXED |

---

## Detailed Bug Reports

### Bug #1 — Race Condition in Staging Directory

**Location:** `process_group()` in all scripts

**Problem:** When `TEMP2_DIR` (staging) is used, the staging filename used only `{parent_name}__{stem}.jxl` format without UUID. When two threads processed files with the same name from different folders, filename collisions could occur.

```python
# BEFORE (vulnerable)
write_jxl = staging_dir / f"{tiff.parent.name}__{tiff.stem}.jxl"
```

**Scenario:**
- Thread 1: `folder1/photo.tif` → `staging/folder1__photo.jxl`
- Thread 2: `folder2/photo.tif` → `staging/folder2__photo.jxl`
- Works if parent names differ, but breaks if same folder name

**Fix:** Added UUID to staging filename:
```python
# AFTER (fixed)
write_jxl = staging_dir / f"{uuid.uuid4().hex}_{tiff.stem}.jxl"
```

**Files affected:**
- `jxl_tiff_encoder.py` line 850
- `jxl_tiff_decoder.py` line 963
- `jxl_jpeg_transcoder.py` line 1094

---

### Bug #2 — Distance Parameter Not Passed to cjxl

**Location:** `jxl_jpeg_transcoder.py`, `jxl_photo.py`

**Problem:** When converting PNG→JXL, the `--distance` parameter was not being passed to the `cjxl` command. The user could specify `--distance` but the value was completely ignored — cjxl would use its default distance instead.

The wrapper (`jxl_photo.py`) also wasn't passing `--distance` to the transcoder for lossy conversions.

**Fix:** `jxl_jpeg_transcoder.py`:
1. Added `distance: float` parameter to `encode_to_jxl()` signature
2. Added `"-d", str(distance)` to the cjxl command
3. Added `distance` parameter to `process_group_convert()`
4. Updated call in `cmd_convert` to pass `args.distance`
5. Added `--distance` argument to the parser

`jxl_photo.py`:
6. Added `--distance` to the transcoder call for lossy conversions

**Verification:** After fix, `distance=1.0` produces visibly smaller JXL files than `distance=0.1`.

---

### Bug #3 — Wrong Delete Confirmation for Lossy Operations

**Location:** `jxl_jpeg_transcoder.py` — `cmd_transcode()` and `cmd_convert()`

**Problem (Part 1 — cmd_transcode):**
When `DELETE_SOURCE` was active for JXL→JPEG (lossy decode), the code called `confirm_deletion_jpeg()` which only requires typing "yes". For lossy operations, it should call `confirm_deletion_lossy()` which requires the current time in HHMM format, ensuring the user understands the operation is irreversible.

**Fix (Part 1):** Added check for lossy decode (`is_lossy_decode`) and calls the appropriate confirmation function:
- Lossy decode (JXL→JPEG): `confirm_deletion_lossy()` (requires HHMM)
- Lossless transcode: `confirm_deletion_jpeg()` (requires "yes")

**Problem (Part 2 — cmd_convert):**
The `cmd_convert` function ALWAYS called `confirm_deletion_lossy()` without checking if the operation was actually lossy. This forced HHMM confirmation even for lossless operations (like PNG→JXL with distance=0).

**Fix (Part 2):** Implemented proper detection logic:
- Direction "to_jxl": lossy if `args.distance > 0`
- Direction "from_jxl": lossy if format is JPEG or if ICC profile is present
- Uses `confirm_deletion_jpeg()` for lossless operations
- Uses `confirm_deletion_lossy()` only for actually lossy operations

---

### Bug #4 — Deadlock in djxl+ImageMagick Pipeline

**Location:** `jxl_jpeg_transcoder.py` — `decode_to_image()`

**Problem:** When using `subprocess.Popen` to pipe djxl output to ImageMagick, the stderr of djxl was not being consumed during magick's `communicate()`. If djxl generated many errors/warnings before magick finished, the stderr buffer would fill and djxl would block waiting for the buffer to be emptied → **deadlock**.

The comment at line 1005 said "Read stderr to prevent deadlock" but the code only read djxl's stderr AFTER `communicate()`, not during it.

```python
djxl_proc = subprocess.Popen(..., stderr=subprocess.PIPE)
magick_proc = subprocess.Popen(..., stdin=djxl_proc.stdout, stderr=subprocess.PIPE)
djxl_proc.stdout.close()
magick_stdout, magick_stderr = magick_proc.communicate(timeout=300)  # only reads magick's stderr!
djxl_stderr = djxl_proc.stderr.read()  # read AFTER, not during — deadlock possible
djxl_proc.wait()
```

**Fix:** Used a background thread to consume djxl's stderr in real-time while magick executes:
```python
def _read_stderr_thread(proc):
    proc.stderr.read()

stderr_thread = threading.Thread(target=_read_stderr_thread, args=(djxl_proc,))
stderr_thread.start()
magick_stdout, magick_stderr = magick_proc.communicate(timeout=300)
stderr_thread.join(timeout=5)
```

**Applied to:** Both JPEG and PNG output paths in `decode_to_image()`.

---

### Bug #5 — PPM Truncation / Buffer Overflow

**Location:** `jxl_tiff_decoder.py` — `read_ppm_to_numpy()`

**Problem:** The function did not validate if the PPM file was read completely. If djxl crashed during decoding, the PPM file would be truncated but the function would still try to process it, causing `ValueError` from failed reshape or worse — corrupted TIFF output.

```python
raw = f.read()  # reads whatever is there, no validation
pixel_data = np.frombuffer(raw, dtype=np.uint8)
img = pixel_data.reshape((height, width, 3))  # fails if raw is incomplete
```

**Fix:** Added expected size calculation and validation:
```python
expected_size = height * width * 3 * (2 if maxval > 255 else 1)
if len(raw) < expected_size:
    raise RuntimeError(f"PPM file truncated: got {len(raw)} bytes, expected {expected_size}")
# Defensive: trim extra data if present
if len(raw) > expected_size:
    raw = raw[:expected_size]
```

**Note:** Also added defensive trimming for cases where djxl writes extra data beyond the expected size.

---

### Bug #6 — Integer Overflow in JXL Box Parser

**Location:** `jxl_tiff_encoder.py` and `jxl_jpeg_transcoder.py` — `reorder_jxl_boxes()`

**Problem:** The function did not validate the box size before slicing. A malicious or corrupted JXL file with `size=0xFFFFFFFF` or absurd values could cause:
- MemoryError (allocating GBs of RAM)
- Infinite loops
- Data corruption

```python
size = int.from_bytes(data[i:i+4], "big")
header, payload = data[i:i+8], data[i+8:i+size]  # no validation!
```

**Fix:** Added size validation with limits:
```python
MAX_BOX_SIZE = 4 * 1024 * 1024 * 1024  # 4GB max

if size > MAX_BOX_SIZE:
    raise ValueError(f"Box size exceeds maximum: {size}")
if size > len(data) - i:
    raise ValueError(f"Invalid box size: {size} at offset {i}")
if size < 8:
    raise ValueError(f"Box size too small: {size}")
```

**Applied to:** Both `jxl_tiff_encoder.py` (line 576-627) and `jxl_jpeg_transcoder.py` (line 238-258).

---

### Bug #7 — Description Capturing exiftool Warnings

**Location:** `jxl_tiff_encoder.py` — `read_existing_description()`

**Problem:** The function captured exiftool warnings along with the actual description value. Result: metadata ended up with text like "No EXIF found | cjxl d=0.1" instead of just the encoding parameters.

```python
# BEFORE (buggy)
for line in r.stdout.splitlines():
    if not line.strip():
        continue
    if not line.startswith("Warning:"):
        description_parts.append(line)
```

**Fix:** Filter out warning lines before processing:
```python
# AFTER (fixed)
for line in r.stdout.splitlines():
    stripped = line.strip()
    if not stripped:
        continue
    # Filter exiftool warnings
    if stripped.startswith(("Warning:", "[minor]", "[major]")):
        continue
    description_parts.append(stripped)
```

If only warnings remain, returns empty string instead of garbled text.

---

### Bug #8 — Missing UUID in process_group_transcode

**Location:** `jxl_jpeg_transcoder.py` — `process_group_transcode()`

**Problem:** The function used `f"{src.parent.name}__{src.stem}{ext}"` for staging filenames instead of UUID. This caused race conditions when files with the same name came from different folders (e.g., `folder1/photo.jpg` and `folder2/photo.jpg`).

```python
# BEFORE (vulnerable — same as bug #1 but in transcode path)
write_path = staging_dir / f"{src.parent.name}__{src.stem}{ext}"
```

**Fix:** Changed to use `uuid.uuid4().hex` like the rest of the codebase:
```python
write_path = staging_dir / f"{uuid.uuid4().hex}_{src.stem}{ext}"
```

---

### Bug #9 — D50 Patch Not Preserved in Repeat Workflow

**Location:** `jxl_photo.py` — repeat workflow (option 2)

**Problem:** When the user chose "Repeat last workflow" (option 2), the `advanced_options` was recreated from scratch with only `overwrite` and `sync`, losing the `d50_patch` and `encode_tag` settings that were configured previously.

**Fix:** Preserved values from last workflow when available:
```python
# Copiar d50_patch do last_advanced se origin for 'tiff'
d50_patch = last_advanced.get('d50_patch', 'auto') if origin == 'tiff' else None
# Copiar encode_tag do last_advanced se origin for 'tiff'
encode_tag = last_advanced.get('encode_tag', 'xmp') if origin == 'tiff' else None
```

---

### Bug #10 — Invalid --resize Option in Wizard

**Location:** `jxl_photo.py` — wizard for TIFF→JXL

**Problem:** The wizard accepted a `--resize` option in Step 6A, stored it in `advanced_options['resize']`, and even tried to pass it to the encoder command. However, none of the scripts (`jxl_tiff_encoder.py`, `jxl_jpeg_transcoder.py`, `jxl_tiff_decoder.py`) actually support a `--resize` flag.

The option was collected but did nothing — misleading to users.

**Fix:** Removed the `--resize` option from the wizard entirely.

---

### Bug #11 — cjxl --lossless_jpeg=1 Incompatible with distance>0

**Location:** `jxl_jpeg_transcoder.py` — `encode_to_jxl()`

**Problem:** cjxl 0.11.2 defaults to `--lossless_jpeg=1` (preserves JPEG data for lossless transcode). However, `--lossless_jpeg=1` is **incompatible** with `distance>0` (lossy mode). Attempting JPEG→JXL lossy conversion caused error:

```
cjxl: Must not set non-zero distance in combination with --lossless_jpeg=1, which is set by default.
```

The toolkit was not passing `--lossless_jpeg=0` when `distance>0`, causing all lossy JPEG→JXL conversions to fail.

**Fix:** Added check in `encode_to_jxl()`:
```python
if distance > 0:
    cmd.append("--lossless_jpeg=0")
```

This allows cjxl to recompress the JPEG data with the specified quality instead of preserving the original.

**Verification:**
```
DSC00004_AdobeRGB_v1.jpg → DSC00004_AdobeRGB_v1.jxl (lossy, distance=1.0)
✅ Converted successfully
```

---

### Bug #12 — Strip Flag Not Implemented

**Location:** `jxl_tiff_encoder.py` — `build_metadata_injection_args()`

**Problem:** The CLI accepted `--strip` flag, but it did nothing. Even with the flag set, all EXIF/XMP metadata was preserved in the output JXL. The `STRIP_METADATA` global existed but was never actually used to modify the exiftool arguments.

**Root Cause:** The function `build_metadata_injection_args()` completely ignored the `strip_metadata` parameter that was passed to it. It always ran the full metadata preservation logic regardless of the flag.

```python
# BEFORE (buggy)
def build_metadata_injection_args(tiff_path, write_path, tmp_dir, ...):
    # strip_metadata parameter accepted but never checked!
    args_lines = ["-overwrite_original"]
    # ... full EXIF/XMP preservation logic always ran
```

**Fix:** Added proper conditional logic:
```python
# AFTER (fixed)
def build_metadata_injection_args(..., strip_metadata=False):
    args_lines = ["-overwrite_original"]
    
    if strip_metadata:
        # Only encoding params, no metadata
        encoding_desc = f"cjxl d={CJXL_DISTANCE} e={CJXL_EFFORT}"
        args_lines.append(f"-xmp-dc:Description={encoding_desc}")
        args_lines.append("-exif:all=")  # Strip all EXIF
        args_lines.append("-xmp:all=")   # Strip all XMP
        # ... return early
    
    # Normal metadata preservation only runs if NOT stripping
```

**Files affected:**
- `jxl_tiff_encoder.py` — Added `STRIP_METADATA` global, updated `build_metadata_injection_args()`, added CLI argument

---

### Bug #13 — Status String Case Inconsistency

**Location:** `jxl_tiff_decoder.py` — `convert_one()` and `process_group()`

**Problem:** `convert_one()` returned status strings in UPPERCASE ("OK", "overwrite"), but `process_group()` checked against lowercase ("ok", "overwrite"). This caused silent failures in status tracking — files that converted successfully were sometimes treated as errors because the string didn't match.

```python
# BEFORE (inconsistent)
def convert_one(...):
    status = "overwrite" if overwritten else "OK"  # "OK" is uppercase!
    return str(jxl_path), status, str(final_path)

def process_group(...):
    for result in results:
        status = result[1]
        if status not in ("ok", "overwrite"):  # Checks lowercase!
            # "OK" would fail this check and be treated as error
```

**Fix:** Standardized on lowercase for internal status, uppercase only for display:
```python
# AFTER (consistent)
def convert_one(...):
    status = "overwrite" if overwritten else "ok"  # lowercase
    label = "OVERWRITE" if overwritten else "OK"   # uppercase for UI
    logger.info(f"[{n}/{total}] {label} | ...")     # display uses label
    return str(jxl_path), status, str(final_path)  # return uses status
```

**Impact:** Fixed silent failures where successful conversions were logged as errors due to case mismatch.

---

## Scripts Affected

- `jxl_jpeg_transcoder.py` — Bug fixes #1, #2, #3, #4, #6, #8, #11
- `jxl_tiff_encoder.py` — Bug fixes #1, #5, #6, #7, #12
- `jxl_tiff_decoder.py` — Bug fixes #1, #5, #13
- `jxl_photo.py` — Bug fixes #2, #9, #10

---

## New Features Added

### D50 Illuminant Patch (TIFF→JXL)
- Configurable via `--d50-patch` CLI flag (on/off/auto)
- `D50_PATCH_MODE` setting in encoder (default: "auto")
- Auto-detects Capture One exports via EXIF Software field
- Fixes ICC rounding errors that cause cjxl warnings
- Statistics shown in conversion summary (applied/skipped count)

### Lossy JPEG→JXL Conversion
- Now works correctly with cjxl 0.11.2
- Added `--lossless_jpeg=0` when distance>0