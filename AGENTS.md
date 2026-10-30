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
  admits the source to `--delete-skipped`. An existing TIFF is judged when
  its group's decode starts; the fresh decode replaces it (locally or out of
  staging) only if it is still the file judged (`_output_changed_since_judged`,
  #560 — an edit saved meanwhile), else `"refused"`
- `jxl_jpeg_transcoder.py` — JPEG↔JXL lossless + JXL→JPEG/PNG lossy.
  Never writes XMP provenance markers into a jbrd container (they break
  `djxl --reconstruct_jpeg`); the encode delete gate proves bit-exact
  recovery with a real reconstruction before unlinking a JPEG; ships a
  `--repair-jbrd` audit/repair mode for archives written by affected
  v2.0.0–v2.0.3 versions. `checksums.md5` lines are filed per output as it
  lands (staging: when its folder leaves staging; otherwise right AFTER the
  atomic swap, never before it — #556); a jbrd JXL without its line is
  adopted only by a real reconstruction hashed before and after
  (`_adopt_reconstructed_archive`; never in a dry run, which runs no
  subprocess), and an overwrite across such a lineless archive is refused
  when the reconstruction gives other bytes (`_lineless_archive_refusal`,
  #557 — positive evidence only: a reconstruction that cannot run proves
  nothing). On the decode direction `--resize-*/--sharpen`
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
  the other scripts; an existing jbrd output (no markers, ever) is
  overwritten only by its own bytes or by an archive of the same JPEG
  (`_jbrd_overwrite_refusal`, #558 — its verbatim copy may be the only one
  once `--delete-source` removed the source); multi-page groups (jxlphoto-mpg, keyed by folder) delete
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
  (`_DERIVED_LABEL`) changes — including the distance/effort suffix
  `/d<d>e<e>` (`REDERIVE_ON_ENCODE_CHANGE`, default True; labels written
  before it carry no suffix and never re-derive for it; `_derived_label`
  itself stays parity-pinned, the suffix is appended in main()) — and drop
  the `jxlphoto-src`/`jxlphoto-srcsum` markers (a derivative must never prove
  the TIFF is archived). `--output-icc`
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
- **`jxlphoto-origin:<v>` (v2.10.0) names what the FIRST master came from**
  (`tiff8`/`tiff16`, `jpeg`/`png8`/`png16`/`png`,
  `jxl-lossy`/`jxl-lossless`/`jxl`). Written once on a clean input, then only
  carried forward (decoder and derivatives keep it); an input with any other
  `jxlphoto-*` token or a `cjxl d=` record but no origin stays UNKNOWN — never
  guess. Never in a jbrd container. It is in the encoder's
  `_INTERNAL_RELATION_PREFIXES` (else an inherited origin is written twice)
  and must NOT join the decoder's `_is_internal_marker` list or any derivative
  `drop` tuple. The only reader is the recompressor's `--on-regeneration`
  guard: `jpeg`/`jxl`/`jxl-lossy` count as one hidden generation
  (`_hidden_generations`); no delete gate reads it. The transcoder writes ONE
  src/srcsum pair: `_inherited_marker_removal_lines` drops the copied one in
  the same call.
- **`xmpMM:History` (v2.13.0) records which toolkit version wrote each
  output**: one entry per write (`converted`, or `derived` for a
  derivative), appended by `_history_args` (parity-pinned in the four
  backends) inside a metadata call that already exists — never a new
  exiftool spawn, never in a jbrd container or a verbatim copy, never
  read back by any gate. The version is `TOOLKIT_VERSION`, identical in
  all five scripts. The recompressor's cjxl runs with `-x strip=xmp`: its
  carried XMP plus the restamp's `-tagsfromfile -xmp:all` makes exiftool
  ADD the copied list to the carried one for every `+=` in that call,
  doubling the History and `dc:Relation` (#531). Do not remove it.
- **`OVERWRITE_GUARD` (v2.14.0)** turns off ONLY the "marker names another
  source" refusal (encoder `_foreign_overwrite_reason`, decoder main()
  block + `_decode_output_is_ours`, recompressor
  `_foreign_overwrite_refusals`, transcoder `_foreign_overwrite_filter`'s
  marker branch). Off + `--delete-source` exits 2 in every backend. It
  never relaxes the jbrd/checksum refusals (#557's reconstruction check and
  #558's jbrd-output rule included), the decoder's original-master
  and edited-TIFF refusals, the derivative own-output check or any delete
  gate. The encoder's `--restamp-moved` and the adopt stamp rewrite a
  master only through `_rewrite_master_relation` (temp + reorder +
  integrity + read-back + mtime kept + `_changed_since` + os.replace) and
  write no History. Every in-place edit that swaps a worked-on copy over the
  original (that one, the thumbnail refresh, `--repair-jbrd`) checks
  `_changed_since` (parity-pinned) right before the swap (#559), like the
  in-place recompression checks `_source_changed_since_read`.
- **Explorer thumbnail box (v2.16.0, `EXPLORER_THUMBNAIL`)**: a 768 px sRGB
  JPEG in an ISO BMFF `uuid` box (UUID `4e0b4016-f51c-473d-9669-bc35aebe3c16`,
  payload `jxlphoto-thumbnail:v1:<bytes>:<fnv>\n` + JPEG) placed before the
  codestream, for jxl-thumb 0.2.0+. `auto` = every output when jxl-thumb is
  the registered `.jxl` handler (`_jxl_thumb_installed`, overridable with
  `JXLPHOTO_JXL_THUMB=1/0`; the suite and the battery pin it to 0), lossless
  only otherwise. NEVER in the EXIF: a 768 px JPEG does not
  fit a JPEG's 64 KB EXIF segment and `djxl x.jxl y.jpg`, which copies the
  EXIF, then fails ("Bogus marker length") — the battery caught it. The
  fingerprint (codestream length + FNV-1a 64 of its first 64 KiB; jxlc, or
  jxlp payloads without their 4-byte index) must stay byte-identical to
  jxl-thumb's `ReadThumbnailBox`. djxl is called WITHOUT `--color_space` for
  the thumbnail (with it, a lossless file is converted while `--icc_out`
  still names the original profile). The refresh
  (`--refresh-explorer-thumbnails`) restores each file's mtime: the
  encoder's sync compares it with the TIFF's. Helpers parity-pinned
  (encoder + recompressor).
- **A split's group id hashes its page set (#303)**, so a TIFF re-encoded
  with FEWER pages would leave the old pages under the old id (#551). After a
  run, `_clean_stale_split_pages` removes a leftover THUMBNAIL page of a TIFF
  the run rewrote completely (same `jxlphoto-src`, a group id the new pages do
  not carry, the TIFF still on disk) and only reports a leftover REAL page —
  never delete one. Derivative runs of the recompressor skip `jxlphoto-thumb`
  pages (#552); a plain recompression keeps them so the split stays complete.
  So does the transcoder's resized/sharpened/colour-converted JXL -> JPEG/PNG
  (`_skip_thumbnail_pages`, #555; no marker read on a dry run).
- **Each manifest entry runs as a SEPARATE child process.** A child's own safety
  checks (`_abort_on_duplicate_outputs`, the output-vs-input collision guard)
  can therefore never see a problem that spans two entries — those guards have
  to live in the wrapper. Two v1.8.1 bugs came from exactly this blind spot.
- **`--delete-source` works in every mode** (since v2.0.0). Every delete path is gated
  by an integrity check plus (for JPEG recovery) `djxl --reconstruct_jpeg` or a
  same-run MD5 match. Keep those gates fail-CLOSED: an unverifiable output must
  block deletion, never be waved through.
- **Per-file exiftool calls use `EXIFTOOL_TIMEOUT`** (each script defines it
  as its codec timeout), never a literal: a 60/120 s limit turned twelve good
  45 MP files into errors under a 17-worker load on 2026-10-06. Only the
  planning-time `*_batch` reads keep their per-batch limit.
  `tests/test_exiftool_timeouts.py` scans every call.
- **Worker memory cap**: the encoder caps `--workers`
  (`_memory_capped_workers`, parity-pinned with the recompressor's unused
  copy). The recompressor caps the full-size cjxl instead
  (`_memory_capped_pipeline` + `_run_cjxl_full`, a semaphore) and spends the
  leftover memory on extra workers that run djxl/magick/exiftool for the next
  files. Its `WORKER_MEMORY_FRACTION` is 1.0, the encoder's 0.8 — on purpose
  (v2.9.0); `test_constants_agree` no longer compares them. A resized
  derivative budgets cjxl by the size it ENCODES (`cjxl_pixels` from
  `_largest_encode_pixels`, #535) and the side steps by the full source.
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
- **ALWAYS test with hostile paths** (owner's rule, 2026-10-17: "SEMPRE da erro").
  The owner's real library has every one of these, often in one path:
  `G:\2025\250118_吾妻山公園_菜の花_Canola [FINAL]\01_Z7\_EXPORT\TIFF16\DSC_0001 (2).tif`
  -- Japanese, `[ ]` brackets, spaces, parentheses, `%`, long paths, sources on
  another drive than `%TEMP%`/the staging folder. Every change that touches a path
  (discovery, cjxl/djxl/magick/exiftool calls, staging, moves, markers, reports)
  gets a REAL test whose fixture path has Japanese characters AND `[ ]` AND a
  space -- an ASCII `tmp_path` alone proves nothing. Known traps: exiftool reads
  its command line in the ANSI code page (Japanese arrives mangled -> argfile with
  `-charset filename=utf8`, `_run_exiftool_argfile`; the argfile's own PATH is
  on that command line too, so `_run_captured` hands an argfile whose path the
  code page cannot hold to exiftool on stdin, `_argfile_via_stdin`, #553); ImageMagick globs `[ ]` and
  expands `%`; a hard link cannot cross drives (a fallback copy must be released
  per file); the 260-character limit (cjxl/djxl cannot open such a path:
  `_long_codec_paths` inside `_run_captured` gives them the `\\?\` prefix,
  #541 — never pass that prefix to ImageMagick, `?` is a wildcard there); exiftool
  format-expands a `%` in the file name given to `-tagsFromFile` and `<=`
  (`5%.tif`, `50%off`: a warning, exit 0, NO metadata copied — #550): pass
  such sources through `_exiftool_source` / `_xmp_block_args`, and never put a
  source's name in a temp file read back with `<=`. The sibling tiff-workflow shipped three such
  bugs on 2026-10-17 because its tests used ASCII paths only.
- After editing any script, run `python -m py_compile` on the changed files.
- Tests: `python -m pytest tests/ -n 8 -q` (pytest-xdist; ~2.5 min on the
  owner's machine, ~16 min serial). On the owner's machine `py` resolves to a
  Python 3.13 without pytest/numpy/tifffile; `python` (3.12) has them — use
  `python` for the suite, the pre-fix proofs and the battery. A test must be
  safe in parallel: its own `tmp_path`, no fixed temp/home path, no assertion
  on a phrase rich may wrap (conftest sets `COLUMNS=240`; normalise
  whitespace when a long path precedes the phrase).
- Baseline: run the whole suite (`-n 8`, ~2.5 min) BEFORE changing anything
  and record the summary — it tells a new failure from an old one or from the
  environment (a codec update). A plan also states the result of its base
  commit. A test that fails at the end is re-run alone against the extracted
  HEAD (`git show HEAD:<file>`).
- The suite and the battery set `JXLPHOTO_LOG_DIR` (conftest / `run()`), so no
  test writes to `Logs\`. Every subprocess call in the four backends goes
  through `_run_captured` except the version probes (`_tool_version`,
  `_warn_if_libjxl_too_old`); `tests/test_subprocess_capture.py` enforces it.
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
  Python traceback. Every check runs inside a HOSTILE folder (Japanese, `[ ]`,
  a space, `%`, another drive than `%TEMP%`; `--ascii-paths` for a plain one
  when telling a path bug from another), plus hostile file names and a path
  past 260 characters. It also runs copies of the owner's four presets
  through `jxl_photo.py --run-preset` (config in a temporary USERPROFILE,
  never theirs) twice, then after a re-export and a renamed event folder, and
  kills the encoder, the in-place recompressor and the transcoder mid-write
  before running them again. Optional fixture folders add the owner's variety:
  `TIFF16_varied` (37 exports, 18 cameras/phones/a film scanner),
  `TIFF_scans_varied` (23 film scans: 8/16-bit gray, no ICC, scanner profiles,
  RGB+IR/alpha 4th sample, 84 MP), `JPEG_varied` (25 JPEG structures, an
  empty and a non-JPEG file), `silverfast_xmp` (ORIGINAL SilverFast scans:
  its `LSI/` XMP and private tags must round-trip) and `TIFF_tagless` (camera
  TIFFs that lost every tag: never a film scan). ~12 minutes; the scratch folder is deleted on success and
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
- `README.md` — short by design: features, install, quick start, a
  one-line-per-item "Good to know", and a SHORT "What's new" for the current
  release (calm, a few bullets, links). Long explanations go to the docs
  below, not here
- `docs/upgrading.md` — what to check when upgrading, newest first (one
  section per release that changed an existing command line's behaviour)
- `docs/behavior_and_limitations.md` — defaults, limits and viewer quirks
  (the README's "Good to know" links into it)
- `docs/file_sizes.md` — measured file sizes per camera and for film scans,
  effort 7 vs 9 (sizes, time, ssimulacra2/butteraugli) and `--buffering 0`;
  libjxl 0.12.0, 2026-10-30. The README's size table is its summary — update
  both together, and never name the owner's shoots or folders there
- `docs/metadata_written.md` — every field the toolkit writes into a photo
  (CreatorTool ICC, encode record, each `jxlphoto-*` token, xmpMM:History)
  and what it reveals. A new token or a new written field goes there in the
  same change — `tests/test_metadata_doc.py` fails on an undocumented
  `jxlphoto-*` token
- `docs/README_jxl_tiff_encoder.md`, `docs/README_jxl_tiff_decoder.md`,
  `docs/README_jxl_jpeg_transcoder.md`, `docs/README_jxl_recompressor.md` —
  per-script CLI, settings and modes (every `--flag` must appear in its doc:
  `tests/test_docs_cover_the_flags.py` enforces it)
- `docs/README_jxl_tools.md` — the interactive wrapper
- `docs/README_manifest.md` — the manifest CSV format: base/optional columns,
  absent-vs-empty-vs-filled semantics, guards, recipes, scheduling. The wrapper
  README keeps only a summary and links here — do not re-document columns there
- `docs/jxl_color_internals.md` — XYB, ICC blobs vs native primaries
- `docs/bug_tracking_since_v1.0.md` — every fix since v1.0, newest round
  first (bugs #1–#171 in their original format: `docs/bug_tracking_archive.md`).
  A new bug takes the header's "Next free number" (then bump it), goes in its
  round's table, and the round's row in the index table is updated;
  `tests/test_bug_tracker_numbers.py` fails on a reused number, a stale next
  number, an out-of-order round or a stale index row
- `docs/version_history.md` — detailed notes for EVERY release, the current
  one included, plus the full release table (the README keeps the last few
  rows)
- `docs/RELEASE_v*.md` — gitignored; local drafts to paste into GitHub Releases

## Releases
- Stable tags: `vX.Y.Z` (e.g. `v1.7.1`); betas: `vX.Y.Z_betaN`.
- **Tone of release notes and the README's "What's new": calm and short.**
  Most fixes close rare edge cases (an unusual combination, an interrupted
  run, a scanner profile): say that — "closes edge cases", "hardening" — and
  never headline a fix with "lost files" / "destroyed the archive" when it
  took an unusual setup to happen. Lead with what the user will notice and
  whether they need to do anything; the mechanism, reproductions and numbers
  belong in `docs/version_history.md` and the bug tracker. An action the
  user really must take goes in `docs/upgrading.md` and gets one clear line
  in the notes. Doc housekeeping (a shorter README, pages moved or split) is
  not a change to announce: it stays out of the release notes, the README's
  "What's new" and the release tables — one plain line in
  `docs/version_history.md` at most.
- **Every release bumps `TOOLKIT_VERSION` in ALL FIVE scripts**, even
  when only one changed, to the version of the newest
  `docs/version_history.md` section — `tests/test_toolkit_version.py`
  fails until they match.
- Commits carry the repo owner's authorship only — **no `Co-Authored-By` or
  `Claude-Session` trailers** (AI assistance is credited in the README's
  Acknowledgments instead, and more than one assistant is used).
- **Every jxl-photo release updates jxl-photo-exe too** (owner's rule,
  2026-10-22), in the sibling repo `../jxl-photo-exe`: `JXLPHOTO_TAG` in its
  `build.py` set to the new tag and `PACKAGE_REVISION` back to 1, its README
  "Current version" line and `docs/version_history.md`, then a tag
  `vX.Y.Z-exe1`: GitHub Actions builds the zip with a build attestation and
  creates a DRAFT release; `python tests/battery_on_exe.py --app <that zip,
  extracted>` (runs the program from a hostile folder; must pass) before it is
  published (regular releases since v2.16.0-exe2, built by GitHub since
  v2.16.2-exe1; jxl-thumb's releases are built the same way). A change
  to jxl-thumb that the exe bundles (its `TOOLS["jxl-thumb"]` URL + SHA-256)
  goes in the same way.
