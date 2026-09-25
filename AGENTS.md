# jxl-photo — agent notes

## ⚠️ Pending work (check first)

- **Open bug — lossy JXL with an ICC blob decodes wrong** (found 2026-09-25).
  Profiles with a table tone curve (ROMM with its linear toe; eciRGB v2 and
  scanner LUT profiles when they are embedded) have no native JXL form, so a
  lossy cjxl stores the whole ICC and djxl returns LINEAR sRGB. The decoder's
  Roundtrip mode pastes the original ICC on those pixels (colours wrong, log
  OK), and the recompressor/transcoder derivative paths assign the XMP ICC the
  same way. The encoder's cautious test only checks brightness, so a profile
  can slip through as "embed". Detect with djxl `--icc_out` vs
  `--orig_icc_out` (different = this case); fix = float decode + convert.
  Measurements: `docs/jxl_color_internals.md`, "Lossy JXL with an ICC blob".
  Remove this entry when fixed.

- **Calibrate the output-sharpening presets** (added 2026-09-25, not done yet).
  `SHARPEN_PRESETS` in `jxl_jpeg_transcoder.py` and `jxl_recompressor.py`
  (parity-pinned, keep both identical) still hold PLACEHOLDER numbers
  (`screen` sigma 0.5 / gain 0.6 / threshold 0.02, `print` sigma 1.0 / gain
  1.0 / threshold 0.02), marked `# CALIBRATE vs C1`. They must match Capture
  One's own output sharpening. Procedure:
  1. The user exports ONE photo from Capture One three times, all at the SAME
     size, as 16-bit sRGB TIFF, no date stamp: `A` = output sharpening off,
     `B` = C1's *screen* preset, `C` = C1's *print* preset (at their usual
     print size/DPI).
  2. Grid-search `sigma`/`gain`/`threshold` of `_sharpen_args(...)` applied to
     `A` to minimise the difference to `B` (then to `C`) — PSNR + SSIMULACRA2,
     edges excluded. A, B and C share C1's own resize, so the difference is the
     sharpening alone.
  3. Change ONLY the numbers in `SHARPEN_PRESETS` (both scripts), note in the
     transcoder/recompressor READMEs which C1 preset they reproduce, re-run
     `tests/test_helper_parity.py` and the suite. Print can only be validated
     on paper: trust C1's preset first; fine-tune later from minilab test prints.
  Remove this entry when done.

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
- `jxl_tiff_decoder.py` — JXL → TIFF (uses `djxl`). Smart sync never
  overwrites a TIFF that lacks its own `jxlphoto-src` marker (an original
  master): that is status `"refused"` — NOT `"skipped"`, because a skip
  admits the source to `--delete-skipped`
- `jxl_jpeg_transcoder.py` — JPEG↔JXL lossless + JXL→JPEG/PNG lossy.
  Never writes XMP provenance markers into a jbrd container (they break
  `djxl --reconstruct_jpeg`); the encode delete gate proves bit-exact
  recovery with a real reconstruction before unlinking a JPEG; ships a
  `--repair-jbrd` audit/repair mode for archives written by affected
  v2.0.0–v2.0.3 versions. On the decode direction `--resize-*/--sharpen`
  write DERIVATIVES (`jxlphoto-derived:<recipe>`) — never deleting, refused
  with the bit-exact recovery, `jxlphoto-src`/`srcsum` stripped from the
  copied metadata (a 2048 px JPEG must never prove the master is archived),
  and an existing non-derivative destination is refused. Derivative = resize
  or sharpen here (`--output-icc`, resize or sharpen in the recompressor); a
  derivative never carries `jxlphoto-src`/`srcsum`
- `jxl_recompressor.py` — JXL → JXL recompressor (v2.1.0): reads the recorded
  lineage chain (`gen=N | cjxl d=/e= | …`, append-only — the encoder and the
  recompressor both append one entry per encode and reconcile `gen` as
  max(stored, lossy-entry count), never increment), refuses counterproductive
  re-encodes (copy/skip/ask), guards repeat lossy generations via
  `--on-regeneration` (gen ≥ 2 + lossy request — encoder outputs are born at
  gen=1, so the guard must not fire on a file's first recompression), copies
  jbrd JXLs verbatim by default, keep-smaller net; same delete gates.
  `--delete-skipped` without `--delete-source` is inert (warning only), like
  the other scripts; multi-page groups (jxlphoto-mpg, keyed by folder) delete
  all-or-nothing — the gate must see EVERY planned page, including the ones
  that failed, were policy-skipped or refused, or it cannot veto;
  in-place promotion goes through a temp file in the destination folder plus
  an atomic `os.replace`, never a cross-volume move onto the only copy.
  `_merge_lineage_blocks` (parity-pinned with the encoder) never dedupes
  inside one field — a repeated `cjxl d= e=` entry is a real generation.
  A DERIVATIVE is `--output-icc` (16-bit colour conversion), `--resize-*` or
  `--sharpen`. All of them: never in place, never deleting, refuse to
  overwrite any destination file that is not one of their own derivatives
  (even with `--overwrite`), re-derive when the recorded recipe
  (`_DERIVED_LABEL`) changes, and drop the `jxlphoto-src`/`jxlphoto-srcsum`
  markers (a derivative must never prove the TIFF is archived). `--output-icc`
  replaces the `ICC:<b64>` in CreatorTool with the target profile; resize/
  sharpen without it keep the source profile and CreatorTool ICC (assigned
  explicitly before the ImageMagick pass, re-assigned after the Lab sharpening
  drops it). The source profile is always assigned explicitly before the
  ImageMagick conversion, and a converted PNG without its `iCCP` refuses the
  encode (traps A1-A5 in the plan doc).
  `--rename-from`/`--rename-to` (transcoder semantics) rename the output at
  planning time, so sync/refusals/duplicate aborts see the final name.
- `jxl_photo.py` — interactive wrapper that invokes the 4 scripts via subprocess

## Architecture gotchas
- **The encoder never converts colour, resizes or sharpens — by design.** It
  writes the MASTER, and its outputs carry the `jxlphoto-src`/`srcsum` proof
  that the delete gates trust. Derivatives (colour/size/sharpening) come from
  the master via the recompressor/transcoder and never carry that proof. Do
  not add such options to the encoder; see "Why the encoder never converts
  colour or resizes" in `docs/README_jxl_tiff_encoder.md`.
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
  (`_marker_matches`, `_validate_export_folder_name`, `_replace_suffix_token`,
  `_is_relative_to`,
  `_abort_on_duplicate_outputs`, `_run_exiftool_argfile`, `_tool_version`,
  plus the verify/integrity family shared by the backends:
  `_verify_jxl_integrity`/`_verify_file_integrity`, `has_jbrd_box`,
  `md5_of_file`, `_warn_distance_clamp`, `_would_skip`, `_decode_jxl_for_verify`,
  `_canon_for_compare`, `_compare_stats`, plus the encode-record lineage
  family shared by the encoder and the recompressor: `_strip_encode_params`,
  `_reconcile_gen`, `_append_encode_entry`, `_log_gen_notes_once`) so each
  stays standalone. Fix bugs in ALL copies — `tests/test_helper_parity.py`
  pins the variants and fails the moment one copy drifts.
- The recompressor's recursive finders skip its OWN output folder names
  (`recompressed_jxl`, `JXL_recompressed`, `16B_JXL_small`, `JXL_small`) — plus
  the configured `EXPORT_JXL_FOLDER` (a custom `--export-jxl-folder` must not
  be re-processed as a source on the next run) — but
  only BELOW the input root: pointing a run AT such a folder to compress it
  again is legitimate. The wrapper's `_manifest_output_collisions` mirror must
  match this exactly (it takes the root into account too) and follows the
  configured folder via `_with_child_marker`, which applies the global on the
  imported child module and restores it afterwards.

## Verification
- After editing any script, run `python -m py_compile` on the changed files.
- Tests: `pytest tests/`
- Prefer verifying real behavior against real photos over reasoning alone — the
  test suite is synthetic/mocked, so codec-path bugs (ICC, bit depth,
  multi-page, channel counts) only show up against actual files.
  `tests/test_audit_round36.py` holds the first real-codec tests (skipped
  when cjxl/djxl/exiftool are absent) — rounds 35/36 found a v2.0.0 data-loss
  bug (XMP markers breaking jbrd reconstruction) and five regressions that
  the whole mocked suite passed. Add a real-codec test for any change that
  touches what exiftool or the codecs write.
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
- `docs/version_history.md` — detailed notes for all superseded releases
  (the README keeps only the current version's changelog in full, plus the
  summary table)
- `docs/RELEASE_v*.md` — gitignored; local drafts to paste into GitHub Releases

## Releases
- Stable tags: `vX.Y.Z` (e.g. `v1.7.1`); betas: `vX.Y.Z_betaN`.
- Commits carry the repo owner's authorship only — **no `Co-Authored-By` or
  `Claude-Session` trailers** (AI assistance is credited in the README's
  Acknowledgments instead, and more than one assistant is used).
