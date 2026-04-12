# Release v1.5.1 — Refinements and Stability

This release improves toolkit robustness with edge case handling and consistency improvements identified during code quality reviews.

## 🎯 Key Improvements

### Reliability
- Refined argument handling in automatic conversion workflows (cmd_auto)
- Standardized `_EXPORT` folder name matching across all modules
- Adjusted MD5 checksum registration when staging is active
- Fixed ICC profile parsing in Matrix mode (s15Fixed16Number handling)

### Consistency  
- Unified function return values (standardized to 4 elements)
- Fixed property access in folder analysis (Auto Mode)
- Corrected destination path passing in manifest system

### Compatibility
- Removed unsupported flags for older djxl versions
- Proper handling of 16-bit image modes in PIL
- Correct file handle management

## 📋 Technical Details

15 refinement adjustments applied to the following components:
- `jxl_tiff_encoder.py` — 4 adjustments
- `jxl_tiff_decoder.py` — 4 adjustments  
- `jxl_jpeg_transcoder.py` — 6 adjustments
- `jxl_photo.py` — 2 adjustments

## 📊 History

- Total improvements since v1.0: 86 items
- Documentation updated with all adjustments

---

**Full changelog:** [docs/bug_tracking_since_v1.0.md](docs/bug_tracking_since_v1.0.md)

**Previous version:** v1.5 — Stability Release
