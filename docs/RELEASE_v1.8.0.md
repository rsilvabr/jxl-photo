# v1.8.0 — libjxl v0.12 Support (Version-Gated)

**Release date:** 2026-07-18  
**Status:** Released

This release adds support for libjxl v0.12.0 with **automatic version detection**: the scripts query `cjxl`/`djxl --version` once per process and only pass v0.12 flags when the binary supports them. On libjxl < 0.12 (or when the version cannot be determined), behavior is identical to v1.7.x — no new flag is ever appended.

## What's new

### `djxl --reconstruct_jpeg` on lossless recovery (automatic)

- On the transcode decode path (JXL → JPEG lossless recovery), `djxl` is now invoked with `--reconstruct_jpeg` when it is ≥ 0.12.
- This makes djxl **fail cleanly** if the original JPEG cannot be reconstructed, instead of silently producing a lossy re-encode. It complements the existing `jbrd` box check with a second, authoritative guard — a failure becomes a per-file error and the batch continues.
- The lossy decode path (`--jpeg_quality`) is untouched: the two flags are mutually exclusive.

### `--buffering` option (opt-in, default: off)

- New `CJXL_BUFFERING` setting in `jxl_tiff_encoder.py` and `jxl_jpeg_transcoder.py`, plus a `--buffering 0|1|2|3` CLI flag on the encoder.
- Default is `None`: the flag is not passed and cjxl uses its own default (2), which is the fast path.
- `0` restores the pre-0.12 behavior (buffer entire image): best compression, most RAM.

## Benchmarks — why `--buffering` stays off by default

Measured on real 16-bit Capture One exports (45 MP Nikon, ProPhoto), TIFF → JXL, `--distance 0` (lossless), effort 7:

| Config | JXL size | vs v0.11.2 | Encode time |
|---|---|---|---|
| v0.11.2 | 141.5 MB | — | 11.2 s |
| v0.12.0 `--buffering=0` | 140.0 MB | **-1.1 %** | **85.7 s** |
| v0.12.0 `--buffering=1` | 141.7 MB | +0.1 % | 12.3 s |
| v0.12.0 `--buffering=2` (cjxl default) | 141.7 MB | +0.1 % | 12.6 s |

(2 files, 200.7 MB of TIFFs, workers=8. Single-file repeats with workers=4 confirmed the timings: ~75 s for `buffering=0` vs ~13 s for 1/2 on a 96 MB TIFF.)

**Conclusion:** on large lossless photo TIFFs, `--buffering=0` buys only ~1.2 % smaller files but costs ~6-7× encode time (it disables the new multithreaded progressive-lossless fast path in v0.12). With v0.12's default (2), lossless size is on par with v0.11.2. If you archive a small set and want maximum density, opt in with `--buffering 0`; for large batches the default is the right trade-off.

## Bug fixes included (from v1.7.2)

- **Wrapper `--delete-source` confirmation could stay invisible on the main wizard path** — `execute_workflow()` now passes `PYTHONUNBUFFERED=1` to the child process, mirroring `_run_subprocess()`.
- **Lossy convert undid the JXL box reorder** — `_copy_metadata()` (exiftool) re-appended metadata boxes after the codestream. `reorder_jxl_boxes()` now runs **after** `_copy_metadata()`, keeping Exif/XMP before the codestream for IrfanView compatibility.

## Compatibility notes

- Passing a v0.12-only flag to an older binary would abort every encode with "Unknown flag"; the version gate prevents this by construction. Unknown/unreadable versions are treated as old (safe fallback).
- `jxl_tiff_decoder.py` needs no changes: it passes no v0.12-only flags.
- New regression tests: `tests/test_version_gating.py` (version parsing, flag gating on/off, missing-`jbrd` per-file error).

## Audit fixes (second pass)

### ⚠️ Breaking change — transcoder modes 4/5 renumbered

- In `jxl_jpeg_transcoder.py`, modes **4 and 5 were swapped** to match the TIFF encoder/decoder: **4 = folder rename (suffix swap)**, **5 = sibling folder** (`JXL_jpeg`/`JPEG_recovered`). Update any saved scripts/manifests that used transcoder modes 4 or 5.
- The wrapper's mode labels were also wrong (they described the old transcoder-only semantics for every direction) and now show the unified meaning.

### Fixes

- **`--dry-run` actually simulates now on every transcoder path** — it was only honored in `--force-convert`; the transcode and auto paths converted for real, and the wizard passes `--dry-run` to all scripts.
- **Wizard mode 8 `--delete-source` was silently dropped** unless you entered advanced options on the TIFF→JXL path. The mode-8 deletion choice now propagates through all paths.
- **Auto mode + staging + `--bit-depth 16`** — outputs were switched to `.png` at runtime, but staging promotion looked for the `.jpg`, silently stranding the result in the staging folder with a UUID name. The extension is now switched **before** output pairs are built (same fix pattern as bug #124).
- **Transcoder mode 1 is flat again** (matches its README and the TIFF decoder). Use modes 2/3/8 for recursive collection.
- **Auto mode processes PNG-only folders** (convert encode to JXL) instead of reporting "No JPEG or JXL files found".
- **Wizard no longer asks the decode mode twice** — Step 6 answers (matrix/basic/none/target ICC) become the defaults in the advanced-options step instead of being discarded, and target ICC is only asked for matrix mode.
- **"Repeat last workflow" preserves `distance`** for lossy conversions (it silently reverted to d=1.0).
- **Lossless transcode decode asks only the simple `yes` confirmation** — the HHMM lossy confirmation was wrongly required, although transcode decode is always lossless (jbrd-gated).
- Progress counter in transcoder modes 6/7 now counts only files that will actually be processed.
- Removed dead code (`_run_pipeline_safe`, unused constants) and small decoder cleanups (unused variable, duplicated `no_icc_clean` check, stale IFD0/IFD1 comment).
