# Deprecated Scripts

**Status:** Legacy / Maintenance Mode  
**Reason Kept:** Fallback for simpler use cases

---

## Why These Are Still Here

The new scripts (`jxl_jpeg_transcoder.py`, etc.) are more powerful but significantly more complex. These older scripts are kept as a **safety fallback** - they work for basic workflows and have less code to potentially break.

**Use the new scripts for:**
- Production workflows
- Batch processing with parallel workers
- ICC color profile conversion
- Complex folder structures (modes 6-7)

**You MAY use these old scripts for:**
- Quick one-off conversions
- Simple workflows where you know they work
- Emergency fallback if the new scripts have issues

---

## Known Issues (Not Fixed)

### `jxl_to_jpg_png.py`
- **Deadlock risk** when using RAM pipeline with large files
  - djxl stderr buffer can fill and block
  - New scripts use threaded stderr reader (fixed)
- **No UUID in staging** - race condition if same filename in different folders
  - New scripts use UUID-based temp filenames (fixed)

### `jxl_to_jpg_png_terminal.ps1`
- Same pipeline issues as the Python version
- Limited error handling (PowerShell pipeline)

---

## Quick Reference

| Feature | Old (this folder) | New (root) |
|---------|-------------------|------------|
| JXL → JPEG/PNG | ✅ Basic | ✅ + ICC conversion + deadlock fix |
| Parallel workers | ✅ Simple | ✅ + Better queue management |
| Staging/SSD | ✅ Basic | ✅ + UUID collision protection |
| EXIF preservation | ✅ Basic | ✅ + Box reorder (IrfanView fix) |
| Error handling | ⚠️ Basic | ✅ + PPM truncation checks |

---

## Recommendation

1. **Start with new scripts** for any new workflow
2. **Test thoroughly** before processing large batches
3. **Keep these as backup** if you encounter issues with the new ones
4. **Report bugs** if new scripts break - they should be better, not worse

---

## No Support

These scripts are:
- ❌ Not actively maintained
- ❌ Not tested with new libjxl versions
- ❌ Not receiving bug fixes

Use at your own risk. The new scripts are the recommended path forward.
