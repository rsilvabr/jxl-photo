# jxl-photo — agent notes

## Do NOT touch (dead code)
- `jxl_jpeg_transcoder_HDR.py` and `hdr/` — abandoned HDR side project, kept
  untracked at the repo root (gitignored). Do not read, edit, analyze, or
  commit these files.
- `deprecated/` — superseded scripts, kept untracked at the repo root
  (gitignored; still in the git history). Out of scope for audits, refactors
  and edits.
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
  replaces the `jxlphoto-iccprofile` token with the target profile; resize/
  sharpen without it keep the source profile and its token (assigned
  explicitly before the ImageMagick pass, re-assigned after the Lab sharpening
  drops it). The source profile is always assigned explicitly before the
  ImageMagick conversion, and a converted PNG without its `iCCP` refuses the
  encode (traps A1-A5 in the plan doc).
  `--rename-from`/`--rename-to` (transcoder semantics) rename the output at
  planning time, so sync/refusals/duplicate aborts see the final name.
  Up-to-date items are set aside BEFORE classification
  (`_mark_up_to_date_items` → action `"skip_uptodate"`: no encode record, no
  jbrd read, never handed to convert_one) — never when deleting, with
  `--overwrite` or in place; a derivative run only sets aside its own
  derivatives with no `_rederive_reason`. A new check that must see every
  planned file has to handle that action (the duplicate abort and the orphan
  sweep count it as a writing item).
- `jxl_photo.py` — interactive wrapper that invokes the 4 scripts via subprocess.
  Menu by task (delivery C of the wrapper redesign): 1 New job, 2 Repeat last job, 3 Presets,
  4 Folder list (manifest), 5 renamed folders (`--restamp-moved`, dry run
  first), 6 Explorer thumbnails, 7 JPEG archives (jbrd), 8 ICC test cache
  (`--clear-icc-cache`), 9 Settings (wrapper defaults, script settings =
  the overlay, reset, move), D dependencies; the beta's letters R/C/S stay
  aliases. A New job = task (`TASKS`) → folder → mode step (`[A]`/`[M]`/
  `[D]`) → quality → review (`_review_screen`)

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
- **The script settings overlay (Phase 4).** Right after its settings block
  (just before `SCRIPT_DIR`, so before `_RUN_DEFAULTS` / the decoder's
  per-run snapshot), each child calls `_apply_settings_overlay(globals(),
  "<module>", _OVERLAY_SETTINGS, _OVERLAY_DERIVED)` on its section of the
  JSON file (`JXLPHOTO_SETTINGS`, set but empty = off; else
  `.jxl_script_settings.json` next to the scripts, gitignored; else the
  home folder). The overlay value IS the script's setting: the per-run reset
  restores it, the wrapper's `_child_setting` sees it. Only names in that
  script's `_OVERLAY_SETTINGS` (kind + range/choices, checked by
  `_overlay_value`) are taken — a new setting is NOT overlay-able until it
  is listed there, and `tests/test_settings_overlay.py` checks every spec
  against the script's own value and every choice against its flag's.
  `_OVERLAY_SAFETY_SETTINGS` (identical in the four: deletion,
  confirmations, guards, provenance, verification thresholds, `OVERWRITE`,
  `STRIP_METADATA`, `AUTO_REPAIR_JBRD`, `EMBED_ICC_IN_JXL`) is never taken; never list one of
  those, a marker constant (`*_PREFIX`, `*_FLAG`) or a value computed at
  import (`PIL_MAX_IMAGE_PIXELS` feeds `Image.MAX_IMAGE_PIXELS`). A setting
  computed from another (`EXIFTOOL_TIMEOUT = CJXL_TIMEOUT`) goes in
  `_OVERLAY_DERIVED`. `setup_logger` logs the audit line
  (`_log_settings_overlay`). The sibling readers of #565
  (`_sibling_str_settings`, the encoder's `_decoder_output_folders`) add the
  overlay's folder names. Helpers parity-pinned. The suite (conftest) and
  the battery run with `JXLPHOTO_SETTINGS=""`; the battery's
  `--settings-overlay <file>` runs with one. The wrapper's menu S edits the
  file from the child's own table (no copy of types in the wrapper).

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
- **The original ICC copy is `jxlphoto-iccprofile:<base64>` in dc:Relation
  (v2.19.0)**, never `jxlphoto-icc:` (that prefix is the page flag
  `jxlphoto-icc:inherited`). Up to v2.18.x it was `ICC:<base64>` appended to
  XMP CreatorTool; every reader goes through `_carried_icc` (token first,
  CreatorTool second — parity-pinned in decoder/transcoder/recompressor), and
  an output written FROM an old file moves it: the encoder strips a carried
  CreatorTool blob, the recompressor's `_legacy_icc_lines` moves it to the
  token. It is in the encoder's `_INTERNAL_RELATION_PREFIXES`; the decoder
  drops it from the TIFF (`CLEANUP_XMP_ICC_MARKER`), the transcoder from
  every JPEG/PNG. An older version cannot read the token — a v2.19.0 'skip'
  master decoded by v2.18.1 comes back with the right pixel numbers but NO
  ICC tag (shown as sRGB; measured 2026-11-02), and a master with its
  profile in the codestream gets djxl's equivalent profile, not the original
  bytes.
- **The Exif box goes before the XMP box (#561)**, or IrfanView shows no
  EXIF. `reorder_jxl_boxes` (all three backends) sorts it first with
  `_exif_box_rank` (parity-pinned); the encoder's `_preload_xmp_block`
  writes the XMP before the EXIF, so without that sort every master comes
  out XMP → Exif (v2.17.0–v2.19.0 did). `--refresh-explorer-thumbnails`
  repairs existing files in place (`_move_exif_first`, status `REORDERED`).
  Any new path that writes boxes must keep that order. The Exif box must
  also be PLAIN (#562): IrfanView cannot read a `brob` (Brotli) box, which
  is cjxl's default for the EXIF/XMP it copies from a JPEG — the transcoder
  passes `_metadata_boxes_flag()` (`--compress_boxes=0` unless
  `COMPRESS_METADATA_BOXES`). The refresh never edits a jbrd container;
  `--repair-jbrd` fixes those (`_relayout_one_jbrd`: plain boxes from cjxl
  run on the rebuilt JPEG, codestream + jbrd byte-identical, same JPEG MD5
  before the swap, new self-hash line in `checksums.md5`).
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
  exiftool spawn, never in a jbrd container or a verbatim copy. Since
  v2.24.0 the encoder's and the recompressor's entries END with the encode
  record (`… | gen=N | cjxl d= e=`, `_history_args(record=)`; `off` writes
  `| no encode record`): that is the record's home, read by
  `_history_record` (parity-pinned) FIRST, then dc:Description / Software
  (files written before; never drop that fallback — it is what the guards
  see on every older archive). `ENCODE_TAG_MODE` only chooses a VISIBLE copy
  (`_visible_record_args`: history = none, the default; comment =
  XMP exif:UserComment; title = dc:Description; software; `xmp` = history).
  Nothing else in the History is read back. The version is `TOOLKIT_VERSION`, identical in
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
- **"Cannot tell" is not "no marker" (#563).** `_read_source_markers_batch`
  (parity-pinned in the four backends) marks a file whose batch failed,
  stalled or returned no entry with `unread: True`; its src/srcsum stay None.
  Every overwrite guard refuses an unread output while `OVERWRITE_GUARD` is
  on (warning when off) and the strict guards name the cause; the delete
  gates already fail closed on it. A new consumer of that reader must not
  read an unread file as a legacy markerless archive.
- **Planning-time batch reads report progress (#566):** every `*_batch`
  reader calls `_batch_read_tick` between batches (parity-pinned with
  `_batch_read_state`). A new batch reader does the same, or a slow disk
  makes the wrapper stop the child as hung (`child_idle_timeout_minutes`).
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
  files. `WORKER_MEMORY_FRACTION` is 1.0 in both (the encoder's was 0.8
  until v2.19.x; the owner's call, 2026-11-02); `test_constants_agree` still
  does not compare them — they may differ again. A resized
  derivative budgets cjxl by the size it ENCODES (`cjxl_pixels` from
  `_largest_encode_pixels`, #535) and the side steps by the full source.
- **Re-run defaults differ per script**: the TIFF encoder/decoder default to
  smart sync (source newer than output), the JPEG transcoder skips existing
  outputs. Not a bug — documented in each README.
- **The encoder's smart sync is a date AND a content question (#638).** A
  TIFF newer than its JXL whose sha256 is the master's `jxlphoto-srcsum` (a
  backup restored with a new date) is a SKIP: `_mark_sync_same_bytes` proves
  the pairs once per run, before anything reads `_would_skip`
  (`_prove_same_bytes` for the thumbnail guard, which runs earlier, during
  page analysis). No readable srcsum = the date alone, as before. Never
  "remember" the proof by moving the master's mtime: the recompressor's sync
  compares a master's date with its derivatives' and would re-derive every
  copy. `_would_skip` is encoder-only since then (the recompressor's copy is
  not parity-pinned any more).
- **The IPTC block of a TIFF (#640, v2.27.0).** A JXL has no IPTC box, so the
  encoder (a) copies the IPTC fields the XMP lacks into the XMP
  (`IPTC_TO_XMP_FILL`, `_fill_xmp_from_iptc`: a call of its own because
  `-wm cg` is global to a call; only when page 0 has tag 33723 — never an
  extra exiftool spawn for a TIFF without IPTC; nothing into the EXIF) and
  (b) carries the block as `jxlphoto-tifftag:33723:7:<hex>` (LONG accepted,
  written back as UNDEFINED; its own `PRIVATE_TAG_MAX_BYTES` limit, which
  never empties the page's other private tags and is never a #620 veto, and
  a program's own oversized tag — Affinity Photo's document — never costs
  the IPTC block; `_iptc_left_out_note` logs a block left out). The decoder writes it back;
  recompressed copies and the transcoder's JPEG/PNG carry the token like
  `35243`. `_exiftool_source`'s MIE dump keeps EXIF and XMP text but turns
  non-Latin IPTC text into `?` (measured 2026-11-11): never copy IPTC from
  its result — a `%` path goes through `_iptc_only_source`. The two routes
  differ, so a test of the IPTC copy runs in a `%` folder AND in one without.
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
  `md5_of_file`, `_warn_distance_clamp`, `_decode_jxl_for_verify`,
  `_canon_for_compare`, `_compare_stats`, `_provenance_marker_args`
  (encoder/decoder/transcoder), `_read_derived_markers_batch`,
  `_capture_output_identity`, `_delete_partial_if_written` (transcoder/
  recompressor), `_jxlinfo_text`, `_codestream_orientation`,
  `_codestream_head`, `_header_orientation`, `_jxl_orientation`,
  `_orientation_reset_args` (decoder/transcoder/recompressor),
  `_reduced_by_integer_decode` (decoder/transcoder), plus the encode-record lineage
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
- **The modes 6/7 anchor** is `_marker_anchor_index` (#601, parity-pinned in
  the four backends, used by the wrapper's collision walk): the first marker
  match at or below the input root, else the closest one above; a path not
  under the root keeps the first match. Never search the absolute path from
  its start again (`Lightroom_Export` above the library matches `_EXPORT`).
- Below the export marker, modes 6/7 of the decoder and the recompressor skip
  the OTHER tools' JXL output folders (#565: `_skip_other_tool_output`, names
  from the siblings' settings via `ast` plus the shipped defaults, per-script
  tables `_OTHER_TOOL_*`; helpers parity-pinned). Never a folder the encoder
  writes masters into (`_MASTER_FOLDER_SOURCES`: `EXPORT_JXL_FOLDER`,
  `CONVERTED_JXL_FOLDER`, `JXL_FOLDER_NAME` — `converted_jxl` is the
  transcoder's name too, M1 of the 261103 review), never mode 7's requested
  subfolder; the transcoder's decode is not folder-filtered (it is how a JPEG
  archive comes back). The wrapper's `_skip_check` mirrors it
  (`_other_tool_skip`). On top of that, the decoder, the recompressor and the
  transcoder's decode leave out DERIVATIVES by their `jxlphoto-derived`
  marker in mode 6 and in mode 7 without an explicit subfolder (#602:
  decoder's marker read, `_drop_derivative_inputs`,
  `_drop_derivative_pairs`); an unreadable marker keeps the file. The
  wrapper's `_without_derivative_collisions` re-checks colliding files.
- **A JXL → JPEG or 8-bit PNG never deletes its source (#600, owner's
  decision T-B).** The transcoder refuses `--force-convert --decode` to
  JPEG/8-bit PNG with `--delete-source` (exit 2) and both lossy gates keep
  such a JXL per file (`_lossy_decode_keep_reason`); the wrapper refuses the
  direct run before the HHMM token (`_lossy_decode_never_deletes`) and drops
  the delete from a manifest row. The bit-exact recovery and a 16-bit PNG
  still delete. Do not reintroduce a lossy-decode delete path.
- **A lossy JPEG → JXL holds the FIRST image of the JPEG only (#641).** An
  HDR gain map (Android's Ultra HDR, ISO 21496-1, Apple's: a second image
  after the picture) is not in it, so both lossy delete gates keep such a
  JPEG (`_gain_map_keep_reason`, next to `_lossy_decode_keep_reason`) and the
  encode warns per file. `_jpeg_gain_map` reads the JPEG's APP1/APP2 segments
  itself (no exiftool run; the whole file only behind an MPF index) and
  raises OSError = "cannot tell" = keep. A camera's MPO (previews) is not a
  gain map; a `jbrd` output holds the whole file and is not judged. Any new
  delete path for a pixel re-encode of a JPEG needs the same gate. Not
  carried into the JXL: the `jhgm` box exists, cjxl/djxl 0.12 cannot write
  or apply it.
- **Orientation of a JXL made from a JPEG (#623, v2.26.0).** cjxl keeps the
  JPEG's pixels as stored and puts the EXIF Orientation in the CODESTREAM
  (lossless and lossy alike); djxl, and cjxl reading a JXL, turn the pixels
  upright. So every output made from such a JXL resets the copied EXIF
  Orientation to 1 and removes XMP `tiff:Orientation`
  (`_orientation_reset_args`; the value comes from `_jxl_orientation`:
  `jxlinfo` first, else `_header_orientation` reads the codestream header in
  Python (#636) — never call `_codestream_orientation(_jxlinfo_text())`
  alone for it again; in the decoder the XMP line
  must ride on the LAST exiftool call — the XMP block copy comes after the
  EXIF one). The transcoder's JPEG is the exception (`JPEG_ORIENTATION =
  "original"`): `_stored_orientation`, then ImageMagick turns the pixels back
  (`_UNROTATE_MAGICK`, exact permutations, always through `_via_magick`;
  4:4:4 forced when that route was taken only for the turn, since ImageMagick
  drops to 4:2:0 below quality 90) and the tag is written back. A
  recompressor re-encode loses the codestream value, so it writes
  `jxlphoto-orientation:<n>` once (`_orientation_marker_lines`): carried by
  the XMP copy, by the decoder (NOT in `_is_internal_marker`) and by the
  encoder (NOT in `_INTERNAL_RELATION_PREFIXES` — it rides along as a user
  value), dropped from every JPEG/PNG the transcoder writes. Never in a jbrd
  container or a verbatim copy. PNG and TIFF outputs stay upright. A TIFF →
  JXL chain has codestream 1 and keeps the TIFF's own tag. The transcoder
  looks for the token in the plain XMP box first (`_xmp_box_mentions`; a
  `brob` XMP or an odd file = None = ask exiftool), so a JXL without it costs
  no exiftool run. `_jxlinfo_text`
  answers from a small cache keyed by path, size and mtime;
  `_jxlinfo_colour` reads through it.
- **The codec's own metadata in a decoded PNG/JPEG (#630/#631).** djxl writes
  the JXL's EXIF/XMP into a PNG as `Raw profile type` text chunks and into a
  JPEG as APP1 (ImageMagick carries it on): exiftool fails on such a PNG when
  it has EXIF and no XMP, and on either when the EXIF is malformed. A PNG
  djxl wrote directly has those chunks removed before `_copy_metadata`
  (`_drop_djxl_png_profiles`); a JPEG only when the copy fails, then ONE
  retry (`_drop_jpeg_codec_metadata`) — dropping always would cost the tags
  `-exif:all` does not copy (InteropIFD). Those are exiftool's "unsafe"
  tags: `_copy_metadata` names `InteropIndex`/`InteropVersion` explicitly
  (#637; cleared again on an `--icc-profile`/`--to-srgb` output, they name
  the source's DCF colour space), and `_exiftool_source`'s MIE dump passes
  `-unsafe` (without it a `%` path loses them before any copy). Never `-m` on the toolkit's own
  initiative: a copy that still fails is the file's error (#571). The one way
  to `-m` is the decoder's opt-in `EXIFTOOL_IGNORE_MINOR` /
  `--exiftool-ignore-minor`, for maker notes exiftool cannot parse (they
  block every write to a TIFF): off by default, refused with
  `--delete-source`, not in the overlay; a default run lists such files at
  its end and names the flag (`_note_minor_metadata`).
  `_run_exiftool_argfile` (four identical copies) adds the `-m` when the
  script's globals have the setting on — only the decoder defines it.
  The other direction (#639): a lossy JPEG → JXL whose cjxl-copied EXIF is
  malformed (a broken IFD1) gets ONE retry — cjxl again with
  `_CJXL_STRIP_EXIF`, the EXIF fresh from the JPEG, `_verify_exif_kept`
  after. Only when the first encode is PROVEN upright (`_jxl_orientation`
  == 1): `-x strip=exif` drops the codestream orientation too, and an
  unreadable one is "cannot tell". Unparseable maker notes stay an error that
  names `--force-transcode`.
- **ImageMagick and a standard sRGB profile (#635).** Reading a PNG,
  ImageMagick drops an `iCCP` it recognises as one of the standard sRGB
  profiles (Windows', most cameras' and phones'); writing one, it writes an
  sRGB chunk instead of the profile. So: never ask `magick` for a PNG's
  profile (`_png_iccp` reads the chunk — parity-pinned, transcoder +
  recompressor); always ASSIGN the source profile explicitly
  (`+profile "*" -profile <file>`) before a target one, or the target is
  assigned instead of converted to; and a magick command that writes a PNG
  whose profile matters passes `-define png:preserve-iCCP=true`
  (`_MAGICK_KEEP_ICCP`, the recompressor's `_TEMP_PNG_ARGS`). A test for this
  needs a RECOGNISED profile (`sRGB Color Space Profile.icm`): one built by
  littleCMS/Pillow never triggers it — which is how the whole suite missed it.
- **Wrapper options live in `REGISTRY`** (`jxl_photo.py`, `# === OPTION
  REGISTRY ===`). Both the direct run and every manifest entry build the
  child's argv through `build_child_cmd`; never add a flag to a builder by
  hand. To pass a new child flag: one `Opt` (directions, kind, group, level,
  label, help, where its value lives — `legacy_key` or `value(ctx)`), or a
  `_policy_*` rule when it needs more than "emit when set"; the value comes
  from the run or the child (`_workflow_or_child`), never a literal default.
  A flag the wrapper deliberately does not pass goes in `NOT_OFFERED` with the
  reason, a flag of a maintenance screen (menus 5-8) in
  `MAINTENANCE_FLAGS`. `tests/test_wrapper_option_registry.py` fails on an
  undeclared child flag; `tests/test_wrapper_argv_golden.py` pins the argv of
  25 runs (regen only from a known-good commit:
  `JXLPHOTO_REGEN_ARGV_GOLDEN=1`). A flag the wizard does not ask about takes
  `legacy_key="extra.<id>"`: its value lives in the run's `extra_options`
  (saved as `last_extra_options`, so presets keep it), the review's `[a]`
  screen sets it (`_all_options_screen`; essentials and delete rules stay in
  the review rows / the mode step's `[D]`, `danger=True` asks first) and the
  review lists it. Combinations
  go in `requires`/`conflicts` (ids), checked by `_registry_rule_problem` on
  the screen and in `execute_workflow` before any HHMM token — e.g.
  `--restamp-then-sync` conflicts with the deletes and Overwrite. Expert
  flags are parsed with the child's `build_parser()` before the run
  (`_expert_flags_problem`) and a `--mode` there is refused
  (`_flags_set_mode`): every pre-run check judged the wizard's mode.
- **The UI layer** (`UI`, `PlainUI`, `RichUI`, `ScriptedUI`) asks by
  question id (`opt.screen`, `opt.<id>`, `restamp.*`, `icccache.confirm`,
  `settings.*`); tests answer by id with `ScriptedUI` (a question with no
  answer left is an error). Since v2.22.1 EVERY question goes through it
  (`job.*`, `review.*`, `mode.*`, `delete.*`, `menu.main`, `presets.*`,
  `session.*`, `manifest.*`, `auto.*`, `thumbs.*`, `jbrd.*`, `gate.hhmm`):
  `tests/test_ui_layer_everywhere.py` fails on an `input()` or a
  `Prompt/Confirm.ask` outside the UI classes. The older questions keep
  their two wordings through `ask_line`/`ask_yes` (one Rich text, one plain
  text, one qid); `markup(rich, plain)` prints per renderer (`rich=None` /
  `plain=_SILENT` = nothing there), and a hand-built Rich table is drawn
  only when `ui.is_rich`.
- **The review** (`_review_init` + `_review_rows` + `_review_screen`) starts
  every job at the state the old Steps 6/6A left when every question was
  answered with Enter, so `[r]` at once runs exactly the old wizard's run:
  `tests/fixtures/review_default_argv.json` pins it per task (regenerate
  only from a commit that still had the old wizard:
  `JXLPHOTO_REGEN_REVIEW_GOLDEN=1`). A new essential question is a row with
  its own rule (what is not offered with `[D]` or in place says so); an
  option of the old Step 6A is an `Opt` the `[a]` screen can edit.
- **Jobs and presets v2** (`# === JOBS ===`): a preset is `{"schema": 2,
  "job": ...}` PLUS the v1 `last_*` snapshot made from the job, because
  `~/.jxl_tools_config.json` is shared with older copies (the execution
  copy, jxl-photo-exe) whose `--run-preset` reads only `last_*`. Every
  replay goes through `_run_saved_session(_preset_session(p))`;
  `job_from_session_v1`/`session_v1_from_job` are lossless (test with the
  owner's real presets, `tests/fixtures/owner_presets_v1.json`). The last
  job stays in the `last_*` fields.
- **Pre-run refusals live in `_GATE_RULES`** (`_job_gates(menu, wf, path)`,
  paths `common`/`direct`/`manifest`/`preset`), called by
  `execute_workflow`, `_execute_manifest_workflow` and `_run_saved_session`
  where their checks always ran. A new pre-run refusal is a rule there
  (`tests/test_job_gates.py` fails on an inline one); the HHMM and in-place
  confirmations stay in `_confirm_archive_mode`/`_confirm_in_place_replace`.

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
- Tests: `python -m pytest tests/ -n 16 -q` (pytest-xdist; ~3.5 min on the
  owner's machine — 32 logical CPUs; `-n 8` took 7.8 min on 2026-11-04, so
  16 is the measured choice — ~16 min serial). On the owner's machine `py` resolves to a
  Python 3.13 without pytest/numpy/tifffile; `python` (3.12) has them — use
  `python` for the suite, the pre-fix proofs and the battery. A test must be
  safe in parallel: its own `tmp_path`, no fixed temp/home path, no assertion
  on a phrase rich may wrap (conftest sets `COLUMNS=240`; normalise
  whitespace when a long path precedes the phrase).
- Baseline: run the whole suite (`-n 16`, ~3.5 min) BEFORE changing anything
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
  `TIFF_scans_varied` (29 film scans: 8/16-bit gray, no ICC, scanner profiles,
  RGB+IR/alpha 4th sample, 84 MP), `JPEG_varied` (70 JPEG structures — phone
  Ultra HDR, camera MPOs, lab scans, old phones — plus an empty, a truncated
  and non-JPEG files), `silverfast_xmp` (ORIGINAL SilverFast scans:
  its `LSI/` XMP and private tags must round-trip), `TIFF_tagless` (camera
  TIFFs that lost every tag: never a film scan), `TIFF_camera_thumb` (camera
  TIFFs with an unflagged thumbnail page), `TIFF_unsupported` (1-bit, CMYK:
  a clean error) and `PNG_varied` (PNG structures from phones and
  screenshots). ~21 minutes with the default
  `--jobs 4` (independent sections side by side; the memory cap, the Explorer
  thumbnails and the interrupted runs always run alone, after them; ~20 min
  with `--jobs 1`); the report keeps the same checks in the same order. A new
  section that sets `os.environ`, reads the free RAM or depends on timing
  runs "alone" in `main()`'s plan. The scratch folder is deleted on success and
  the report lands in `AI_tools/<YYMMDD>_battery_report.md`. **Run it before
  every release** and after any change to what the codecs/exiftool read or
  write, to a delete gate, to the in-place paths, or to a function's
  signature/return shape — then paste the report into the coder report or the
  review. A new behavior that only a real file can prove gets a new check
  there (and a real-codec pytest when it fits in a few seconds). The fixtures
  are the owner's local photos (ask for the folder, or read
  `JXLPHOTO_FIXTURES`; the layout is in the script's docstring) and are never
  modified.
- **Wizard screens by eye** — `python tools/ui_tour.py [--out FOLDER]` walks
  every screen of `jxl_photo.py` with Rich (22 sessions: menus, every task to
  its review, [a]/[c]/[x]/[d]/[s], Auto Mode, [D], settings, the maintenance
  menus) in a throw-away home and a hostile library folder, and saves each
  screen to `FOLDER/screens/`. Read them after any change to what the wizard
  shows (rc 130 = that session ran out of answers: see where in its file).
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
- `README.md` — short by design (about 240 lines since 2026-11-11, from 690):
  the size table, features one line each, a five-line install, the wizard's
  quick start, one command per script, a one-line-per-item "Good to know",
  the documentation table and a SHORT "What's new" for the current
  release ONLY (calm, a few bullets, one line of links). No "In vX.Y.Z"
  sections for earlier releases, no release table, no table of contents
  (owner's rule, 2026-11-11: the README must not grow). Anything longer goes
  to a page below with a link and a row in the documentation table
- `docs/install.md` — the Python install step by step (downloads, PATH,
  verify, troubleshooting); the per-script pages link to it
- `docs/why_jxl.md` — the long "Why JPEG XL": the size table again, the
  grain tests, the r/jpegxl measurements, the comparison with ZIP TIFF
- `docs/commands_and_settings.md` — everyday commands per script, "After
  conversion", recommended settings, "What a re-run redoes", how the ICC
  profile is kept, the encode record
- `docs/related_projects.md` — jxl-photo-exe, jxl-thumb, tiff-workflow,
  photo-backup (the README keeps one line each)
- `docs/whats_new.md` — the short "What's new" of every recent release,
  newest first, with the at-a-glance table of the last releases. Each release
  adds its section at the top (the same bullets as the README's) and one
  table row; the README's own section is replaced, never appended to
- `docs/upgrading.md` — what to check when upgrading, newest first (one
  section per release that changed an existing command line's behaviour)
- `docs/behavior_and_limitations.md` — defaults, limits and viewer quirks
  (the README's "Good to know" links into it)
- `docs/file_sizes.md` — measured file sizes per camera and for film scans,
  effort 7 vs 9 (sizes, time, ssimulacra2/butteraugli) and `--buffering 0`;
  libjxl 0.12.0, 2026-10-30. The README's size table is its summary (the
  same table is in `docs/why_jxl.md`) — update the three together, and never
  name the owner's shoots or folders there
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
- **The release notes' first line points to the Windows program** (owner's
  rule, 2026-11-08): `> **Windows program, no Python needed:**
  [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest)
  — …`; the README says it right under "Current version" too.
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

## Pending list
The owner keeps one list of everything still pending, across all repos, in
`../PENDENCIAS.md` (the folder above this repo, outside git, in Portuguese).
Read it when asked what is left to do. When work here finishes an item, leaves
something for later, or waits on a decision from the owner, update that file in
the same session: remove what is done, add what is new. It is private: never
copy its contents into tracked files, commits or release notes.
