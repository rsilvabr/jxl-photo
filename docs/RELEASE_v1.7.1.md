# v1.7.1 — Cautious ICC Strategy Cache

**Release date:** 2026-07-12  
**Status:** Released

This release implements the `cautious` mode of `--icc-png-strategy`. Instead of relying only on the size/class heuristic, `cautious` runs a small 8-bit and 16-bit round-trip test for each unseen ICC profile and caches the result. Profiles that survive the round-trip are embedded in the PNG `iCCP` chunk; profiles that cause severe darkening are skipped.

## What's new

### `--icc-png-strategy cautious`

- A 64×64 neutral RGB gradient is encoded through `cjxl` + `djxl` **with** the ICC embedded.
- Both 8-bit and 16-bit synthetic images are tested.
- A profile is considered safe only when the decoded mean is ≥ 70 % of the original mean (and ≥ 10/255).
- The result is cached per ICC hash in a cross-platform user directory.

### ICC cache

- Default location:
  - Windows: `%APPDATA%\jxl-photo\icc-cache\icc_cache.json`
  - Linux/macOS: `~/.config/jxl-photo/icc-cache/icc_cache.json`
- Override with `--icc-cache-dir <dir>`.
- Clear with `--clear-icc-cache`.

### Other changes

- `cautious` is no longer a fallback to `heuristic`; it now runs the real round-trip test.
- The ICC cache key includes `distance` and `modular` flag, so changing lossy parameters invalidates prior cautious results.
- The encoder now verifies that `cjxl` is present in `PATH` before starting conversion.

## CLI examples

```bash
# Cautious mode: test and cache each unseen ICC profile
python jxl_tiff_encoder.py "F:\Photos" --mode 2 --distance 0.1 --icc-png-strategy cautious

# Use a custom cache directory
python jxl_tiff_encoder.py "F:\Photos" --mode 2 --distance 0.1 --icc-png-strategy cautious --icc-cache-dir "E:\jxl-photo-cache"

# Clear the cache and exit
python jxl_tiff_encoder.py --clear-icc-cache
```

## Notes

- The `cautious` test uses `effort=1` internally for speed; the final encode still uses the configured `--effort`.
- First run on a large library with many different profiles may be slower while tests run. Subsequent runs are instant because the cache is used.
- The `heuristic` strategy remains the default for backwards compatibility.
