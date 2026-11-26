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
  input root as segments). A JXL this run wrote that fails the delete gate's
  round-trip check is an error and is renamed `<stem>_jxlphoto-error.jxl`
  (`ERROR_SUFFIX`, #701, the owner's call) — every failing page, only on a real verdict
  (`_is_verdict`: "could not verify", a check that raised, a page gone are
  `_NO_VERDICT` and keep the old warning) — never the final name, so the next
  run encodes the TIFF again; every finder skips such a file
  (`_is_error_output` in `_iter_jxls` and the transcoder's `_iter_by_ext`). A
  TIFF changed while its page was encoded is an error before the swap, delete
  or not (#699): compared with what THIS page read (`_stat_identity` at the
  start of the page's reads, not the planning's record); a TIFF gone
  meanwhile keeps its JXL. Each page's master takes its own IFD's EXIF
  (#696: `-ifd0:all#` after `-exif:all#` for page 0, `-IFD0:all#<IFDn:all#`
  for page N — `-ifdN:all#` would write IFDN of the master; the `#` on BOTH
  sides, #713: rc5's `-IFD0:all<IFDn:all#` wrote the printed form, and
  Orientation 1 matched "Rotate 180";
  `_exiftool_source(ifd=)` for the MIE container of a `%` path, which holds
  IFD0 and IFD1 only: it is built with the page's values forced into its
  IFD0, `-IFD0:all#<IFDn:all#`, and the encoder's last copy from such a
  container is `-ifd0:all#`, or page 2 and after took other pages' values —
  rc6's reviews S5 and second S2). The cautious ICC probe (#693): cjxl's own error exit,
  twice, is the verdict "skip" (cached); a timeout, a killed process or a
  djxl failure is "cannot tell" — that file keeps its profile in XMP only,
  nothing cached. A GRAY profile is probed on a grey image (#731: on an RGB
  one cjxl refused it and every grey profile was "skip"; the cache key's test
  generation is `t=3` — bump it whenever the probe's verdicts may change). A
  page with an extra sample and NO `ExtraSamples` tag (VueScan's RGBI) is
  recorded `jxlphoto-extrasamples:0` (#729, TIFF 6.0: unspecified): every
  reader takes "no marker" as alpha. `--provenance adopt` (#732) proves an
  unmarked archive by its OWN kind (`_jxl_is_lossless`, jxlinfo; unknown =
  the run's distance): lossless = pixel-identical, lossy = `ADOPT_MIN_PSNR`
  (35 dB, a safety setting, never in the overlay — not the delete gate's
  20 dB), and a `DateTimeOriginal`/`SubSecTimeOriginal`/`ImageUniqueID` both
  files carry that differs refuses it first (`_capture_identity_mismatch`). A
  #701 rename that fails tries `<stem> (n)_jxlphoto-error.jxl`, never over a
  file, then deletes the failed JXL (`_set_failed_output_aside`, #741)
- `jxl_tiff_decoder.py` — JXL → TIFF (uses `djxl`). Also accepts
  `--exclude-folders` (same semantics as the encoder's). Smart sync never
  overwrites a TIFF that lacks its own `jxlphoto-src` marker (an original
  master): that is status `"refused"` — NOT `"skipped"`, because a skip
  admits the source to `--delete-skipped`. An existing TIFF is judged when
  its group's decode starts; the fresh decode replaces it (locally or out of
  staging) only if it is still the file judged (`_output_changed_since_judged`,
  #560 — an edit saved meanwhile), else `"refused"`. A TIFF with fewer full
  pages than its group's JXLs is decoded again, not "up to date"
  (`_fewer_pages_than_group`, #703; thumbnail entries are not counted).
  `extract_icc_from_xmp` raises on a failed read (#746): "cannot tell" is
  never "no profile" (the group is an error, its JXLs kept). The
  EXIF copy leaves out, by NAME, the tags the `jxlphoto-tifftag` tokens
  restore (`_restored_tag_exclusions`, #698: ExifTool knows 65000+ by name
  and moved them), and a command of its own puts back the camera's ExifIFD
  tags of those names (`_restored_tag_exif_copy`, `-wm cg`: a group-qualified
  exclusion deleted the raw ones — ExifTool writes a name in one IFD by
  removing it from the others). Its delete gate keeps a lossless JPEG archive
  (a jbrd JXL) and a JXL whose box cannot be read (`_jbrd_box_state`, #745): a
  TIFF of its pixels is not the archive — only the transcoder's bit-exact
  recovery deletes one
- `jxl_jpeg_transcoder.py` — JPEG↔JXL lossless + JXL→JPEG/PNG lossy.
  Never writes XMP provenance markers into a jbrd container (they break
  `djxl --reconstruct_jpeg`); the encode delete gate proves bit-exact
  recovery with a real reconstruction before unlinking a JPEG; the lossless
  encode accepts only a JXL WITH a jbrd box — without one it is an error for
  the file (#654: cjxl reads by content, and a PNG named `.jpg` came out as a
  lossy JXL logged as OK), so that path never writes XMP; with cjxl 0.13+
  both paths that write a jbrd archive (the lossless transcode, the convert
  path at d=0 on a JPEG) pass `--jpeg_reconstruction_lfs=0`
  (`_jpeg_archive_flags`, #669): cjxl 0.13 otherwise marks 4:4:4 and gray
  JPEGs for LF smoothing, and djxl 0.12/0.11 then rebuild ANOTHER JPEG — an
  archive must be rebuilt bit for bit by every djxl, never drop it (every
  lossy PIXEL encode of the toolkit likewise passes `--strip_alpha=0` with
  cjxl 0.13+, `_keep_alpha_flags` in the common module, #670: cjxl 0.13
  drops an all-opaque alpha otherwise — the encoder's `invisible_flag`, the
  recompressor's `_keep_invisible_flag`, the transcoder's convert); ships a
  `--repair-jbrd` audit/repair mode for archives written by affected
  v2.0.0–v2.0.3 versions — its "OK" for an archive with a `checksums.md5`
  line means the rebuilt JPEG IS the line's (`REBUILDS ANOTHER JPEG`
  otherwise), and a file it cannot read is `COULD NOT BE READ`
  (`_jbrd_box_state`, common: None = cannot tell; `has_jbrd_box` stays
  True/False), both errors (#738). **The decode direction deletes an archive
  only when its recovered JPEG matched the `checksums.md5` line THIS run**
  (`result[3]`, #733; a skipped JPEG without a line is kept too):
  `djxl --reconstruct_jpeg`'s exit 0 is no proof — djxl 0.12/0.11 rebuild
  ANOTHER JPEG with exit 0 from an archive of #669's kind; never put a
  reconstruction back as the decode direction's proof.
  `DELETE_SOURCE_REQUIRE_MD5 = False` relaxes the ENCODE direction only.
  Auto mode: a colour conversion sends jbrd JXLs to the convert group like a
  resize (#735; `_auto_jxl_output`, the wizard's plan, reads the same flags),
  and a JPEG + the archive (jbrd box) whose line is its MD5 are up to date
  both ways, dropped from EVERY list a group is planned from — the convert
  list too (`_archive_pairs_up_to_date`, #737; not with `--overwrite`); only a
  pair one of whose directions would write, or any pair of a colour/resize
  run (the archive's copy would land on its original), is proven — a plain
  run hashes no JPEG (review S6); the archive is bound to its line by content
  (`_archive_of`: self-hash, else a reconstruction), and an archive alone whose
  copy would land on its JPEG is left as it is (`_archives_onto_their_jpeg`).
  The encode direction (JPEG/PNG → JXL) skips the decode-output folders where
  THIS run writes them (`_decode_output_folder_names`: the shipped names
  always; a renamed `RECOVERED_JPEG_FOLDER` or non-default `--output-name` in
  modes 1/3 only; the run's `--export-jpeg-folder` in modes 6/7 only — NEVER a
  settings name for every mode: a symmetric setup hid the user's own JPEG
  folders, second review R2) and mode 4's `JPEG_SUFFIX_REPLACE_DEC` as a TOKEN
  wherever `_replace_suffix_token` puts it — unless it equals
  `JPEG_SUFFIX_TO_REPLACE` (#736). In mode 6 / 7 without a subfolder it
  leaves out JPEG/PNG sources carrying `jxlphoto-derived`
  (`_drop_derivative_pairs(..., overwrite=)`, which reads them with
  `_image_xmp_mentions` — the XMP segment in Python, no ExifTool — only for a
  pair that would write, with `--overwrite`, when `DELETE_SOURCE` is on, or
  whose output another source shares; auto mode does that across its JPEG
  and PNG groups too, `_label_shared_exports`).
  `checksums.md5` lines are filed per output as it
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
  and an existing non-derivative destination is refused. Derivative = resize,
  sharpen or a colour conversion here (`--to-srgb`, `--icc-profile` since
  rc5, #691: `_COLOUR_DERIVATIVE`, `_derivative_output`; `--output-icc`,
  resize or sharpen in the recompressor); a derivative never carries
  `jxlphoto-src`/`srcsum`. `_lossy_decode_keep_reason` keeps a `jbrd` JXL
  against every pixel decode (#692), a master whose 16-bit PNG has another
  profile than the one it carries in XMP (#691's PNGs from older versions: a
  faithful decode embeds exactly that profile, measured) and a master that
  carries `jxlphoto-tifftag` tokens, which no PNG holds (#734). A 16-bit PNG
  export the run did NOT write (skipped) never deletes its master (#747): it
  may have been retouched since — only an export this run writes does. A
  self-hash line naming other bytes falls back to the reconstruction in
  `_jxl_binds_to_archived_jpeg` (#757), and `--repair-jbrd` re-files it. Its
  ImageMagick route writes into the short `TEMP_DIR` folder and Python moves
  the file beside the final name (#754: that temp, 37 characters longer than
  the final path, broke from 223 on). A colour-only run
  leaves to the skip logic (`should_process`), instead of refusing, an
  existing output with no derived label whose marks name THIS source
  (`_check_derivative_targets`, `_provenance_ok`) — never forced: such a file
  may be an older version's colour export, a faithful export or the user's
  retouch of it, and nothing tells them apart (second review of rc5, M1).
  Every derivative (resize, sharpen, colour) leaves out a scanner's IR channel
  (`jxlphoto-extrasamples:0`: `-alpha off` in `_output_magick_args`,
  `_master_has_ir_channel`) and drops that marker (#714, rc6); a faithful
  decode keeps the JXL's channels (the owner's call)
- `jxl_recompressor.py` — JXL → JXL recompressor (v2.1.0): reads the recorded
  lineage chain (`gen=N | cjxl d=/e= | …`, append-only — the encoder and the
  recompressor both append one entry per encode and reconcile `gen` as
  max(stored, lossy-entry count), never increment), refuses counterproductive
  re-encodes (copy/skip/ask), guards repeat lossy generations via
  `--on-regeneration` (gen ≥ 2 + lossy request — encoder outputs are born at
  gen=1, so the guard must not fire on a file's first recompression), copies
  jbrd JXLs verbatim by default, keep-smaller net; same delete gates.
  `--delete-skipped` without `--delete-source` is inert (warning only), like
  the other scripts. An encode record the planning batch could not read is
  "cannot tell", never "no record": the file is left as it is and counted as
  an error (`unread` → action `skip_unread`, #690 — read as unknown origin it
  was re-encoded lossy in place). Its own output folders are skipped by the
  names of its SETTINGS (`_own_output_folder_names`, #710). A 4th channel
  whose `jxlphoto-extrasamples` says it is not alpha (a scanner's IR) is
  dropped before a derivative's conversion and from the thumbnail (#695,
  #700); a derivative run makes no copy of a scanner's IR MASK page
  (`jxlphoto-subfiletype` & 4, `_is_mask_page`, common, #730 — the
  transcoder's `_skip_thumbnail_pages` too); `_derive_pixels` refuses when
  djxl reported neither profile (#739: "cannot tell" is not "own space"); an
  in-place group held by keep-smaller says so (#742); an existing jbrd output (no markers, ever) is
  overwritten only by its own bytes or by an archive of the same JPEG
  (`_jbrd_overwrite_refusal`, #558 — its verbatim copy may be the only one
  once `--delete-source` removed the source); multi-page groups (jxlphoto-mpg, keyed by folder) delete
  all-or-nothing — the gate must see EVERY planned page, including the ones
  that failed, were policy-skipped or refused, or it cannot veto;
  in-place promotion goes through a temp file in the destination folder plus
  an atomic `os.replace`, never a cross-volume move onto the only copy; a
  group is swapped all or nothing (`_swap_group_in_place`, #752: each
  original set aside as `<name>.<id>.jxlphoto-swap`, the re-encode put in
  place, every original back on any failure) — the second name is a HARD
  LINK (the page's name never goes missing; a rename where a volume has
  none), the swap runs under `_run_with_ctrl_c_held` (common: a press waits
  until the group is done, then goes to the run's handler), every page is
  checked right before it (#559) and must have had its marks read (#753), and
  `_sweep_swap_leftovers` removes a leftover second name of the same file
  and names any other one. A verbatim copy (policy, lossy
  ICC blob, keep-smaller) is proven by size + MD5 against its source
  (`_verbatim_copy_verified`, #755): the container walk refuses another
  program's bare codestream, and the gate takes the MD5 for such a copy.
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
  `[D]`) → quality → review (`_review_screen`). `jxl_photo.py
  encode|decode|recompress|transcode ARGS` runs that backend as if started
  directly (`_run_backend`, runpy, the exe's names; dispatched at the TOP of
  the file, right after `_import_common()` and before the wizard's imports
  and definitions — keep it there: v2.31.0-rc4, 35–40 ms a call — so its exit
  code and Ctrl+C are the backend's own).
- **Flags are spelled in full (v2.30.0):** the four `build_parser` have
  `allow_abbrev=False`. Never turn it back on: the wrapper's checks of the
  expert flags, presets and manifests assume a flag means exactly its name.
- `jxl_photo_common.py` — not a script: the helpers the five share, one copy
  each. It must sit next to them (see "The common module").

## photo-backup depends on this repo (the contract)
The sibling repo `photo-backup` (since its v0.14.0-rc2; JPEG since rc3) runs
jxl-photo to keep a TIFF or a JPEG as JXL in a backup and to make it again on
restore, and it reads the marks this repo writes. What it needs is written
down, item by item (JP-1 to JP-23), in `../photo-backup/docs/jxlphoto_contract.md`
(仕様書). In short:
- `jxl_photo.py encode --version` prints `jxl-photo X.Y.Z` (JP-1); a backup
  refuses a version older than photo-backup's `JXLPHOTO_MIN_VERSION`
  (2.31.0-rc3 since its v0.14.0-rc6: the first that refuses a temp folder with
  a `%`, JP-22, and takes ExifTool's `Error opening file` as a failure, #686;
  a test version counts as older than its release, and test versions of one
  number go by their own number) and a restore one older than
  `JXLPHOTO_RESTORE_MIN_VERSION` (2.30.0, the first whose `jxl_photo.py` has
  `_BACKEND_COMMANDS`).
- `jxl_photo.py encode <TIFF> <folder> --mode 0 --distance D --scan-distance S
  --eight-bit-distance E --effort N --workers 1 --verify-roundtrip` (JP-2):
  every page decoded and compared with its TIFF page before it takes its final
  name (JP-20, since v2.31.0-rc1, `_VERIFY_EACH_PAGE`: `--verify-roundtrip`
  without `--delete-source`; a page that fails or cannot be checked is an error
  and gets no JXL; no djxl = exit before any file; a deleting run checks in its
  delete gate only). The contract test counts the log's `>Round-trip verified
  page N (...)` lines against the JXLs written, and photo-backup marks a
  `CONVERT FAILED` with `(JP-20)` when the error says `round-trip
  verification`: never reword either line without changing photo-backup
  first; one
  `.jxl` per page, page 0 `<stem>.jxl` (JP-3); an uncompressed `xml ` box
  before the codestream (JP-4); `jxlphoto-srcsum` = the first 16 hex of the
  SHA-256 of the TIFF's raw SHA-256 digest (JP-5: NEVER change this formula —
  backups already made check it; add a new mark instead); `jxlphoto-pages`,
  `jxlphoto-page`, `jxlphoto-mpg` on every page of a multipage TIFF (JP-6); the
  distances by class (JP-7); the TIFF untouched (JP-8); no JXL claiming a TIFF
  it could not encode (JP-9); the class marks `jxlphoto-scan:` (a scan) and
  `jxlphoto-origin:tiff8|tiff16`, in the order scan > 8-bit > the rest (JP-17:
  photo-backup derives from them whether a TIFF's JXL is lossless).
- `jxl_photo.py transcode <JPEG> <folder> --mode 0 --workers 1 --effort N
  --force-transcode` writes one `<stem>.jxl`, a lossless JPEG recompression, and
  exits non-zero for a JPEG it cannot keep losslessly (JP-15); `jxl_photo.py
  transcode <JXL> <folder> --mode 0 --workers 1 --format jpeg`, with the JXL
  alone in its folder (no `checksums.md5`), rebuilds the original JPEG byte for
  byte (JP-16) — photo-backup keeps a JPEG's JXL only after checking that
  SHA-256, so never put marks into a JPEG's JXL.
- libjxl's own djxl, stdin to stdout (`djxl - - --output_format jpeg`), rebuilds
  that JPEG from the transcoder's JXL (JP-18: photo-backup's proof right after
  JP-15 and its restore of a JPEG — the jbrd archive must be what a plain djxl
  rebuilds, #669). `jxl_photo.py encode|decode|transcode ARGS` is the backend
  script run as the main program (`_BACKEND_COMMANDS`, `_run_backend`), and
  each backend does the same when Python runs it with `runpy.run_module` from
  its folder (JP-19: photo-backup starts the scripts that way, with its own
  bytecode cache).
- `JXLPHOTO_SETTINGS` set and empty turns the settings overlay off
  (`_settings_overlay_path`, JP-21): photo-backup runs every backup and restore
  with the settings written in the scripts, never an overlay made for the
  owner's presets. Its test looks for the setting's name in the INFO line
  `Script settings overlay: ...` (`_log_settings_overlay`): keep that wording.
- photo-backup never gives jxl-photo a temp folder (TMPDIR/TEMP/TMP; `TEMP_DIR`
  is empty without the overlay) with a `%` in its path, and every backend
  refuses one at its start (JP-22, `_percent_temp_problem`): exit 2 before any
  file is touched, the reason on stderr. The contract test runs an encode and
  a transcode with such a folder and wants exit 2, no file written, the source
  unchanged and a `%` in stderr.
- Since its v0.14.0-rc7 photo-backup converts through `<command> --serve <the
  single call's options>` (encode JP-2, decode JP-10, transcode JP-15, the
  rebuild JP-16), one process per worker thread (JP-23, `_serve`,
  `_serve_job`). It relies on: the first stdout line `{"serve": 1, ...,
  "script": "<backend>.py"}`; a job line with exactly the string keys `id`,
  `input`, `output` (absolute, the folder exists); one answer per job with the
  same `id`, `exit` = the single call's code (WHICH non-zero code is free),
  `error` not empty when it fails — the first ERROR messages, so the JP-20 page
  failure stays in the first two, at most 1000 characters; a job's output
  folder holding what a single call writes; exit 0 when stdin closes; one log,
  `JXLPHOTO_LOG_DIR\<backend>\..._<pid>.log`, with a job's lines written before
  its answer (it copies a failed job's part). Free: `outputs`, the end line's
  numbers, a malformed line's answer, going on after a failed job (it ends the
  process and starts another).
- `jxl_photo.py decode <folder> <out> --mode 0 --workers 1 --depth-policy
  preserve_original --no-preview` gives exactly one TIFF with the original's
  pages and bit depths (JP-10) and leaves its input alone (JP-11).
- With `JXLPHOTO_LOG_DIR` set, nothing is written next to the scripts (JP-12):
  a backup keeps a copy of this folder's top-level `.py` files (+ README,
  LICENSE, docs) and runs it from there on restore, so every runtime file must
  be one of those (JP-13).
**After changing the encoder, the decoder, the JPEG transcoder, the marks,
`_BACKEND_COMMANDS` or `_serve`:**
run photo-backup's contract test against this checkout — `set
PHOTOBACKUP_JXLPHOTO=<this repo>\jxl_photo.py`, then, in photo-backup,
`python -m pytest tests/test_jxlphoto_contract.py -v` (about 30 s). A failure
names its item; the contract doc's table of versions says which jxl-photo
version passes which items. A change on purpose to an item: photo-backup
changes first.

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
  file from the child's own table (no copy of types in the wrapper), then
  applies it again to the scripts the wizard has imported
  (`_refresh_child_overlays`: from `_SETTINGS_OVERLAY["script"]`, the values
  each script had before the overlay, and `_RUN_DEFAULTS` follows) — the
  wizard reads its children's settings in process and sends some of them on
  the command line, where they win over the file (#716).

## The common module (`jxl_photo_common.py`)
- The helpers the scripts share live there, ONE copy each (plan
  `AI_tools/261112_Claude_plan_common-module.md`, branch `common-module`).
  **Where this file calls a helper "parity-pinned" in two to five scripts,
  it is now that one copy in `jxl_photo_common.py`**: fix it there, once.
  It sits next to the five scripts and ships with them: the exe's
  `scripts\`, `jxl-photo_LOCAL`, `tools/ui_tour.py`'s app folder.
- What stays per script ON PURPOSE: each script's own logic (`main`,
  `build_parser`, `process_group`, `convert_one`, `resolve_output`, the
  encoder's and the transcoder's finders, ...); `_derivative_metadata_args`
  (only the recompressor rewrites the CreatorTool ICC for `--output-icc`),
  `_cjxl_buffering_flag` (the transcoder's differs), `_would_skip`
  (encoder-only since #638); and `_import_common`, which loads the module. A
  helper that becomes identical in two scripts goes to the common module,
  never into both. `setup_logger` is common (each script names its logger in
  `_LOGGER_NAME`; the transcoder's last line says `Log:`,
  `_LOG_SAVED_LABEL`), and so are the decoder's and the recompressor's JXL
  finders (`_iter_jxls`, `find_jxls_mode6/7`: the `EXCLUDE_FOLDERS` filter
  runs where the script has that setting) and `_abort_on_duplicate_outputs`.
- The prose that explains a name the common module defines sits over its
  definition THERE, once; a script keeps the bare binding (and its section
  header). Prose about a setting or state that stays in the script stays
  with it.
- Each script loads the copy in ITS OWN folder (`_import_common`, the one
  helper every script keeps, parity-pinned) — never another copy found on
  `sys.path`: a test or a pre-fix proof loads scripts from another folder,
  and the exe also carries a frozen copy. A missing one or one of another
  `TOOLKIT_VERSION` is an `ImportError` with the way out ("copy every file
  of the release together") — a script run on its own prints that line and
  exits, without a traceback; an in-process import still gets the
  `ImportError`. The wrapper's version warning lists it with the children
  (`_VERSIONED_CHILDREN`).
- **Two kinds of function.** A pure helper (reads nothing from the script)
  is bound by name: `X = _common.X`. A helper that reads a setting, the
  logger, `__file__`, the run state or calls another toolkit helper takes
  the script's namespace `g` as its first parameter and reads all of it
  through `g` at CALL time (`g.TEMP_DIR`, `g.logger.warning(...)`,
  `g._run_captured(...)`); the script keeps a one-line shim
  `def X(*a, **k): return _common.X(_G, *a, **k)`. Why: the overlay, the
  per-run reset (`_RUN_DEFAULTS`) and the wrapper's `_with_child_marker`
  change the SCRIPT's globals; a test's `monkeypatch.setattr(enc, ...)`
  must reach the code; each script logs to its own logger (a
  `getLogger("jxl_photo_common")` has no handler); `_history_args` writes
  the script's name (`g.__file__`) into every photo's xmpMM:History. The
  state (caches, staging holds, the abort flag, `_planned_outputs`) stays
  defined in each script and is read through `g`: the suite loads the four
  modules in one process.
- **Never** a setting there (the settings stay at the top of each script),
  never `__file__`, `__name__`, `logger` or `globals()` read directly
  there. `tests/test_common_module.py` enforces it, plus: every global read
  there is defined there (a moved helper with a bare `TEMP_DIR` would raise
  NameError only on its rare path), one copy only (a script binds or shims
  a common name, never defines it again). `tests/test_common_g_reach.py`:
  every `g.<name>` a script can reach in common exists in that script (an
  AttributeError otherwise, on the rare path only).
- **Test doubles.** A test patches the SCRIPT (`monkeypatch.setattr(enc,
  "_run_captured", fake)`, or `enc.time` / `enc.subprocess`), as before. A
  `g`-function sees it through `g`. For the names the common module reads
  DIRECTLY (a pure helper, a standard module), conftest mirrors the double
  into the common module for the test, when the common code that script
  uses reads that name — the moved code sees what it saw when it lived in
  the script. The mirror sits on `pytest.MonkeyPatch.setattr` itself, so a
  `MonkeyPatch.context()` is covered like the fixture. The `mock.patch`
  forms (`patch.object(enc, "X")`, `patch("jxl_tiff_encoder.X")`) cannot be
  mirrored and are refused statically (`tests/test_common_module.py`).
- Moving a function there: every copy byte-identical first; a function that
  reads nothing from the script is bound by name, any other one takes `g`
  and reads every script global through it; run state stays in the script
  (an UPPERCASE name can be state too: `_STAGING_HOLDS`); a constant moves
  only when it is identical, internal (never a setting, never run-scoped)
  and not built from a constant that stays (`GUARD_DOC_URL` reads
  `TOOLKIT_URL`).
- A pre-fix proof extracts `jxl_photo_common.py` from the same commit as
  the scripts (see Verification).

## Architecture gotchas
- **djxl returns a lossy ICC-blob file in LINEAR sRGB**: never paste/assign the
  original ICC without checking `--icc_out` == `--orig_icc_out`
  (`_djxl_icc_args`/`_decoded_in_original_space`, parity-pinned in all four
  scripts). The correct decode is a float PFM CONVERTED to the original
  profile; the decoder fails closed when magick is absent.
- **Full-size decodes stay in memory (round 87, v2.30.2-rc2).** The
  round-trip check before a delete (#673), the recompressor's profile probes
  (#674), the jbrd gate's rebuilt JPEG (#679), a derivative's source (#680)
  and the decoder's pixels (#681: roundtrip and none modes, `basic` and an
  ICC blob keep the PNG) come from djxl's stdout (`_run_to_memory`;
  `_pam_header`/`_pam_array`, common), never a 16-bit PNG in `TEMP_DIR`
  (230 MB and 8.6 s of libpng deflate per 45 MP page). djxl writes a PAM/PPM
  at the FILE's depth whatever `--bits_per_sample` says (0.12 and 0.13; only
  its PNG writer obeys), so every reader checks MAXVAL (255/65535 only, a
  12-bit file keeps the PNG), a decode that gets no PAM at all keeps the PNG
  too (djxl writes none for 32-bit float samples, #683: a failed PAM decode
  never raises before the PNG route has run), and a route that asks another
  depth keeps the PNG
  (a full-size 8-bit JPEG of a 16-bit master: fed 16-bit samples, its pixels
  came out up to 46 levels apart). A derivative takes the memory route only
  for an RGB/RGBA master with its ICC in XMP decoded in its own space
  (`_decode_in_memory_for_magick`, `_decode_in_memory_for_derivative`); a JPEG
  then gets the master's EXIF resolution from `_jfif_density_args` (the PNG's
  EXIF gave it to ImageMagick before), and anything else or any failure keeps
  the PNG route. The master's tags a derivative reads come from ONE cached
  exiftool process (`_source_tags`: keyed by path, size and mtime_ns, only for
  a file unchanged for 5 s). The decoder writes the JPEG preview as page 1 of
  its FIRST TiffWriter (`_jpeg_preview_array`; `_preview_page_kwargs` passes
  `metadata=None, software=''`, like every page, so `copy_metadata` runs no
  `-ifd1:` clean-up, #682) — never rebuild the finished TIFF for it again.
  ExifTool writes a whole temporary copy of a TIFF for ANY command, even one
  that changes nothing (measured 2026-11-15: 241 MB, 0.7 s per 45 MP TIFF):
  never add an exiftool command on a TIFF just in case; a clean-up that may
  find nothing waits for a read, as the IFD0 Software/ImageDescription ones do. A grey image on the
  transcoder's ImageMagick route gets `+profile "!icc,*"` (#672): without a
  profile step ImageMagick keeps the codec's EXIF/XMP from djxl's PNG, and a
  master's XMP over 64 KB becomes a broken APP1 that turns every later
  exiftool write into a no-op. Every change of this round was proven byte
  for byte against the code before on real files, with the clock frozen.
- **The recompressor converts colour in Python (v2.31.0-rc2, `FAST_COLOR_CONVERSION`).** An
  `--output-icc` derivative of a master decoded in memory, with no resize or sharpening, is
  converted by `_convert_matrix_shaper` (common) when both profiles pass
  `_matrix_shaper_profile` (colorants + `curv` gamma/identity or `para` types 0/3, no LUT tag,
  curves from black): littleCMS's math curve by curve (cmsgamma.c types 1/4 and their
  inverses), float32, the target curve read from a 2**20 table at sqrt(linear), in chunks of
  65 536 pixels with the buffers allocated once, the tables built once per run and shared by
  every worker (`_colour_transform`), then `make_png_bytes` (common) to cjxl's stdin — no
  ImageMagick, no `conv.png`. Each of those three choices was measured: `pow` per sample,
  tables per file (out of the CPU cache with 24 workers) or another chunk size made the SRGB
  run no faster, or slower, than ImageMagick. Proven against ImageMagick (≤ 1 in 65535) on real
  masters and in-gamut colours; near black, a colour outside the target's gamut converted to a
  pure-gamma target can differ 2 or 3 levels, where ImageMagick's own result also strays from
  the exact computation (`test_out_of_gamut_cube`). Anything else returns None and keeps
  ImageMagick. Never widen what is accepted (table curves, other `para` forms, LUT profiles)
  without the same proof; the PNG a worker holds while it waits for a cjxl slot is budgeted
  (`_FAST_PNG_BYTES_PER_PIXEL`). Since v2.31.0 the ImageMagick route's PNG stays in memory too
  (`png:-` read by `_run_to_memory`, whose `input=` writes the raw pixels first: ImageMagick
  reads its whole input before it writes; the iCCP checks and `_png_without_colour_chunks`
  take the bytes) — no `conv.png` in `TEMP_DIR` — and every derivative run budgets that PNG.
  `_run_to_memory`'s stdout pipe is 1 MB on Windows (`_output_pipe`, `_PIPE_BUFFER`):
  ImageMagick writes a PNG in small pieces, and through the default pipe each piece waited
  for the reading thread, which sleeps while the pipe is empty — a 2048 px derivative's
  13 MB PNG took 2.0 s against 1.0 s for a file, and the resize run was slower than with
  `conv.png` (found after the phase-1 review; djxl writes large blocks and never noticed).
  Never go back to `subprocess.PIPE` there.
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
  `_exif_box_rank` (parity-pinned); the encoder's XMP-block command
  (`_xmp_block_preload_args`, first of its metadata writes) writes the XMP
  before the EXIF, so without that sort every master comes
  out XMP → Exif (v2.17.0–v2.19.0 did). `--refresh-explorer-thumbnails`
  repairs existing files in place (`_move_exif_first`, status `REORDERED`).
  Any new path that writes boxes must keep that order: the rule has ONE copy,
  `_ordered_box_parts`, used by `reorder_jxl_boxes` and by the encoder's
  one-pass assembly (`_assemble_from_stub`, #675). The Exif box must
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
  (`_hidden_generations`); no delete gate reads the mark itself. An input
  whose marks could not be read (`_read_origin_inputs`: no answer, an error,
  no entry) is named in `_ORIGIN_UNREAD` — run state of the encoder, the
  transcoder and the recompressor, cleared per run, in `_SERVE_FILE_CACHES` —
  and a deleting run keeps it (#753): its output has no origin, and only
  that input could give it one. The transcoder writes ONE
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
  all five scripts. Since v2.30.0 the `SoftwareAgent` ends with
  ` with libjxl <v>`: every caller passes `codec=` (its own cjxl/djxl command
  name — cjxl for a JXL written, djxl for a decode) and `_history_args` reads
  the version from `_tool_version`'s per-run cache (no process per file;
  omitted when unknown). Write-only like the rest; a new History call site
  passes `codec=` too. The recompressor's cjxl runs with `-x strip=xmp`: its
  carried XMP plus the restamp's `-tagsfromfile -xmp:all` makes exiftool
  ADD the copied list to the carried one for every `+=` in that call,
  doubling the History and `dc:Relation` (#531). Do not remove it. The
  record clean-up (`_strip_encode_params`) takes out only the toolkit's own
  blocks and keeps the rest of a field byte for byte (#697: it re-joined
  every `|` of a caption).
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
  still names the original profile). The full-size decode never touches
  the disk (#671: a 272 MB PAM per 45 MP output in TEMP_DIR set the pace
  of a whole rebuild on a slow SSD): djxl writes the PAM to stdout, read
  into memory by `_run_to_memory`, and magick reads the samples RAW from
  stdin (`_pam_raw_input`, `-size WxH -depth N -endian MSB rgb:-` then
  `+size +depth +endian`, or `-depth 16` stays in force and the JPEG
  changes). Never hand magick a PAM/PNG/MIFF on stdin: it copies such an
  input to a temp file first (measured), raw samples it does not. magick
  starts after djxl has EXITED — djxl writes `--icc_out` after the image.
  A PAM raw samples cannot carry (a 12-bit MAXVAL) goes through
  `thumb.pam`, the old route. The refresh
  (`--refresh-explorer-thumbnails`) restores each file's mtime: the
  encoder's sync compares it with the TIFF's. Helpers parity-pinned
  (encoder + recompressor). **Since rc5 (V2, the owner's yes) the thumbnail
  is made from pixels the script already holds** where it has them: the
  encoder's TIFF page before cjxl, in the page's own profile (`icc_bytes`,
  the real one even when the PNG goes without it), and the recompressor's
  Python colour conversion (`_MEMORY_THUMB`, keyed by the source) —
  `_thumbnail_from_array` (raw samples on magick's stdin in the array's own
  byte order, `_array_raw_input`), through the decode route's own command
  (`_thumbnail_jpeg`, one copy). Proven against the decode on 73 real outputs
  (identical wherever the TIFF has a profile). Any failure falls back to the
  decode of the finished file, as before.
- **A split's group id hashes its page set (#303)**, so a TIFF re-encoded
  with FEWER pages would leave the old pages under the old id (#551). After a
  run, `_clean_stale_split_pages` removes a leftover THUMBNAIL page of a TIFF
  the run rewrote completely (same `jxlphoto-src`, a group id the new pages do
  not carry, the TIFF still on disk) and only reports a leftover REAL page —
  never delete one. Derivative runs of the recompressor skip `jxlphoto-thumb`
  pages (#552) and a camera's UNFLAGGED thumbnail page (#642:
  `_is_camera_thumbnail_page`, parity-pinned with the transcoder — a page
  other than the first, at most 160 px; the encoder keeps archiving it as a
  real page so the TIFF comes back whole, do not reclassify it there: the
  decoder restores every thumbnail as SubfileType 1 and `exclude` would drop
  it) and a scanner's IR mask page (#730: `jxlphoto-subfiletype` & 4,
  `_is_mask_page`); a plain recompression keeps them so the split stays complete.
  So does the transcoder's resized/sharpened/colour-converted JXL -> JPEG/PNG
  (`_skip_thumbnail_pages`, #555; no marker read on a dry run).
- **Each manifest entry runs as a SEPARATE child process.** A child's own safety
  checks (`_abort_on_duplicate_outputs`, the output-vs-input collision guard)
  can therefore never see a problem that spans two entries — those guards have
  to live in the wrapper. Two v1.8.1 bugs came from exactly this blind spot.
  Each row's options (`_row_effs`) are COPIES of the run's: a question that
  changes the run's options (`_confirm_lossy_delete_skipped`) is followed by
  making them again (#712). A file Source's tree is its folder in
  `_manifest_needs_collision_scan` (#715); a mode-2 Destination inside its
  folder Source is in place when the recompressor's plan says so
  (`_folder_row_replaces_files`, #723); a missing Source is that row's error,
  the others run (#724); a cancelled row of an unattended manifest is no
  success (#723). "Does this JXL → JXL run replace its sources in place?" has
  ONE answer for the direct run, a repeat and a preset:
  `_jxl_run_replaces_in_place` — the wizard's in-place confirmation, the
  unattended refusal and the child's `--delete-confirm-off` (`_policy_delete`,
  through `_recompress_entry_in_place`) must never disagree; modes 3–7 ask the
  recompressor's plan with a probe name in BOTH helpers
  (`_recompressor_plan_maps_onto_itself`, #759: mode 5 run AT
  `JXL_recompressed`, mode 6 at `<marker>\16B_JXL_small`), under the run's
  marker (`_mode_config_with_marker`) and its expert flags (rc6's review M1:
  the flag went out for a mode-2 run whose confirmation was keyed on modes
  0/8, and nobody was asked). A manifest decides each row's answer ONCE,
  before its confirmation, and hands it to the row's command
  (`_in_place_planned`): read again at build time, the disk showed the JXLs an
  earlier row had just written (second review S1).
- **`--delete-source` works in every mode** (since v2.0.0). Every delete path is gated
  by an integrity check plus (for JPEG recovery) `djxl --reconstruct_jpeg` or a
  same-run MD5 match. In a deleting run, a copy out of staging to another
  volume is compared with the staged file (size + MD5) before either goes
  (`_promote_from_staging`, `_same_volume`, #749); one volume is a rename. Keep those gates fail-CLOSED: an unverifiable output must
  block deletion, never be waved through. The encoder's pixel checks
  (`VERIFY_ROUNDTRIP`, 74 % of a deleting run) are started up front in a pool of
  the run's memory-capped worker count (`_delete_sources`, v2.31.0) — only for
  the sources the cheap gates before them do not already keep
  (`_check_worth_starting`: pages settled, nothing left out, the provenance of
  a skipped output, read first by `_skipped_archive_proof_of`) — and the
  gate's loop (`_delete_checked_sources`) reads each result where it called the
  check: same order of checks, same lines; a source's check still comes before
  its own delete. A check that raises keeps its own source ("the check itself
  failed") and the loop goes on judging the others.
- **"Cannot tell" is not "no marker" (#563).** `_read_source_markers_batch`
  (parity-pinned in the four backends) marks a file whose batch failed,
  stalled or returned no entry with `unread: True`; its src/srcsum stay None.
  Every overwrite guard refuses an unread output while `OVERWRITE_GUARD` is
  on (warning when off) and the strict guards name the cause; the delete
  gates already fail closed on it. A new consumer of that reader must not
  read an unread file as a legacy markerless archive.
- **Planning-time batch reads run on ONE engine (refactor F7):**
  `_exiftool_batch` (common) does the chunks of 400, the argfile,
  `EXIFTOOL_BATCH_TIMEOUT`/`EXIFTOOL_BATCH_STALL` (#537) and the progress
  ticks (#566: without them a slow disk makes the wrapper stop the child as
  hung). Each reader keeps its rules in callbacks run where its code ran
  (`on_data` inside the same try, `on_no_output`, `on_error`, `on_done`;
  `strict=True` when a non-zero exit with output still counts as unread;
  `progress=False` for a reader with its own progress lines). A new batch
  reader uses it: `tests/test_batch_stall.py` fails on one that loops on
  its own. `python tools/batch_equivalence.py <old rev> <new rev|WORKTREE>`
  runs every reader old against new on the same fake exiftool answers.
  Since #676 the engine hands exiftool a metadata STUB of each JXL container
  (`_batch_on_stubs`: every box but the Explorer thumbnail, the codestream cut
  to its first bytes, in a `jxlstub_` folder of `TEMP_DIR`, named by index)
  and maps the answers back — exiftool reads the WHOLE file for any question,
  `-fast` or not. Anything less than a clean answer for every stub re-reads
  that chunk from the files; a bare codestream is read itself. A reader needs
  nothing for it; `tests/test_planning_stubs.py` compares the ten readers,
  stub against file, on real files.
- **Per-file exiftool calls use `EXIFTOOL_TIMEOUT`** (each script defines it
  as its codec timeout), never a literal: a 60/120 s limit turned twelve good
  45 MP files into errors under a 17-worker load on 2026-10-06. Only the
  planning-time `*_batch` reads keep their per-batch limit.
  `tests/test_exiftool_timeouts.py` scans every call.
- **A file's exiftool commands share ONE process (v2.30.0, refactor F6).**
  `_exiftool_commands` (common) runs them with `-execute` in between, each
  with its own status, stdout and stderr (`-echo3`/`-echo4 ${status}`
  markers; a missing marker = status -1, fail closed), when
  `_exiftool_batch_ok`: the script's OWN `_run_exiftool_argfile` and
  `_run_captured` are in place (a test double sees every command alone, as
  before) and exiftool reports each status (12.15+, probed once per run in
  `_EXIFTOOL_BATCH`; conftest clears it per test). Otherwise one process per
  command, exactly as before. The encoder's writes after cjxl go through
  `_run_exiftool_writes` (the first failure raises its old message), on a
  STUB of cjxl's output since #675 (`_write_metadata_stub`;
  `_retarget_exiftool_writes` copies each argfile with the stub's path in
  place of the output's — never edits one, None when that path is not there
  exactly once); `_assemble_from_stub` then writes the master ONCE (boxes,
  thumbnail box, codestream) instead of seven rewrites, and any failure of
  that route falls back to the old steps on the untouched file; its
  reads in `build_metadata_injection_args` are prefetched
  (`_prefetch_exiftool_reads`: answered only for the EXACT same arguments,
  read-only commands only — never an `-o` extraction, which fails when run
  twice); the decoder's `copy_metadata` is two batches and a failure never
  stops the next (`stop_at_failure=False`). A new exiftool call on those
  paths joins the list; a command that must stay apart from another (the XMP
  block before the edits, `-wm cg`, a clear before a `+=`) stays a separate
  COMMAND, which no longer costs a process. **The decoder rewrites the TIFF
  ONCE (v2.31.0):** ExifTool rewrites the whole TIFF for any command that
  writes, so the JXL's XMP block goes into a small file of its own first
  (`-o <tmp>.xmp -tagsfromfile <jxl> -XMP`, byte for byte; a `%` path: the
  file `_xmp_block_args` read), the XMP edits (the dc:Relation all-`=` lines
  of `_collapse_list_reset`, provenance, pixel sum, History, the orientation's
  XMP line) are made THERE, and ONE command writes the EXIF, `-XMP<=` the
  edited block and the IPTC into the TIFF. Never move an XMP edit into the
  TIFF's command while it writes the block: the block wins and the edit is
  ignored. A `%` in the temp folder (ExifTool expands it in the names of `-o`
  and `<=`): every backend REFUSES such a folder at the start of a run and of
  a serve process (`_percent_temp_problem`, the effective folder — TEMP_DIR,
  else the system's; the owner's call, 2026-11-21: some steps failed file by
  file there and the encoder silently kept only the XMP fields ExifTool can
  write). `copy_metadata` keeps the old route for a direct call: the block
  straight from a plain-named JXL into the TIFF, the edits in a second
  command (`direct_block`). The batch
  ends with a read of the TIFF's dc:Relation, and the copy fails unless
  this run's `jxlphoto-src` is there (Fable's review: a block file ExifTool
  could not open gave a TIFF with the EXIF only and an OK). The
  dc:Relation the edits decide from is the JXL's own (read
  before). The Software/ImageDescription clean-ups read the TIFF after it and
  run only when they find tifffile's defaults (a master made from a TIFF
  tifffile wrote); they are cosmetic and never fail the copy. The encoder's PNG for cjxl is
  zlib level 0 (B2: 16-bit photo data barely compresses; 6.55 s → 0.40 s of
  Python per 43 MP file). Peak Python memory per pixel is the same for an
  RGB page and ~1.3–2.9 B/px higher for a 16-bit page of 4 channels (RGB+IR,
  RGBA), where the level-1 PNG used to shrink; `_PYTHON_BYTES_PER_PIXEL`
  budgets per channel (review V5).
- **ExifTool kept open (`EXIFTOOL_STAY_OPEN`, v2.31.0).** On Windows every
  per-file ExifTool command goes to an ExifTool started once with
  `-stay_open True -@ -` (~15 ms a command against ~200 ms for a fresh
  process, measured 2026-11-21): `_exiftool_via_server` (common), used by
  `_run_exiftool_argfile` (after the prefetch lookup), `_exiftool_commands`,
  `_prefetch_exiftool_reads` and `_run_exiftool_file` (the encoder's two
  argfile calls; `binary` keeps a `-b` answer as bytes). Each script has its
  own pool (`_EXIFTOOL_POOL = _common._ExiftoolPool()`, state of the script;
  the transcoder and the recompressor have `_EXIFTOOL_BATCH` for it): a thread
  takes a process for one call and gives it back; processes are told apart by
  ExifTool command AND working directory; idle ones are kept up to the most
  ever busy (at least 4, at most 8: ~50 MB each after a big TIFF, outside the
  memory budget), the most recently used taken first; one idle for 120 s is
  closed at the next call, one is replaced after 1000 commands, all close at
  exit. Its rules: used only when `_exiftool_batch_ok` and the script's
  `subprocess` (module, `run`, `Popen`) is the real one (a test double of a
  runner or of subprocess turns it off, so the double sees every command as
  before); ONE command at a time (two in the pipe could deadlock: a long one
  fills stdin while the answer to the one before fills stdout); the answer is
  read in the calling thread with PeekNamedPipe, each byte looked at about
  once (`_AnswerScan`: searching from the start after each 4 KB read made a
  32 MB answer quadratic) and the deadline checked every round
  (`_spawn_exiftool_server` is the only new subprocess owner,
  `tests/test_subprocess_capture.py`); a timeout kills the process and raises
  `subprocess.TimeoutExpired`; a command written to it that it ended without
  answering gets status -1 and is NEVER sent again (a write could run twice,
  even one it may have died just before reading), one that could not be
  written at all goes once to a fresh process. ExifTool does not end when its
  stdin closes (measured), so every process is in a Windows Job Object of its
  own (kill on close; `kill()` ends the whole job, so a launcher's child — an
  old PAR-packed exiftool.exe, a Chocolatey shim — goes too): it never
  outlives Python, however Python ends. No Job Object = no kept-open process
  (the old path). The planning-time batch reads (their stall rule) and the
  `-ver` probe stay processes of their own (a probe on the kept-open process
  would wait the whole timeout for an ExifTool without `-echo4`).
  **A command that changes how LATER commands write or read XMP never goes
  there** (`_taints_xmp_prefixes`): (1) a command that writes XMP fields
  copied from a file (a field copy `-xmp:all`/`-all:all`/`-xmp-<ns>:…`, or
  `_exiftool_source`'s MIE dump): afterwards ExifTool writes some namespaces
  with ITS prefix (measured: Google's container item `GItem:` where the file
  and a fresh process keep `Item:`); (2) a struct write other than
  xmpMM:History (`_STRUCT_WRITE`): afterwards that struct's flattened tags
  are named differently (`Thumbnails={...}`, then `ThumbnailWidth` read back
  EMPTY with status 0 — Fable's review). The History append, then the
  `HistoryParameters` reads the scripts make, answer as a fresh process does
  (`test_the_history_append_then_its_reads_match_a_fresh_process`): a new
  struct write or a new read of a flattened struct tag needs the same proof.
  Such a command — or a batch holding one — runs in a process of its own,
  exactly as before v2.31.0. Each command ends with `-execute<N>`: ExifTool
  answers `{ready<N>}`, so a command it refused BEFORE running it (an option
  that took the `-echo3` line as its value) is a complete answer at once —
  status -1, its own words — and that process is retired, instead of a wait
  of the whole timeout. **"Error opening file" is a failure**
  (`_unread_source_failed`, every entry point): ExifTool answers a
  `-TAG<=FILE` that exists but cannot be opened (held by an antivirus) with
  that line and exit status 0 when the command wrote anything else, and the
  output then lacks all that file held. Not closed: an ExifTool that runs as
  a launcher could start its child before `_put_in_job` (none here; 13.59
  has no child process). `tests/test_exiftool_stay_open.py`.
- **Serve mode (`--serve`, v2.31.0; encoder, decoder, transcoder).** For a
  program that converts many files one call at a time (photo-backup since its
  v0.14.0-rc7: JP-23 in "photo-backup depends on this repo" lists what it
  relies on — a change there goes through photo-backup first). The
  script's `__main__` calls `_common._serve(_G, main, argv)` instead of
  `main()` when the command line has `--serve` (`main()` itself refuses the
  flag): one JSON job per stdin line (`id`, `input`, `output`: absolute paths
  only — an empty string was the process's working folder), each run as
  `main()` with `sys.argv` = input, output and the serve line's options, one
  JSON answer per job (`exit`, `outputs` new in the folder, `error` = the
  FIRST two ERROR lines the job logged, the cause, as photo-backup reads a
  single call's output; else the last line it printed on stderr), a
  presentation line first and an end line at EOF. What a job prints on stderr
  (argparse's usage from a check `main()` makes itself, tifffile's warnings
  through `logging.lastResort`) is caught (`redirect_stderr`) and copied into
  the log: one usage text per job filled an unread stderr pipe and stopped
  the process. The abort latch of the job before is reset (`_reset_abort`).
  Its rules: refused before the first job (`_serve_refusal`, the script's own
  parse plus the EFFECTIVE `DELETE_SOURCE`/`DELETE_SKIPPED`): any delete, a
  confirmation switch, `--clean-staging`, `--help`/`--version`, and each
  script's maintenance modes (`_SERVE_REFUSED`; the encoder's
  `--provenance adopt` in common); stdin and stdout become the channels and
  fds 0/1 point at NUL (nothing printed can corrupt an answer or fill a pipe
  nobody reads); ONE log for all jobs (`_SERVE_LOG`: `setup_logger` hands it
  back); every per-file cache keyed by path/size/date is emptied before each
  job (`_SERVE_FILE_CACHES`, and run state a run clears only at its end, the
  `_planned_outputs`), so **a new module-level container that remembers files
  across runs goes in that tuple** (`test_a_job_leaves_no_state_for_the_next`:
  job B after job A and after job C must leave every data global of the
  script and of common the same, scalars included, in all three scripts);
  an unexpected exception is answered and ENDS the process (state may be
  half done); on Windows the process puts itself in a kill-on-close Job
  Object, so its cjxl/djxl/ExifTool die with it. The wizard never sends it:
  `NOT_OFFERED`, and `_expert_flags_problem` refuses it. `tests/test_serve.py`
  (subprocesses: the loop moves fds 0 and 1).
- **Ctrl+C (#702, rc5).** While a backend's pool runs, the first Ctrl+C
  sets the run's own flag `_ctrl_c_seen` and latches the abort switch
  (`_ctrl_c_finishes_the_run`: a SIGINT handler in the main thread, no lock —
  that thread may hold `_abort_lock`): no file starts, the ones in progress
  finish, NO source is deleted after it (every delete gate asks `_interrupted`
  per source, the recompressor's too), the summary is printed, and `main()`
  ends with KeyboardInterrupt after it (`_end_if_interrupted`), which
  `__main__` turns into exit 130; a serve process ends with 130 without
  answering that job, as before. A second Ctrl+C raises at once, also after
  another abort (a full disk). The handler's entry clears the flag. A press
  during a planning stops at once, as before: the transcoder enters the
  handler in `main()` UNARMED (`armed=False`), before its commands' planning,
  and their own `_reset_abort` arms it (`_ctrl_c_handler_active`; it also
  keeps a press already latched). A new pool and a new delete gate go inside
  that block, and a gate asks `_interrupted` at each source and again right
  before its delete. The recompressor's gate runs inside it too, its first
  pass asks `_interrupted` before each costly check, and a group's deletes in
  the recompressor and the decoder run under `_run_with_ctrl_c_held` (common;
  #751, #762: a press waits until the group is done, then the run's handler
  acts). The encoder's planning pools cancel their queued work on the way out
  (#761). The suite's conftest clears the latch and the flag after
  every test.
- **A version probe that does not answer is not "an old cjxl" (#694).**
  `_tool_version` tries twice (`_VERSION_PROBE_TIMEOUTS`, 10 then 60 s) and a
  run whose cjxl version stays unknown warns once (`_warn_if_version_unknown`,
  naming #669 and #670): the 0.13 flags (`_jpeg_archive_flags`,
  `_keep_alpha_flags`) are left out then.
- **The failed files again at the end (v2.31.0).** Without `--summary-json`,
  each `emit_summary_json` calls `_log_failure_recap` (common): `Errors (N):`
  and the first ten `  -> name: reason` at WARNING. Never ERROR: each failure
  already logged its ERROR line and photo-backup's `_JXLPHOTO_ERROR` counts
  the lines that START with ERROR. The transcoder's `main()` resets its
  `_run_summary` first (`record_summary()`), or a serve job could repeat the
  failures of the job before it.
- **A tool's error text goes through `_stderr_tail` (#688, v2.31.0-rc4).**
  One line: the lines that say something (not libjxl's banner, progress or
  speed, `_STDERR_NOISE`), whole, joined by ` | `, the last ones that fit in
  `_STDERR_LIMIT` (400); a line that does not fit keeps its start and its end
  (`_cut_middle`). A message that quotes what a tool printed uses it — never
  `stderr[:N]` or `[-N:]` again: the cut fell inside a sentence or quoted only
  cjxl's banner, and the cause went to the next log lines, which carry no time
  and do not start with ERROR (photo-backup reads the lines that do; the serve
  answer's `error` and `--summary-json` carry the same text, and the recap at
  the end shortens it in the middle to 300 characters, `_cut_middle`).
- **A TMP of 261 characters or more (#689, v2.31.0-rc4).** On Windows a
  process whose TMP (its TEMP only when TMP is not set at all: an empty TMP
  is taken) is 261 UTF-16 units or longer hangs inside `GetTempPathW`, the
  call that hands out the temp folder — and starting ANY program makes it
  (measured: Python starting `cmd` or `djxl --version`; 260 runs; a child
  GIVEN such a TMP by a normal parent runs). `_long_temp_problem` (common) is
  checked right after `parse_args` in every backend's `main()`, in
  `_serve_refusal` and in the wizard's `_main()`, before the first child: a new
  entry point that starts a program, or calls anything that asks Windows for
  its temp folder, checks it first. Python's `tempfile` reads the variables
  itself and does not hang. `TEMP_DIR` is not concerned (never put in a
  child's environment).
- **EXIF is copied by VALUE (#684, v2.31.0):** every `-tagsfromfile ...
  -exif:all` is `-exif:all#`, and `_exiftool_source`'s MIE container has
  `-exif:all#` after its `-all:all`. A printed value converted back is lossy
  (MaxApertureValue f/2.83 → f/2.8, FocalLength 6.62 → 6.6 mm); the value is
  exact to ExifTool's rational precision (an APEX rational is rewritten from
  its number: ApertureValue 8.99999999 can come back 9.00000007). A TIFF never
  gives `-b -Exif` a block (0 bytes, every TIFF measured), so the copy is the
  master's EXIF. A new EXIF copy uses `#` too.
- **One decode per page for the Explorer thumbnail and the page check
  (v2.31.0):** while `_VERIFY_EACH_PAGE` (photo-backup's
  `--verify-roundtrip`), `convert_one` sets the encoder's thread-local
  `_KEPT_DECODE.wanted`; `_make_explorer_thumbnail` then keeps its PAM with
  `_codestream_digest` (sha256 of the codestream payloads), and
  `_decode_jxl_for_verify` takes it (`_take_kept_decode`,
  once) only when the file it checks has the same digest — pixels and profiles
  come from the codestream only. Normal runs keep nothing. (Since rc6 the
  thumbnail's djxl always writes `--orig_icc_out`: a decode in the file's own
  space takes the profile the master carries in XMP when it fits the pixels,
  `_carried_profile_of` — a lossy "skip" master's thumbnail was in sRGB's
  colours, #722.) Anything else decodes as before. Since rc5 the thumbnail comes from
  the page in memory (V2), so the check decodes once on its own; the kept
  decode serves only when that route fails and the file is decoded for the
  thumbnail.
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
  **cjxl's threads (V1, rc5):** while a pool runs, each full-size encode gets
  `--num_threads=max(4, 2 × logical cores ÷ encodes at once)`
  (`_cjxl_threads_for`; `_cjxl_threads_while` sets `_CJXL_THREADS` for the
  pool and clears it after, however it ends; one encode at a time: no flag,
  every core) — the encoder's pool, the recompressor's cjxl slots. The files
  are byte for byte the same with any thread count (measured: lossy,
  lossless, `--buffering 0`, 16-bit RGB, 8-bit gray).
- **Leftover temps.** `_ORPHAN_TEMP_RE` (identical in the four) matches the
  run's temps and ExifTool's own temp of one (`<temp>_exiftool_tmp`, #763);
  a serve answer's `outputs` never lists one. A lone `--delete-skipped` is
  inert in every child's refusals too, as their help says (#756).
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
  recompressed JXL copies carry the `jxlphoto-tifftag` tokens (they can
  still be decoded to a TIFF); a JPEG/PNG never does (#646: `_copy_metadata`
  on a JXL source, `_derivative_metadata_args`). The Photoshop image
  resources (tag 34377, #643) follow the IPTC block's rule — carried at 4 KB
  or less, left out alone when larger (`_photoshop_left_out_note`: one line
  at the end of the run, a warning only for saved paths), never a #620 veto —
  and an IPTC block that lives only inside them is found by `_iptc_stream`
  (both routes of the fill). `_exiftool_source`'s MIE dump keeps EXIF and XMP text but turns
  non-Latin IPTC text into `?` (measured 2026-11-11): never copy IPTC from
  its result — a `%` path goes through `_iptc_only_source`. The two routes
  differ, so a test of the IPTC copy runs in a `%` folder AND in one without.
- Shared helpers have ONE copy, in `jxl_photo_common.py` (see "The common
  module" for what stays per script on purpose). `_verify_file_integrity`
  exists ONLY in the transcoder — a JXL/JPEG/PNG/TIFF superset, not a
  shared copy of `_verify_jxl_integrity`.
- The recompressor's recursive finders skip its OWN output folder names
  (`recompressed_jxl`, `JXL_recompressed`, `16B_JXL_small`, `JXL_small`) — plus
  the configured `EXPORT_JXL_FOLDER` (a custom `--export-jxl-folder` must not
  be re-processed as a source on the next run) — but
  only BELOW the input root: pointing a run AT such a folder to compress it
  again is legitimate. A custom (non-shipped) name that hides files is said at
  WARNING, naming the setting (`_custom_output_folder`; the encoder's
  `_custom_decoder_folder` for the decoder's names, #758) — only for files
  with no source beside the folder or in a sibling one (`_has_source_near`:
  a folder of the tool's own outputs must not warn); the skip stays.
- **The wrapper asks the children which files a run takes AND where each
  output goes (refactor C1, v2.30.0), with the run's own command line
  (v2.30.1).** Its file count (`_count_origin_files`) asks each child's
  `_planned_sources(args, read_markers=False)` and its collision walk
  (`_manifest_output_collisions`, folder AND file rows) asks
  `_planned_pairs(args, read_markers=False)` — (source, output) per file —
  where `args` is the child's own parse (`_child_plan_args`) of the argv
  `build_child_cmd(..., quiet=True)` builds for that run (the direct run's
  workflow, each manifest row's `_row_effective_options`): every flag the run
  gets reaches the plan, the resize, `--output-name` and the expert flags
  included (#663, #664 — the plan used to pick its own few flags and missed
  those). `quiet` only silences the notes some rules print; the argv is the
  run's. The walk runs inside `_with_child_marker` with the marker, subfolder,
  folder and exclusion of that same parse (`_child_plan_globals`), restored
  afterwards. Every `main()`/`cmd_*` resolves through the same
  `_planned_output` (encoder `_plan_one`, decoder per page group, the
  recompressor's rename via `_renamed_output` after its refusals, the
  transcoder's three commands), so the wrapper cannot disagree with a run.
  A new rule about which files or where goes INTO the child's function,
  never into the wrapper; a new flag that moves outputs needs nothing more
  than its registry entry. What stays in the wrapper is its own question:
  two rows on one output, one row's output that is another's Source
  (`cross_sink`), the derivatives re-checked only where files collide, the
  split-TIFF page probe, the count's "files of the origin format". The
  decoder names a TIFF after its page group (markers); the scan asks about
  each JXL, an approximation. The transcoder routes once per run
  (`_planned_groups`) and places each file once (`_output_on_route`); an
  auto run's JXL is placed by its jbrd box, read only when the two routes
  would name the output differently, and never for a derivative, which
  re-encodes every JXL (`_auto_jxl_output`, `_derivative_requested` reads the
  flags, not the globals main() fills). Its count places files only in
  modes 6/7, the only ones that leave a file out, and its finders resolve
  one real path per FOLDER (`_iter_by_ext`; #665: a resolve() per file was
  three quarters of a 40 000-file count). `tests/test_plan_is_the_run.py`
  pins the plan to the scripts' own dry runs of the same argv (19 rows).
  The manifest's decision to skip the collision walk reads each row's marker
  from that same parse (`_row_plan_markers`, #708: an `--export-marker` in
  the expert flags), and a JXL → JXL row is in place when the recompressor's
  own plan says so (`_recompressor_replaces_file`).
  Nothing here may open exiftool or a whole TIFF (#652): the count and the
  walk read no marker and no box. `python tools/verdicts.py <old rev>
  <new rev|WORKTREE>` compares the wrapper's counts and collisions of two
  versions on hostile trees (each version in its own process,
  `tools/verdicts_worker.py`); v2.30.0's accepted differences against
  v2.30.0_beta1 are #655–#659.
- **The modes 6/7 anchor** is `_marker_anchor_index` (#601, parity-pinned in
  the four backends): the first marker match at or below the INPUT root,
  else the closest one above; a path not under the root keeps the first
  match. Never search the absolute path from its start again
  (`Lightroom_Export` above the library matches `_EXPORT`), and never anchor
  on the output folder: until #660 the decoder and the transcoder passed it
  when an output positional was given (an explicit manifest Destination), and
  their outputs landed in `Lightroom_Export/<folder>/<the library's path>`.
- Below the export marker, modes 6/7 of the decoder and the recompressor skip
  the OTHER tools' JXL output folders (#565: `_skip_other_tool_output`, names
  from the siblings' settings via `ast` plus the shipped defaults, per-script
  tables `_OTHER_TOOL_*`; helpers parity-pinned). Never a folder the encoder
  writes masters into (`_MASTER_FOLDER_SOURCES`: `EXPORT_JXL_FOLDER`,
  `CONVERTED_JXL_FOLDER`, `JXL_FOLDER_NAME` — `converted_jxl` is the
  transcoder's name too, M1 of the 261103 review), never mode 7's requested
  subfolder; the transcoder's decode is not folder-filtered (it is how a JPEG
  archive comes back). The wrapper asks the child (`_planned_sources`), never
  a copy. On top of that, the decoder, the recompressor and the
  transcoder's decode leave out DERIVATIVES by their `jxlphoto-derived`
  marker in mode 6 and in mode 7 without an explicit subfolder (#602:
  decoder's marker read, `_drop_derivative_inputs`,
  `_drop_derivative_pairs`); an unreadable marker keeps the file. The
  wrapper's `_without_derivative_collisions` re-checks colliding files.
- **A pixel decode is a full copy only when `_reduced_by_integer_decode`
  (common) says so:** no float, no more than 16 bits, the frames kept, and
  (#750) at most one extra channel, Alpha — jxlinfo's `RGB+Alpha+Depth` is
  not one (the toolkit's RGB + IR masters read `RGB+Alpha`).
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
  encode warns per file. So do a Motion Photo (`MotionPhoto`/`MicroVideo` in
  its XMP) and any data after the picture that no MPF index explains
  (`_jpeg_trailer_keep_reason`, #748; `_jpeg_main_eoi_end` finds where the
  main image ends) — complete JPEG images there are a camera's previews,
  copies like an MPO's (`_tail_is_jpeg_previews`, the owner's call) —, and
  `_copy_metadata` drops the GCamera Motion Photo XMP. `_jpeg_gain_map` reads the JPEG's APP1/APP2 segments
  itself (no exiftool run; the whole file only behind an MPF index) and
  raises OSError = "cannot tell" = keep. A camera's MPO (previews) is not a
  gain map; a `jbrd` output holds the whole file and is not judged. Any new
  delete path for a pixel re-encode of a JPEG needs the same gate. Not
  carried into the JXL: the `jhgm` box exists, cjxl/djxl 0.12 cannot write
  or apply it. The XMP that announces a gain map (`XMP-hdrgm`,
  `XMP-GContainer`) is removed by `_copy_metadata` from every output of the
  convert path (#645); the lossless archive is never edited.
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
- **`jxl_photo.py setup` (the tools on PATH, `# === SETUP` section).** It
  writes PATH values only through `_write_path` (re-read and compare-and-swap
  against what the diagnosis read, a backup in
  `%LOCALAPPDATA%\jxl-photo\path-backups\`, the write, the
  `WM_SETTINGCHANGE` broadcast, a `changes.log` line), never `setx` (it cuts
  at 1024 characters) and never PowerShell; the system PATH only through the
  elevated `--apply-system-path` plan (`_elevated_system_path_edit` →
  `_apply_system_path_plan`, the plan's SHA-256 checked). The suite and
  `tools/ui_tour.py` set `JXLPHOTO_SETUP_PATH_STORE` (a JSON copy of the
  values, `_JsonPathStore`) and a throwaway `LOCALAPPDATA` — conftest pins
  them, `tests/test_setup_tools.py` asserts it: no test touches the
  registry, and neither may a by-hand run (only the read-only `--check`).
  conftest also replaces winreg's writers (`SetValueEx`, `DeleteValue`, ...)
  with a refusal, so a test that drops the variable still writes nothing.
  Each backup names its store (`_store_id`: `registry` or `json:<file>`) and
  `[z]` restores only one of the store in use. A suggested fix (`[f]`) never
  removes a user folder that holds other programs (a losing copy does no
  harm; `[r]` still can, naming them); a folder with `;` or `%NAME%` in its
  path is never written into PATH.
  `_SETUP_TESTED_TOOLS` / `_SETUP_TESTED_PACKAGES` change together with
  jxl-photo-exe's `build.py` (`TOOLS`, `REQUIREMENTS`) and the "Tested with"
  sentence of `docs/install.md` (the same test enforces it). The winner of
  each tool mirrors the scripts: PATH order, and for ExifTool the NAME order
  first (`exiftool`, `exiftool-k`, `exiftool(-k)`); every probe runs with
  stdin NUL.
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
  (`_flags_set_mode`): every pre-run check judged the wizard's mode. "Do
  the flags delete / set the mode?" is the child's own parse too
  (`_child_flags`): every caller of `_flags_request_delete` /
  `_flags_set_mode` / `_append_expert_flags` passes the direction
  (`tests/test_flags_ask_the_child.py`), and reading the tokens is only the
  fallback when the child cannot be imported — it reads the exact spellings
  (since v2.30.0 every child refuses an abbreviation).
  Never re-implement argparse's rules (abbreviations, spellings) in the
  wrapper again: six bugs came from that copy.
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
  job stays in the `last_*` fields. **A NEW job starts from the last run of
  its own task** (`ToolConfig.task_memory`, not a `last_*` name, so no
  snapshot or preset carries it; read through `_task_memory(workflow)`,
  #704/#705); a value equal to the script's setting is kept there as None,
  "the script's own" (`_script_own_fields`, #709). A new read of a `last_*`
  value on a new-job path goes through `_task_memory`. **The last job saved
  is the run:** `_RUN_SCOPED_SESSION_FIELDS` are cleared before each save and
  `_pending_session_fields` are read, not popped — [s], [d] and [r] each save
  the same job (#706, #707). The legacy shortcut of `_task_memory` (a config
  with no task memory at all) never applies to a conversion two tasks share
  (`_CONVERSIONS_OF_TWO_TASKS`, #717). A mode-6/7/99 job saves the export
  marker it ran with, even the wrapper's (#720). Menu 9's TIFF distance is
  the last job's only when that job archived TIFFs (#718); its staging folder
  is the next job's (#719).
- **A delete typed in the expert flags is a delete** for every check before
  the HHMM (#725, the owner's call): `_job_deletes(wf)`, `_registry_rule_problem`
  merges it — and every registry option the flags set
  (`_flags_set_options`: the child's own parse, by argparse dest, never the
  spelling: the decoder's `--none` is `force_none`) counts for the
  `conflicts` — the gate `guard_with_delete` (a `--no-overwrite-guard` next to a
  delete is refused; a remembered 'off' is left out by
  `_append_overwrite_guard_flag(deletes=)`), a derivative typed there is
  refused next to a delete by the scripts' own rule (`_flags_derivative_refused`:
  resize/sharpen on every route, a colour conversion everywhere but the
  transcoder's bit-exact route, `_BIT_EXACT_CONVERSIONS` — the wizard's own
  output profile too, #756) or in place, `_lossy_decode_never_deletes` reads
  the format and the depth from the child's parse of the flags (#756), the
  decoder's `--none` like its decode mode, and a
  manifest whose rows cannot all delete is refused (the wizard cannot take a
  flag out of a row, as `_policy_delete` does for [D]). A new check of "does
  this run delete?" uses `_job_deletes`, never `advanced_options` alone.
- **Pre-run refusals live in `_GATE_RULES`** (`_job_gates(menu, wf, path)`,
  paths `common`/`direct`/`manifest`/`preset`), called by
  `execute_workflow`, `_execute_manifest_workflow` and `_run_saved_session`
  where their checks always ran. A new pre-run refusal is a rule there
  (`tests/test_job_gates.py` fails on an inline one); the HHMM and in-place
  confirmations stay in `_confirm_archive_mode`/`_confirm_in_place_replace`.
  The manifest's refusals that need the run's own intermediate state (the
  mode-7 subfolder it derives, the collision scan's output folders) or ask
  a question are `_manifest_step_*` methods, called in order by
  `_execute_manifest_workflow` (`tests/test_manifest_steps.py` pins each one
  and the order); never an inline refusal there either.

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
  formats the name it WRITES like printf (`%d`, `%o`, `%x`: `100%off` became
  `1000ff`, #647) — every path magick writes goes through `_magick_out_path`
  (parity-pinned, four backends), inputs as they are; a test folder ending in
  `%` proves nothing, name it `100%off %d`; a hard link cannot cross drives (a fallback copy must be released
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
  16 is the measured choice — ~16 min serial). On the owner's machine
  `python` is 3.12 and `py` is 3.13 (`%LOCALAPPDATA%\py.ini`; 3.9–3.15 are
  installed, each with the packages and pytest: `py -3.X -m pytest`) — use
  `python` for the suite, the pre-fix proofs and the battery, and `py -3.13`
  when a change depends on the Python version (#766). A test must be
  safe in parallel: its own `tmp_path`, no fixed temp/home path, no assertion
  on a phrase rich may wrap (conftest sets `COLUMNS=240`; normalise
  whitespace when a long path precedes the phrase).
- Baseline: run the whole suite (`-n 16`, ~3.5 min) BEFORE changing anything
  and record the summary — it tells a new failure from an old one or from the
  environment (a codec update). A plan also states the result of its base
  commit. A test that fails at the end is re-run alone against the extracted
  HEAD (`git show HEAD:<file>`).
- The suite and the battery set `JXLPHOTO_LOG_DIR` (conftest / `run()`), so no
  test writes to `Logs\` — an ad-hoc probe script must set it too, or it
  writes there. Both also run with a throwaway `APPDATA`, and so does each
  side of every `tools/side_by_side.py` scenario, empty at its start (since
  rc7: the encoder's cautious ICC cache, `%APPDATA%\jxl-photo\icc-cache`, is
  the toolkit's only `APPDATA` reader; the suite's lossy test encodes filled
  this PC's real cache, and a side found or missed its entry by this PC's
  history), with `PYTHONUSERBASE` pinned so a subprocess keeps the same user
  site. A probe script or a contract test run by hand that encodes lossy
  sets `APPDATA` (or `--icc-cache-dir`) too. Every subprocess call in the four backends goes
  through `_run_captured` except the version probes (`_tool_version`,
  `_warn_if_libjxl_too_old`), `_run_to_memory` (#671, then the decodes of
  round 87 — see "Full-size decodes stay in memory": an output that must
  never reach the disk, read into memory in the calling thread — the same
  no-reader-thread rules; `_run_captured`'s stdout is a temp FILE by design)
  and `_spawn_exiftool_server` (the kept-open ExifTool, v2.31.0, read the same
  way); `tests/test_subprocess_capture.py` enforces it. A child's input goes
  through `_write_stdin` (common), never `subprocess.run(input=...)`: since
  Python 3.13 that starts a writer thread on Windows (#766).
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
  git show HEAD:jxl_photo_common.py > <tmpdir>/jxl_photo_common.py   # always with it
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
  every screen of `jxl_photo.py` with Rich (24 sessions: menus, every task to
  its review, [a]/[c]/[x]/[d]/[s], Auto Mode, [D], settings, the maintenance
  menus, `setup` and `setup --check` on a JSON copy of the PATH values) in a
  throw-away home and a hostile library folder, and saves each
  screen to `FOLDER/screens/`. Read them after any change to what the wizard
  shows (rc 130 = that session ran out of answers: see where in its file).
- **Refactor proofs: old against new** (none of them touches the repo's
  files; each was proven against a planted change before it was trusted):
  - `python tools/side_by_side.py --ref <rev> [--new WORKTREE|<rev>|<folder>]
    [--quick] [--lane w1|default|both] [--expect FILE]` runs 27 scenarios of
    the four backends on small real fixtures (`--fixtures`/`JXLPHOTO_FIXTURES`)
    with both versions, in the SAME hostile folder restored in between, and
    compares exit codes, `##JXLSUM##`, console and log lines, and the whole
    folder after every step (JXL codestream, boxes and order, every exiftool
    tag, decoded pixels, `checksums.md5`). Only what varies between two runs
    of the same code is masked. `--quick` is 8 scenarios, ~3 min; the full set
    runs at the end of a phase. A scenario meant to write that left the folder
    unchanged is reported (a layout mistake cannot pass as "same": a run
    folder whose name ENDS in `_export` is a marker match).
  - `python tools/wizard_transcripts.py --ref <rev> [--new ...]` types the
    `ui_tour` sessions in both screens (Rich and plain) with both versions and
    compares what was printed, every child command line (the child is not
    run) and the config file.
  - `python tools/move_check.py <rev-before> <rev-after|WORKTREE>` proves a
    commit only MOVED code into new functions: same AST, every origin
    variable the block reads is a parameter, every value the origin reads
    after the call comes back, no `global` lost, no `return` inside the block.
  - `python tools/mutate.py <spec.json>` breaks one thing in a COPY of the
    repo and checks the named tests fail (tiff-workflow's spec format).
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
- `docs/known_issues.md` — only the bugs that changed FILES the toolkit
  wrote: which versions, how to find those files (an ExifTool `-if` on the
  `xmpMM:History` version, tested on a real master) and what to do. A fix of
  such a bug adds its entry at the top in the same change; everything else
  stays in the bug tracker only. The README's documentation table gets its
  row at the next release
- `docs/file_sizes.md` — measured file sizes per camera and for film scans,
  effort 7 vs 9 (sizes, time, ssimulacra2/butteraugli) and `--buffering 0`;
  libjxl 0.12.0, 2026-10-30. The README's size table is its summary (the
  same table is in `docs/why_jxl.md`) — update the three together, and never
  name the owner's shoots or folders there
- `docs/performance.md` — how the toolkit stays fast, SHORT on purpose
  (owner's rule, 2026-11-16, like the README): a table of headline numbers,
  the six ideas one paragraph each with a link to their section, the life of
  one photo, the speed settings. It does not grow with each release: only a
  new idea or a new headline number changes it
- `docs/performance_details.md` — the long version: the design it started
  with (worker threads around the codec processes, the image handed to cjxl
  in memory, staging, smart sync, effort 7), then every speed and memory
  change by theme, with an "At a glance" table by version and the numbers
  each release measured. A change made for speed or memory adds its row
  there and its paragraph in the same change (the full detail stays in
  `docs/version_history.md`); never name the owner's shoots, folders or
  presets in either page
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
- `docs/prerelease_archive.md` — the notes of the GitHub pre-releases whose
  release pages were removed (the owner's call, 2026-11-23: too many
  pre-releases), as published, with the tag, the commit and the date. A later
  clean-up adds its releases there FIRST (from the API's JSON, kept in
  `AI_tools/`), then deletes the pages with `gh release delete <tag> --yes`
  (never `--cleanup-tag`: the git tags stay), sparing any release with a
  reaction and the current test release

## Releases
- Stable tags: `vX.Y.Z` (e.g. `v1.7.1`); test releases (GitHub
  pre-releases): `vX.Y.Z-rcN` since v2.30.1-rc1 — `TOOLKIT_VERSION` carries
  the suffix, and it is what every file converted with that release records
  in its history, so `vX.Y.Z` stays for the final and each edited test
  release takes the next `-rcN`. The older `vX.Y.Z_betaN` tags stay as they
  are; the wizard's update check (`_version_key`) reads both.
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
- **A final's notes compare it with the previous FINAL release, never with
  its last test release** (owner's rule, 2026-11-23): the GitHub notes, the
  README's "What's new", `whats_new.md` and the final's `version_history.md`
  section say what changed since the last stable release (the rc sections
  keep the per-test detail). A final's code is its last rc's, with the
  version number only; it is still verified on real photos against the
  previous final (old × new on the same copies) before it is tagged.
- **Every release bumps `TOOLKIT_VERSION` in ALL FIVE scripts and in
  `jxl_photo_common.py`** (each script refuses a common module of another
  version), even when only one changed, to the version of the newest
  `docs/version_history.md` section — `tests/test_toolkit_version.py`
  fails until they match.
- Commits carry the repo owner's authorship only — **no `Co-Authored-By` or
  `Claude-Session` trailers** (AI assistance is credited in the README's
  Acknowledgments instead, and more than one assistant is used).
- **Every STABLE jxl-photo release updates jxl-photo-exe too** (owner's rule,
  2026-10-22; since 2026-11-20 a test release `-rcN` gets no exe, the owner's
  call: its notes still point to the latest stable exe), in the sibling repo
  `../jxl-photo-exe`: `JXLPHOTO_TAG` in its
  `build.py` set to the new tag and `PACKAGE_REVISION` back to 1, its README
  "Current version" line and `docs/version_history.md`, then a tag
  `vX.Y.Z-exe1`: GitHub Actions builds the zip with a build attestation and
  creates a DRAFT release; `python tests/battery_on_exe.py --app <that zip,
  extracted>` (runs the program from a hostile folder; must pass) before it is
  published (regular releases since v2.16.0-exe2, built by GitHub since
  v2.16.2-exe1; jxl-thumb's releases are built the same way). A change
  to jxl-thumb that the exe bundles (its `TOOLS["jxl-thumb"]` URL + SHA-256)
  goes in the same way, and `_SETUP_TESTED_TOOLS` in `jxl_photo.py` when the
  exe's TOOLS change.

## Pending list
The owner keeps one list of everything still pending, across all repos, in
`../PENDENCIAS.md` (the folder above this repo, outside git, in Portuguese).
Read it when asked what is left to do. When work here finishes an item, leaves
something for later, or waits on a decision from the owner, update that file in
the same session: remove what is done, add what is new. It is private: never
copy its contents into tracked files, commits or release notes.
