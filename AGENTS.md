# jxl-photo — agent notes

## Do NOT touch (dead code)
- `jxl_jpeg_transcoder_HDR.py` and `hdr/` — abandoned HDR side project, kept
  untracked at the repo root (gitignored). Do not read, edit, analyze, or
  commit these files.
- `deprecated/` — superseded scripts, tracked only for history. Out of scope
  for audits, refactors and edits.
- `claude/` — stale copies of the active scripts from the v1.7.0 era, kept
  untracked at the repo root (gitignored). They still match on a repo-wide
  grep, so treat any hit there as noise: the duplicated-helper rule below means
  a fix applied to `claude/jxl_*.py` by mistake would look right and do nothing.
- `PIP/` — a PyPI packaging attempt (gitignored) whose `src/jxl_photo/` holds a
  fourth copy of all four scripts, frozen at v1.8.1 and roughly 2 000 lines
  behind each. Same rule as `claude/`: grep hits there are noise. If that
  package is ever published, the copies have to be refreshed first — the
  `pyproject.toml` version is not what the code in it is.

## Active scripts
- `jxl_tiff_encoder.py` — TIFF → JXL (uses `cjxl`)
- `jxl_tiff_decoder.py` — JXL → TIFF (uses `djxl`)
- `jxl_jpeg_transcoder.py` — JPEG↔JXL lossless + JXL→JPEG/PNG lossy
- `jxl_recompressor.py` — JXL → JXL recompressor (v2.1.0): reads the recorded
  `cjxl d=/e=`, refuses counterproductive re-encodes (copy/skip/ask), copies
  jbrd JXLs verbatim by default, keep-smaller net; same delete gates
- `jxl_photo.py` — interactive wrapper that invokes the 4 scripts via subprocess

## Architecture gotchas
- **Each manifest entry runs as a SEPARATE child process.** A child's own safety
  checks (`_abort_on_duplicate_outputs`, the output-vs-input collision guard)
  can therefore never see a problem that spans two entries — those guards have
  to live in the wrapper. Two v1.8.1 bugs came from exactly this blind spot.
- **`--delete-source` works in every mode** (since v2.0.0). Every delete path is gated
  by an integrity check plus (for JPEG recovery) `djxl --reconstruct_jpeg` or a
  same-run MD5 match. Keep those gates fail-CLOSED: an unverifiable output must
  block deletion, never be waved through.
- **Re-run defaults differ per script**: the TIFF encoder/decoder default to
  smart sync (source newer than output), the JPEG transcoder skips existing
  outputs. Not a bug — documented in each README.
- Helper functions are deliberately duplicated across the scripts
  (`_marker_matches`, `_replace_suffix_token`, `_is_relative_to`,
  `_abort_on_duplicate_outputs`, `_run_exiftool_argfile`, `_tool_version`,
  plus the verify/integrity family shared by the backends:
  `_verify_jxl_integrity`/`_verify_file_integrity`, `has_jbrd_box`,
  `md5_of_file`, `_warn_distance_clamp`, `_would_skip`, `_decode_jxl_for_verify`,
  `_canon_for_compare`, `_compare_stats`) so each stays standalone. Fix bugs in
  ALL copies — `tests/test_helper_parity.py` pins the variants and fails the
  moment one copy drifts.
- The recompressor's recursive finders skip its OWN output folder names
  (`recompressed_jxl`, `JXL_recompressed`, `16B_JXL_small`, `JXL_small`) — but
  only BELOW the input root: pointing a run AT such a folder to compress it
  again is legitimate. The wrapper's `_manifest_output_collisions` mirror must
  match this exactly (it takes the root into account too).

## Verification
- After editing any script, run `python -m py_compile` on the changed files.
- Tests: `pytest tests/`
- Prefer verifying real behavior against real photos over reasoning alone — the
  test suite is synthetic/mocked, so codec-path bugs (ICC, bit depth,
  multi-page, channel counts) only show up against actual files.
- When fixing a bug, prove the new regression test **fails against the pre-fix
  code**, not merely that it passes after. Extract the old file rather than
  stashing:
  ```
  git show HEAD:jxl_photo.py > <tmpdir>/jxl_photo.py   # then run the test there
  ```
  Do **not** use `git stash` for this: the repo can carry unrelated stashes, and
  a `stash pop` may apply the wrong one and leave a merge conflict.

## Docs map
- `README.md` — current release, install, quick start
- `docs/README_jxl_tiff_encoder.md`, `docs/README_jxl_tiff_decoder.md`,
  `docs/README_jxl_jpeg_transcoder.md`, `docs/README_jxl_recompressor.md` —
  per-script CLI, settings and modes (every `--flag` must appear in its doc:
  `tests/test_docs_cover_the_flags.py` enforces it)
- `docs/README_jxl_tools.md` — the interactive wrapper
- `docs/jxl_color_internals.md` — XYB, ICC blobs vs native primaries
- `docs/bug_tracking_since_v1.0.md` — every fix since v1.0
- `docs/version_history.md` — "What's New" for releases before v1.8
- `docs/RELEASE_v*.md` — gitignored; local drafts to paste into GitHub Releases

## Releases
- Stable tags: `vX.Y.Z` (e.g. `v1.7.1`); betas: `vX.Y.Z_betaN`.
- Commits carry the repo owner's authorship only — **no `Co-Authored-By` or
  `Claude-Session` trailers** (AI assistance is credited in the README's
  Acknowledgments instead, and more than one assistant is used).
