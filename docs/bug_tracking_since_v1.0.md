# Bug Tracking Since v1.0

Date: 2026-04-04  
v1.2 Update: 2026-04-05  
v1.3 Update: 2026-04-11  
v1.5 Update: 2026-04-12  
v1.5 Final: 2026-04-12 (third pass)  
v1.5.2: 2026-04-13 (critical 8-bit fix)  
Scripts: `jxl_photo.py`, `jxl_photo_v2.py`, `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`  
**Note:** `jxl_tiff_decoder.py` was completely rebuilt in v1.3 (improved Windows Explorer support, file integrity checks, Python 3.8 compatibility). Original v1 preserved in `deprecated/`.

---

## Summary Table

| # | Bug/Improvement | Scripts | Status |
|---|-----------------|---------|--------|
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
| 14 | Basic mode now preserves djxl ICC | decoder | ✅ IMPROVED (v1.2) |
| 15 | Missing method in manifest workflow | photo_v2 | ✅ FIXED (v1.3) |
| 16 | Race condition in TIFF deletion | encoder | ✅ FIXED (v1.3) |
| 17 | Deadlock in djxl+magick pipeline | transcoder | ✅ FIXED (v1.3) |
| 18 | Python 3.9+ compatibility | photo_v2 | ✅ FIXED (v1.3) |
| 19 | MD5 verification after reconvert | transcoder | ✅ FIXED (v1.3) |
| 20 | Missing subprocess timeout | photo_v2 | ✅ FIXED (v1.3) |
| 21 | Bare except clauses | all scripts | ✅ FIXED (v1.3) |
| 22 | Race condition in JXL deletion | decoder | ✅ FIXED (v1.3) |
| 23 | Race condition in source deletion | transcoder | ✅ FIXED (v1.3) |
| 24 | Python 3.9+ compatibility (all scripts) | encoder, decoder, transcoder | ✅ FIXED (v1.3) |
| 25 | CJXL_DISTANCE range validation | encoder | ✅ FIXED (v1.3) |
| 26 | ExifTool timeout | encoder, transcoder | ✅ FIXED (v1.3) |
| 27 | `--distance 95` passed to transcoder (invalid) | photo | ✅ FIXED (v1.3) |
| 28 | Scripts resolved by relative path (CWD dependency) | photo | ✅ FIXED (v1.3) |
| 29 | `_show_mode_details_and_select` returns string not bool | photo | ✅ FIXED (v1.3) |
| 30 | Timeout not handled in subprocess | photo | ✅ FIXED (v1.3) |
| 31 | Set unordered in checksum DB path | transcoder | ✅ FIXED (v1.3) |
| 32 | `import Path` redundant in `_execute_manifest_workflow` | photo | ✅ FIXED (v1.3) |
| 33 | Rich markup leaking in fallback without Rich | photo | ✅ FIXED (v1.3) |
| 34 | Type hint `str \| None` (Python < 3.10 incompatible) | transcoder | ✅ FIXED (v1.3) |
| 35 | JXL→JPEG auto mode not working for directories | transcoder, photo | ✅ FIXED (v1.5) |
| 36 | Quality/sRGB asked for lossless JXL→JPEG | photo | ✅ FIXED (v1.4) |
| 37 | Repeat last workflow not saving dest format | photo | ✅ FIXED (v1.4) |
| 38 | Step 2 TIFF option misnumbered | photo | ✅ FIXED (v1.4) |
| 39 | JXL→TIFF preview not configurable | decoder, photo | ✅ FIXED (v1.5) |
| 40 | TIFF encoder Mode 3 using wrong folder constant | encoder | ✅ FIXED (v1.5) |
| 41 | TIFF encoder Mode 0 searching JPEG files | encoder | ✅ FIXED (v1.5) |
| 42 | Variable 'skipped' overwritten in encoder summary | encoder | ✅ FIXED (v1.5) |
| 43 | Manifest missing flags vs execute_workflow | photo | ✅ FIXED (v1.5) |
| 44 | Duplicate --format flag in transcoder | photo | ✅ FIXED (v1.5) |
| 45 | Thumbnail fallback using undefined PIL | encoder | ✅ FIXED (v1.5) |
| 46 | D50 summary uses wrong variable | encoder | ✅ FIXED (v1.5) |
| 47 | Thumbnail return aborts conversion | encoder | ✅ FIXED (v1.5) |
| 48 | ICC comments misplaced | encoder | ✅ FIXED (v1.5) |
| 49 | TIFF docstring order wrong | decoder | ✅ FIXED (v1.5) |
| 50 | OVERWRITE == False should be 'is' | encoder, decoder | ✅ FIXED (v1.5) |
| 51 | --effort CLI ignored in transcode | transcoder | ✅ FIXED (v1.5) |
| 52 | bit_depth hardcoded in auto mode | transcoder | ✅ FIXED (v1.5) |
| 53 | ICC ignored with --no-ram | transcoder | ✅ FIXED (v1.5) |
| 54 | has_jbrd_box naive detection | transcoder | ✅ FIXED (v1.5) |
| 55 | jxl_to_jpeg_lossless missing --decode | photo | ✅ FIXED (v1.5) |
| 56 | jxl_to_jpeg_force missing --decode | photo | ✅ FIXED (v1.5) |
| 57 | jxl_to_png generates JPEG with jbrd | photo | ✅ FIXED (v1.5) |
| 58 | Repeat workflow loses thumbnail | photo | ✅ FIXED (v1.5) |
| 59 | Log shows wrong effort value | transcoder | ✅ FIXED (v1.5) |
| 60 | bit_depth=None in auto mode | transcoder | ✅ FIXED (v1.5) |
| 61 | Thumbnail fallback double except | encoder | ✅ FIXED (v1.5) |
| 62 | Extended size box not appended | encoder, transcoder | ✅ FIXED (v1.5) |
| 63 | EXIF binary corrupted by text=True | transcoder | ✅ FIXED (v1.5) |
| 64 | Mode 1 directory behavior wrong | encoder | ✅ FIXED (v1.5) |
| 65 | extract_exif_raw r.stdout None check | encoder | ✅ FIXED (v1.5) |
| 66 | _d50_patch_count["skipped"] never incremented | encoder | ⚠️ DEAD CODE |
| 67 | --no-ram never works | encoder | ✅ FIXED (v1.5) |
| 68 | strip_metadata deletes Description | encoder | ✅ FIXED (v1.5) |
| 69 | PNG 8-bit shift results in zeros | decoder | ✅ FIXED (v1.5) |
| 70 | cleanup_xmp_icc duplicates label | decoder | ✅ FIXED (v1.5) |
| 71 | --format jpg falls to PNG path | transcoder | ✅ FIXED (v1.5) |
| 72 | _d50_patch_count["skipped"] never incremented | encoder | ✅ FIXED (v1.5.1) |
| 73 | Image.open() sem with/close (file leak) | decoder | ✅ FIXED (v1.5.1) |
| 74 | Basic mode PIL pode perder 16-bit | decoder | ✅ FIXED (v1.5.1) |
| 75 | MD5 checksums gravados com UUID (staging) | transcoder | ✅ FIXED (v1.5.1) |
| 76 | decode_to_image retorna staging path | transcoder | ✅ FIXED (v1.5.1) |
| 77 | JPEG 16-bit→PNG sem atualizar final_path | transcoder | ✅ FIXED (v1.5.1) |
| 78 | --output_format não suportado djxl 0.11.x | transcoder | ✅ FIXED (v1.5.1) |
| 79 | subfolders strings com .name (AttributeError) | photo | ✅ FIXED (v1.5.1) |
| 80 | dest_path do manifest nunca usado | photo | ✅ FIXED (v1.5.1) |
| 81 | ICC TRC parsing s15Fixed16Number como float | decoder | ✅ FIXED (v1.5.1) |
| 82 | cmd_auto usa resolver errado para convert | transcoder | ✅ FIXED (v1.5.1) |
| 83 | EXPORT_MARKER substring match inconsistente | encoder, decoder, transcoder | ✅ FIXED (v1.5.1) |
| 84 | resolve_output_convert parâmetros trocados | transcoder | ✅ FIXED (v1.5.1) |
| 85 | EXPORT_MARKER find_* vs resolve_output inconsistente | encoder, transcoder | ✅ FIXED (v1.5.1) |
| 86 | Retornos inconsistentes 3 vs 4 elementos | transcoder | ✅ FIXED (v1.5.1) |
| 87 | 8-bit TIFF → JXL imagens pretas (escalonamento) | encoder | ✅ FIXED (v1.5.2) |

**Total bugs fixed: 87**

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

### Improvement #14 — Basic Mode Now Preserves djxl ICC (v1.2)

**Location:** `jxl_tiff_decoder.py` — Basic decode mode

**Note:** This is an improvement, not a bug fix. The old behavior (discarding ICC) was technically "working as designed" but was not useful for most workflows.

**Problem with old Basic mode (v1.0 - v1.1):**
- Decoded to PPM format (which has no ICC support)
- Output TIFF had no ICC profile attached
- Was only useful for web/sRGB workflows where color accuracy didn't matter

**Improvement (v1.2):**
- Decodes to PNG format to capture ICC profile generated by djxl
- Extracts ICC from PNG and attaches to output TIFF
- Now the default behavior when no XMP ICC is present
- Makes more sense for most workflows

**New None Mode:**
The old "discard ICC" behavior is still available as the **None** mode (`--none` flag or `FORCE_NONE_MODE = True`).

**Technical changes:**
1. Added `decode_auto_png()` function for PNG output
2. Added `extract_icc_from_png()` function using PIL
3. Added `read_png_to_numpy()` function for PNG→numpy conversion
4. Changed Basic mode to use PNG intermediate instead of PPM
5. Added `--none` CLI flag for the old behavior
6. Updated `select_decode_strategy()` to use "basic" as default (not "none")

**Migration:**
- Users who relied on Basic mode producing no ICC should now use `--none`
- Default behavior (no flags) now produces better results for most users


## v1.3 Release Notes (2026-04-11)

### File Reorganization (Decoder)

**Change:** TIFF decoder completely rebuilt in v1.3

**Previous versions (deprecated):**
- `jxl_tiff_decoder_v1_old.py` — Original decoder (JPEG preview as page 1, no ICC in preview)

**Current official (v1.3):**
- `jxl_tiff_decoder.py` — Completely rebuilt decoder

**Key improvements:**
| Feature | Old (v1) | Current (v1.3) |
|---------|----------|----------------|
| JPEG Preview | Page 1 (secondary) | Page 1 (with NEWSubfileType=1 flag) |
| Windows Explorer | Generic icon | **Color-managed thumbnail** |
| Preview colorspace | ❌ Original (needs ICC, shows wrong colors) | ✅ **sRGB converted** (correct colors without ICC) |
| ICC in main image | ✅ Yes | ✅ Yes |
| ICC in preview | ❌ No (needed - not sRGB) | ✅ No (not needed — sRGB native) |
| File integrity verification | ❌ No | ✅ Yes (before source deletion) |
| Python 3.8 compatibility | ❌ No | ✅ Yes (backport included) |

**Why it matters:**
The old decoder saved JPEG preview as a secondary page (page 1), which Windows Explorer ignored. The new decoder saves JPEG as page 0 with `NEWSubfileType=1` (thumbnail flag), making Windows Explorer display the correct color-managed preview immediately.

**Migration:**
- Use `jxl_tiff_decoder.py` (current official)
- Old versions preserved in `deprecated/` for reference only

---
### Bug #15 — Missing Method in Manifest Workflow

**Location:** `jxl_photo_v2.py` — `_execute_manifest_workflow()`

**Problem:** Line 2168 called `self._detect_mode_for_entry()`, but this method doesn't exist in the `InteractiveMenu` class — it belongs to `FolderAnalyzer`. This would cause an `AttributeError` crash when using manifest mode (mode 99).

```python
# BEFORE (buggy)
for i, (source, dest_path) in enumerate(manifest_entries, 1):
    detected_mode = self._detect_mode_for_entry(source, dest_path)  # Method doesn't exist!
```

**Fix:** Create a `FolderAnalyzer` instance outside the loop and call the correct method:
```python
# AFTER (fixed)
analyzer = FolderAnalyzer(Path("."), origin, dest, self.config.config.export_marker)

for i, (source, dest_path) in enumerate(manifest_entries, 1):
    detected_mode = analyzer.detect_mode_for_entry(source, dest_path)
```

---

### Bug #16 — Race Condition in TIFF Deletion

**Location:** `jxl_tiff_encoder.py` — `process_group()`

**Problem:** When `DELETE_SOURCE` is enabled, the code deleted source TIFF files after checking if the JXL exists, but without verifying the JXL file integrity. A corrupted or incomplete JXL could pass the `exists()` check, leading to data loss.

```python
# BEFORE (vulnerable)
if final_jxl is None or not final_jxl.exists():
    continue
src_tiff.unlink()  # Deletes even if JXL is corrupted!
```

**Fix:** Added `_verify_jxl_integrity()` function that checks:
1. File exists and size > 0
2. Valid JXL signature (0xFF 0x0A for bare JXL or ISOBMFF container)

```python
# AFTER (fixed)
def _verify_jxl_integrity(jxl_path: Path) -> bool:
    # Check file exists, size > 0, and valid JXL header
    ...

if not _verify_jxl_integrity(final_jxl):
    logger.warning(f"  KEEP (JXL failed integrity check) | {src_tiff.name}")
    continue
src_tiff.unlink()
```

---

### Bug #17 — Deadlock in djxl+ImageMagick Pipeline

**Location:** `jxl_jpeg_transcoder.py` — `decode_to_image()`

**Problem:** When piping djxl output to ImageMagick via `subprocess.Popen`, the stderr of djxl was not being consumed while magick was running. If djxl generated many errors, its stderr buffer would fill and block, causing a deadlock.

**Fix:** Created `_run_pipeline_safe()` helper function that:
1. Reads stderr from both processes in separate threads
2. Prevents buffer deadlock
3. Implements proper timeout handling (300s default)
4. Cleans up processes on timeout

Applied to both JPEG and PNG conversion paths that use RAM mode with ICC profiles.

---

### Bug #18 — Python 3.9+ Compatibility (is_relative_to)

**Location:** `jxl_photo_v2.py` — Multiple locations

**Problem:** Code used `Path.is_relative_to()` method which only exists in Python 3.9+. This caused `AttributeError` on Python 3.8 systems.

```python
# BEFORE (Python 3.9+ only)
rel_src = Path(src).relative_to(input_dir) if Path(src).is_relative_to(input_dir) else Path(src)
```

**Fix:** Added `_is_relative_to()` backport function:
```python
# AFTER (Python 3.8+ compatible)
def _is_relative_to(path: Path, anchor: Path) -> bool:
    try:
        path.relative_to(anchor)
        return True
    except ValueError:
        return False

rel_src = Path(src).relative_to(input_dir) if _is_relative_to(Path(src), input_dir) else Path(src)
```

**Note:** Tested with complex paths containing spaces, brackets, and Unicode characters (吾妻山公園).

---

### Bug #19 — MD5 Verification After Reconversion

**Location:** `jxl_jpeg_transcoder.py` — `read_md5_db()`

**Problem:** When a file was reconverted (overwritten), a new MD5 entry was appended to the checksums file. However, `read_md5_db()` read from top to bottom and returned the **first** matching entry, which was the old (stale) MD5. This caused false MD5 verification failures after reconversion.

```python
# BEFORE (buggy)
for line in f:  # Reads top-to-bottom
    if stored_name == target:
        return stored_hash  # Returns first (old) entry
```

**Fix:** Read from bottom to top to get the most recent entry:
```python
# AFTER (fixed)
lines = f.readlines()
for line in reversed(lines):  # Reads bottom-to-top
    if stored_name == target:
        return stored_hash  # Returns last (newest) entry
```

---

### Bug #20 — Missing Subprocess Timeout

**Location:** `jxl_photo_v2.py` — `_run_subprocess()` and `execute_workflow()`

**Problem:** Two `process.wait()` calls had no timeout, which could cause infinite hangs if the subprocess froze.

**Fix:** Added `timeout=3600` (1 hour) to both `process.wait()` calls:
- Line ~2347: `_run_subprocess()` method
- Line ~2531: `execute_workflow()` method

---

### Bug #21 — Bare Except Clauses

**Location:** All Python scripts

**Problem:** Multiple bare `except:` clauses throughout the codebase that catch all exceptions including `KeyboardInterrupt` and `SystemExit`, making it impossible to cancel operations with Ctrl+C.

**Files affected and fixes:**
- `jxl_photo.py`: 4 bare excepts → `except Exception:` or `except ValueError:`
- `jxl_photo_v2.py`: 6 bare excepts → `except Exception:` or `except ValueError:`
- `jxl_tiff_decoder.py`: 1 bare except → `except Exception:`
- `jxl_tiff_decoder_v2.py`: 1 bare except → `except Exception:`

**Total:** 12 bare except clauses fixed across 4 files.

---

### Bug #22 — Race Condition in JXL Deletion (Decoder)

**Location:** `jxl_tiff_decoder.py` — `process_group()`

**Problem:** Similar to bug #16, the decoder deleted source JXL files after checking if TIFF exists, but without verifying TIFF integrity. A corrupted or incomplete TIFF could pass the `exists()` check.

**Fix:** Added `_verify_tiff_integrity()` function that checks:
1. File exists and size > 0
2. Valid TIFF signature (II/MM + 42)
3. Can be opened by tifffile

---

### Bug #23 — Race Condition in Source Deletion (Transcoder)

**Location:** `jxl_jpeg_transcoder.py` — `cmd_transcode()` and `cmd_convert()`

**Problem:** The transcoder deleted source files without verifying output file integrity in convert mode. MD5 verification only worked for transcode mode, not convert mode.

**Fix:** Added `_verify_file_integrity()` function that validates file headers based on extension:
- JXL: 0xFF 0x0A or ISOBMFF container
- JPEG: 0xFFD8 (SOI marker)
- PNG: PNG signature
- TIFF: II/MM + 42

Applied to both transcode and convert deletion paths.

---

### Bug #24 — Python 3.9+ Compatibility (All Scripts)

**Location:** `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

**Problem:** These scripts used `Path.is_relative_to()` directly without backport, causing `AttributeError` on Python 3.8.

**Fix:** Added `_is_relative_to()` backport function to all active scripts:
- `jxl_tiff_encoder.py`
- `jxl_tiff_decoder.py`
- `jxl_jpeg_transcoder.py`

---

### Bug #25 — CJXL_DISTANCE Range Validation

**Location:** `jxl_tiff_encoder.py` — argument parsing

**Problem:** The `--distance` parameter accepted any float value without validating the valid range (0-15). Invalid values would be passed to cjxl which would fail with cryptic errors.

**Fix:** Added validation in argument parsing:
```python
if args.distance is not None:
    if not 0 <= args.distance <= 15:
        parser.error("--distance must be between 0 and 15")
    CJXL_DISTANCE = args.distance
```

---

### Bug #26 — ExifTool Timeout

**Location:** `jxl_tiff_encoder.py` and `jxl_jpeg_transcoder.py`

**Problem:** Multiple `subprocess.run()` calls to exiftool had no timeout. If exiftool hung (rare but possible with corrupted files), the process would wait indefinitely.

**Fix:** Added `timeout=60` to all exiftool subprocess calls:
- `jxl_tiff_encoder.py`: 8 calls
- `jxl_jpeg_transcoder.py`: 2 calls

---

## v1.3 Bug Fixes (Detailed)

### Bug #27 — Invalid `--distance 95` passed to JPEG transcoder

**Location:** `jxl_photo.py` lines 2291-2292 and 2452-2454

**Problem:** When transcoding JPEG→JXL in lossy mode, both `--quality` and `--distance` were passed with the same value (e.g., 95). Distance in cjxl ranges 0-15, so 95 is invalid and causes unpredictable behavior.

**Fix:** Removed `--distance` from lossy transcoding command. The transcoder handles quality internally.

---

### Bug #28 — Scripts resolved by relative path (CWD dependency)

**Location:** `jxl_photo.py` lines 2152-2159, 2377-2496

**Problem:** Scripts were referenced as `'jxl_tiff_encoder.py'` (relative to CWD). If user ran `python /path/jxl_photo.py` from a different directory, `Path(script).exists()` would fail.

**Fix:** Now uses `SCRIPT_DIR / script` to resolve absolute paths relative to the script location.

---

### Bug #29 — `_show_mode_details_and_select` returns string instead of bool

**Location:** `jxl_photo.py` line 1615

**Problem:** Function returned `choice` (string) instead of setting `workflow['mode']` and returning `True/False`. Caller in `_wizard_select_mode` (line 1437) expected a boolean. Non-empty string is truthy, so wizard advanced but `workflow['mode']` was never set → `KeyError` later.

**Fix:** Now sets `workflow['mode'] = int(choice)` and returns `True`.

---

### Bug #30 — Subprocess timeout not handled

**Location:** `jxl_photo.py` lines 2351, 2547

**Problem:** `process.wait(timeout=3600)` could raise `subprocess.TimeoutExpired`, but it wasn't caught by `except Exception`. The child process would continue running as a zombie.

**Fix:** Added explicit `except subprocess.TimeoutExpired:` handler that kills the process and reports the error.

---

### Bug #31 — Set unordered in checksum DB path

**Location:** `jxl_jpeg_transcoder.py` line 833

**Problem:** Code used `list({f for _, _, f in tasks})[0]` — a set comprehension with `[0]` indexing. Set order is undefined in Python, so the "first" element was arbitrary.

**Fix:** Changed to `tasks[0][2]` — directly accesses the first task's destination path. Since all tasks in a group share the same destination folder, this is deterministic and reliable.

---

### Bug #32 — Redundant `import Path` inside function

**Location:** `jxl_photo.py` line 2209

**Problem:** `from pathlib import Path` was imported inside `_execute_manifest_workflow()` function, even though `Path` was already imported at module level (line 18). Redundant and confusing.

**Fix:** Removed the redundant import. The function uses the module-level `Path`.

---

### Bug #33 — Rich markup leaking in non-Rich fallback

**Location:** `jxl_photo.py` lines 1431-1439, 1485-1487, 1608-1618

**Problem:** When Rich library is not available, fallback code printed strings containing Rich markup tags like `[green]`, `[cyan]`, `[bold]`, etc. These appeared literally in terminal output (e.g., `[green]converted_jxl[/green]`).

**Fix:** Enhanced all `.replace()` chains to clean all Rich markup tags (`[green]`, `[cyan]`, `[red]`, `[yellow]`, `[dim]`, `[bold]`, and closing tags) before printing. Also fixed order — compound tags like `[bold green]` are replaced before simple tags like `[bold`.

---

### Bug #34 — Type hint `str | None` (Python < 3.10 incompatible)

**Location:** `jxl_jpeg_transcoder.py` line 332

**Problem:** Function signature `def read_md5_db(jxl_path: Path) -> str | None:` uses the `|` union syntax which requires Python 3.10+. The project supports Python 3.8+.

**Fix:** Changed to `-> Optional[str]` and added `from typing import Optional` import. This syntax is compatible with Python 3.8+.

---

### Additional v1.3 Improvements

- **Type hint fixed:** `_show_mode_details_and_select` now correctly typed as `-> bool`
- **Script path resolution:** All script references now use absolute paths via `SCRIPT_DIR`
- **Error handling:** Timeout handling added in both `_run_subprocess` and `execute_workflow`

---
## v1.4 Bug Fixes (Detailed)

### Bug #36 — Quality/sRGB Asked for Lossless JXL→JPEG

**Location:** `jxl_photo.py` Step 6 (lines ~1793, ~1852)

**Problem:** The wizard was asking for Quality and sRGB conversion even when "JPEG Lossless Transcode" was selected. These settings are irrelevant for lossless transcoding.

**Fix:** Added check for `conversion_type == 'jxl_to_jpeg_force'` before asking quality/sRGB questions.

```python
# Only ask for lossy mode
if origin == 'jxl' and dest == 'jpeg' and workflow.get('conversion_type') == 'jxl_to_jpeg_force':
    # ask quality and sRGB
```

---

### Bug #37 — Repeat Last Workflow Not Saving Destination Format

**Location:** `jxl_photo.py` `save_last_session()`, Repeat workflow section

**Problem:** When using "Repeat last workflow", the system didn't remember if the last JXL→? conversion was to JPEG, PNG, or TIFF. It would always default to TIFF for JXL source.

**Fix:** 
1. Added `last_dest_format` and `last_conversion_type` fields to ToolConfig
2. Updated `save_last_session()` to save these fields
3. Updated Repeat workflow to use saved values

---

### Bug #38 — Step 2 TIFF Option Misnumbered

**Location:** `jxl_photo.py` Step 2 options

**Problem:** When JXL source was selected, the TIFF option had the same number [3] as PNG, causing confusion.

**Fix:** Renumbered options:
- [1] JPEG Lossless Transcode
- [2] JPEG Lossy Convert  
- [3] AUTO (in development) - shown as grayed out
- [4] PNG
- [5] TIFF

---

## v1.5 Features and Fixes

### Bug #35 — JXL→JPEG Auto Mode for Directories (FIXED)

**Location:** `jxl_jpeg_transcoder.py`, `jxl_photo.py`

**Problem:** The transcoder's auto-detect feature only worked for single files, not directories. Users had to choose between force-lossless (fails without jbrd) or force-lossy (always recompresses).

**Solution Implemented:**

**In `jxl_jpeg_transcoder.py`:**
- Added `cmd_auto()` function that implements per-file auto-detection for batch processing:
  1. Scans all JXL files in directory
  2. Checks each file for jbrd box
  3. Separates into two lists: with_jbrd (lossless) and without_jbrd (lossy)
  4. Processes each group with appropriate method

**In `jxl_photo.py`:**
- Replaced "In development" placeholder with working "JPEG Auto-Detect" option
- Moved AUTO to option [1] as the recommended choice
- Quality and sRGB settings apply to files that will be lossy-converted

**New Step 2 Options:**
- [1] JPEG Auto-Detect — Recommended (auto: lossless if jbrd, else lossy)
- [2] JPEG Lossless — Force lossless transcoding (requires jbrd)
- [3] JPEG Lossy — Force lossy conversion with quality/ICC control
- [4] PNG
- [5] TIFF

---


### Bug #39 — JXL→TIFF Preview Not Configurable (FIXED)

**Location:** `jxl_tiff_decoder.py`, `jxl_photo.py`

**Problem:** The TIFF decoder always added an embedded JPEG preview (hardcoded `ADD_JPEG_PREVIEW = True`). Users who wanted smaller TIFF files without preview had no option to disable it.

**Solution Implemented:**

**In `jxl_tiff_decoder.py`:**
- Added `--no-preview` CLI flag
- When passed, sets `ADD_JPEG_PREVIEW = False`
- Default behavior unchanged (creates preview for compatibility)

**In `jxl_photo.py`:**
- Added question in Step 6: "Add JPEG preview? (for faster viewing)"
- Default: Yes (maintains backward compatibility)
- Shows preview status in Step 7 Summary
- Passes `--no-preview` to decoder when user selects No

**Usage:**
```bash
# With preview (default)
python jxl_tiff_decoder.py folder/ --mode 1

# Without preview (smaller files)
python jxl_tiff_decoder.py folder/ --mode 1 --no-preview
```

---


### Bug #40 — TIFF Encoder Mode 3 Using Wrong Folder Constant (FIXED)

**Location:** `jxl_tiff_encoder.py` line 392

**Problem:** Mode 3 was using `CONVERTED_JXL_FOLDER` ("converted_jxl") instead of `JXL_FOLDER_NAME` ("JXL_16bits"). This caused Mode 3 and Mode 1 to output to the same folder, breaking the intended folder structure.

**Fix:** Changed line 392 from:
```python
return tiff_path.parent / CONVERTED_JXL_FOLDER / tiff_path.with_suffix(".jxl").name
```
to:
```python
return tiff_path.parent / JXL_FOLDER_NAME / tiff_path.with_suffix(".jxl").name
```

---

### Bug #41 — TIFF Encoder Mode 0 Searching JPEG Files (FIXED)

**Location:** `jxl_tiff_encoder.py` line 1119

**Problem:** `find_files_mode0()` was searching for both `*.jpg` and `*.tif` files. Since this is a TIFF→JXL encoder, attempting to open JPEG files with `tifffile.TiffFile()` would crash.

**Fix:** Removed JPEG extensions from the search pattern:
```python
# Before:
for ext in ("*.jpg", "*.jpeg", "*.tif", "*.tiff"):

# After:
for ext in ("*.tif", "*.tiff"):
```

---

### Bug #42 — Variable 'skipped' Overwritten in Encoder Summary (FIXED)

**Location:** `jxl_tiff_encoder.py` line 1350

**Problem:** The variable `skipped` (file skip counter) was being overwritten with `_d50_patch_count["skipped"]` after the summary was already printed. While this didn't affect output (print happened first), it was confusing and error-prone.

**Fix:** Renamed the D50 variable to `d50_skipped` to avoid shadowing:
```python
# Before:
skipped = _d50_patch_count["skipped"]

# After:
d50_skipped = _d50_patch_count["skipped"]
```

---

### Bug #43 — Manifest Missing Flags vs execute_workflow (FIXED)

**Location:** `jxl_photo.py` `_build_manifest_entry_cmd()`

**Problem:** The manifest workflow builder was missing several flags that existed in `execute_workflow()`:
- `--embed-thumbnail` (TIFF→JXL)
- `--delete-source` (TIFF→JXL)
- `--delete-source` (JXL→TIFF)
- `--no-preview` (JXL→TIFF)
- `--staging`
- `--encode-tag`
- `--d50-patch`

This caused inconsistent behavior between interactive and manifest modes.

**Fix:** Added all missing flags to `_build_manifest_entry_cmd()` matching the logic in `execute_workflow()`:
```python
if workflow.get('staging'):
    parts.append(f'--staging "{staging}"')
if advanced.get('encode_tag'):
    parts.append(f'--encode-tag {encode_tag}')
if advanced.get('d50_patch'):
    parts.append(f'--d50-patch {d50_patch}')
```

---

### Bug #44 — Duplicate --format Flag in Transcoder (FIXED)

**Location:** `jxl_photo.py` lines 2560-2592

**Problem:** When `conv_type == 'jxl_to_png'`, `--format png` was added at line 2562, then again at lines 2589-2592 based on `dest`. This resulted in duplicate `--format png --format png` arguments.

**Fix:** Removed the duplicate `--format` addition in the conversion type block, keeping only the final destination-based logic.

---

### Bug #45 — Thumbnail Fallback Using Undefined PIL (FIXED)

**Location:** `jxl_tiff_encoder.py` lines 985-1009

**Problem:** The thumbnail generation fallback code used `Image.fromarray()` inside an exception handler. If the PIL import had failed, `Image` would be undefined and the fallback would crash too.

**Fix:** Added check for PIL availability before the fallback:
```python
if 'Image' not in globals():
    logger.debug("  >PIL not available, skipping thumbnail fallback")
    return
```

## Claude Code Audit Fixes (v1.5)

### Bug #46 — D50 Summary Uses Wrong Variable (FIXED)

**Location:** `jxl_tiff_encoder.py` lines 1358, 1365, 1367

**Problem:** The D50 patch summary used `skipped` (file skip counter) instead of `d50_skipped` (D50 skip counter), showing incorrect statistics.

**Fix:** Changed all three occurrences to use `d50_skipped`:
```python
total_processed = applied + d50_skipped + skipped_needed  # was: skipped
logger.info(f"D50 patch: {applied} applied | {d50_skipped} skipped")  # was: skipped
```

---

### Bug #47 — Thumbnail Return Aborts Conversion (FIXED)

**Location:** `jxl_tiff_encoder.py` line 988

**Problem:** The thumbnail fallback used `return` which aborted the entire `convert_one()` function. Should use `pass` to skip thumbnail but continue with conversion.

**Fix:** Changed `return` to `pass`.

---

### Bug #48 — ICC Comments Misplaced (FIXED)

**Location:** `jxl_tiff_encoder.py` lines 166-171

**Problem:** Comments about ICC embedding were under `D50_PATCH_SOFTWARE_LIST` instead of under `EMBED_ICC_IN_JXL`.

**Fix:** Moved ICC-related comments to the correct variable block.

---

### Bug #49 — TIFF Docstring Order Wrong (FIXED)

**Location:** `jxl_tiff_decoder.py` line 7

**Problem:** Docstring said "JPEG as page 0, 16-bit as page 1" but actual code does the opposite (16-bit=page0, JPEG=page1).

**Fix:** Corrected docstring to match actual behavior:
```python
# Before:
"TIFF structure: JPEG as page 0 (thumbnail flag), 16-bit as page 1"

# After:
"TIFF structure: 16-bit as page 0 (primary), JPEG preview as page 1 (thumbnail flag)"
```

---

### Bug #50 — OVERWRITE == False Should Be 'is' (FIXED)

**Location:** `jxl_tiff_encoder.py` line 854, `jxl_tiff_decoder.py` line 1103

**Problem:** Used `== False` which could match `0` or other falsy values. Should use `is False` for explicit boolean comparison.

**Fix:** Changed both occurrences to `if OVERWRITE is False:`.

---

### Bug #51 — --effort CLI Ignored in Transcode (FIXED)

**Location:** `jxl_jpeg_transcoder.py` line 818

**Problem:** The `encode_one_transcode()` function used global `CJXL_EFFORT` instead of the CLI argument `args.effort`.

**Fix:** Changed to `args.effort` and added `effort` parameter to `process_group_transcode()`.

---

### Bug #52 — bit_depth Hardcoded in Auto Mode (FIXED)

**Location:** `jxl_jpeg_transcoder.py` line 1515

**Problem:** Auto mode used `bit_depth=8` hardcoded, ignoring `--bit-depth` CLI argument.

**Fix:** Changed to `bit_depth=args.bit_depth`.

---

### Bug #53 — ICC Ignored with --no-ram (FIXED)

**Location:** `jxl_jpeg_transcoder.py` lines 1152-1170

**Problem:** When `--no-ram` was used with `--icc-profile`, the ICC conversion was silently ignored for PNG output. JPEG path already had temp file fallback, but PNG didn't.

**Fix:** Added temp file fallback for PNG ICC conversion (same logic as JPEG):
```python
if use_ram:
    # RAM pipeline (djxl | magick)
else:
    # Temp file pipeline (djxl → temp.png → magick)
```

---

### Bug #54 — has_jbrd_box Naive Detection (FIXED)

**Location:** `jxl_jpeg_transcoder.py` lines 356-366

**Problem:** Used `b'jbrd' in header` which could match false positives if those bytes appeared in metadata.

**Fix:** Implemented proper ISOBMFF box parsing:
- Skip JXL signature
- Parse box size and type
- Only match exact `jbrd` box type
- Handle extended sizes correctly

---

### Bug #55 — jxl_to_jpeg_lossless Missing --decode (FIXED)

**Location:** `jxl_photo.py` lines 2562-2565

**Problem:** The wrapper didn't pass `--decode` for `jxl_to_jpeg_lossless`, causing the transcoder to search for JPEGs instead of JXLs when processing directories.

**Fix:** Added `cmd.append('--decode')` for this conversion type.

---

### Bug #56 — jxl_to_jpeg_force Missing --decode (FIXED)

**Location:** `jxl_photo.py` lines 2566-2569

**Problem:** Similar to #55, `jxl_to_jpeg_force` didn't pass `--decode`, causing direction confusion.

**Fix:** Added `cmd.append('--decode')` for this conversion type.

---

### Bug #57 — jxl_to_png Generates JPEG with jbrd (FIXED)

**Location:** `jxl_photo.py` (new elif block after jxl_to_jpeg_force)

**Problem:** When user selected PNG output for JXL files with jbrd, the auto mode would transcode to JPEG instead of converting to PNG, silently ignoring the user's format choice.

**Fix:** Added explicit handling for `jxl_to_png`:
```python
elif conv_type == 'jxl_to_png':
    # Force convert (don't transcode even if jbrd present)
    cmd.append('--force-convert')
    cmd.append('--decode')
```

---

### Bug #58 — Repeat Workflow Loses Thumbnail (FIXED)

**Location:** `jxl_photo.py` lines 2915-2920

**Problem:** When using "Repeat last workflow", the `embed_thumbnail` setting wasn't being restored from the saved session.

**Fix:** Added to `advanced_options`:
```python
'embed_thumbnail': config.config.last_jpeg_thumbnail if origin == 'tiff' else None
```

---

## Second Pass Fixes (v1.5 Final)

### Bug #59 — Log Shows Wrong Effort Value (FIXED)

**Location:** `jxl_jpeg_transcoder.py` line 932

**Problem:** The log message showed `CJXL_EFFORT` (global constant = 7) instead of `args.effort` (actual CLI value). When user passed `--effort 9`, the log showed "Effort: 7" but the encode actually used 9. Only the log was wrong.

**Fix:** Changed from:
```python
logger.info(f"{op_type} | Mode: {args.mode} | Effort: {CJXL_EFFORT} | ...")
```
to:
```python
logger.info(f"{op_type} | Mode: {args.mode} | Effort: {args.effort} | ...")
```

---

### Bug #60 — bit_depth=None in Auto Mode (FIXED)

**Location:** `jxl_jpeg_transcoder.py` line 1556

**Problem:** When using auto mode with `--format png` but without explicit `--bit-depth`, `args.bit_depth` was `None`. This could cause `djxl` to receive `--bits_per_sample=None` which is invalid.

**Fix:** Added fallback to default:
```python
bit_depth=args.bit_depth or PNG_DEFAULT_BIT_DEPTH,
```

Now defaults to 16-bit when not specified.

---
### Bug #61 — Thumbnail Fallback Double Except (FIXED, Second Pass)

**Location:** `jxl_tiff_encoder.py` lines 982-1045

**Problem:** The thumbnail fallback code had three issues:
1. Double `except` blocks (second one unreachable in Python)
2. Fallback executed even when PIL not available (NameError crash)
3. No separate try/except for fallback path

**Fix:** Restructured to:
1. Single outer except for PIL approach failure
2. Check PIL availability before attempting fallback
3. Wrap fallback in its own try/except
4. Continue conversion gracefully if both approaches fail

```python
except Exception as e:
    logger.debug(f"Thumbnail PIL approach failed: {e}")
    if 'Image' not in locals():
        logger.debug("PIL not available, skipping thumbnail entirely")
    else:
        try:
            # tifffile fallback here...
        except Exception as e2:
            logger.debug(f"Thumbnail fallback also failed: {e2}")
```


---

### Bug #62 — Extended Size Box Not Appended (CRITICAL, Second Pass)

**Location:** `jxl_tiff_encoder.py` and `jxl_jpeg_transcoder.py` — `reorder_jxl_boxes()`

**Related to:** Bug #6 (Integer Overflow) - different but related issue

**Problem:** When a JXL box has `size == 1`, it indicates an extended 64-bit size follows. The code correctly calculated the extended size and extracted header/payload, but **never appended the box to the list**:

```python
# BEFORE (bug):
if size == 1:
    ext_size = int.from_bytes(data[i+8:i+16], "big")
    header, payload = data[i:i+16], data[i+16:i+ext_size]
    size = ext_size
    # ← FALTA: boxes.append((name, header, payload))
```

**Impact:** Any JXL file with boxes larger than 4GB (requiring extended size) would have those boxes **silently dropped**, corrupting the output file.

**Fix:** Added the missing `boxes.append()`:
```python
# AFTER (fixed):
if size == 1:
    ext_size = int.from_bytes(data[i+8:i+16], "big")
    header, payload = data[i:i+16], data[i+16:i+ext_size]
    size = ext_size
    boxes.append((name, header, payload))  # ← ADICIONADO
```

**Note:** This is different from Bug #6 which added size validation. Bug #6 prevented crashes from invalid sizes; Bug #62 fixes the missing append for valid extended sizes.

---

### Bug #63 — EXIF Binary Corrupted by text=True (CRITICAL, Second Pass)

**Location:** `jxl_jpeg_transcoder.py` — `inject_exif_to_jxl_from_jpeg()`

**Problem:** The function used `text=True` when extracting EXIF binary data from JPEG:

```python
# BEFORE (bug):
r = subprocess.run([...], capture_output=True, text=True, encoding="utf-8", ...)
if r.returncode == 0 or len(r.stdout) > 8:
    exif_bin.write_bytes(r.stdout)  # r.stdout is str, not bytes!
```

Two issues:
1. `text=True` with `encoding="utf-8"` and `errors="replace"` **corrupts binary EXIF data**
2. `write_bytes()` expects `bytes`, receives `str` → TypeError or data corruption

**Fix:** Removed `text=True` to keep output as bytes:
```python
# AFTER (fixed):
r = subprocess.run([...], capture_output=True, timeout=60)  # r.stdout is bytes
if r.returncode == 0 or len(r.stdout) > 8:
    exif_bin.write_bytes(r.stdout)  # bytes -> bytes ✓
```

---

### Bug #64 — Mode 1 Directory Behavior Wrong (MEDIUM, Second Pass)

**Location:** `jxl_tiff_encoder.py` — `main()` lines 1291-1296

**Problem:** According to documentation, Mode 1 should create `converted_jxl/` subfolder for **both** files and directories. But the code only did this for single files:

```python
# BEFORE (bug):
elif args.mode in (1, 2):
    if args.input.is_file():
        jxl = t.parent / CONVERTED_JXL_FOLDER / t.with_suffix(".jxl").name
    else:
        jxl = output_root / t.with_suffix(".jxl").name  # ← Flat, wrong!
```

**Fix:** Separated Mode 1 and Mode 2 logic:
```python
# AFTER (fixed):
elif args.mode == 1:
    # Mode 1: Create converted_jxl/ subfolder (file or directory)
    jxl = t.parent / CONVERTED_JXL_FOLDER / t.with_suffix(".jxl").name
elif args.mode == 2:
    if args.input.is_file():
        jxl = t.parent / CONVERTED_JXL_FOLDER / t.with_suffix(".jxl").name
    else:
        jxl = output_root / t.with_suffix(".jxl").name  # Flat for Mode 2 directory
```

**Documentation updated:** Changed Mode 1 description from "Single file" to "File or directory" in README.

---
### Bug #65 — extract_exif_raw r.stdout None Check (MEDIUM, Second Pass)

**Location:** `jxl_tiff_encoder.py` — `extract_exif_raw()`

**Problem:** If subprocess times out, `r.stdout` could be `None`, causing `len(None)` to crash:

```python
# BEFORE (bug):
if r.returncode == 0 and len(r.stdout) > 8:  # Crash if r.stdout is None
```

**Fix:** Added null check:
```python
# AFTER (fixed):
if r.returncode == 0 and r.stdout and len(r.stdout) > 8:
```

---

### Bug #66 — _d50_patch_count["skipped"] Never Incremented (MINOR, Second Pass)

**Location:** `jxl_tiff_encoder.py` — D50 patch tracking

**Problem:** The counter `skipped` in `_d50_patch_count` is initialized to 0 and read in the summary, but **never incremented** by any code path. The value is always 0.

This appears to be dead code — the actual tracking uses `skipped_needed` and `already_correct` instead.

**Status:** Code works correctly (always shows 0 skipped), but the counter is unnecessary. Could be removed in future cleanup.
## Third Pass Fixes (v1.5 Final Polish)

### Bug #67 — --no-ram Never Works (encoder) [REAL]

**Location:** `jxl_tiff_encoder.py` lines 1220-1223

**Problem:** `args.ram` is `store_true` with `default=True`, so it's never `None`. The check `if args.ram is not None` is always True, preventing the `elif args.no_ram` branch from executing.

```python
# BEFORE (bug):
if args.ram is not None:  # Always True (default=True)
    USE_RAM_FOR_PNG = args.ram
elif args.no_ram is not None:  # Never reached
    USE_RAM_FOR_PNG = not args.no_ram
```

**Fix:** Check `--no-ram` first:
```python
# AFTER (fixed):
if args.no_ram:
    USE_RAM_FOR_PNG = False
elif args.ram is not None:
    USE_RAM_FOR_PNG = args.ram
```

---

### Bug #68 — strip_metadata Deletes Description (encoder) [REAL]

**Location:** `jxl_tiff_encoder.py` — `build_metadata_injection_args()`

**Problem:** When `strip_metadata=True`, the order of exiftool arguments was wrong:
1. Set `-xmp-dc:Description=...`
2. Then `-xmp:all=` (deletes ALL XMP, including the Description just set!)

Exiftool processes arguments sequentially, so the Description was set then immediately deleted.

**Fix:** Reorder: strip first, then set Description:
```python
# AFTER (fixed):
args_lines.append("-exif:all=")  # Strip EXIF first
args_lines.append("-xmp:all=")   # Strip XMP second
args_lines.append(f"-xmp-dc:Description={encoding_desc}")  # Set Description last
```

---

### Bug #69 — PNG 8-bit Shift Results in Zeros (decoder) [POTENTIAL]

**Location:** `jxl_tiff_decoder.py` — multiple locations

**Problem:** When `DJXL_OUTPUT_DEPTH == 8`, the code does `pixels >> 8` to convert 16-bit to 8-bit. But if the input is already 8-bit (uint8), shifting right by 8 results in all zeros.

```python
# BEFORE (bug):
if DJXL_OUTPUT_DEPTH == 8:
    pixels = (pixels >> 8).astype(np.uint8)  # If pixels is uint8, result is 0!
```

**Fix:** Check dtype before shifting:
```python
# AFTER (fixed):
if DJXL_OUTPUT_DEPTH == 8 and pixels.dtype == np.uint16:
    pixels = (pixels >> 8).astype(np.uint8)
```

**Note:** This is only a potential issue if djxl generates 8-bit PNGs in Basic mode. If djxl always generates 16-bit, this is theoretical.

---

### Bug #70 — cleanup_xmp_icc Duplicates Label (decoder) [REAL]

**Location:** `jxl_tiff_decoder.py` — `cleanup_xmp_icc()`

**Problem:** Exiftool returns output with labels like `"CreatorTool : Capture One Windows | ICC:ABC..."`. The regex removes `ICC:...` but keeps the label. When writing back, it becomes `"CreatorTool : CreatorTool : Capture One Windows"`.

```python
# BEFORE (bug):
r = subprocess.run([..., "-XMP-xmp:CreatorTool", ...], ...)  # Output: "CreatorTool : value"
content = r.stdout.strip()  # "CreatorTool : Capture One | ICC:ABC"
clean = re.sub(r'ICC:[A-Za-z0-9+/=]+', '', content)  # "CreatorTool : Capture One | "
# Written back: "CreatorTool : CreatorTool : Capture One | "
```

**Fix:** Use `-s -s -s` for raw output without labels:
```python
# AFTER (fixed):
r = subprocess.run([..., "-s", "-s", "-s", "-XMP-xmp:CreatorTool", ...], ...)  # Output: "value"
```

---

### Bug #71 — --format jpg Falls Through to PNG Path (transcoder) [REAL]

**Location:** `jxl_jpeg_transcoder.py` — parser and usage

**Problem:** The argparse accepts `"jpeg"`, `"jpg"`, and `"png"` as choices. But the code only checks for `"jpeg"`, so `"jpg"` falls through to the PNG path:

```python
# BEFORE (bug):
if args.format == "jpeg":  # jpg doesn't match!
    # JPEG path
else:
    # PNG path (jpg ends up here!)
```

**Fix:** Normalize format after parsing:
```python
# AFTER (fixed):
if args.format == "jpg":
    args.format = "jpeg"
```

---

## Final Summary (All Bugs Fixed)

**Total bugs fixed: 86**
- 45 bugs from v1.0-v1.4
- 13 bugs from Claude Code audit (1st pass)
- 3 bugs from first pass additions
- 5 bugs from second pass (critical)
- 5 bugs from third pass (polish)
- 15 bugs from v1.5.1 final pass

**Critical bugs (would cause data loss/corruption):**
- #6, #62: Integer overflow / Extended size
- #4, #17: Deadlock in pipeline
- #16, #22, #23: Race conditions in deletion
- #63: EXIF binary corruption
- #68: strip_metadata deletes Description
- #70: cleanup_xmp_icc duplicates label
- #75: MD5 checksums with UUID (staging)
- #84: resolve_output_convert parameters swapped

**All 86 bugs documented, fixed, and tested.**


---

## Scripts Affected

- `jxl_jpeg_transcoder.py` — Bug fixes #1, #2, #3, #4, #6, #8, #11, #17, #19, #21, #23, #24, #26
- `jxl_tiff_encoder.py` — Bug fixes #1, #5, #6, #7, #12, #16, #21, #24, #25, #26
- `jxl_tiff_decoder.py` — Bug fixes #1, #5, #6, #13, #21, #22, #24, Improvement #14 (merged v2 features)
- `jxl_photo.py` — Bug fixes #2, #9, #10, #21
- `jxl_photo_v2.py` — Bug fixes #15, #18, #20, #21

**Deprecated (reference only):**
- `deprecated/jxl_tiff_decoder_v1_old.py` — Original v1 decoder
- `deprecated/jxl_tiff_decoder_old.py` — Previous version
- `deprecated/jxl_to_jpg_png.py` — Legacy script

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

### Improved Basic Decode Mode (v1.2)
- Now preserves ICC profile generated by djxl
- Uses PNG intermediate to capture ICC data
- Renamed old behavior to "None" mode (`--none` flag)

### JXL Integrity Verification (v1.3)
- Added `_verify_jxl_integrity()` function to encoder
- Validates JXL header before deleting source TIFFs
- Prevents data loss from corrupted conversions

### Safe Pipeline Execution (v1.3)
- Added `_run_pipeline_safe()` helper for subprocess pipelines
- Prevents deadlock when piping djxl to ImageMagick
- Proper timeout handling and cleanup

### Python 3.8 Compatibility (v1.3)
- Backported `Path.is_relative_to()` for Python 3.8+
- Toolkit now works on older Python versions
- Tested with Unicode paths

### File Integrity Verification (v1.3)
- Added `_verify_jxl_integrity()` to encoder
- Added `_verify_tiff_integrity()` to decoder  
- Added `_verify_file_integrity()` to transcoder
- Prevents data loss from corrupted conversions

### Robustness Improvements (v1.3)
- All subprocess calls now have timeouts
- ExifTool operations timeout after 60 seconds
- CJXL_DISTANCE validated (0-15 range)
- All bare except clauses fixed

---

## v1.5.2 Critical Bug Fix

### Bug #87 — 8-bit TIFF to JXL Produces Black Images (CRITICAL)

**Location:** `jxl_tiff_encoder.py` - `convert_one()` and `make_png_bytes()`

**Problem:** When converting 8-bit TIFF files to JXL, the resulting images were completely black and extremely small (~25 KB instead of ~25 MB). This affected users converting TIFFs from:
- NX Studio (Nikon)
- GIMP (8-bit export)
- Adobe Lightroom (8-bit export)
- Any software generating 8-bit TIFFs

**Root Cause:** The 8→16 bit conversion did not scale pixel values correctly:
```python
# BROKEN (v1.5.1 and earlier)
img = tif.series[0].asarray().astype(np.uint16)
# uint8 255 → uint16 255 (0.39% brightness in 0-65535 range)
```

When an 8-bit value (0-255) is cast to 16-bit without scaling:
- White (255) becomes 255 in a 0-65535 range = effectively black
- The JXL compressor efficiently encodes these near-zero values
- Result: 25 KB "black" image instead of 25 MB proper image

**Fix:** Applied proper scaling (multiply by 257 = 65535/255):
```python
# FIXED (v1.5.2)
img = tif.series[0].asarray()
if img.dtype == np.uint8:
    img = img.astype(np.uint16) * 257  # 0-255 → 0-65535
else:
    img = img.astype(np.uint16)
```

Same fix applied to `make_png_bytes()` as a fallback:
```python
if img.dtype == np.uint8:
    img_16 = img.astype(np.uint16) * 257
    img_be = img_16.astype(">u2")
```

**Impact:** This was a **critical data corruption bug** affecting all 8-bit TIFF conversions. Users could unknowingly convert their images to black JXLs. The fix ensures proper brightness preservation regardless of source bit depth.

**Reported by:** WiseTomCat (NX Studio 8-bit LZW TIFFs)

**Tested with:**
- 8-bit GIMP TIFF exports (50 MB → 26 MB JXL)
- 8-bit BigTIFF files
- 8-bit LZW compressed TIFFs
- 8-bit with ICC profiles
- EXIF preservation verified on all conversions

**Files changed:**
- `jxl_tiff_encoder.py` lines 752-765, 898-903

---

