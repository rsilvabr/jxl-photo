# JXL-Photo Testing Checklist

Manual testing checklist for jxl-photo toolkit. Run these tests before each release or after major changes.

## Test Environment Setup

- [ ] Python 3.9+ installed and working
- [ ] cjxl/djxl (libjxl) v0.11.2+ available in PATH
- [ ] exiftool available in PATH
- [ ] ImageMagick available in PATH (for transcoder)
- [ ] Dependencies installed: `pip install tifffile numpy pillow imagecodecs`

---

## 1. TIFF → JXL Encoding Tests

### 1.1 Basic Conversion (Mode 0)
**Setup:** Use `E:\TESTAR\tiff\TESTAR\` folder with sample files

- [ ] Convert single 16-bit TIFF to JXL
  - **Command:** `py jxl_tiff_encoder.py "input.tif" "output.jxl"`
  - **Verify:** JXL file created, size ~25-30MB for 36MP photo
  - **Verify:** File opens in IrfanView/Windows Photos (not black)
  
- [ ] Convert 8-bit TIFF to JXL  
  - **Verify:** JXL file size is reasonable (~25MB, not ~25KB)
  - **Verify:** Image displays correctly (not black)
  - **Verify:** EXIF preserved (check Make, Model in output)

- [ ] Convert TIFF with ICC profile
  - **Verify:** ICC profile embedded in JXL
  - **Verify:** `exiftool output.jxl` shows color space info

### 1.2 Compression Options
- [ ] Test lossless (`--distance 0`)
  - **Verify:** Perfect roundtrip (decode and compare pixel values)
  
- [ ] Test near-lossless (`--distance 0.1`)
  - **Verify:** File smaller than lossless
  - **Verify:** Visual quality indistinguishable from original

- [ ] Test lossy (`--distance 1.0`)
  - **Verify:** Significantly smaller file
  - **Verify:** Quality acceptable for web/delivery use

### 1.3 Metadata Preservation
- [ ] EXIF preservation
  - **Verify:** Camera Make, Model, Lens, ISO present in JXL
  - **Verify:** DateTimeOriginal preserved
  
- [ ] XMP preservation
  - **Verify:** Rating, keywords, processing history preserved
  
- [ ] ICC profile preservation
  - **Verify:** Profile embedded (check with exiftool)
  - **Verify:** D50 patch applied correctly for Capture One exports

### 1.4 Various TIFF Types
- [ ] LZW compressed TIFF
- [ ] ZIP compressed TIFF  
- [ ] Uncompressed TIFF
- [ ] BigTIFF format
- [ ] Grayscale TIFF
- [ ] RGB TIFF (3 channels)
- [ ] TIFF with alpha channel (RGBA)

---

## 2. JXL → TIFF Decoding Tests

### 2.1 Basic Decoding (Mode 0)
- [ ] Decode JXL to 16-bit TIFF
  - **Verify:** TIFF created successfully
  - **Verify:** 16-bit depth preserved (`exiftool -BitsPerSample`)
  
- [ ] Decode JXL to 8-bit TIFF
  - **Verify:** 8-bit output
  - **Verify:** No banding visible in smooth gradients

### 2.2 Color Handling
- [ ] JXL with embedded ICC → TIFF
  - **Verify:** ICC profile in output TIFF
  - **Verify:** Colors match original

- [ ] JXL without ICC (generic/consumer)
  - **Verify:** Decodes to sRGB or specified output profile

### 2.3 Preview Generation
- [ ] Decode with preview (`--embed-thumbnail`)
  - **Verify:** Two-page TIFF created (page 0 = preview, page 1 = main)
  - **Verify:** Windows Explorer shows thumbnail

---

## 3. JPEG ↔ JXL Transcoding

### 3.1 JPEG → JXL (Lossless Transcode)
- [ ] JPEG with jbrd box → JXL
  - **Verify:** Lossless transcoding (can reconstruct identical JPEG)
  - **Verify:** jbrd box present in JXL

### 3.2 JPEG → JXL (Lossy Convert)
- [ ] JPEG without jbrd → JXL
  - **Verify:** Quality setting respected
  - **Verify:** ICC profile converted correctly

### 3.3 JXL → JPEG
- [ ] Transcoded JXL (with jbrd) → JPEG
  - **Verify:** Bit-identical to original JPEG
  
- [ ] Converted JXL (lossy) → JPEG
  - **Verify:** Quality acceptable
  - **Verify:** sRGB conversion if requested

### 3.4 Auto Mode
- [ ] Mixed directory (some with jbrd, some without)
  - **Verify:** Lossless for files with jbrd
  - **Verify:** Lossy for files without jbrd

---

## 4. Roundtrip Tests

### 4.1 TIFF → JXL → TIFF
- [ ] 16-bit TIFF roundtrip
  - **Verify:** Pixel values within tolerance (lossy) or identical (lossless)
  - **Verify:** EXIF preserved
  - **Verify:** ICC preserved

- [ ] 8-bit TIFF roundtrip  
  - **Verify:** Brightness maintained (critical after #87 fix)
  - **Verify:** No black images

### 4.2 JPEG → JXL → JPEG
- [ ] JPEG roundtrip (transcode)
  - **Verify:** Bit-identical reconstruction

---

## 5. Batch Processing Tests

### 5.1 Directory Processing
- [ ] Process directory with subdirectories
  - **Verify:** Recursive processing works
  - **Verify:** Folder structure preserved (depending on mode)

### 5.2 Staging Mode
- [ ] Test with `--staging` flag
  - **Verify:** Files written to staging dir first
  - **Verify:** Moved to final destination after completion

### 5.3 Resume Capability
- [ ] Interrupt conversion mid-process
  - **Verify:** Can resume with `--sync` flag
  - **Verify:** Skips already-converted files

---

## 6. Edge Cases & Error Handling

### 6.1 Invalid Inputs
- [ ] Corrupted TIFF file
  - **Verify:** Graceful error, doesn't crash
  
- [ ] Corrupted JXL file
  - **Verify:** Error message, continues with other files

- [ ] Non-existent directory
  - **Verify:** Clear error message

### 6.2 Large Files
- [ ] 100+ MP image (if available)
  - **Verify:** Doesn't crash
  - **Verify:** Reasonable memory usage

### 6.3 Special Characters
- [ ] Files with Unicode names (日本語, émojis, etc.)
  - **Verify:** Processes correctly
  - **Verify:** Output filenames correct

---

## 7. Wizard Interface (jxl_photo.py)

### 7.1 Menu Navigation
- [ ] All menus display correctly
- [ ] Back navigation works
- [ ] Invalid input handled gracefully

### 7.2 Workflow Execution
- [ ] Complete wizard for TIFF → JXL
  - **Verify:** All steps work
  - **Verify:** Conversion executes correctly

- [ ] Repeat last workflow
  - **Verify:** Settings preserved
  - **Verify:** Executes correctly

### 7.3 Manifest Mode
- [ ] Create and execute manifest
  - **Verify:** CSV created correctly
  - **Verify:** All entries processed

---

## 8. Performance Checks

### 8.1 Speed
- [ ] Measure conversion time for 10 files
  - **Baseline:** Should complete in reasonable time (depends on hardware)

### 8.2 Memory
- [ ] Monitor RAM usage during batch conversion
  - **Verify:** No excessive memory growth
  - **Verify:** No memory leaks (stable usage over time)

---

## 9. Regression Tests (Critical Bugs)

Verify these specific bugs remain fixed:

- [ ] **Bug #87:** 8-bit TIFF not producing black images
- [ ] **Bug #63:** EXIF not corrupted (text mode)
- [ ] **Bug #68:** strip_metadata not deleting Description
- [ ] **Bug #41:** Mode 0 finds TIFF files (not searching JPEG)
- [ ] **Bug #35:** JXL→JPEG auto mode works for directories
- [ ] **Bug #28:** Scripts resolve with absolute paths

---

## 10. Platform Specific

### Windows
- [ ] Windows Explorer shows JXL thumbnails (if thumbnail embedded)
- [ ] Long paths handled correctly (paths > 260 chars)
- [ ] Spaces in paths handled correctly

### Cross-platform (if testing on Mac/Linux)
- [ ] Path handling works correctly
- [ ] No Windows-specific path separators hardcoded

---

## Test Output Paths

For each test run, record the output file paths here for manual verification:

### TIFF → JXL Tests
| Test | Input File | Output File Path | File Size | Visual Check |
|------|-----------|------------------|-----------|--------------|
| 16-bit conversion | | | | ☐ |
| 8-bit conversion | | | | ☐ |
| With ICC | | | | ☐ |
| Lossless | | | | ☐ |
| Lossy (d=1.0) | | | | ☐ |

### JXL → TIFF Tests
| Test | Input File | Output File Path | File Size | Visual Check |
|------|-----------|------------------|-----------|--------------|
| 16-bit decode | | | | ☐ |
| 8-bit decode | | | | ☐ |
| With preview | | | | ☐ |

### JPEG ↔ JXL Tests
| Test | Input File | Output File Path | File Size | Visual Check |
|------|-----------|------------------|-----------|--------------|
| JPEG → JXL lossless | | | | ☐ |
| JPEG → JXL lossy | | | | ☐ |
| JXL → JPEG | | | | ☐ |

### Roundtrip Tests
| Test | Original | After Roundtrip | Pixel Match | Metadata Match |
|------|----------|-----------------|-------------|----------------|
| TIFF → JXL → TIFF | | | ☐ | ☐ |
| JPEG → JXL → JPEG | | | ☐ | ☐ |

### Batch Tests
| Test | Input Folder | Output Location | Files Converted | All Valid? |
|------|-------------|-----------------|-----------------|------------|
| Directory batch | | | | ☐ |
| Recursive | | | | ☐ |

## Test Sign-off

| Tester | Date | Result |
|--------|------|--------|
|        |      | ☐ PASS / ☐ FAIL |

### Notes:
<!-- Add any issues found, observations, or suggestions here -->

### How to Fill This Checklist:
1. Run each test command
2. Copy the output file path to this checklist
3. Note the file size (should be reasonable - 25MB+ for photos, not 25KB)
4. Open the file in your preferred viewer (IrfanView, Windows Photos, GIMP)
5. Check "Visual Check" if image displays correctly (not black/corrupted)
6. For roundtrip tests, compare original vs output side-by-side

