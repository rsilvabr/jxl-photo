# Release v1.5 — Stability & Reliability

## 🚀 New Features

- **AUTO Mode (Complete)** — Fully functional per-file auto-detection for JXL→JPEG batch processing. 
  Automatically separates files with jbrd (lossless transcode) from those without (lossy convert).
- **PNG 8-bit/16-bit Output** — Configurable bit depth for PNG conversion (was hardcoded to 8-bit)
- **ICC Conversion with --no-ram** — ICC profile conversion now works even without RAM pipeline
- **Mode 1 Directory Support** — Mode 1 now correctly creates `converted_jxl/` subfolder for directories

## 🔧 Improvements

- **CLI Consistency** — Unified argument handling across all scripts
- **Prompt Simplification** — Mode selection now shows `[0-8/A/M/?]` instead of verbose text
- **Extended Size Support** — JXL files >4GB now handled correctly (64-bit box sizes)
- **EXIF Preservation** — Binary EXIF data no longer corrupted during JPEG→JXL transcode
- **Better Error Messages** — Clearer feedback when PIL is unavailable for thumbnails
- **D50 Summary Accuracy** — Fixed variable shadowing in patch statistics

## 🐛 Bug Fixes

- Fixed `--no-ram` being ignored (always used RAM pipeline)
- Fixed `--format jpg` routing to PNG path instead of JPEG
- Fixed `strip_metadata` deleting Description after setting it
- Fixed PNG 8-bit conversion resulting in all zeros
- Fixed `cleanup_xmp_icc` duplicating CreatorTool label
- Fixed `--effort` CLI argument being ignored in transcode mode
- Fixed thumbnail fallback aborting entire conversion on PIL error
- Fixed missing `--decode` flags for JXL→JPEG lossless/force modes
- Fixed jxl_to_png generating JPEG when jbrd present
- Fixed repeat workflow losing thumbnail setting
- Fixed 16 bugs from Claude Code audit (see full list in docs)

## 📁 Repository Organization

- Added `.gitignore` for Python, logs, and IDE files
- Moved build files to `NOT_COMMIT/` (local only)
- Cleaned repository history of test artifacts
- Improved documentation structure

---

**Previous:** v1.4 — JXL→JPEG conversion modes, workflow fixes  
**Previous:** v1.3 — Auto Mode foundation, staging fixes, Python 3.8 compatibility  
**Previous:** v1.2 — TIFF decoder rebuild with Windows Explorer support  
**Previous:** v1.0 — Initial release

**Full changelog:** [docs/bug_tracking_since_v1.0.md](docs/bug_tracking_since_v1.0.md)

---

### 🎯 Highlight

This release focuses on **stability and reliability**. After 71 bugs identified and fixed through extensive auditing, the toolkit is now production-ready with robust error handling, consistent CLI behavior, and comprehensive edge case coverage.
