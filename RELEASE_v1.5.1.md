# Release v1.5.1 — Bug Fixes & Polishing

## 🐛 Bug Fixes

- **Fixed** `--perceptual` ghost flag in decoder README (flag documented but never existed)
- **Fixed** `--compression uncompressed` being rejected by argparse (now accepts `uncompressed` as alias for `none`)
- **Fixed** `dict[Path, list]` type hint incompatible with Python 3.8 (now uses `Dict[Path, list]`)
- **Fixed** `effort` parameter being asked (and discarded) in JXL→TIFF workflow (djxl doesn't use effort)
- **Fixed** dead code `RECONVERT` variable in transcoder

## 📝 Documentation Improvements

- **Standardized** Python requirement to 3.9+ across all READMEs (was inconsistent: 3.8+, 3.10+, 3.12+)
- **Added** `imagecodecs` to all requirements lists
- **Cleaned** bug tracking documentation (removed duplicates)

## ✨ New Features

- **Added** `imagecodecs` dependency check in wrapper status bar
  - Shows `[✓] imagecodecs` when installed
  - Shows `[⚠] imagecodecs (LZW/ZIP TIFFs need: pip install imagecodecs)` when missing

---

**Full Changelog:** Compare v1.5..v1.5.1

**Previous:** v1.5 — Stability & Reliability Release
