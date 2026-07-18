# v1.7.2 — Stability Fixes

**Release date:** 2026-07-18  
**Status:** Released

Bug-fix release following the v1.7.1 audit.

## Bug fixes

- **Wrapper `--delete-source` confirmation could stay invisible on the main wizard path** — `execute_workflow()` now passes `PYTHONUNBUFFERED=1` to the child process, mirroring `_run_subprocess()`. (The manifest path was fixed in v1.7.1; the main "New workflow" path was missed, so the child's `input()` prompt could stay block-buffered in the pipe and look like a hang.)
- **Lossy convert undid the JXL box reorder** — `_copy_metadata()` (exiftool) re-appends metadata boxes at the end of the file, so running it after `reorder_jxl_boxes()` silently undid the reorder. The reorder now runs **after** the metadata copy, keeping Exif/XMP before the codestream for IrfanView compatibility — the same invariant the TIFF encoder already documented.

## Verification

- Lossy convert: metadata boxes (`brob`/`xml`) precede the codestream and EXIF survives the round-trip.
- Wrapper subprocess: a child blocked on `input()` now has its prompt delivered through the pipe immediately.
- Lossless transcode round-trip: JPEG → JXL → JPEG with MD5 PASS.
- TIFF 16-bit round-trip (real Capture One export): pixel-identical, ICC and EXIF preserved.
