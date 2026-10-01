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
- `jxl_tiff_encoder.py` — TIFF → JXL (uses `cjxl`). Also accepts
  `--exclude-folders` (folder NAMES, ';'-separated, matched relative to the
  input root as segments)
- `jxl_tiff_decoder.py` — JXL → TIFF (uses `djxl`). Also accepts
  `--exclude-folders` (same semantics as the encoder's). Smart sync never
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

## Settings are never hardcoded
- Every script's user-editable settings live at the TOP of the script
  (`EXPORT_MARKER`, `EXPORT_*_FOLDER`, `DELETE_CONFIRM`, `TEMP2_DIR`, ...).
  Code must READ those constants — never repeat their default as a literal
  (`"_EXPORT"`, `"16B_JXL"`, `"path"`). A literal equal to the default passes
  every test and silently discards the user's edit. Two cases that shipped
  into review in round 43: the transcoder's `main()` reset its globals to
  literals, and the wrapper omitted `--export-marker` when it equalled
  `"_EXPORT"`.
- Resetting run-scoped globals: restore the import-time snapshot
  (`_RUN_DEFAULTS`), then apply the flags one way.
- The wrapper reads a child's setting from the child module
  (`_child_setting`); a shipped default may appear only as the fallback when
  the child cannot be imported. It always passes its own marker to the child.

## Architecture gotchas
- **djxl returns a lossy ICC-blob file in LINEAR sRGB**: never paste/assign the
  original ICC without checking `--icc_out` == `--orig_icc_out`
  (`_djxl_icc_args`/`_decoded_in_original_space`, parity-pinned in all four
  scripts). The correct decode is a float PFM CONVERTED to the original
  profile; the decoder fails closed when magick is absent.
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
  plus `_path_excluded_below_root` (the discovery filter of the
  encoder/decoder and the filter behind the wrapper's manifest collision
  walk — three parity-pinned copies),
  plus the verify/integrity family shared by the backends:
  `_verify_jxl_integrity` (encoder + recompressor; `_verify_file_integrity`
  exists ONLY in the transcoder — a JXL/JPEG/PNG/TIFF superset, not a
  shared copy),
  `has_jbrd_box`,
  `md5_of_file`, `_warn_distance_clamp`, `_would_skip`, `_decode_jxl_for_verify`,
  `_canon_for_compare`, `_compare_stats`, `_provenance_marker_args`
  (encoder/decoder/transcoder), `_read_derived_markers_batch`,
  `_capture_output_identity`, `_delete_partial_if_written` (transcoder/
  recompressor), plus the encode-record lineage
  family shared by the encoder and the recompressor: `_strip_encode_params`,
  `_reconcile_gen`, `_append_encode_entry`, `_log_gen_notes_once`) so each
  stays standalone. One pair diverges ON PURPOSE: `_derivative_metadata_args` —
  only the recompressor rewrites the CreatorTool ICC for `--output-icc`, a flag
  that never reaches the transcoder. Fix bugs in ALL copies —
  `tests/test_helper_parity.py`
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
- **Changing a function's signature** (or adding a keyword argument to a
  call): first `grep -rn "<name>" --include=*.py .` — ignoring `claude/`,
  `PIP/` and `deprecated/` — and list every caller AND every test double
  (`monkeypatch.setattr(..., "<name>", lambda ...)`, `mock.patch`, fake
  classes). A double with a fixed arity breaks on a new keyword even when
  every real call site is fine. New parameters go last, with a default. The
  wrapper imports child functions in-process (resolvers, finders,
  `_validate_export_folder_name`), so a child's signature reaches
  `jxl_photo.py` too. A plan that changes a signature must include this list.
  The same applies to a function's RETURN shape (a tuple gaining a member):
  list every consumer that unpacks it — round 43's #468 crashed every
  JPEG/PNG → JXL `--force-convert` run because one loop unpacked 4 names.
- **Real-photo battery** — `py tools/real_photo_battery.py --fixtures <folder>`
  runs the four scripts as unattended subprocesses on COPIES of real photos
  (16-bit exports, an RGB+IR film scan, JPEGs made from them) and checks the
  files on disk: delete gates (own / foreign / markerless archives),
  confirmations on a closed stdin, markers, exclusions, ICC + pixels against
  an independent djxl decode, multi-page split → reconstruct, in-place group
  replacement and veto, JPEG ↔ JXL bit-exact, and that no run ends in a
  Python traceback. ~4 minutes; the scratch folder is deleted on success and
  the report lands in `AI_tools/<YYMMDD>_battery_report.md`. **Run it before
  every release** and after any change to what the codecs/exiftool read or
  write, to a delete gate, to the in-place paths, or to a function's
  signature/return shape — then paste the report into the coder report or the
  review. A new behavior that only a real file can prove gets a new check
  there (and a real-codec pytest when it fits in a few seconds). The fixtures
  are the owner's local photos (ask for the folder, or read
  `JXLPHOTO_FIXTURES`; the layout is in the script's docstring) and are never
  modified.
- **Run the tool twice and feed it its own output** when testing a sync,
  skip or delete path: the second run over files the toolkit wrote itself is
  where the delete-skipped, provenance and in-place bugs live (the sibling
  tiff-workflow found two of three serious findings of one round that way).

## Editing source safely
- **Bash heredocs eat backslashes**, even with a quoted delimiter
  (`python - <<'PY'`): a Windows path or a regex like `'[\\/]'` can land
  mangled — and the patch may still "succeed". Use the Edit tool for exact
  edits. When a bulk edit needs Python, write the script to a file, `assert
  s.count(old) == N` before replacing, and verify the bytes on disk afterwards.
  (Round 43: a heredoc patch hit `invalid escape sequence '\T'` and silently
  skipped one replacement.)
- **Stage files by name, never `git add -A`**: the repo root carries the
  owner's untracked drafts and test images.

## AI workflow: planner → coder → reviewer
Heavy changes run in three steps, each leaving a document in `AI_tools/`
(gitignored, local only). Names: `YYMMDD_<Tool>_<kind>_<topic>.md`, e.g.
`261002_Claude_plan_exclude-folders.md`, `261002_DeepSeek_report_exclude-folders.md`,
`261003_Claude_review_exclude-folders.md`. Same `<topic>` across the three.

**1. Plan (Claude/Opus).** Literal and complete — the coder must not have to
make a design decision. Every plan states:
- each change as file + function (+ line hint), current behavior → required
  behavior, and WHY;
- every decision already taken (no "either A or B"); out-of-scope items named;
- the signature checklist from *Verification* (every caller and test double)
  when a signature changes;
- the tests to write, which must be real-codec/real-exiftool ones (anything
  touching what exiftool or the codecs read/write), and the exact command
  that proves each one FAILS against the pre-fix code;
- the rules most often broken: settings never hardcoded, fix ALL copies of a
  duplicated helper, docs for every new `--flag`, no `git stash`, no commits;
- acceptance criteria: the commands to run and the results expected.

**2. Code (DeepSeek or another coder).** Follow the plan literally. When the
plan is wrong, ambiguous, or the code differs from what it describes: do not
improvise a design — take the most conservative option (fail closed, change
less) and record it. Never commit. Write the report:
- per plan item: done / partly / not done, with the files and functions touched;
- every decision taken that the plan did not dictate, and why;
- every deviation from the plan, and why;
- the commands actually run with their REAL output (pytest summary lines,
  the pre-fix failure proof) — never "tests pass" without the output;
- the real-photo battery's summary line and any failed check, when the change
  is one the *Verification* section says needs it;
- what was NOT verified, and open questions for the reviewer.
Finish by telling the user the report's path.

**3. Review (Claude/Opus).** Read the plan, the report and the full diff;
re-run the tests, the pre-fix proofs and the real-photo battery instead of
trusting the report; write the review with
must-fix / should-fix items and a commit verdict.

## Docs map
- `README.md` — current release, install, quick start
- `docs/README_jxl_tiff_encoder.md`, `docs/README_jxl_tiff_decoder.md`,
  `docs/README_jxl_jpeg_transcoder.md`, `docs/README_jxl_recompressor.md` —
  per-script CLI, settings and modes (every `--flag` must appear in its doc:
  `tests/test_docs_cover_the_flags.py` enforces it)
- `docs/README_jxl_tools.md` — the interactive wrapper
- `docs/README_manifest.md` — the manifest CSV format: base/optional columns,
  absent-vs-empty-vs-filled semantics, guards, recipes, scheduling. The wrapper
  README keeps only a summary and links here — do not re-document columns there
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
