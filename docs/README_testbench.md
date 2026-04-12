# jxl-photo Testbench

Automated testbench for the JXL conversion toolkit. Runs tests in all conversion directions and verifies integrity.

## 📋 Requirements

- Python 3.9+
- Test folder with sample images (see structure below)
- All toolkit dependencies installed (cjxl, djxl, exiftool, etc.)

## 📁 Test Folder Structure

```
E:\TESTAR (or configured folder)
├── tiff\          # 16-bit TIFF files for testing
│   ├── photo1.tif
│   └── photo2.tif
├── jxl\           # JXL files for testing
│   ├── photo1.jxl
│   └── photo2.jxl
└── jpg\           # JPEG files for testing
    ├── photo1.jpg
    └── photo2.jpg
```

> **Tip:** Use a folder with few files (3-5 of each type) for quick tests.

## 🚀 Usage

### Run all tests
```powershell
python testbench.py
```

### Quick mode (only first 3 tests)
```powershell
python testbench.py --quick
```

### Keep output files for inspection
```powershell
python testbench.py --keep-outputs
```

### See detailed output
```powershell
python testbench.py --verbose
```

### Use different test folder
```powershell
python testbench.py --input-dir "D:\MyTests" --output-dir "D:\TestResults"
```

## 🧪 Tests Performed

| # | Test | Script | Verification |
|---|------|--------|--------------|
| 1 | TIFF → JXL | `jxl_tiff_encoder.py` | JXL files created, compression verified |
| 2 | JXL → TIFF | `jxl_tiff_decoder.py` | TIFF files created, preview generated |
| 3 | JPEG → JXL | `jxl_jpeg_transcoder.py` | JXL files created |
| 4 | JXL → JPEG | `jxl_jpeg_transcoder.py` | JPEG files created |
| 5 | Roundtrip | encoder + decoder | TIFF → JXL → TIFF, integrity verification |

## 📊 Interpreting Results

### ✓ PASS
Test completed successfully and all expected files were created.

### ✗ FAIL
Test failed. Possible causes:
- Script syntax error
- Missing dependency (cjxl, djxl, exiftool)
- Bug in the code
- Corrupted input files

### ⊘ SKIP
Test was skipped (input files not found in the test folder).

## 🔧 Troubleshooting

### "No TIFF files found"
Make sure the `tiff/` folder exists within the test directory and contains `.tif` or `.tiff` files.

### "Encoder failed"
Check if:
- `cjxl.exe` is in PATH
- `exiftool.exe` is in PATH
- Python packages are installed: `pip install tifffile numpy pillow imagecodecs`

### "Command timed out"
Very large files or too many files can cause timeout. Use `--quick` mode or reduce the number of test files.

## 📝 Example Output

```
============================================================
jxl-photo Testbench v1.3
============================================================

Started: 2026-04-10 18:30:00
Input dir: E:\TESTAR
Output dir: E:\TESTAR_OUTPUT

============================================================
TEST 1: TIFF → JXL (jxl_tiff_encoder.py)
============================================================

ℹ Found 6 TIFF files to convert
✓ Converted 6 TIFF files to JXL

============================================================
TEST 2: JXL → TIFF (jxl_tiff_decoder.py)
============================================================

ℹ Found 6 JXL files to convert
⚠ JPEG preview generation had issues (check logs)
✓ Converted 6 JXL files to TIFF

============================================================
TEST SUMMARY
============================================================

✓ PASS  TIFF → JXL          Created 6 JXL files
✓ PASS  JXL → TIFF          Created 6 TIFF files
✓ PASS  JPEG → JXL          Created 4 JXL files
✓ PASS  JXL → JPEG          Created 6 JPEG files
✓ PASS  Roundtrip           Roundtrip successful: DSC00001.tif

Results:
  Passed:  5
  Failed:  0
  Skipped: 0
  Total:   5

Finished: 2026-04-10 18:35:00
```

## 🔄 Development Workflow Integration

### Before committing changes
```powershell
# 1. Run testbench
python testbench.py

# 2. If everything passes, commit

# 3. If it fails, fix before committing
```

### Regression tests
```powershell
# After fixing a bug, run testbench to ensure nothing else broke
python testbench.py --verbose
```

## 🐛 Reporting Bugs

If the testbench finds failures:

1. Run with `--verbose` to get details
2. Run with `--keep-outputs` to inspect files
3. Check logs in `Logs/` within the script folder
4. Report the issue with:
   - Testbench output
   - Relevant log files
   - Environment description (Windows version, Python version)

## 📄 License

MIT License - same as the main project.
