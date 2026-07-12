# v1.7.1 — Cautious ICC Strategy Cache + Audit Fixes

**Release date:** 2026-07-13  
**Status:** Released

This release implements the `cautious` mode of `--icc-png-strategy` and includes a large set of audit-driven fixes for the TIFF/JXL round-trip, auto mode, and robustness.

## What's new

### `--icc-png-strategy cautious`

- A 64×64 neutral RGB gradient is encoded through `cjxl` + `djxl` **with** the ICC embedded.
- Both 8-bit and 16-bit synthetic images are tested.
- A profile is considered safe only when the decoded mean is ≥ 70 % of the original mean (and ≥ 10/255).
- The result is cached per ICC hash in a cross-platform user directory.

### ICC cache

- Default location:
  - Windows: `%APPDATA%\jxl-photo\icc-cache\icc_cache.json`
  - Linux/macOS: `~/.config/jxl-photo\icc-cache\icc_cache.json`
- Override with `--icc-cache-dir <dir>`.
- Clear with `--clear-icc-cache`.

### Other changes

- `cautious` is no longer a fallback to `heuristic`; it now runs the real round-trip test.
- The ICC cache key includes `distance` and `modular` flag, so changing lossy parameters invalidates prior cautious results.
- The encoder now verifies that `cjxl` is present in `PATH` before starting conversion.

## Bug fixes

### v1.7.1 final audit fixes

- **Grayscale standalone TIFFs were reconstructed as RGB** — the `jxlphoto-grayscale` marker was only written for split multi-page pages; single-page grayscale TIFFs were restored as 3-channel RGB. The marker is now written for every grayscale page, including standalone files.
- **`--none` mode was ignored for standalone files with `_pageN`/`_thumbnail` suffixes** — the decoder derived its strategy only from `page_idx == 0`; standalone files with a non-zero page suffix kept the default strategy and added preview + full metadata. Strategy is now taken from the first non-thumbnail page in the group.
- **`os.cpu_count()` returning `None` crashed argument parsing** in `jxl_jpeg_transcoder.py`, `jxl_tiff_decoder.py`, and `jxl_tiff_encoder.py`. Defaults now fall back to 4.
- **RGBA TIFFs were rejected by the encoder** even though the decoder and `make_png_bytes()` fully support alpha. The rejection was removed, so RGBA TIFFs round-trip correctly with `extrasamples=UNASSALPHA`.
- **D50 patch summary under-counted actually-patched files** because `already_correct` mixed applied-correct files with skipped-correct files. Counters are now split into `applied_already_correct` and `skipped_already_correct`.
- **Manifest auto-mode routed any folder containing the substring `jxl` as mode 6/7** — e.g. `JXL_archive`. The substring check was removed; only the configured export marker is used for that heuristic.
- **Embedded JPEG thumbnail was always generated from page 0** — when encoding a multi-page page with `page_idx > 0`, the thumbnail came from the first page. Both the PIL and tifffile fallback paths now use the page being encoded.
- **`reorder_jxl_boxes()` failed on bare codestreams** (`0xFF 0x0A`). Both the encoder and the JPEG transcoder now return early for bare codestreams.
- **Obsolete comments in `add_jpeg_preview()`** described the page order backwards. Comments updated to match the actual Capture One-like structure (main image on page 0, preview on page 1).
- **Transcoder used invalid `--output_format=png` flag with `djxl`** in the ICC/RAM conversion path. `djxl` rejects that flag, so `--to-srgb` and `--icc-profile` were broken in the default RAM mode. Fixed by decoding to a temporary PNG (format by extension) and then converting with ImageMagick.
- **Staging orphan with `--format jpeg --bit-depth 16`** — JPEG does not support 16-bit, so the output switched to PNG, but the staging file kept a `.jpg` extension and was never moved. Format is now switched to PNG before staging files are created.
- **`make_png_bytes()` always wrote 16-bit PNGs**, so the cautious ICC "8-bit" test was actually a 16-bit test. It now writes 8-bit PNGs for uint8 input and 16-bit PNGs for uint16 input.
- **`read_ppm_to_numpy()` failed when PPM width/height were on separate lines** or had comments mixed with tokens. The parser now reads tokens until magic, width, height, and maxval are all available.

### Additional v1.7.1 audit fixes

- **Transcoder `--force-transcode --decode` crashed the whole batch on JXL files without `jbrd` box** — the check was outside the per-file `try` block and raised in the worker. The check is now inside the function's `try` and returns a per-file error.
- **Wrapper `--delete-source` confirmation could be invisible/trapped** when the child process output was buffered. The wrapper now runs child scripts with `PYTHONUNBUFFERED=1`.
- **Lossy convert paths lost EXIF/XMP/IPTC metadata** — added best-effort exiftool copy in `encode_to_jxl` and `decode_to_image`.
- **`decode_one_transcode` never reported `reconvert`** on overwrite; it now returns the correct status.
- **`--multipage-mode skip` always encoded page 0**, even when the only real page was at a different index. It now uses the detected real page index.
- **`find_jpegs_*` and the wrapper extension mapping ignored `.jfif`/`.jpe` files**; those extensions are now included.

### Earlier v1.7.1 beta fixes

- Fixed `UnboundLocalError` in `collect_multipage_groups()` when `--no-reconstruct-multipage` is used.
- Auto mode + `--delete-source` now correctly deletes sources converted lossily, not just losslessly transcoded files.
- `has_jbrd_box()` now parses extended (64-bit) ISOBMFF sizes correctly.
- Added a warning when the decoder falls back to PIL for 16-bit RGB/RGBA PNGs, since PIL degrades precision.
- `--delete-source` confirmation in auto mode is now gated to mode 8, matching the actual deletion logic.
- `--target-icc` / `--thumbnail-handling=generate` warnings are emitted after the logger is configured.
- The TIFF encoder’s `ignore` multi-page mode now reads the real samples-per-pixel of page 0 instead of assuming RGB, so grayscale single-page TIFFs are encoded as grayscale.
- `jxl_photo.py` repeat workflow now restores `add_preview` and other advanced settings.
- `add_jpeg_preview()` no longer leaks an open `Image.open()` handle.

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
- The `cautious` strategy is now the default; `heuristic` remains available as a faster fallback.
