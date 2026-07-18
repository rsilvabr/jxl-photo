# jxl-photo — agent notes

## Do NOT touch (dead side project)
- `jxl_jpeg_transcoder_HDR.py` and `hdr/` — abandoned HDR side project, kept
  untracked. Do not read, edit, analyze, or commit these files.

## Active scripts
- `jxl_tiff_encoder.py` — TIFF → JXL (uses `cjxl`)
- `jxl_tiff_decoder.py` — JXL → TIFF (uses `djxl`)
- `jxl_jpeg_transcoder.py` — JPEG↔JXL lossless + JXL→JPEG/PNG lossy
- `jxl_photo.py` — interactive wrapper that invokes the 3 scripts via subprocess

## Verification
- After editing any script, run `python -m py_compile` on the changed files.
- Tests: `pytest tests/`

## Releases
- Stable tags: `vX.Y.Z` (e.g. `v1.7.1`); betas: `vX.Y.Z_betaN`.
