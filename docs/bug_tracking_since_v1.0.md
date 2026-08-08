# Bug Tracking Since v1.0

Date: 2026-04-04  
v1.2 Update: 2026-04-05  
v1.3 Update: 2026-04-11  
v1.5 Update: 2026-04-12  
v1.5 Final: 2026-04-12 (third pass)  
v1.5.2: 2026-04-13 (critical 8-bit fix)
v1.5.3 / 2026-07-04: Critical fixes for 16-bit roundtrip, Matrix/Basic mode, cmd_auto, and wrapper integration
v1.7 / 2026-07-06: Multi-page TIFF support with configurable split/skip/ignore and thumbnail handling
v1.8.1 / 2026-07: The audit release — ~120 bugs fixed across 12 audit rounds (see top section)
v1.9.0_beta2 / 2026-08-01: Full-repo audit — 21 bugs fixed (2 critical/data-safety), 2 suspected bugs proven false positives with real files (see top section)
v1.9.2 / 2026-08-05: Post-v1.9.1 audit — 10 bugs fixed (2 data-safety), validated against the real 738 MB scanner files (see top section)
v1.9.3 / 2026-08-06: Round-24 audit — 10 bugs fixed (3 data-safety/blocking), all in the wrapper's manifest layer and the summary contract (see top section)
v1.10.1 / 2026-08-07: Round-26 audit — 6 bugs fixed in the new delete/verify layer, 5 of them introduced by round 25's own feature work (see top section)
v1.9.4 / 2026-08-06: Round-25 audit — 8 bugs fixed (1 crash, 1 silent page drop, 1 delete gate), plus the decoder/transcoder worker-pool parity the encoder got in v1.9.0 (see top section)
Scripts: `jxl_photo.py`, `jxl_photo_v2.py`, `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`
**Note:** `jxl_tiff_decoder.py` was completely rebuilt in v1.3 (improved Windows Explorer support, file integrity checks, Python 3.8 compatibility). Original v1 preserved in `deprecated/`.

---

## Round-29 audit (2026-08-08)

External audit of HEAD after round 28, verified item by item against the code
before anything was changed. The conversion core (pixels/ICC/multi-page) came
out clean again — every finding is in the orchestration added by rounds 27-28:
provenance, delete-in-any-mode, `--delete-skipped` and dry-run.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 277 | **Exit 2 means two different things and the wrapper reported both as the second.** argparse exits 2 on a rejected command line; the children exit 2 on a safety abort. A wrapper-built command no script could accept was reported as "Aborted by a safety check (e.g. duplicate output destinations)", sending the user to look for a collision that never existed. The manifest recap added "Nothing was deleted", which exit 2 also cannot promise — the disk-full abort fires part way through a run and completed entries did their own deleting | wrapper | ✅ FIXED (`_stream_child` keeps argparse's own `<prog>.py: error: ...` line, which is the only thing separating the two cases; both the single-run and the manifest paths report a rejected command line as what it is — nothing ran, nothing was touched, and it is a wrapper bug. The abort message no longer promises what it cannot know) |
| 278 | **`--provenance adopt` does not exist in the decoder or the transcoder, and the wrapper offered it for every direction.** Only the encoder declares it; the other two are `choices=["path", "content"]`. The wizard asked path/content/adopt in every collapsing mode, and all six emission sites passed the answer straight through — so a JXL→TIFF or JPEG↔JXL delete run built a command line that died at argparse before reading a file, reported as #277's fake safety abort. In a manifest it took every remaining entry with it. A saved session replayed the same `adopt` on later runs. The decoder's own `--none` warning told the user to pass `--provenance adopt` — an instruction impossible to follow. `docs/bug_tracking_since_v1.0.md` claimed #271 was fixed in all three scripts | wrapper, decoder, docs | ✅ FIXED (`_supports_provenance_adopt` gates the OFFER, not just the emission: the wizard lists adopt only for TIFF→JXL. The six emission sites collapse into one `_append_provenance_flags`, which downgrades a stored `adopt` to the strict `path` and says so — the safe direction, since `path` refuses and deletes nothing where adopt would have been argparse exit 2. The decoder's `--none` warning now states the real consequence and that it has no adopt. #271's scope corrected to encoder-only; `--provenance` documented in all three READMEs) |

| 279 | **`--dry-run` modifies the archive it is simulating over.** The encoder's provenance block sits ~90 lines above the dry-run gate, so `--dry-run --provenance adopt` ran the full adopt scan (a decode of every unmarked output) and then wrote `jxlphoto-src`/`jxlphoto-srcsum` into real JXLs with `exiftool -overwrite_original`. Reproduced on a real archive. Same class as #235. The block also sits above the delete confirmation, so declining it (exit 3) left the files stamped anyway | encoder | ✅ FIXED (a dry run neither scans nor stamps — it reports how many outputs carry no record and states that the count is an UPPER BOUND, since the scan a real run performs can still refuse them. The stamping moved below BOTH gates: `--dry-run` never reaches it, and a declined confirmation exits 3 with the archive untouched. What to stamp is still decided in the same place, so the healing pass is unchanged for a confirmed run) |

| 280 | **Mode 0 with an output folder collapses structure, and nothing guarded it.** `_COLLAPSING_MODES = {2,4,5,6,7}` in all four files, but mode 0 honours the output folder (`resolve_output`: `if input_root != jxl_path.parent`) and then writes every file into it, flat — exactly what mode 2 does. Mode 0 is also FLAT, so `_abort_on_duplicate_outputs` never sees the clash: the recorded marker is the only defence there is. **Reproduced end to end**: A decoded to `out/foto.tif` with its JXL deleted, then B (same filename, different photo) overwrote `out/foto.tif` and had its JXL deleted too — `1 OK, 1 overwrites, Sources DELETED: 1`, and A's photo gone for good. Exactly #268 | encoder, decoder, transcoder, wrapper | ✅ FIXED (new shared `_run_collapses_structure(mode, output_arg, source_root)` in all four files, in `SHARED_HELPERS`. Mode 0 counts as collapsing only when an output folder was given AND it differs from the source folder — mode 0 IN PLACE stays unguarded on purpose, since demanding a marker where nothing can collide is #271's dead end again. The `--strip` and `--none` warnings name the new case too) |

**Still open after this round:** the decoder and the transcoder have no
migration path for an archive written before the markers existed (#271's gap,
now stated honestly instead of pointing at a flag that does not exist). Porting
adopt to the decoder is mechanical — decode the JXL, compare against the
existing TIFF, which is real proof. The transcoder's lossless JXL→JPEG path has
proof too (jbrd/MD5), but its lossy convert directions have none, so what
"adopt" would even mean there is a design question, not a port.

---

## v1.10.3 — Round-28 audit (2026-08-07)

Full audit after round 27, weighted towards anything that can destroy
irreplaceable data. **The codec paths are clean**: four real fixtures (Capture
One 16-bit, a Z8 export, and two film scans including the 756 MB 3-page RGB+IR
one) round-trip with every real page pixel-identical, the ICC byte-identical and
`SubfileType` preserved (0 and 4). Provenance markers are present on every real
output, including both pages of a multi-page split.

Everything below is in the provenance layer added in round 27 — five of the six
are consequences of that work. **All six are fixed.** 23 new regression tests
(`tests/test_provenance_adopt.py`); full suite 815 → 830.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 271 | **A pre-existing archive is refused outright, with no way forward.** Any output written before the markers existed has none, so `_provenance_ok` returns False and every file is refused: `REFUSING 3 file(s) ... no provenance marker (archived by an older version)`. That is EVERY archive anyone already owns. The message offers "rename them, pick a mode that keeps folder structure, or drop --delete-source" — none of which lets an existing library carry on being archived. Fail-closed is right; having no migration path is not | encoder | ✅ FIXED (`--provenance adopt`, **encoder only**. It resolves ONLY the "I cannot tell" case and PROVES the pairing rather than assuming it: each unmarked output is decoded and compared against its source — the adopt scan, ON by default — and then STAMPED, so the archive heals in a single pass and the strict check applies from the next run on. A MISMATCHING marker is still refused: adopt never relaxes "I can tell it is wrong". `--no-adopt-scan` trades the proof for speed, warning per file. The encoder's strict refusal message names `--provenance adopt` as the way forward. The decoder and the transcoder declare `choices=["path", "content"]` and have NO adopt: an archive written by them before the markers existed still has no migration path, and the wrapper no longer offers a flag they reject — see #278 and #282)
| 272 | **Refused files exit 0.** `provenance_blocked` is dropped from `all_items` and never reaches `err`, so a run that refused every file exits 0. A scheduled job sees a clean run; only the log says otherwise. Verified: 3 files refused, exit code 0 | encoder, decoder, transcoder | ✅ FIXED (a refusal is a failure: it lands in `err`, in the summary's `failures` list — so the wrapper's manifest recap shows it — and the run exits 1. Verified: the same refusal that exited 0 now exits 1)
| 273 | **`--strip` silently disables the provenance record.** `build_metadata_injection_args` returns early under `strip_metadata`, before the marker lines, so a stripped archive carries none — and is then refused by #271's path on the next run. Nothing warns. Verified empirically | encoder | ✅ FIXED (warns. `--strip` promises no metadata and the marker IS metadata, so writing it anyway would break the flag's contract — instead the run says plainly that these outputs carry no record and that a later delete run in a collapsing mode will refuse them until `--provenance adopt`. Only fires where it can bite: delete armed, or a collapsing mode)
| 274 | **The decoder's `--none` mode does the same.** `copy_metadata` is skipped entirely for `strategy == 'none'`, and the marker is written inside it. Verified: normal decode → marker present, `--none` → absent | decoder | ✅ FIXED (same treatment: `--none` keeps the v1.6.0 minimal-metadata contract and the marker is XMP, so the warning names the consequence rather than quietly breaking the contract)
| 275 | **The transcoder honours `--provenance` but nothing ever passes it.** The wrapper emits the flag only in the encoder and decoder branches, and `[D]` asks the question only when both origin and dest are TIFF/JXL — so every JPEG↔JXL run from the wizard is stuck on the default `path`, with no way to pick `content` after moving folders | wrapper | ✅ FIXED (the wrapper emits `--provenance` in the transcoder branches too, and `[D]` asks the question for EVERY direction whose layout collapses folders, not just the TIFF ones)
| 276 | The decoder writes the marker in its OWN exiftool call, after the dc:Relation rewrite, so every output costs one extra process spawn (~100 ms). On a 5 000-file library that is minutes of pure overhead, and it folds naturally into the rewrite that already runs | decoder | ✅ FIXED (the clear stays its own invocation — `-XMP-dc:Relation=` and `+=` in one exiftool call do NOT sequence as clear-then-append for a list tag, and the stale internal markers survived; the leak test caught it — but everything ADDED now goes in a single call: two invocations instead of three, one instead of two when there is nothing to clear)

---

## v1.10.2 — Round-27 audit (2026-08-07)

Full-toolkit audit after the delete/verify work. Recorded BEFORE fixing so a
later regression check has something to compare against.

All five are fixed. 38 new regression tests across
`tests/test_provenance.py` and `tests/test_delete_reporting.py`; full suite
797 → 815. Every fix was proven against the pre-fix tree first.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 266 | **Deletions never reach the run summary.** `deleted`/`deleted_skipped` are locals in `process_group`; nothing propagates them, so `emit_summary_json` has no count and the wrapper's end-of-manifest recap never says how many originals were removed. A 20-entry manifest can delete 50 000 masters and the recap shows only OK/ovw/skip/corrupt/err. Sources KEPT by a refusing gate (integrity, round-trip, checksum mismatch) are likewise per-file WARNING lines that no summary aggregates | encoder, decoder, transcoder, wrapper | ✅ FIXED (a module-level `_delete_stats` in each of the three scripts feeds `emit_summary_json`: sources deleted, of which already archived, and sources KEPT by a refusing gate — the number that says something needs looking at. The wrapper's recap gives them their own undimmed line above the rest, because burying an irreversible count among "Thumbnails excluded" made a run that removed 50 000 masters look like one that removed none)
| 267 | **`--dry-run` does not preview the deletions.** A dry run of `--delete-source` prints the planned outputs and stops; not one line says deletion is armed or how many sources would go. Only the `--delete-skipped` subset is previewed (encoder and decoder; the transcoder has no preview at all). The dry run is exactly where this is checked | encoder, decoder, transcoder | ✅ FIXED (every dry run that has `--delete-source` armed now says so, with the count and the gates that stand in front of it — encoder, decoder and all three transcoder entry points)
| 268 | **Cross-RUN output collision destroys data in the collapsing modes.** `_abort_on_duplicate_outputs` only sees collisions WITHIN one run. In modes 2/4/5/6/7 the output path drops folder structure, so a file added later in a different folder resolves to the same output. Reproduced: mode 5, `root/A/foto.tif` archived and deleted; later `root/B/foto.tif` (different image, same stem) is newer than the archive, so smart sync RECONVERTS over it and deletes B too. **A's photo then exists nowhere** — its TIFF was deleted in run 1 and its JXL overwritten in run 2. The log said "1 overwrites". `--verify-roundtrip` does NOT help: B verifies correctly against B. The commit that opened deletion to every mode claimed `_abort_on_duplicate_outputs` "keeps the collapsing modes safe" — true within a run, false across runs | encoder, decoder, transcoder | ✅ FIXED in ALL THREE (every output records WHICH source made it: `jxlphoto-src:` = location, `jxlphoto-srcsum:` = image, both written always since the content id is hashed from the array already in memory. With `--delete-source` in a collapsing mode, an EXISTING output whose marker does not match the source about to replace it is refused: not converted, not overwritten, nothing deleted. `--provenance path` (default) compares the location — free, and survives re-exporting in place; `--provenance content` also accepts a matching image, so it survives MOVED folders, at the cost of reading each source. content is deliberately a SUPERSET of path: content alone would refuse a legitimately re-edited file and break the sync workflow. The decoder records the same markers; the transcoder does too, except on the LOSSLESS JXL→JPEG path, whose output must stay byte-identical and therefore cannot carry one — there `checksums.md5` already holds the original JPEG's hash keyed by the JXL, which is a stronger proof. The three helper copies are identical and enforced by `tests/test_helper_parity.py`, which caught the drift immediately) |
| 269 | **The `[D]` confirmation over-counts for modes 6/7.** `_count_origin_files` counts by extension (flat for 0/1, recursive otherwise) and ignores the marker filter and the tool-output filters, so gate 2 announced "23 TIFF file(s)" for a mode-6 run that touches 3. The count is the whole point of that gate — it is what makes a wrong folder visible before the token is charged | wrapper | ✅ FIXED (`_count_origin_files` asks the CHILD'S OWN finder — `find_tiffs_mode6/7`, `find_jxls_mode6/7`, the flat and recursive ones — honouring the configured export marker and putting the child's global back afterwards. Mode 6 on the same tree now reports 3, not 23)
| 270 | Minor delete-reporting inconsistencies: the encoder labels a MIXED source (one page skipped, one freshly converted) as "already archived" because `skipped_finals` is merely non-empty; and `deleted_skipped` is tracked only in `process_group_transcode`, so the transcoder's two lossy paths never print the "(N already archived)" total | encoder, transcoder | ✅ FIXED ("already archived" only when NOTHING was written for that source this run; a mixed source reads "partly already archived". All three scripts count the archived subset)

---

## v1.10.1 — Round-26 audit (2026-08-07)

An audit of what the delete/verify work itself changed, rather than of the
toolkit at large. **Five of the six findings were introduced by that work**, and
the suite was green for all of them — every one sat in a gap the new tests did
not cover.

32 new regression tests; full suite 731 → 763.

### Unreachable feature

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 260 | **`[D]` existed in only ONE of the three mode selectors.** The `choice == "8"` arming was removed from all three at once, but `[D]` was added to the Step-4 menu alone — so Auto Mode → "pick manually" (`_wizard_select_mode_manual`) and the `?` detail view (`_show_mode_details_and_select`) lost the delete workflow ENTIRELY, with no hint that it existed. Worse than a dead end: typing `D` there looped forever, because the key was not in `valid_choices` and the prompt just re-asked | wrapper | ✅ FIXED (`_DELETE_CHOICE` + `_handle_mode_choice` + `_delete_entry_line` are shared by all three; the menus keep their own presentation, only the CHOICE is common. `tests/test_delete_skipped_children.py` enumerates the selectors and asserts each routes `D`, still takes a plain layout, and never arms delete on mode 8 — so a fourth selector cannot quietly skip it) |

### Wrong information on screen

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 261 | **The abort said "nothing was deleted" and then deleted on the next line.** `_signal_abort` logs during the pool; the delete gate runs after the pool drains, so sources whose output was written and verified BEFORE the abort latched are still removed. The behaviour is right — each one passed every gate — but the message flatly contradicted the log below it. Pre-existing for mode 8; round 25 widened it to every mode and to `--delete-skipped` | encoder, decoder, transcoder | ✅ FIXED (the message now says queued files were not attempted and that already-verified sources may still be deleted below. Suppressing the deletes instead would strand a whole run's worth of archived masters for no safety gain) |
| 262 | **Mode 8 was still painted red with "⚠️ WARNING!" in four places, while `[D]` — the entry that actually deletes — was a plain line.** Mode 8 no longer deletes anything; the danger colour pointed at the safe option and away from the destructive one. Same class as #250, which fixed the Step-7 summary and missed the Step-4 menus | wrapper | ✅ FIXED (red and the warning move to `[D]`; mode 8 reads "In-place recursive" and its detail panel says the originals are kept) |
| 265 | A comment still explained the mode-8 delete arming that had been deleted | wrapper | ✅ FIXED |

### Gates

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 263 | **`--delete-skipped` made `--delete-s` an ambiguous abbreviation.** argparse rejects it outright, so a preset carrying it in expert flags now: charged the HHMM token (`_flags_request_delete` still matched it against `--delete-source`), launched the child, and died on "ambiguous option". A silent regression for anything already saved | wrapper | ✅ FIXED (`_flags_request_delete` no longer claims a prefix that also matches `--delete-skipped`; `_flags_ambiguous_delete` reports it and `execute_workflow` refuses BEFORE any gate, telling the user to spell the flag out) |
| 264 | **The lossy `delete_skipped` confirmation lived inside `[D]` only.** "Repeat last workflow" and presets rebuild `advanced_options` from the stored session and never walk through the wizard, so the extra gate for the one direction that can prove nothing was skipped there. The HHMM token was still charged, so it was not unprotected — but the gate was not universal | wrapper | ✅ FIXED (`_confirm_lossy_delete_skipped` runs at execution time, covering wizard, repeat and preset alike; `[D]` marks the workflow so it is never asked twice, and a dry run never asks) |

---

## v1.9.4 — Round-25 audit (2026-08-06)

Four-script audit, wrapper last. 24 new regression tests
(`tests/test_audit_round25.py`); full suite went 627 → 651. Every fix was
proven against the pre-fix code first: 17 of the 24 fail on the v1.9.3 tree,
the other 7 are deliberate "do not over-fix" guards (valid modes 0-8, split
mode's existing thumbnail accounting, a single-page TIFF with no thumbnail,
non-TIFF directions, per-folder staging flush, a complete group still
deleting, explicit Mode cells).

The codec paths came out clean again and were re-validated end to end after
the edits, against the real files rather than fixtures: the 739 MB 3-page
RGB+IR scan round-trips with the RGB page AND the `SubfileType=4` IR page
pixel-identical and the 217 KB scanner ICC byte-identical, and a Capture One
16-bit ProPhoto export round-trips pixel-identical with its preview page
restored. A two-folder mode-3 decode with staging and a two-folder JPEG↔JXL
transcode (MD5 PASS both ways) exercised the new single-pool paths on real
files.

### Crash

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 252 | **The transcoder was the only script whose `--mode` had no `choices`.** `--mode 9` (or `-1`, or `42`) sailed through argparse and died inside `resolve_output_transcode` with a raw `ValueError` traceback — after `setup_logger()`, the settings header and `Files found: N` had already been printed, so it read as a mid-run crash rather than a bad flag. Reachable from the wrapper too: expert flags are appended to the child's argv verbatim. The encoder has had `choices=[0..8]` and the decoder `choices=range(9)` all along | transcoder | ✅ FIXED (`choices=range(9)`; `default=None` kept, since `main()` picks `TRANSCODE_DEFAULT_MODE`/`CONVERT_DEFAULT_MODE` from it. Exit 2 = invalid arguments, per the documented table) |

### Silent data drop

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 253 | **`--multipage-mode skip` discarded thumbnail pages without a single word.** `skip` refuses genuinely multi-page files, so a TIFF with ONE real page sails through it — but its embedded preview does not, and the `skip` planner never touched `_thumbnails_dropped` or `_discarded_thumb_sources`. No summary line, no `extras["Thumbnails excluded"]` for the wrapper's recap, and in mode 8 + delete the `(embedded thumbnail page not encoded)` note — the one thing that warns the decoded TIFF will not have that page back — never fired. That is the shape of **every** file in the Capture One fixtures and of the film scans, so on a real library this was silent on every file. Reproduced against the real `_DSC0003_ProPhoto-g22_v1.tif` | encoder | ✅ FIXED (`_record_dropped_thumbnails` is now the single accounting path for both the `split` and `skip` planners, so the two cannot drift again; the summary names `--multipage-mode skip` and points at `split_all`, because `--thumbnail-mode include` does nothing in skip mode) |

### Delete gates

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 258 | **A marked multi-page group arriving with pages MISSING decoded to a valid one-page TIFF, and mode 8 deleted the JXL for it.** `collect_multipage_groups` groups on whatever files the run was given, so decoding `scan_page2.jxl` alone (a single-file input, or a folder holding only part of a split) produced `scan_page2.tif` and, with `--delete-source`, removed the source. Every downstream check passes — the file that was written IS complete — so nothing could notice. Same blind spot the encoder closed with `_discarded_real_page_sources` | decoder | ✅ FIXED (a marked group whose only member is not page 0 is logged as INCOMPLETE and recorded in `_incomplete_groups`; the mode-8 gate refuses it, fail-closed. A lone page 0 is the ordinary single-output case and is untouched) |

### Wrong information on screen

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 254 | **The Auto Mode report printed the display SAMPLE as the subfolder count.** `analyze()` stores the real count in `subfolder_count` and truncates `subfolders` to five for display — the fix #248 made for `_recommend` and missed here — so a tree with 12 shoots reported `Subfolders: 5` and `... and 2 more`. The mode-4 heuristic read `subfolders[:3]` of that same sample, so a `*_TIFF` folder sorted past position 5 never got the rename mode recommended | wrapper | ✅ FIXED (report uses `subfolder_count`; `analyze()` also exports `subfolder_names` — every immediate subfolder — and the mode-4 heuristic decides on that, not on a display sample) |
| 255 | **The plain-text Step-7 manifest summary dropped the whole Config line.** The `rich` branch has always rendered `extra_info`; the non-`rich` branch printed Mode/Manifest/Entries/Workers and went straight to `Type YES`. On a terminal without `rich`, a manifest that deletes originals asked for confirmation without ever saying so — `DELETE SOURCE: ON (!)`, `DRY RUN`, staging and the expert-flags warning were all invisible. Same class as #163/#168. Neither branch showed the multi-page line either, although its whole purpose is to be the last word on what happens to extra pages before YES — and a manifest is the largest run the wrapper starts | wrapper | ✅ FIXED (plain-text branch prints `Config:`; both branches print `Multi-page TIFF:` for TIFF→JXL, with the same yellow flag on the page-dropping choices) |
| 257 | `cmd_auto` folded `"reconvert"` into `ok` and never counted it, then passed `overwritten=0` to `record_summary()`. The wrapper's manifest recap showed `ovw 0` for every JXL↔JPEG auto entry, even on a pass that reconverted the whole folder. `cmd_transcode` and `cmd_convert` both report it | transcoder | ✅ FIXED (tallied like the other two; the AUTO MODE complete line shows it as well) |

### Performance parity

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 256 | **The decoder and the transcoder still drained the worker pool at every output-folder boundary.** The encoder fixed exactly this in v1.9.0 and measured it: 10s in a single folder vs 33s spread over eight, on eight real 45 MP TIFFs. Both other scripts still ran `for dest_folder, group_pairs in groups.items(): process_group_*(...)` — one pool per folder — and modes 3/5/6/7 create one output folder per shoot, so a library decode ran at a fraction of `--workers` (four call sites: decoder `main`, `cmd_transcode`, `cmd_convert`, `_process_file_group`) | decoder, transcoder | ✅ FIXED (one pool per run, with the encoder's per-destination flush ported over: each folder's outputs leave staging the moment that folder's last file lands, so staging still never holds the whole batch. Verified on real files: a two-folder mode-3 decode ran both folders concurrently and drained staging per folder) |

### Latent

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 259 | **The three manifest guards ran on the RAW manifest mode while the run used the DETECTED one.** A legacy manifest (no Mode cell) reaches `_manifest_source_overlaps` / `_manifest_needs_collision_scan` / `_manifest_output_collisions` as `mode=None`, so the collision scan falls back to a flat-Destination model — but `_execute_manifest_workflow` then runs those entries through `detect_mode_for_entry`, which resolves them to 0/6/7. For anything detected as 6/7 the guard was checking a folder the child never writes to | wrapper | ✅ FIXED (modes are resolved ONCE, up front, and the same resolved list feeds the guards, the mode-8 gate, the run loop and the not-started recap. Explicit Mode cells are returned unchanged by `detect_mode_for_entry`, so nothing about non-legacy manifests moves) |

---

## v1.9.3 — Round-24 audit (2026-08-06)

Four-script audit, wrapper last. 40 new regression tests
(`tests/test_audit_round24.py`); full suite went 587 → 627. Every fix was
proven against the pre-fix code first: 31 of the 40 fail on the v1.9.2 tree,
the other 9 are deliberate "do not over-fix" guards (mode 0 in-place, mode 8,
disjoint folders, identical Sources, legacy `None` modes, valid ranges).

The codec paths came out clean again and were re-validated end to end after the
edits: lossless TIFF→JXL→TIFF is pixel-identical on a Capture One 16-bit
ProPhoto export, and the 739 MB 3-page RGB+IR scan round-trips
`SubfileType 0/1/4` exactly. Everything below is the wrapper's manifest layer
and the child→wrapper summary contract.

### Data-safety

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 242 | **The manifest collision guard was still blind for mode 0.** #234 re-opened the (expensive) scan for mode 0 on the correct premise that mode 0 HONORS the Destination column — but `_manifest_output_collisions`' resolver still returned `f.parent` for it, so the scan ran and found nothing. Two entries sharing one Destination collapsed onto a single output: reproduced end to end with the real encoder, entry 2 logging `SKIP (sync: JXL up to date)` and the run exiting 0. Applies to all three directions | wrapper | ✅ FIXED (mode 0 resolves through the Destination cell, like mode 2; mode 8 keeps its own in-place branch. Mode 0 is flat, so `f.parent == src_root` and Destination == Source resolves identically) |
| 243 | **Auto-generated manifests for the RECURSIVE modes processed every file several times.** `compute_folder_mappings` emits one entry per folder holding origin files, but modes 3/4/5 make the child walk the whole tree from each Source — so the root entry already covered every subfolder entry. N child processes writing the same outputs, decided by sync-mtime luck | wrapper | ✅ FIXED (`_outermost_with_counts` keeps only the outermost folders and folds the dropped folders' counts into their parent, so the preview stays honest; modes 4/5 exclude the root as an ancestor too, since it is not an eligible entry there) |

### Blocking / false refusals

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 244 | **The overlap guard ignored the mode and flagged nested Sources in the FLAT modes.** Modes 0/1 are non-recursive in all three children, so `root` next to `root/sub1` cannot hand the same file to two processes — but `_manifest_source_overlaps` compared paths only. Every mode-0/1 manifest the wrapper's own Auto Mode generates with files in the scan root triggered it; **unattended (`--run-preset`) that is a hard refusal**, so a scheduled mode-0 manifest preset simply never ran | wrapper | ✅ FIXED (nesting only counts when the CONTAINING entry's mode recurses; identical Sources always count; a legacy manifest with no Mode cell is treated as recursive — fail closed) |
| 245 | **`_session_number_error` blessed `"7.0"` and every consumer then did a plain `int()`/`str()`.** `_as_exact_int` accepts `7.0` and `"7"` on purpose (Excel and hand-edited JSON produce both), but `int("8.0")` raises — a raw ValueError traceback out of a repeat/preset run — and `--workers 8.0` reached the CHILD's argparse as a command line the user never typed. A mode stored as the NUMBER `99` also failed the `== "99"` manifest test and fell through to "No manifest entries found!" | wrapper | ✅ FIXED (`_session_int` coerces what the validator blesses; used by `_run_saved_session` and `_describe_session`) |

### The child → wrapper summary contract

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 246 | **Transcoder dry runs reported themselves as real runs.** `cmd_transcode` and `cmd_convert` returned before `record_summary()`, so `emit_summary_json` printed the untouched module default: `dry_run: false, ok: 0, log: ""`. The manifest recap showed the simulation as a finished run with a row of zeros and never printed its `[DRY RUN]` banner. `cmd_auto` set the flag but still reported 0 planned outputs | transcoder | ✅ FIXED (all three dry-run paths record the PLANNED count with `dry_run=True`, matching the encoder and decoder) |
| 247 | **A child that found no files emitted no summary at all (decoder) or a blank one (transcoder).** The recap then printed `(no summary - ok)` in RED, whose documented meaning is "the child crashed, was killed, or never launched" — an empty or mistyped Source folder was reported as a crash, and the transcoder's `log: ""` also dropped that entry from the "Child logs" list | decoder, transcoder | ✅ FIXED (all four "no input files" paths report an honest zeroed summary with the log path) |

### Wrong information on screen

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 248 | **The Auto Mode report promised folder names no script creates.** `FolderAnalyzer.format_report`'s `mode_names` carried literal, never-formatted `{dest}` placeholders (`"Subfolder (converted_{dest})"`, `"Recursive subfolders ({dest}_files)"`), and `_recommend` printed `creates 'jxl_files' subfolder` where the real folder is `JXL_16bits`. Same class as #161/#169, which fixed the wizard tables and missed the analyzer | wrapper | ✅ FIXED (both go through `_dest_folder_names`, the helper #161 created for exactly this) |
| 249 | **The multi-page help announced the wrong default, and the plain-text branch fell back to it.** Both branches printed `ignore = encode page 0 only, drop the rest (default)` — the default has been `split` since v1.8.2, and `mp_default` two lines below says so. Worse, the non-`rich` branch resolved an unrecognised answer to `"ignore"`, so a typo silently selected the one mode that discards image data: on the real film scans that drops the `SubfileType=4` IR page of every file. (The `rich` branch cannot reach it — it uses `choices=`.) | wrapper | ✅ FIXED (help lists `split` first and marks it the default; both plain-text fallbacks resolve to the DEFAULT, not to a fixed value) |
| 250 | Mode 8 was labelled `DELETE originals` in the Step-7 summary even with `delete_source` off. Mode 8 is in-place recursive; the deletion is a separate opt-in already shown as `DELETE SOURCE: ON (!)` right below | wrapper | ✅ FIXED (`In-place recursive`) |

### Polish

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 251 | **The transcoder validated neither `--distance` nor `--quality`.** `--distance 99` and `--quality 500` sailed through argparse and failed inside cjxl/djxl once per file, with the real cause named nowhere; the encoder has rejected out-of-range distances up front all along. The transcoder also lacked the encoder's "cjxl clamps every lossy distance below 0.05" warning, although the wrapper feeds it `--distance` on the `convert_lossy` workflow | transcoder | ✅ FIXED (both ranges checked in `main()`, exit 2 = invalid arguments; `_warn_distance_clamp` called after `setup_logger()` in `cmd_convert`/`cmd_auto`, per #238) |
| — | Docs: the README's space-estimate bullet read as a toolkit-wide feature; only `jxl_tiff_encoder.py` has a preflight | docs | ✅ FIXED (scoped to TIFF → JXL) |

---

## v1.9.2 — Post-v1.9.1 audit (2026-08-05)

Four-script audit with the codec paths exercised against the real fixtures
(Capture One 16-bit exports, and the 738 MB RGB+IR film scans whose IR page is a
separate `SubfileType=4` page). 32 new regression tests
(`tests/test_audit_round23.py`) plus 9 call-site parity tests; full suite went
546 → 587. Every fix was proven against the pre-fix code first.

The core codec paths came out clean: lossless TIFF→JXL→TIFF is pixel-identical
for RGB, grayscale, gray+alpha, RGBA and 8-bit sources, and lossy `d=0.1` shows
no brightness bias on the big scanner profiles (the "cautious" ICC strategy
correctly skips the 217 KB SilverFast profile). The bugs were all in the
surrounding policy: which runs are refused, what a dry run is allowed to touch,
and what reaches the log.

### Data-safety

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 234 | **The manifest collision guard skipped the modes that actually collide.** #233 narrowed the scan to modes 2/4/5 on the premise that "modes 0/1/3/6/7/8 write only inside each Source's own tree" — false twice: mode 0 HONORS the Destination column, and modes 6/7 write to `<marker>/16B_JXL`, a **sibling** of the Source shared by every entry under the same marker. Two sibling sources under one `_EXPORT` (`TIFF16` + `AdobeRGB`, which do NOT overlap, so the overlap guard stays quiet) produced ONE output: the second entry logged `SKIP (sync: JXL up to date)` and the run exited 0. Reproduced end-to-end | wrapper | ✅ FIXED (`_manifest_needs_collision_scan`: mode 0 scanned unless Destination == Source; modes 6/7 scanned when two entries share a marker dir, or when the marker sits below the Source; 1/3/8 still skipped, and the cheap path is preserved for the auto-generated one-entry-per-export-folder shape) |
| 235 | **`--clean-staging` deleted files during `--dry-run`.** The sweep ran at argument-parsing time, before the dry-run gate, in all three children. A simulation silently removed the failed outputs the KEEP path had preserved for inspection | all 3 | ✅ FIXED (sweep moved into the real-run path, with an explicit "skipped (dry run)" line; the transcoder's stale `checksums.md5` deletion is gated the same way) |

### Consistency across the duplicated helpers (fixed in ALL copies)

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 236 | **The transcoder's documented `TEMP2_DIR` setting was dead code.** All three `cmd_*` did `TEMP2_DIR = args.staging` unconditionally, wiping the script-level setting whenever `--staging` was absent — while `docs/README_jxl_jpeg_transcoder.md` tells users to edit it ("Set `TEMP2_DIR` to SSD when source is on HDD"). The encoder and decoder assign conditionally; this is the #217 fix that never reached the third copy | transcoder | ✅ FIXED (`_apply_staging_args`: `--staging` overrides, its absence does not erase; `_report_staging_leftovers` also reports the effective dir) |
| 237 | The helper-parity test compared helper BODIES only, so three byte-identical copies of `_clean_staging` hid two call-site drifts (#235, #236) | tests | ✅ FIXED (AST call-site checks: the sweep must take `TEMP2_DIR`, must be dry-run gated, and leftovers must be reported for the effective dir — these 9 tests fail on the pre-fix tree in exactly the 5 places that had drifted) |

### Visibility

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 238 | **Messages emitted before `setup_logger()` were lost.** The module logger has no handler at that point: `logger.info` was dropped outright (the entire `--clean-staging` sweep report — files vanished with no line anywhere) and `logger.warning` fell through to `logging.lastResort`, printing unformatted on stderr and never reaching the log file (the `--distance` clamp warning) | all 3 | ✅ FIXED (both moved after `setup_logger()`) |
| 239 | **A manifest kept launching entries after a child exited 2.** Exit 2 means "the output volume filled, nothing was deleted, retry later" — the whole point of the v1.9.0 abort. The wrapper folded it into the generic failure branch and launched every remaining entry against the same full disk, restoring the grinding the abort had removed | wrapper | ✅ FIXED (rc 2 stops the loop, reports how many entries were not started, and lists them as `not started` in the recap) |

### Fidelity and polish

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 240 | **`SubfileType=4` (MASK) was downgraded to 2 on decode.** Film scanners tag the IR/transparency page 4; `tifffile` rejects the value on its `subfiletype=` parameter, so the decoder mapped it to 2 (PAGE) and every round trip silently demoted the IR page to an ordinary extra page. Confirmed on the real `raw_scan`/`raw_scan_2` files | decoder | ✅ FIXED (`_page_subfiletype_kwargs` writes the raw tag 254 via `extratags` for values tifffile refuses; the real 738 MB 3-page scan now round-trips `0/1/4` exactly) |
| 241 | Three smaller ones: `should_process()` could raise `OSError` on a TOCTOU **outside** the worker's try block (bare "error", no message) where the encoder/decoder handle it; `_preflight_space` dropped the destination estimate whenever the output folder did not exist yet — i.e. on the first run, when it matters most; and mode 1 accepted an output positional it silently ignores | transcoder, encoder, decoder | ✅ FIXED (TOCTOU treated as stale; preflight walks up to the nearest existing ancestor, same volume; mode 1 warns that the folder is ignored) |

---

## v1.9.0_beta2 — Full-repo audit (2026-08-01)

A fresh four-script audit (wrapper last), every finding reproduced against the
pre-fix code before the fix was accepted. 47 new regression tests
(`tests/test_audit_priority1-4.py`); full suite went 490 → 537. Two of the
audit's suspected bugs were **proven false positives with real files** and are
listed as such at the bottom.

### Critical / data-safety

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 209 | **Mode-8 delete gate certified a file the run did not deliver.** With staging + overwrite of a pre-existing output, a FAILED `shutil.move` (locked destination, ACL) left the stale old file at the final path: it passed `exists()` + integrity, and the source was deleted while the fresh, verified output sat in staging under a UUID name. Same hole in all three children (decoder JXL→TIFF, encoder TIFF→JXL, both transcoder transcode and convert gates) | all 3 | ✅ FIXED (each group tracks which finals actually left staging — `moved_finals` — and the delete gate requires membership; `process_group_convert` now returns `(results, moved_finals)`) |
| 210 | **`--delete-s` bypassed every wrapper gate.** The children run argparse with `allow_abbrev=True`, so any unambiguous prefix of `--delete-source` deletes — but `_flags_request_delete` only matched the full spelling. A preset storing `--delete-s --delete-c` passed the unattended gate and deleted originals from Task Scheduler with no confirmation anywhere — the exact scenario the gate exists to prevent | wrapper | ✅ FIXED (any unambiguous prefix of either spelling is detected; ambiguous prefixes like `--delete` / `--delete-c` alone are left to argparse's own rejection) |

### Silent wrong behavior

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 211 | Auto mode with `--bit-depth 16` and no `--format` produced **8-bit PNGs via the wrong pipeline**: the JPEG+16-bit→PNG pre-switch tested `args.format == "jpeg"`, which is `None` (default JPEG) in `cmd_auto` and `"jpg"` under the alias — pairs were built as `.png` but the worker got `fmt="jpeg"` and ran djxl's JPEG branch without `--bits_per_sample=16` | transcoder | ✅ FIXED (switch evaluates the effective format in `cmd_auto` and `cmd_convert`) |
| 212 | The manifest cross-entry collision guard only scanned each Source's **direct children**, but mode 2 is recursive-flat: `A\deep\foto.tif` (entry 1) and `B\foto.tif` (entry 2) sharing a Destination both become `<dest>\foto.jxl`, invisible to the wrapper and to any child (separate processes) | wrapper | ✅ FIXED (mode 2 scans recursively) |
| 213 | An **empty Destination cell** (the README's recommended format for modes 1/3/4/5/6/7) falls back to Source at load time, which bucketed every entry separately and disabled the guard exactly for the mode-5 collapse (`sub1\foto.tif` + `sub2\foto.tif` → `<root>\JXL_16bits\foto.jxl`) it was built to catch | wrapper | ✅ FIXED (the guard now resolves each file's output with the child script's OWN resolver — lazy import, honoring user-edited folder constants — and skips files the child would skip: outside the export marker, decoder-output folders) |
| 214 | Duplicate or **nested Source folders** re-processed the same files as two child processes writing the same outputs on sync-mtime luck; the collision guard deliberately ignores a file compared with itself, so it could not see this | wrapper | ✅ FIXED (manifest refused up front; `2024` vs `2024_final` is correctly NOT flagged) |

### Consistency across the duplicated helpers (fixed in ALL copies)

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 215 | `_replace_suffix_token` gave up when the FIRST regex match failed the left-boundary test: `MyTIFF_TIFF` → `MyTIFF_TIFF_JXL` instead of `MyTIFF_JXL` | all 4 | ✅ FIXED (`finditer`, first token-valid match; the four copies are logically identical for the first time, so the helper left `PINNED_VARIANTS` and is covered by the parity agreement test) |
| 216 | `_marker_matches`' `endswith` had no left anchor: a custom `EXPORT_MARKER = "EXPORT"` matched a folder named `ReExport` (the default `_EXPORT` was safe — its own underscore anchors it) | all 4 | ✅ FIXED (endswith requires a token boundary or the marker's own leading anchor) |
| 217 | `--clean-staging` only ran when `--staging` was ALSO passed — with staging from the script setting (the documented way) the flag was silently inert | encoder, decoder | ✅ FIXED (cleans the effective staging dir; warns when none is configured) |
| 218 | The libjxl version-floor comparison accepted 0.11.0/0.11.1 while the warning text and README name 0.11.2 | encoder, decoder | ✅ FIXED |

### Polish (manifest parsing, naming, logs, docs)

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 219 | A fractional Mode cell (`7.5`) was silently truncated to mode 7 by `int(float())` | wrapper | ✅ FIXED (refused like any other invalid value) |
| 220 | `_parse_child_summary` raised `ValueError` on a wrong-typed field, killing a finished manifest run at the summary block | wrapper | ✅ FIXED (wrong types degrade to 0) |
| 221 | A manifest with mode-8 rows never got `--delete-source` from the wizard — "DELETE originals" was silently not honored, and the user was never asked | wrapper | ✅ FIXED (the wizard asks and marks `delete_source`; declining keeps sources, said out loud) |
| 222 | Expert-flag deletion charged the wrapper's HHMM token but left the child's OWN confirmation active — a second, invisible prompt mid-stream | wrapper | ✅ FIXED (`--delete-confirm-off` appended after the flags when the wrapper gates) |
| 223 | Comment rows could poison the Direction guard; a folder literally named `source` was eaten as the CSV header; stem comparison was unconditionally lowercased (false collisions on case-sensitive filesystems) | wrapper | ✅ FIXED (header requires a real column name; stems use `normcase`) |
| 224 | Non-rich repeat panel showed `Quality` for distance-driven JPEG→JXL-lossy (the rich panel showed `Distance`) | wrapper | ✅ FIXED |
| 225 | Decoder run header logged `Overwrite: no` when `OVERWRITE = True` came from the script setting | decoder | ✅ FIXED |
| 226 | A marked 2-page group (1 real page at idx≥1 + 1 thumbnail) decoded with `--thumbnail-handling ignore` kept the `_page<N>` suffix — `scan_page1.tif` instead of `scan.tif`, breaking round-trip naming (standalone third-party `_pageN` files were and are untouched) | decoder | ✅ FIXED (`_group_naming_path` takes the pre-filter group size) |
| 227 | Without imagecodecs, `--depth 8` decoded fine, then EVERY output failed the integrity check (which cannot read the JPEG preview page back) and was deleted as a partial — with a message blaming the TIFF | decoder | ✅ FIXED (up-front warning naming the package and `--no-preview`) |
| 228 | The staging sweep logged `KEEP in staging (md5_fail)` for a file the worker had already deleted | transcoder | ✅ FIXED |
| 229 | Docs drift: `--output-suffix` default, `reconvert=` log labels, "jxl_photo.py does not write a log" (it writes combined manifest logs), a decoder comment claiming ignored thumbnails are deleted (they are deliberately KEPT) | docs | ✅ FIXED |

### Second-audit regressions (found in the fixes above, fixed before release)

A second external audit reviewed these fixes and found three regressions in the
priority-2 collision-guard rewrite (bugs 212/213), plus one over-strict gate:

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 230 | The guard's skip set was hardcoded and wrong for every direction: the transcoder skips ITS OWN output folders (`recovered_jpeg`/`jpeg_recovered`/`converted`) via `_is_tool_output_path`, the encoder skips decoder-output folders ONLY in modes 6/7 (its other finders are unfiltered), and the decoder and the transcoder's decode direction skip NOTHING (round-trip sources). False positive: a JPEG→JXL manifest over a tree containing `recovered_jpeg/` was refused. False negative: a mode-2 collision over `converted_tiff/` was missed | wrapper | ✅ FIXED (the guard delegates to each child's own skip logic per direction/mode) |
| 231 | The child's resolver ran on directories and sidecars before the `is_file`/suffix filter, spamming mode-4/5 warnings (`Output outside input tree`, missing suffix token) through the wrapper — thousands of lines before the run started | wrapper | ✅ FIXED (filter before resolving; child logger silenced while the guard resolves — the real run emits its own lines) |
| 232 | `_manifest_source_overlaps` was a hard abort, refusing legitimate intentional manifests (`E:\Fotos` in mode 6 + `E:\Fotos\2024_EXPORT` in mode 0 — different outputs, no conflict) | wrapper | ✅ CHANGED (attended: loud warning + confirmation, default No; unattended presets: still refused, fail-closed like the delete gates) |
| 233 | The collision guard **silently rescanned every Source tree** before anything ran: recursive glob + full sort + per-file stat + a resolver call per source file, with zero output — on a multi-year library on an external drive the run looked hung for minutes before the first `Executing:` line | wrapper | ✅ FIXED (guard skipped for per-source output modes 0/1/3/6/7/8 — a cross-entry collision there requires overlapping Sources, which the overlap check already refuses; per-entry `Collision check: scanning …` progress lines when the scan does run, i.e. modes 2/4/5) |

Coverage gap closed with these: the guard's tests now exercise the transcoder
(encode/decode) and decoder directions, not just tiff2jxl.

---

### Proven NOT bugs (with real files)

- **exiftool `-s` parsing in `get_exif_software` / `read_existing_creator_tool`** —
  the audit suspected bare-value output broke both parsers (D50 "auto" never
  firing; the stale-ICC cleanup dead). Proven wrong against real ExifTool + a
  real Capture One TIFF: a single `-s` still prints `Tag : value` (only
  `-s -s -s` prints values only), the parse was correct, and
  `should_apply_d50_patch` already returned `True` on the pre-fix code.
  Locking tests pin the real output format.
- **Real-run summary "omitting" marker skips** — `find_tiffs_mode6/7` filter
  marker-outsiders before planning, so `skipped_by_mode` is unreachable and
  `skipped_files` is always 0: dry-run and real-run summaries already agree.

---

## Post-v1.8.1 — Real-batch usability fixes (2026-07-27)

Found by running a 4762-file Capture One library through mode 6 — the kind of
scale the synthetic test suite never reproduces.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 195 | Planning phase opened every TIFF serially with no output at all: on an external drive a large library sat silent for minutes and looked hung | encoder | ✅ FIXED (scan runs on a thread pool capped at 16, logs `Analyzing TIFF pages (N workers)`, progress every ~5%, and the elapsed time; plan order still follows input order) |
| 196 | `--thumbnail-mode exclude` logged one WARNING per file — an export library where every TIFF has a preview page produced thousands of lines and buried the real errors, for a drop the user had explicitly requested | encoder | ✅ FIXED (counted and reported once in the run summary; `--warn-thumbnail-discard` restores the per-file lines) |
| 197 | Thumbnails dropped in `split` mode were counted in `_multipage_ignored`, so the run summary blamed `--multipage-mode ignore` and told the user to "re-run with --multipage-mode split" — which is what they had already done | encoder | ✅ FIXED (separate `_thumbnails_dropped` counter and its own summary line) |
| 198 | Per-file discard warnings were uncapped in `ignore` mode too | encoder | ✅ FIXED (`DISCARD_WARN_LIMIT = 20`, then a suppression notice; totals always in the summary) |
| 199 | `--dry-run` returned before the discard summary, so a dry run never reported dropped pages once the per-file warnings were quieted | encoder | ✅ FIXED (`_log_discard_summary()` called from both exits) |
| 200 | Wizard asked "Thumbnail handling when splitting" after `split_all`, where the encoder ignores the answer | wrapper | ✅ FIXED (question asked only for `split`; both prompts now list what each mode does, and the Step 7 summary states `split_all` always includes thumbnails) |
| 201 | Duplicate-output abort did not explain the cause; nested marker folders (`Untitled Export/Untitled Export/`) collapse onto one destination in modes 6/7 | all 3 | ✅ FIXED (abort prints a hint naming the one-level collapse rule) |
| 202 | **Data loss:** mode 8 + `--delete-source` deleted a multi-page TIFF after encoding page 0 only. Every existing gate passed — the single JXL written *is* valid and complete — so nothing downstream could notice the other pages existed only in the source. Reproduced: 3-real-page TIFF in, 1 JXL out, source unlinked | encoder | ✅ FIXED (planning records which sources lost real pages; the delete gate refuses them: `KEEP source (pages were discarded...)`) |
| 203 | `--multipage-mode` defaulted to `ignore`, i.e. the default silently dropped pages | encoder + wrapper | ✅ CHANGED (default is now `split`; a single-real-page TIFF still yields exactly `photo.jxl`, so ordinary photos are unaffected) |

| 204 | **Throughput:** the thread pool was fed one OUTPUT FOLDER at a time. A folder with fewer files than `--workers` could never fill the pool, and the pool drained at every folder boundary — modes 3/5/6/7 create one output folder per shoot, so a photo library ran at roughly one worker (CPU ~4% with `--workers 12`) | encoder | ✅ FIXED (one pool for the whole run; staging still flushes per folder, fired when that folder's last file lands). Measured on 8 real 45 MP TIFFs, 8 workers: **33s → 10s** when spread over 8 folders (10s in a single folder, 47s fully serial) |
| 205 | Manifest CSV written as UTF-8 **without BOM**: Excel opens it with the system ANSI codepage, so `240419_山羊公園_長瀞岩畳` displayed as 文字化け — and saving from there wrote the broken bytes back | wrapper | ✅ FIXED (written as `utf-8-sig`; readers use `utf-8-sig`, which also strips a BOM that would otherwise land in the first header cell and turn the header into a data row) |
| 206 | A manifest re-saved by Excel in the ANSI codepage crashed the reader with `UnicodeDecodeError` | wrapper | ✅ FIXED (refused with an actionable message). **Deliberately not** decoded with a guessed codepage: a wrong guess yields a plausible path pointing elsewhere, and these paths drive a converter that deletes sources in mode 8. Pure-ASCII manifests are valid UTF-8, so hand-written files are unaffected |

**Thumbnail pages are deliberately NOT covered by fix 202.** A thumbnail is a
reduced-resolution copy of a page that *is* in the output (TIFF `is_reduced` /
`is_subifd`), so `--thumbnail-mode exclude` does not block deletion — it would
block it for every Capture One export, since they all carry a preview. The delete
line says `(embedded thumbnail page not encoded)` instead. Use
`--thumbnail-mode include` when the decoded TIFF must reproduce the original page
structure exactly.

**Not a bug:** the duplicate-output abort itself. Modes 6/7 drop one folder level
under the marker by design, so `X/photo.tif` and `X/X/photo.tif` legitimately map
to the same `X/16B_JXL/photo.jxl`. The guard stopping the run is what prevents a
silent overwrite.

---

## v1.8.3 — Manifest run reporting (2026-07-27)

Found by running a 3-entry manifest over folders holding thousands of files each:
the user starts it, leaves, and comes back hours later to find out whether
anything broke.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 207 | A manifest run reported only `3 OK \| 0 skipped \| 0 errors` — **entries**, not files. Each entry is a separate child with its own log file, so the per-folder totals scrolled away and the only surviving number counted folders. Answering "did any file fail?" meant opening N logs by hand | wrapper | ✅ FIXED (children emit a machine-readable summary under `--summary-json`; the wrapper aggregates it into a per-entry table, a file-level TOTAL, and the list of failed files with their paths) |
| 208 | **Miscounted:** a corrupt or truncated TIFF was reported as `skipped by multipage policy`. `convert_multipage()` returns an empty list both when a policy asked for the file to be dropped (`--multipage-mode skip`, `--thumbnail-mode exclude`) and when the file has no readable pages at all — the caller could not tell them apart, so a damaged file was filed under a policy that never asked for it, and vanished into the `skipped` count | encoder | ✅ FIXED (new `UnreadableTiff`, raised when the analyzer found neither a real page nor a thumbnail; counted, logged and reported in its own bucket) |

**Why "corrupt" is not an error.** A damaged input is not a failed run: exit codes
keep their current meaning (`1` = the process failed on a file), so automation
reading them does not start firing on broken photos. The count gets its own
column and its own section in the summary instead.

**How the split is decided.** No heuristic: a healthy TIFF always has at least one
page, so "the analyzer returned neither a real page nor a thumbnail" is a reliable
signal. In `split_all` it is stronger still — that mode encodes every page there
is, so an empty result can only mean the file has none.

---

## v1.8.1 — The Audit Release (2026-07)

Twelve full audit rounds on the 4 scripts, each verified with reproductions and real-data batteries (Capture One 16-bit exports, 700 MB RGB+IR film scans). All fixes ship with regression tests (174 passing in `tests/`). Only the highest-impact bugs are detailed individually here; the full list is in `docs/RELEASE_v1.8.1.md`.

### Critical / data-safety

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 172 | `--no-verify` + djxl < 0.12: source deleted with a stored-but-never-compared MD5 — no verification at all | transcoder | ✅ FIXED (delete gate requires `md5_verified` from the same run, or djxl ≥ 0.12's `--reconstruct_jpeg`) |
| 173 | Invalid output recorded as OK: `cjxl`/`djxl` returning 0 with an empty/truncated file; smart sync then skipped it forever | all 3 | ✅ FIXED (every successful output passes an integrity check; failures deleted + per-file error) |
| 174 | Bare JXL codestream passed the delete gate on a 2-byte signature | encoder + transcoder | ✅ FIXED (container required; every toolkit output is one) |
| 175 | Partial/corrupt output left at destination on failure, then treated as "up to date" by smart sync | encoder + transcoder | ✅ FIXED (partial deleted on error; pre-existing outputs preserved) |
| 176 | MD5-failed decode output kept at destination and skipped on re-run | transcoder | ✅ FIXED (bad output deleted immediately) |
| 177 | `checksums.md5` recorded coverage for unvalidated outputs | transcoder | ✅ FIXED (written only after integrity passes) |
| 178 | Multipage groups merged across folders (same marker id) — one TIFF with duplicated pages, mode 8 deleted all copies | decoder | ✅ FIXED (group key is `(folder, group-id)`; duplicates demoted to standalone) |
| 179 | Source TIFF named `*_page<N>` / `*_thumbnail` corrupted page order and thumbnail roles on reconstruction | encoder + decoder | ✅ FIXED (authoritative `jxlphoto-page:` / `jxlphoto-thumb` XMP markers; filename is fallback only) |
| 180 | Modes 6/7 crashed with `IndexError` when a *filename* matched the `_EXPORT` marker | all 3 | ✅ FIXED (marker matches directory parts only) |
| 181 | Wizard "JXL → JPEG Auto" also converted folder JPEGs/PNGs *into* JXL (and could delete them in mode 8) | wrapper + transcoder | ✅ FIXED (`--from-jxl` flag) |
| 182 | Wizard "JPEG → JXL lossy" also converted (and in mode 8 deleted) folder PNGs | wrapper + transcoder | ✅ FIXED (`--from-jpeg` flag) |
| 183 | Auto mode encoded before decoding: `photo.jpg` + `photo.jxl` in one folder → JXL source overwritten before decoding | transcoder | ✅ FIXED (decode runs first + output==input abort when a write would happen; reruns are idempotent) |
| 184 | `shutdown`/locked-file `shutil.move` or `unlink()` aborted the whole batch mid-run | all 3 | ✅ FIXED (guarded; kept with warning) |
| 185 | TIFF integrity gate accepted truncated TIFFs (header-only check) | decoder + transcoder | ✅ FIXED (forced read of the last pixel of the last page) |
| 186 | JPEG/PNG integrity gates accepted truncation (SOI/signature only) | transcoder | ✅ FIXED (EOI / IEND required) |
| 187 | Wizard mode 8: double delete confirmation — wrapper HHMM + invisible child prompt → apparent infinite hang | wrapper + all 3 | ✅ FIXED (`--delete-confirm-off`; wrapper confirms once) |
| 188 | Manifest entries with mode 8 deleted without any HHMM gate | wrapper | ✅ FIXED |

### High

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 189 | ICC cautious-cache read-modify-write race across workers (corrupted/lost cache) | encoder | ✅ FIXED (lock + atomic write + cjxl-versioned key) |
| 190 | exiftool calls with raw paths in argv: `[ ]` treated as wildcards, non-ASCII paths broken on Windows | all 3 | ✅ FIXED (UTF-8 argfiles everywhere, `FileName=UTF8` + value charset) |
| 191 | Multi-line `dc:Description` injected bogus argfile lines | encoder | ✅ FIXED (newline sanitization) |
| 192 | Wizard passed `--delete-source` without suppressing child prompt; child blocked on stdin forever (timeout was dead code) | wrapper | ✅ FIXED (idle-timeout runner; HHMM in wrapper + confirm-off in child) |
| 193 | Multipage/grayscale XMP marker writes never checked exiftool's return code — silent round-trip corruption | encoder | ✅ FIXED (failure = per-file error) |
| 194 | `--force-transcode` on a `.jxl` routed to *encode* (`djxl file.jxl file.jxl`) | transcoder | ✅ FIXED (routes to jbrd-gated decode) |
| 195 | Auto + `--force-convert --format png` produced 8-bit PNGs on the JXL fallback | transcoder | ✅ FIXED (PNG default 16-bit preserved) |
| 196 | Convert modes 1/3 flattened the tree into one `converted/` folder (cross-folder collisions) | transcoder | ✅ FIXED (per-folder subfolders, aligned with transcode) |
| 197 | `copy_metadata` wiped legitimate user `ImageDescription`/`Software` (substring match on "shape"/"tifffile") | decoder | ✅ FIXED (only tifffile shaped-JSON/defaults cleared) |
| 198 | Delivered JPEG/PNG carried the `ICC:<base64>` CreatorTool blob (incl. the bare-blob common case) | transcoder | ✅ FIXED (stripped; wrong-profile pointer after sRGB conversion eliminated) |
| 199 | Gray+alpha JXLs failed TIFF writing (`expected 3, got 2`) when unmarked; LA preview failed with "cannot write mode LA as JPEG" | decoder | ✅ FIXED (minisblack + extrasample; LA→L preview) |
| 200 | Mode-7 Auto Mode preview promised one subfolder but the run processed all (seed wiped in Step 5) | wrapper | ✅ FIXED (seed preserved; recommendation requires a single origin subfolder) |
| 201 | `--icc-profile`/`--to-srgb` validated but silently inert: decode without ImageMagick delivered unconverted files | transcoder | ✅ FIXED (guard after direction auto-detect; hard failure) |
| 202 | `--to-srgb` used `magick -colorspace` (mathematical reinterpretation, wrong for wide gamut) | transcoder | ✅ FIXED (real sRGB ICC via `-profile`) |
| 203 | Integrity gate rejected (and deleted!) bit-exact JPEGs with trailing data after the EOI (Motion Photos, appended payloads — preserved by jbrd on purpose); MD5 check never ran | transcoder | ✅ FIXED (EOI/IEND searched in the last 64 KB instead of required at EOF) |
| 204 | Without `imagecodecs`, JXL→TIFF decode silently quantized 16-bit RGB/RGBA PNGs to 8-bit (output still "16-bit", data degraded) | decoder + wrapper | ✅ FIXED (hard per-file error for 16-bit RGB/RGBA/LA PNGs without imagecodecs; wrapper shows ✗ + required-for-16-bit message) |
| 205 | Auto Mode → [P] manifest → [Y] on mode 7 lost the auto-detected export subfolder (ran as mode 6; C1 trees aborted on duplicate destination) | wrapper | ✅ FIXED (same propagation as the direct [Y] path) |
| 206 | "Repeat last workflow" silently reapplied `delete_source` and expert flags without showing them | wrapper | ✅ FIXED (Last Workflow Settings table now shows DELETE SOURCE: ON and expert flags) |
| 207 | `.jfif`/`.jpe` outputs refused by the integrity gate (unknown extension) | transcoder | ✅ FIXED (added to the JPEG branch) |
| 208 | Editing workers/quality/effort in "Edit default settings" had no effect on any run: the screen wrote `default_*` while the wizard and "Repeat last workflow" both read `last_*` from the previous session. Read as "the repeat resets my workers" | wrapper | ✅ FIXED (a value changed in settings is also adopted as the `last_*` for the next run; untouched values still keep what the last run used, and the screen flags a default the saved session is overriding) |
| 209 | Manifest runs (mode 99) were never saved, so they could not be repeated and a recurring sync meant redoing the whole wizard | wrapper | ✅ FIXED (the CSV path is persisted and the repeat re-reads the file, picking up Excel edits; the `Direction` and path-traversal guards were extracted so the repeat validates exactly like the wizard) |

### Medium (selection)

- Exit codes (`0/1/2/3`) implemented across all scripts; wrapper distinguishes safety-abort from failure and cancelled.
- exiftool timeouts on big files (10 s) raised to 60–180 s; djxl timeouts 120 → 600 s.
- Worker exceptions can no longer kill a batch in any script (futures guarded; TOCTOU `stat()` guarded).
- Mode 4 folder rename: case-insensitive, first-token-only, `name_DEST` fallback; wrapper preview matches.
- `_marker_matches`: token boundaries (`exports`/`EXPORTED_RAWS`/`reexport` rejected; `Export_Lightroom`/`Lightroom_Export` accepted).
- Orientation tag round-trips (pipeline never rotates pixels).
- Duplicate-output abort is case-insensitive and lists conflicting source files.
- Finder filters: JPEG/PNG scans skip only toolkit decode-output folders *relative to scan root*; encoder modes 6/7 skip decoder output folders; JXL scans unfiltered (no round-trip breakage).
- `reorder_jxl_boxes`: `brob`/`jbrd` moved before codestream; size-0 box header rewritten when regrouped; raises on truncated extended boxes.
- `--delete-source` confirmations never fire on dry runs; dry runs never create folders or require cjxl.
- Palette/CMYK/planar-separate/spp∉{1,3,4} TIFFs rejected early with a rejected-files log.
- Grayscale detection driven by the actual array, not TIFF metadata.
- `_verify_jxl_integrity`/`_verify_file_integrity` walk the full box chain and require a codestream box.
- `read_png_to_numpy`: imagecodecs shape errors are hard per-file errors (no silent 16→8-bit degrade); LA PNGs preserved.
- `extract_trc_from_icc`: gamma read at the correct ICC offset (+12) for parametric curve types 1/2.
- `--output-suffix` revived in convert mode 2 (explicit output vs suffix folder).
- `extract_icc_native`: `-o` placed before the input file (exiftool is order-sensitive).
- Repeat-last: HHMM re-asked for mode 8; mode-2 output dir only reused for the same input folder; no live-config mutation; JXL→JPEG defaults to auto (jbrd-safe).
- Wrapper: idle-timeout child runner, Ctrl+C kills child, markup escaped, cp1252-safe stdout, quoted pasted paths, clamps in both UIs, Step-7 shows mode config + DELETE flag, mode-7 subfolder asked in Step 5.
- Manifest: `Direction` guard column, picker, Excel `7.0` mode parsing, header-row detection, `..` path-part check, Destination-ignored warning, dry-run forwarded to children.
- Stale `jxlphoto-*` relation markers cleaned on re-encode; stale ICC blob removed from existing CreatorTool.
- JPEG preview rewrite no longer injects tifffile default tags; previews never upscale.
- `read_ppm_to_numpy` accepts single-line PNM headers and validates truncation.
- TRC/gamma offsets, D50 dedup stats, uppercase extension finders, `--container=1` lossy-only.
- Test-suite hygiene: tests no longer depend on exiftool/rich/root semantics (fixtures + `skipif`).
- 18th round hygiene: `default_depth` initialized on all paths (no latent `UnboundLocalError`); ICC sniff checks `srgb` before `adobe` (log label); `get_exif_software` cache bounded (1024); 3 tests skip cleanly without imagecodecs (`importorskip`).

---

## v1.7 Multi-page TIFF Support

**Scripts:** `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_photo.py`

### Problem
Previously, `jxl_tiff_encoder.py` read only `tif.series[0]` and silently discarded any additional TIFF pages. This caused data loss for scanner multi-page TIFFs, layered files, or any other multi-page source.

### Solution
Added explicit multi-page handling across the TIFF workflow:

1. **Encoder (`jxl_tiff_encoder.py`)**
   - Detects "real" pages vs thumbnails using `page.is_reduced` and `page.is_subifd`.
   - New settings/CLI:
     - `MULTIPAGE_TIFF_MODE` / `--multipage-mode {ignore,skip,split,split_all}`
     - `THUMBNAIL_MODE` / `--thumbnail-mode {exclude,include}`
     - `THUMBNAIL_SUFFIX` / `--thumbnail-suffix`
   - Split output naming:
     - Page 0 → `photo.jxl`
     - Page N → `photo_pageN.jxl`
     - Thumbnail page N → `photo_pageN_thumbnail.jxl`
   - Split pages carry an XMP group marker in `XMP-dc:Relation` (prefix `jxlphoto-mpg:`). Using a list field preserves any existing `dc:Relation` values the user had.

2. **Decoder (`jxl_tiff_decoder.py`)**
   - Reconstructs multi-page TIFFs from pages that carry a matching group marker.
   - Files without the marker always decode as standalone TIFFs, even if their names look like pages (`scan.jxl` + `scan_page2.jxl` are never merged).
   - Markers are read in batch via exiftool to avoid one subprocess per file (performance regression fixed during audit).
   - Reconstructs a single multi-page TIFF with `tifffile.TiffWriter` writing each page sequentially.
   - New settings/CLI:
     - `THUMBNAIL_HANDLING` / `--thumbnail-handling {ignore,include,generate}`
     - `THUMBNAIL_SUFFIX` / `--thumbnail-suffix`
     - `RECONSTRUCT_MULTIPAGE` / `--no-reconstruct-multipage` to disable reconstruction entirely
   - JPEG preview is skipped when reconstructing multi-page TIFFs.
   - `--thumbnail-handling generate` is recognized but not yet implemented; it falls back to `include` with a warning.

3. **Per-page ICC preservation**
   - `jxl_tiff_encoder.py` extracts each page's own ICC (tag 34675) and falls back to IFD0's ICC when absent; inherited pages are flagged with `jxlphoto-icc:inherited` in `dc:Relation`.
   - `jxl_tiff_decoder.py` restores each page's own ICC tag; pages flagged as inherited are reconstructed without an ICC tag, matching the original structure.

4. **Grayscale and SubfileType preservation**
   - `jxl_tiff_encoder.py` records each page's original `SubfileType` and `SamplesPerPixel` and flags grayscale pages with `jxlphoto-grayscale` in `dc:Relation`.
   - Inherited RGB ICC is not applied to grayscale pages, avoiding libpng iCCP errors on scanner IR/mask pages.
   - `jxl_tiff_decoder.py` restores grayscale pages as 2D single-channel TIFFs and restores non-zero `SubfileType` values. `SubfileType=4` (MASK) is mapped to `PAGE` (`2`) because tifffile does not accept `MASK` on normal image pages, preserving the "additional page" semantics.

5. **Wrapper (`jxl_photo.py`)**
   - Advanced options now ask for multi-page mode and thumbnail handling for TIFF→JXL.
   - JXL→TIFF advanced options ask how to handle `_thumbnail.jxl` files.
   - New `ToolConfig` fields: `last_multipage_mode`, `last_thumbnail_mode`, `last_thumbnail_suffix`, `last_thumbnail_handling`.

### Audit fixes during v1.7 development
- Single-page metadata loss: preview step now runs **before** metadata copy, so EXIF/XMP survive.
- Concurrent mode-8 delete: status lookup keyed by main-JXL path instead of positional `zip(tasks, results)`.
- Corrupt TIFF at planning time: `convert_multipage` is wrapped per-file; ignore mode short-circuits without opening the file.
- Skip-status key mismatch: encoder skipped returns now use the same `((path, page_idx), ...)` key as ok/error.
- `tifffile.py` default tags: `Software` and shaped-JSON `ImageDescription` are cleared when the source has no EXIF/XMP to overwrite them (None mode).

### Test
New regression test: `tests/test_multipage.py`
- Creates a synthetic 3-page TIFF (real, thumbnail, real).
- Verifies encoder modes: ignore, skip, split exclude, split include.
- Verifies decoder reconstruction with ignore/include.
- Verifies single-page metadata roundtrip (Make/Software preserved).
- Verifies independent files with page-like names are **not** merged.
- Verifies user's existing `dc:Relation` survives split and the internal marker does not leak into the final TIFF.

---

## Summary Table

| # | Bug/Improvement | Scripts | Status |
|---|-----------------|---------|--------|
| 1 | Race condition in staging (UUID) | encoder, decoder, transcoder | ✅ FIXED |
| 2 | Distance not passed to cjxl | transcoder, photo | ✅ FIXED |
| 3 | Wrong delete confirmation for lossy ops | transcoder | ✅ FIXED |
| 4 | Deadlock in djxl+ImageMagick pipeline | transcoder | ✅ FIXED |
| 5 | PPM truncation / buffer overflow | decoder | ✅ FIXED |
| 6 | Integer overflow in JXL box parser | encoder, transcoder | ✅ FIXED |
| 7 | Description capturing exiftool warnings | encoder | ✅ FIXED |
| 8 | Missing UUID in process_group_transcode | transcoder | ✅ FIXED |
| 9 | D50 patch not preserved in repeat workflow | photo | ✅ FIXED |
| 10 | Invalid --resize option in wizard | photo | ✅ REMOVED |
| 11 | cjxl --lossless_jpeg=1 incompatible with distance>0 | transcoder | ✅ FIXED |
| 12 | Strip flag not implemented | encoder | ✅ FIXED |
| 13 | Status string case inconsistency | decoder | ✅ FIXED |
| 15 | Missing method in manifest workflow | photo_v2 | ✅ FIXED (v1.3) |
| 16 | Race condition in TIFF deletion | encoder | ✅ FIXED (v1.3) |
| 17 | Deadlock in djxl+magick pipeline | transcoder | ✅ FIXED (v1.3) |
| 19 | MD5 verification after reconvert | transcoder | ✅ FIXED (v1.3) |
| 20 | Missing subprocess timeout | photo_v2 | ✅ FIXED (v1.3) |
| 22 | Race condition in JXL deletion | decoder | ✅ FIXED (v1.3) |
| 23 | Race condition in source deletion | transcoder | ✅ FIXED (v1.3) |
| 25 | CJXL_DISTANCE range validation | encoder | ✅ FIXED (v1.3) |
| 26 | ExifTool timeout | encoder, transcoder | ✅ FIXED (v1.3) |
| 27 | `--distance 95` passed to transcoder (invalid) | photo | ✅ FIXED (v1.3) |
| 28 | Scripts resolved by relative path (CWD dependency) | photo | ✅ FIXED (v1.3) |
| 29 | `_show_mode_details_and_select` returns string not bool | photo | ✅ FIXED (v1.3) |
| 30 | Timeout not handled in subprocess | photo | ✅ FIXED (v1.3) |
| 31 | Set unordered in checksum DB path | transcoder | ✅ FIXED (v1.3) |
| 33 | Rich markup leaking in fallback without Rich | photo | ✅ FIXED (v1.3) |
| 36 | Quality/sRGB asked for lossless JXL→JPEG | photo | ✅ FIXED (v1.4) |
| 37 | Repeat last workflow not saving dest format | photo | ✅ FIXED (v1.4) |
| 38 | Step 2 TIFF option misnumbered | photo | ✅ FIXED (v1.4) |
| 40 | TIFF encoder Mode 3 using wrong folder constant | encoder | ✅ FIXED (v1.5) |
| 41 | TIFF encoder Mode 0 searching JPEG files | encoder | ✅ FIXED (v1.5) |
| 42 | Variable 'skipped' overwritten in encoder summary | encoder | ✅ FIXED (v1.5) |
| 43 | Manifest missing flags vs execute_workflow | photo | ✅ FIXED (v1.5) |
| 44 | Duplicate --format flag in transcoder | photo | ✅ FIXED (v1.5) |
| 45 | Thumbnail fallback using undefined PIL | encoder | ✅ FIXED (v1.5) |
| 46 | D50 summary uses wrong variable | encoder | ✅ FIXED (v1.5) |
| 47 | Thumbnail return aborts conversion | encoder | ✅ FIXED (v1.5) |
| 51 | --effort CLI ignored in transcode | transcoder | ✅ FIXED (v1.5) |
| 52 | bit_depth hardcoded in auto mode | transcoder | ✅ FIXED (v1.5) |
| 53 | ICC ignored with --no-ram | transcoder | ✅ FIXED (v1.5) |
| 54 | has_jbrd_box naive detection | transcoder | ✅ FIXED (v1.5) |
| 55 | jxl_to_jpeg_lossless missing --decode | photo | ✅ FIXED (v1.5) |
| 56 | jxl_to_jpeg_force missing --decode | photo | ✅ FIXED (v1.5) |
| 57 | jxl_to_png generates JPEG with jbrd | photo | ✅ FIXED (v1.5) |
| 58 | Repeat workflow loses thumbnail | photo | ✅ FIXED (v1.5) |
| 59 | Log shows wrong effort value | transcoder | ✅ FIXED (v1.5) |
| 60 | bit_depth=None in auto mode | transcoder | ✅ FIXED (v1.5) |
| 61 | Thumbnail fallback double except | encoder | ✅ FIXED (v1.5) |
| 62 | Extended size box not appended | encoder, transcoder | ✅ FIXED (v1.5) |
| 63 | EXIF binary corrupted by text=True | transcoder | ✅ FIXED (v1.5) |
| 64 | Mode 1 directory behavior wrong | encoder | ✅ FIXED (v1.5) |
| 65 | extract_exif_raw r.stdout None check | encoder | ✅ FIXED (v1.5) |
| 67 | --no-ram never works | encoder | ✅ FIXED (v1.5) |
| 68 | strip_metadata deletes Description | encoder | ✅ FIXED (v1.5) |
| 69 | PNG 8-bit shift results in zeros | decoder | ✅ FIXED (v1.5) |
| 70 | cleanup_xmp_icc duplicates label | decoder | ✅ FIXED (v1.5) |
| 71 | --format jpg falls to PNG path | transcoder | ✅ FIXED (v1.5) |
| 72 | _d50_patch_count["skipped"] never incremented | encoder | ✅ FIXED (v1.5.1) |
| 73 | Image.open() without with/close (file leak) | decoder | ✅ FIXED (v1.5.1) |
| 74 | Basic mode PIL can lose 16-bit | decoder | ✅ FIXED (v1.5.1) |
| 75 | MD5 checksums saved with UUID (staging) | transcoder | ✅ FIXED (v1.5.1) |
| 76 | decode_to_image returns staging path | transcoder | ✅ FIXED (v1.5.1) |
| 77 | JPEG 16-bit→PNG without updating final_path | transcoder | ✅ FIXED (v1.5.1) |
| 79 | subfolders strings with .name (AttributeError) | photo | ✅ FIXED (v1.5.1) |
| 80 | manifest dest_path never used | photo | ✅ FIXED (v1.5.1) |
| 81 | ICC TRC parsing s15Fixed16Number as float | decoder | ✅ FIXED (v1.5.1) |
| 82 | cmd_auto uses wrong resolver for convert | transcoder | ✅ FIXED (v1.5.1) |
| 83 | EXPORT_MARKER substring match inconsistent | encoder, decoder, transcoder | ✅ FIXED (v1.5.1) |
| 84 | resolve_output_convert parameters swapped | transcoder | ✅ FIXED (v1.5.1) |
| 85 | EXPORT_MARKER find_* vs resolve_output inconsistent | encoder, transcoder | ✅ FIXED (v1.5.1) |
| 86 | Inconsistent returns 3 vs 4 elements | transcoder | ✅ FIXED (v1.5.1) |
| 87 | 8-bit TIFF → JXL black images (scaling) | encoder | ✅ FIXED (v1.5.2) |
| 88 | OVERWRITE log reports "no" when default is "smart" | encoder, decoder | ✅ FIXED |
| 89 | PIL_MAX_IMAGE_PIXELS config ignored in decoder | decoder | ✅ FIXED |
| 90 | 8-bit PNG not scaled to 16-bit in decoder | decoder | ✅ FIXED |
| 91 | `cmd_auto` routes JPEG folders to decode instead of encode | transcoder | ✅ FIXED (v1.5.3) |
| 92 | Staging moves failed outputs to final destination | encoder, decoder, transcoder | ✅ FIXED (v1.5.3) |
| 93 | `cmd_auto --delete-source` deletes without confirmation | transcoder | ✅ FIXED (v1.5.3) |
| 94 | Basic mode PNG reader downgrades 16-bit to 8-bit | decoder | ✅ FIXED (v1.5.3) |
| 95 | Matrix mode `--color_space` token incompatible with libjxl v0.11.x | decoder | ✅ FIXED (v1.5.3) |
| 96 | `--export-marker` case mismatch and missing propagation | encoder, decoder, transcoder, photo | ✅ FIXED (v1.5.3) |
| 97 | Mode 2 output folder handling inconsistent | encoder, decoder, transcoder, photo | ✅ FIXED (v1.5.3) |
| 98 | Manifest modes 6/7 reset to 0/2 by wrapper | photo | ✅ FIXED (v1.5.3b) |
| 99 | `logger` undefined in wrapper aborts workflow | photo | ✅ FIXED (v1.5.3) |
| 100 | Extended size box validation rejects valid JXL containers | encoder, transcoder | ✅ FIXED (v1.5.3a) |
| 101 | `--jpeg_quality` ignored in direct JXL→JPEG path | transcoder | ✅ FIXED (v1.5.3) |
| 102 | Orphaned alternate-extension staging file left on failure | transcoder | ✅ FIXED (v1.5.3) |
| 103 | `make_png_bytes` crashes on float/CMYK/unsupported shapes | encoder | ✅ FIXED (v1.5.3) |
| 104 | D50 patch statistics printed wrong count | encoder | ✅ FIXED (v1.5.3b) |
| 105 | Encoder staging `status_map` mismatches under concurrency | encoder | ✅ FIXED (v1.5.3a) |
| 106 | Basic mode grayscale 8-bit PNG not scaled to 16-bit | decoder | ✅ FIXED (v1.5.3a) |
| 107 | `--format jpeg` in auto mode forces PNG / double-dot filename | transcoder | ✅ FIXED (v1.5.3a) |
| 108 | Skipped files spam "KEEP in staging" warning | encoder, decoder, transcoder | ✅ FIXED (v1.5.3b) |
| 109 | Wizard offers non-functional "skip" existing-file option | photo | ✅ FIXED (v1.5.3b) |
| 110 | Adobe RGB / ProPhoto RGB ICC aliases fail (PIL limitation) | decoder, photo | ✅ FIXED (v1.5.3b) |
| 111 | Wrapper detects marker as substring while scripts use prefix/suffix | photo | ✅ FIXED (v1.5.3c) |
| 112 | CMYK TIFFs silently treated as RGBA | encoder | ✅ FIXED (v1.5.3c) |
| 113 | Dead `global _counter` in `_process_file_group` | transcoder | ✅ FIXED (v1.5.3c) |
| 114 | D50 patch corrupts ICC profiles shorter than 80 bytes | encoder | ✅ FIXED |
| 115 | Encoder modes 2 and 8 fail on single file input | encoder | ✅ FIXED |
| 116 | Multi-page TIFF loses IFD1 metadata during decode | decoder | ✅ FIXED |
| 117 | XMP ICC extraction accepts invalid base64 / fake ICC | decoder | ✅ FIXED |
| 118 | PNG `target_depth=8` grayscale conversion broken | decoder | ✅ FIXED |
| 119 | Matrix mode silently quantizes 16-bit to 8-bit via LittleCMS | decoder | ✅ FIXED (limitation documented) |
| 120 | Wrapper repeat workflow loses advanced options and basic parameters | photo | ✅ FIXED |
| 121 | Wrapper manifest mode ignores advanced options | photo | ✅ FIXED |
| 122 | Repeat workflow offered for manifest mode 99 | photo | ✅ FIXED |
| 123 | `jxl_jpeg_transcoder.encode_to_jxl` returns 3-element tuples instead of 4 | transcoder | ✅ FIXED |
| 124 | Lossy JXL encoding darkens images with large scanner ICCs in PNG iCCP | encoder | ✅ FIXED |
| 120d | Embedded JPEG thumbnail always generated from page 0 | encoder | ✅ FIXED (v1.7.1) |
| 121d | `reorder_jxl_boxes()` fails on bare codestreams | encoder, transcoder | ✅ FIXED (v1.7.1) |
| 122d | Obsolete comments in `add_jpeg_preview()` | decoder | ✅ FIXED (v1.7.1) |
| 123d | Transcoder uses invalid `--output_format=png` flag | transcoder | ✅ FIXED (v1.7.1) |
| 124d | Staging orphan with `--format jpeg --bit-depth 16` | transcoder | ✅ FIXED (v1.7.1) |
| 125 | `make_png_bytes()` always writes 16-bit PNGs | encoder | ✅ FIXED (v1.7.1) |
| 126 | `add_jpeg_preview()` obsolete comment and dead code | decoder | ✅ FIXED (v1.7.1) |
| 127 | `read_ppm_to_numpy()` fails when dimensions are on separate lines | decoder | ✅ FIXED (v1.7.1) |
| 128 | `--force-transcode --decode` crashes on missing `jbrd` box | transcoder | ✅ FIXED (v1.7.1) |
| 129 | Wrapper `--delete-source` confirmation invisible/blocked | photo | ✅ FIXED (v1.7.1) |
| 130 | Lossy convert paths drop EXIF/XMP/IPTC metadata | transcoder | ✅ FIXED (v1.7.1) |
| 131 | `decode_one_transcode()` never reports reconvert on overwrite | transcoder | ✅ FIXED (v1.7.1) |
| 132 | `--multipage-mode skip` always encodes page 0 | encoder | ✅ FIXED (v1.7.1) |
| 133 | JPEG scanners ignore `.jfif` / `.jpe` files | transcoder, photo | ✅ FIXED (v1.7.1) |
| 134 | `_copy_metadata()` runs after `reorder_jxl_boxes()`, undoing the reorder | transcoder | ✅ FIXED (v1.7.2) |
| 135 | `execute_workflow()` missing `PYTHONUNBUFFERED` (main wizard path) | photo | ✅ FIXED (v1.7.2) |
| 136 | `--dry-run` ignored on transcode/auto paths (converts for real) | transcoder | ✅ FIXED (v1.8.0) |
| 137 | Wizard mode 8 `--delete-source` dropped on most paths | photo | ✅ FIXED (v1.8.0) |
| 138 | Auto mode + staging + 16-bit: output stranded in staging as UUID | transcoder | ✅ FIXED (v1.8.0) |
| 139 | Transcoder mode 1 recursive (docs/decoder say flat) | transcoder | ✅ FIXED (v1.8.0) |
| 140 | Transcoder modes 4/5 inverted vs encoder/decoder (+ wrong wrapper labels) | transcoder, photo | ✅ FIXED (v1.8.0) |
| 141 | Auto mode does nothing on PNG-only folders | transcoder | ✅ FIXED (v1.8.0) |
| 142 | Wizard asks decode mode twice; second pass discards the first | photo | ✅ FIXED (v1.8.0) |
| 143 | Repeat workflow loses `distance` for lossy conversions | photo | ✅ FIXED (v1.8.0) |
| 144 | HHMM lossy confirmation required for lossless transcode decode | transcoder | ✅ FIXED (v1.8.0) |
| 145 | Progress total counts files filtered out by modes 6/7 | transcoder | ✅ FIXED (v1.8.0) |
| 146 | Wizard mode 2 output positional after flags breaks argparse on Python < 3.12.7 | photo | ✅ FIXED (v1.8.0) |
| 147 | D50 patch stats count per page in multipage splits | encoder | ✅ FIXED (v1.8.1) |
| 148 | `check_dependencies(force=...)` ignores the `force` parameter | photo | ✅ FIXED (v1.8.1) |
| 149 | Mode 8 delete flag lost on manual/detail mode-selection paths | photo | ✅ FIXED (v1.8.1) |
| 150 | `cmd_auto` ignores script-level `DELETE_SOURCE`/`DELETE_CONFIRM` | transcoder | ✅ FIXED (v1.8.1) |
| 151 | Multipage marker batch: 32k cmdline limit + `[` wildcards + silent fallback | decoder | ✅ FIXED (v1.8.1) |
| 152 | Mode 6 output collision across `_EXPORT` subfolders | encoder, decoder, transcoder | ✅ FIXED (v1.8.1) |
| 153 | Decoder partial output + smart sync skips forever | decoder | ✅ FIXED (v1.8.1) |
| 154 | `shlex.split` posix mangles Windows paths in expert flags | photo | ✅ FIXED (v1.8.1) |
| 155 | Repeat workflow loses quality for `jxl_to_jpeg_force/auto` | photo | ✅ FIXED (v1.8.1) |
| 156 | `--decode` ignored for directories | transcoder | ✅ FIXED (v1.8.1) |
| 157 | `--ram`/`--no-ram` is a no-op in transcoder decode | transcoder | ✅ FIXED (v1.8.1, help/docs) |
| 158 | `cmd_auto` progress total counts files filtered by modes 6/7 | transcoder | ✅ FIXED (v1.8.1) |
| 159 | `--container=1` applied on lossless (d=0) in `encode_to_jxl` | transcoder | ✅ FIXED (v1.8.1) |
| 160 | `cleanup_xmp_icc` leaves leading `\| ` when ICC marker is mid-string | decoder | ✅ FIXED (v1.8.1) |
| 161 | Wizard texts promise wrong folder names (modes 1/3, auto preview) | photo | ✅ FIXED (v1.8.1) |
| 162 | Step 7 summary shows Quality for distance-driven lossy | photo | ✅ FIXED (v1.8.1) |
| 163 | Pure-text wizard fallback asks target ICC outside matrix mode | photo | ✅ FIXED (v1.8.1) |
| 164 | `decode_auto` dead code in decoder | decoder | ✅ FIXED (v1.8.1) |
| 165 | Lowercase-only globs miss `.TIF`/`.JXL` on case-sensitive filesystems | encoder, decoder, transcoder | ✅ FIXED (v1.8.1) |
| 166 | ICC verify commands reference swapped XMP fields (docs) | docs | ✅ FIXED (v1.8.1) |
| 167 | Mode 7 default + `HHMMSS` format wrong in tools README (docs) | docs | ✅ FIXED (v1.8.1) |
| 168 | Step 7 plain-text summary shows Quality for distance-driven lossy | photo | ✅ FIXED (v1.8.1) |
| 169 | Mode 1 detail example hardcodes `converted_{dest}` | photo | ✅ FIXED (v1.8.1) |
| 170 | "KEEP in staging" logged for partial outputs already discarded | decoder | ✅ FIXED (v1.8.1) |
| 171 | Cross-group collision invisible in auto mode (photo.jpg + photo.png) | transcoder | ✅ FIXED (v1.8.1) |

**Total bugs fixed: 165**

> **Note:** Items related to new features, code quality, and compatibility have been moved to:
> - [`new_features_since_v1.0.md`](new_features_since_v1.0.md) — for new capabilities and behavior changes
> - [`code_quality_refactoring.md`](code_quality_refactoring.md) — for internal cleanups, compat backports, and dead code

## Detailed Bug Reports

### Bug #1 — Race Condition in Staging Directory

**Location:** `process_group()` in all scripts

**Problem:** When `TEMP2_DIR` (staging) is used, the staging filename used only `{parent_name}__{stem}.jxl` format without UUID. When two threads processed files with the same name from different folders, filename collisions could occur.

```python
# BEFORE (vulnerable)
write_jxl = staging_dir / f"{tiff.parent.name}__{tiff.stem}.jxl"
```

**Scenario:**
- Thread 1: `folder1/photo.tif` → `staging/folder1__photo.jxl`
- Thread 2: `folder2/photo.tif` → `staging/folder2__photo.jxl`
- Works if parent names differ, but breaks if same folder name

**Fix:** Added UUID to staging filename:
```python
# AFTER (fixed)
write_jxl = staging_dir / f"{uuid.uuid4().hex}_{tiff.stem}.jxl"
```

**Files affected:**
- `jxl_tiff_encoder.py` line 850
- `jxl_tiff_decoder.py` line 963
- `jxl_jpeg_transcoder.py` line 1094

---

### Bug #2 — Distance Parameter Not Passed to cjxl

**Location:** `jxl_jpeg_transcoder.py`, `jxl_photo.py`

**Problem:** When converting PNG→JXL, the `--distance` parameter was not being passed to the `cjxl` command. The user could specify `--distance` but the value was completely ignored — cjxl would use its default distance instead.

The wrapper (`jxl_photo.py`) also wasn't passing `--distance` to the transcoder for lossy conversions.

**Fix:** `jxl_jpeg_transcoder.py`:
1. Added `distance: float` parameter to `encode_to_jxl()` signature
2. Added `"-d", str(distance)` to the cjxl command
3. Added `distance` parameter to `process_group_convert()`
4. Updated call in `cmd_convert` to pass `args.distance`
5. Added `--distance` argument to the parser

`jxl_photo.py`:
6. Added `--distance` to the transcoder call for lossy conversions

**Verification:** After fix, `distance=1.0` produces visibly smaller JXL files than `distance=0.1`.

---

### Bug #3 — Wrong Delete Confirmation for Lossy Operations

**Location:** `jxl_jpeg_transcoder.py` — `cmd_transcode()` and `cmd_convert()`

**Problem (Part 1 — cmd_transcode):**
When `DELETE_SOURCE` was active for JXL→JPEG (lossy decode), the code called `confirm_deletion_jpeg()` which only requires typing "yes". For lossy operations, it should call `confirm_deletion_lossy()` which requires the current time in HHMM format, ensuring the user understands the operation is irreversible.

**Fix (Part 1):** Added check for lossy decode (`is_lossy_decode`) and calls the appropriate confirmation function:
- Lossy decode (JXL→JPEG): `confirm_deletion_lossy()` (requires HHMM)
- Lossless transcode: `confirm_deletion_jpeg()` (requires "yes")

**Problem (Part 2 — cmd_convert):**
The `cmd_convert` function ALWAYS called `confirm_deletion_lossy()` without checking if the operation was actually lossy. This forced HHMM confirmation even for lossless operations (like PNG→JXL with distance=0).

**Fix (Part 2):** Implemented proper detection logic:
- Direction "to_jxl": lossy if `args.distance > 0`
- Direction "from_jxl": lossy if format is JPEG or if ICC profile is present
- Uses `confirm_deletion_jpeg()` for lossless operations
- Uses `confirm_deletion_lossy()` only for actually lossy operations

---

### Bug #4 — Deadlock in djxl+ImageMagick Pipeline

**Location:** `jxl_jpeg_transcoder.py` — `decode_to_image()`

**Problem:** When using `subprocess.Popen` to pipe djxl output to ImageMagick, the stderr of djxl was not being consumed during magick's `communicate()`. If djxl generated many errors/warnings before magick finished, the stderr buffer would fill and djxl would block waiting for the buffer to be emptied → **deadlock**.

The comment at line 1005 said "Read stderr to prevent deadlock" but the code only read djxl's stderr AFTER `communicate()`, not during it.

```python
djxl_proc = subprocess.Popen(..., stderr=subprocess.PIPE)
magick_proc = subprocess.Popen(..., stdin=djxl_proc.stdout, stderr=subprocess.PIPE)
djxl_proc.stdout.close()
magick_stdout, magick_stderr = magick_proc.communicate(timeout=300)  # only reads magick's stderr!
djxl_stderr = djxl_proc.stderr.read()  # read AFTER, not during — deadlock possible
djxl_proc.wait()
```

**Fix:** Used a background thread to consume djxl's stderr in real-time while magick executes:
```python
def _read_stderr_thread(proc):
    proc.stderr.read()

stderr_thread = threading.Thread(target=_read_stderr_thread, args=(djxl_proc,))
stderr_thread.start()
magick_stdout, magick_stderr = magick_proc.communicate(timeout=300)
stderr_thread.join(timeout=5)
```

**Applied to:** Both JPEG and PNG output paths in `decode_to_image()`.

---

### Bug #5 — PPM Truncation / Buffer Overflow

**Location:** `jxl_tiff_decoder.py` — `read_ppm_to_numpy()`

**Problem:** The function did not validate if the PPM file was read completely. If djxl crashed during decoding, the PPM file would be truncated but the function would still try to process it, causing `ValueError` from failed reshape or worse — corrupted TIFF output.

```python
raw = f.read()  # reads whatever is there, no validation
pixel_data = np.frombuffer(raw, dtype=np.uint8)
img = pixel_data.reshape((height, width, 3))  # fails if raw is incomplete
```

**Fix:** Added expected size calculation and validation:
```python
expected_size = height * width * 3 * (2 if maxval > 255 else 1)
if len(raw) < expected_size:
    raise RuntimeError(f"PPM file truncated: got {len(raw)} bytes, expected {expected_size}")
# Defensive: trim extra data if present
if len(raw) > expected_size:
    raw = raw[:expected_size]
```

**Note:** Also added defensive trimming for cases where djxl writes extra data beyond the expected size.

---

### Bug #6 — Integer Overflow in JXL Box Parser

**Location:** `jxl_tiff_encoder.py` and `jxl_jpeg_transcoder.py` — `reorder_jxl_boxes()`

**Problem:** The function did not validate the box size before slicing. A malicious or corrupted JXL file with `size=0xFFFFFFFF` or absurd values could cause:
- MemoryError (allocating GBs of RAM)
- Infinite loops
- Data corruption

```python
size = int.from_bytes(data[i:i+4], "big")
header, payload = data[i:i+8], data[i+8:i+size]  # no validation!
```

**Fix:** Added size validation with limits:
```python
MAX_BOX_SIZE = 4 * 1024 * 1024 * 1024  # 4GB max

if size > MAX_BOX_SIZE:
    raise ValueError(f"Box size exceeds maximum: {size}")
if size > len(data) - i:
    raise ValueError(f"Invalid box size: {size} at offset {i}")
if size < 8:
    raise ValueError(f"Box size too small: {size}")
```

**Applied to:** Both `jxl_tiff_encoder.py` (line 576-627) and `jxl_jpeg_transcoder.py` (line 238-258).

---

### Bug #7 — Description Capturing exiftool Warnings

**Location:** `jxl_tiff_encoder.py` — `read_existing_description()`

**Problem:** The function captured exiftool warnings along with the actual description value. Result: metadata ended up with text like "No EXIF found | cjxl d=0.1" instead of just the encoding parameters.

```python
# BEFORE (buggy)
for line in r.stdout.splitlines():
    if not line.strip():
        continue
    if not line.startswith("Warning:"):
        description_parts.append(line)
```

**Fix:** Filter out warning lines before processing:
```python
# AFTER (fixed)
for line in r.stdout.splitlines():
    stripped = line.strip()
    if not stripped:
        continue
    # Filter exiftool warnings
    if stripped.startswith(("Warning:", "[minor]", "[major]")):
        continue
    description_parts.append(stripped)
```

If only warnings remain, returns empty string instead of garbled text.

---

### Bug #8 — Missing UUID in process_group_transcode

**Location:** `jxl_jpeg_transcoder.py` — `process_group_transcode()`

**Problem:** The function used `f"{src.parent.name}__{src.stem}{ext}"` for staging filenames instead of UUID. This caused race conditions when files with the same name came from different folders (e.g., `folder1/photo.jpg` and `folder2/photo.jpg`).

```python
# BEFORE (vulnerable — same as bug #1 but in transcode path)
write_path = staging_dir / f"{src.parent.name}__{src.stem}{ext}"
```

**Fix:** Changed to use `uuid.uuid4().hex` like the rest of the codebase:
```python
write_path = staging_dir / f"{uuid.uuid4().hex}_{src.stem}{ext}"
```

---

### Bug #9 — D50 Patch Not Preserved in Repeat Workflow

**Location:** `jxl_photo.py` — repeat workflow (option 2)

**Problem:** When the user chose "Repeat last workflow" (option 2), the `advanced_options` was recreated from scratch with only `overwrite` and `sync`, losing the `d50_patch` and `encode_tag` settings that were configured previously.

**Fix:** Preserved values from last workflow when available:
```python
# Copiar d50_patch do last_advanced se origin for 'tiff'
d50_patch = last_advanced.get('d50_patch', 'auto') if origin == 'tiff' else None
# Copiar encode_tag do last_advanced se origin for 'tiff'
encode_tag = last_advanced.get('encode_tag', 'xmp') if origin == 'tiff' else None
```

---

### Bug #10 — Invalid --resize Option in Wizard

**Location:** `jxl_photo.py` — wizard for TIFF→JXL

**Problem:** The wizard accepted a `--resize` option in Step 6A, stored it in `advanced_options['resize']`, and even tried to pass it to the encoder command. However, none of the scripts (`jxl_tiff_encoder.py`, `jxl_jpeg_transcoder.py`, `jxl_tiff_decoder.py`) actually support a `--resize` flag.

The option was collected but did nothing — misleading to users.

**Fix:** Removed the `--resize` option from the wizard entirely.

---

### Bug #11 — cjxl --lossless_jpeg=1 Incompatible with distance>0

**Location:** `jxl_jpeg_transcoder.py` — `encode_to_jxl()`

**Problem:** cjxl 0.11.2 defaults to `--lossless_jpeg=1` (preserves JPEG data for lossless transcode). However, `--lossless_jpeg=1` is **incompatible** with `distance>0` (lossy mode). Attempting JPEG→JXL lossy conversion caused error:

```
cjxl: Must not set non-zero distance in combination with --lossless_jpeg=1, which is set by default.
```

The toolkit was not passing `--lossless_jpeg=0` when `distance>0`, causing all lossy JPEG→JXL conversions to fail.

**Fix:** Added check in `encode_to_jxl()`:
```python
if distance > 0:
    cmd.append("--lossless_jpeg=0")
```

This allows cjxl to recompress the JPEG data with the specified quality instead of preserving the original.

**Verification:**
```
DSC00004_AdobeRGB_v1.jpg → DSC00004_AdobeRGB_v1.jxl (lossy, distance=1.0)
✅ Converted successfully
```

---

### Bug #12 — Strip Flag Not Implemented

**Location:** `jxl_tiff_encoder.py` — `build_metadata_injection_args()`

**Problem:** The CLI accepted `--strip` flag, but it did nothing. Even with the flag set, all EXIF/XMP metadata was preserved in the output JXL. The `STRIP_METADATA` global existed but was never actually used to modify the exiftool arguments.

**Root Cause:** The function `build_metadata_injection_args()` completely ignored the `strip_metadata` parameter that was passed to it. It always ran the full metadata preservation logic regardless of the flag.

```python
# BEFORE (buggy)
def build_metadata_injection_args(tiff_path, write_path, tmp_dir, ...):
    # strip_metadata parameter accepted but never checked!
    args_lines = ["-overwrite_original"]
    # ... full EXIF/XMP preservation logic always ran
```

**Fix:** Added proper conditional logic:
```python
# AFTER (fixed)
def build_metadata_injection_args(..., strip_metadata=False):
    args_lines = ["-overwrite_original"]
    
    if strip_metadata:
        # Only encoding params, no metadata
        encoding_desc = f"cjxl d={CJXL_DISTANCE} e={CJXL_EFFORT}"
        args_lines.append(f"-xmp-dc:Description={encoding_desc}")
        args_lines.append("-exif:all=")  # Strip all EXIF
        args_lines.append("-xmp:all=")   # Strip all XMP
        # ... return early
    
    # Normal metadata preservation only runs if NOT stripping
```

**Files affected:**
- `jxl_tiff_encoder.py` — Added `STRIP_METADATA` global, updated `build_metadata_injection_args()`, added CLI argument

---

### Bug #13 — Status String Case Inconsistency

**Location:** `jxl_tiff_decoder.py` — `convert_one()` and `process_group()`

**Problem:** `convert_one()` returned status strings in UPPERCASE ("OK", "overwrite"), but `process_group()` checked against lowercase ("ok", "overwrite"). This caused silent failures in status tracking — files that converted successfully were sometimes treated as errors because the string didn't match.

```python
# BEFORE (inconsistent)
def convert_one(...):
    status = "overwrite" if overwritten else "OK"  # "OK" is uppercase!
    return str(jxl_path), status, str(final_path)

def process_group(...):
    for result in results:
        status = result[1]
        if status not in ("ok", "overwrite"):  # Checks lowercase!
            # "OK" would fail this check and be treated as error
```

**Fix:** Standardized on lowercase for internal status, uppercase only for display:
```python
# AFTER (consistent)
def convert_one(...):
    status = "overwrite" if overwritten else "ok"  # lowercase
    label = "OVERWRITE" if overwritten else "OK"   # uppercase for UI
    logger.info(f"[{n}/{total}] {label} | ...")     # display uses label
    return str(jxl_path), status, str(final_path)  # return uses status
```

**Impact:** Fixed silent failures where successful conversions were logged as errors due to case mismatch.

---

### Improvement #14 — Basic Mode Now Preserves djxl ICC (v1.2)

**Location:** `jxl_tiff_decoder.py` — Basic decode mode

**Note:** This is an improvement, not a bug fix. The old behavior (discarding ICC) was technically "working as designed" but was not useful for most workflows.

**Problem with old Basic mode (v1.0 - v1.1):**
- Decoded to PPM format (which has no ICC support)
- Output TIFF had no ICC profile attached
- Was only useful for web/sRGB workflows where color accuracy didn't matter

**Improvement (v1.2):**
- Decodes to PNG format to capture ICC profile generated by djxl
- Extracts ICC from PNG and attaches to output TIFF
- Now the default behavior when no XMP ICC is present
- Makes more sense for most workflows

**New None Mode:**
The old "discard ICC" behavior is still available as the **None** mode (`--none` flag or `FORCE_NONE_MODE = True`).

**Technical changes:**
1. Added `decode_auto_png()` function for PNG output
2. Added `extract_icc_from_png()` function using PIL
3. Added `read_png_to_numpy()` function for PNG→numpy conversion
4. Changed Basic mode to use PNG intermediate instead of PPM
5. Added `--none` CLI flag for the old behavior
6. Updated `select_decode_strategy()` to use "basic" as default (not "none")

**Migration:**
- Users who relied on Basic mode producing no ICC should now use `--none`
- Default behavior (no flags) now produces better results for most users


## v1.3 Release Notes (2026-04-11)

### File Reorganization (Decoder)

**Change:** TIFF decoder completely rebuilt in v1.3

**Previous versions (deprecated):**
- `jxl_tiff_decoder_v1_old.py` — Original decoder (JPEG preview as page 1, no ICC in preview)

**Current official (v1.3):**
- `jxl_tiff_decoder.py` — Completely rebuilt decoder

**Key improvements:**
| Feature | Old (v1) | Current (v1.3) |
|---------|----------|----------------|
| JPEG Preview | Page 1 (secondary) | Page 1 (with NEWSubfileType=1 flag) |
| Windows Explorer | Generic icon | **Color-managed thumbnail** |
| Preview colorspace | ❌ Original (needs ICC, shows wrong colors) | ✅ **sRGB converted** (correct colors without ICC) |
| ICC in main image | ✅ Yes | ✅ Yes |
| ICC in preview | ❌ No (needed - not sRGB) | ✅ No (not needed — sRGB native) |
| File integrity verification | ❌ No | ✅ Yes (before source deletion) |
| Python 3.8 compatibility | ❌ No | ✅ Yes (backport included) |

**Why it matters:**
The old decoder saved JPEG preview as a secondary page (page 1), which Windows Explorer ignored. The new decoder saves JPEG as page 0 with `NEWSubfileType=1` (thumbnail flag), making Windows Explorer display the correct color-managed preview immediately.

**Migration:**
- Use `jxl_tiff_decoder.py` (current official)
- Old versions preserved in `deprecated/` for reference only

---
### Bug #15 — Missing Method in Manifest Workflow

**Location:** `jxl_photo_v2.py` — `_execute_manifest_workflow()`

**Problem:** Line 2168 called `self._detect_mode_for_entry()`, but this method doesn't exist in the `InteractiveMenu` class — it belongs to `FolderAnalyzer`. This would cause an `AttributeError` crash when using manifest mode (mode 99).

```python
# BEFORE (buggy)
for i, (source, dest_path) in enumerate(manifest_entries, 1):
    detected_mode = self._detect_mode_for_entry(source, dest_path)  # Method doesn't exist!
```

**Fix:** Create a `FolderAnalyzer` instance outside the loop and call the correct method:
```python
# AFTER (fixed)
analyzer = FolderAnalyzer(Path("."), origin, dest, self.config.config.export_marker)

for i, (source, dest_path) in enumerate(manifest_entries, 1):
    detected_mode = analyzer.detect_mode_for_entry(source, dest_path)
```

---

### Bug #16 — Race Condition in TIFF Deletion

**Location:** `jxl_tiff_encoder.py` — `process_group()`

**Problem:** When `DELETE_SOURCE` is enabled, the code deleted source TIFF files after checking if the JXL exists, but without verifying the JXL file integrity. A corrupted or incomplete JXL could pass the `exists()` check, leading to data loss.

```python
# BEFORE (vulnerable)
if final_jxl is None or not final_jxl.exists():
    continue
src_tiff.unlink()  # Deletes even if JXL is corrupted!
```

**Fix:** Added `_verify_jxl_integrity()` function that checks:
1. File exists and size > 0
2. Valid JXL signature (0xFF 0x0A for bare JXL or ISOBMFF container)

```python
# AFTER (fixed)
def _verify_jxl_integrity(jxl_path: Path) -> bool:
    # Check file exists, size > 0, and valid JXL header
    ...

if not _verify_jxl_integrity(final_jxl):
    logger.warning(f"  KEEP (JXL failed integrity check) | {src_tiff.name}")
    continue
src_tiff.unlink()
```

---

### Bug #17 — Deadlock in djxl+ImageMagick Pipeline

**Location:** `jxl_jpeg_transcoder.py` — `decode_to_image()`

**Problem:** When piping djxl output to ImageMagick via `subprocess.Popen`, the stderr of djxl was not being consumed while magick was running. If djxl generated many errors, its stderr buffer would fill and block, causing a deadlock.

**Fix:** Created `_run_pipeline_safe()` helper function that:
1. Reads stderr from both processes in separate threads
2. Prevents buffer deadlock
3. Implements proper timeout handling (300s default)
4. Cleans up processes on timeout

Applied to both JPEG and PNG conversion paths that use RAM mode with ICC profiles.

---

### Bug #18 — Python 3.9+ Compatibility (is_relative_to)

**Location:** `jxl_photo_v2.py` — Multiple locations

**Problem:** Code used `Path.is_relative_to()` method which only exists in Python 3.9+. This caused `AttributeError` on Python 3.8 systems.

```python
# BEFORE (Python 3.9+ only)
rel_src = Path(src).relative_to(input_dir) if Path(src).is_relative_to(input_dir) else Path(src)
```

**Fix:** Added `_is_relative_to()` backport function:
```python
# AFTER (Python 3.8+ compatible)
def _is_relative_to(path: Path, anchor: Path) -> bool:
    try:
        path.relative_to(anchor)
        return True
    except ValueError:
        return False

rel_src = Path(src).relative_to(input_dir) if _is_relative_to(Path(src), input_dir) else Path(src)
```

**Note:** Tested with complex paths containing spaces, brackets, and Unicode characters (吾妻山公園).

---

### Bug #19 — MD5 Verification After Reconversion

**Location:** `jxl_jpeg_transcoder.py` — `read_md5_db()`

**Problem:** When a file was reconverted (overwritten), a new MD5 entry was appended to the checksums file. However, `read_md5_db()` read from top to bottom and returned the **first** matching entry, which was the old (stale) MD5. This caused false MD5 verification failures after reconversion.

```python
# BEFORE (buggy)
for line in f:  # Reads top-to-bottom
    if stored_name == target:
        return stored_hash  # Returns first (old) entry
```

**Fix:** Read from bottom to top to get the most recent entry:
```python
# AFTER (fixed)
lines = f.readlines()
for line in reversed(lines):  # Reads bottom-to-top
    if stored_name == target:
        return stored_hash  # Returns last (newest) entry
```

---

### Bug #20 — Missing Subprocess Timeout

**Location:** `jxl_photo_v2.py` — `_run_subprocess()` and `execute_workflow()`

**Problem:** Two `process.wait()` calls had no timeout, which could cause infinite hangs if the subprocess froze.

**Fix:** Added `timeout=3600` (1 hour) to both `process.wait()` calls:
- Line ~2347: `_run_subprocess()` method
- Line ~2531: `execute_workflow()` method

---

### Bug #21 — Bare Except Clauses

**Location:** All Python scripts

**Problem:** Multiple bare `except:` clauses throughout the codebase that catch all exceptions including `KeyboardInterrupt` and `SystemExit`, making it impossible to cancel operations with Ctrl+C.

**Files affected and fixes:**
- `jxl_photo.py`: 4 bare excepts → `except Exception:` or `except ValueError:`
- `jxl_photo_v2.py`: 6 bare excepts → `except Exception:` or `except ValueError:`
- `jxl_tiff_decoder.py`: 1 bare except → `except Exception:`
- `jxl_tiff_decoder_v2.py`: 1 bare except → `except Exception:`

**Total:** 12 bare except clauses fixed across 4 files.

---

### Bug #22 — Race Condition in JXL Deletion (Decoder)

**Location:** `jxl_tiff_decoder.py` — `process_group()`

**Problem:** Similar to bug #16, the decoder deleted source JXL files after checking if TIFF exists, but without verifying TIFF integrity. A corrupted or incomplete TIFF could pass the `exists()` check.

**Fix:** Added `_verify_tiff_integrity()` function that checks:
1. File exists and size > 0
2. Valid TIFF signature (II/MM + 42)
3. Can be opened by tifffile

---

### Bug #23 — Race Condition in Source Deletion (Transcoder)

**Location:** `jxl_jpeg_transcoder.py` — `cmd_transcode()` and `cmd_convert()`

**Problem:** The transcoder deleted source files without verifying output file integrity in convert mode. MD5 verification only worked for transcode mode, not convert mode.

**Fix:** Added `_verify_file_integrity()` function that validates file headers based on extension:
- JXL: 0xFF 0x0A or ISOBMFF container
- JPEG: 0xFFD8 (SOI marker)
- PNG: PNG signature
- TIFF: II/MM + 42

Applied to both transcode and convert deletion paths.

---

### Bug #24 — Python 3.9+ Compatibility (All Scripts)

**Location:** `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

**Problem:** These scripts used `Path.is_relative_to()` directly without backport, causing `AttributeError` on Python 3.8.

**Fix:** Added `_is_relative_to()` backport function to all active scripts:
- `jxl_tiff_encoder.py`
- `jxl_tiff_decoder.py`
- `jxl_jpeg_transcoder.py`

---

### Bug #25 — CJXL_DISTANCE Range Validation

**Location:** `jxl_tiff_encoder.py` — argument parsing

**Problem:** The `--distance` parameter accepted any float value without validating the valid range (0-15). Invalid values would be passed to cjxl which would fail with cryptic errors.

**Fix:** Added validation in argument parsing:
```python
if args.distance is not None:
    if not 0 <= args.distance <= 15:
        parser.error("--distance must be between 0 and 15")
    CJXL_DISTANCE = args.distance
```

---

### Bug #26 — ExifTool Timeout

**Location:** `jxl_tiff_encoder.py` and `jxl_jpeg_transcoder.py`

**Problem:** Multiple `subprocess.run()` calls to exiftool had no timeout. If exiftool hung (rare but possible with corrupted files), the process would wait indefinitely.

**Fix:** Added `timeout=60` to all exiftool subprocess calls:
- `jxl_tiff_encoder.py`: 8 calls
- `jxl_jpeg_transcoder.py`: 2 calls

---

## v1.3 Bug Fixes (Detailed)

### Bug #27 — Invalid `--distance 95` passed to JPEG transcoder

**Location:** `jxl_photo.py` lines 2291-2292 and 2452-2454

**Problem:** When transcoding JPEG→JXL in lossy mode, both `--quality` and `--distance` were passed with the same value (e.g., 95). Distance in cjxl ranges 0-15, so 95 is invalid and causes unpredictable behavior.

**Fix:** Removed `--distance` from lossy transcoding command. The transcoder handles quality internally.

---

### Bug #28 — Scripts resolved by relative path (CWD dependency)

**Location:** `jxl_photo.py` lines 2152-2159, 2377-2496

**Problem:** Scripts were referenced as `'jxl_tiff_encoder.py'` (relative to CWD). If user ran `python /path/jxl_photo.py` from a different directory, `Path(script).exists()` would fail.

**Fix:** Now uses `SCRIPT_DIR / script` to resolve absolute paths relative to the script location.

---

### Bug #29 — `_show_mode_details_and_select` returns string instead of bool

**Location:** `jxl_photo.py` line 1615

**Problem:** Function returned `choice` (string) instead of setting `workflow['mode']` and returning `True/False`. Caller in `_wizard_select_mode` (line 1437) expected a boolean. Non-empty string is truthy, so wizard advanced but `workflow['mode']` was never set → `KeyError` later.

**Fix:** Now sets `workflow['mode'] = int(choice)` and returns `True`.

---

### Bug #30 — Subprocess timeout not handled

**Location:** `jxl_photo.py` lines 2351, 2547

**Problem:** `process.wait(timeout=3600)` could raise `subprocess.TimeoutExpired`, but it wasn't caught by `except Exception`. The child process would continue running as a zombie.

**Fix:** Added explicit `except subprocess.TimeoutExpired:` handler that kills the process and reports the error.

---

### Bug #31 — Set unordered in checksum DB path

**Location:** `jxl_jpeg_transcoder.py` line 833

**Problem:** Code used `list({f for _, _, f in tasks})[0]` — a set comprehension with `[0]` indexing. Set order is undefined in Python, so the "first" element was arbitrary.

**Fix:** Changed to `tasks[0][2]` — directly accesses the first task's destination path. Since all tasks in a group share the same destination folder, this is deterministic and reliable.

---

### Bug #32 — Redundant `import Path` inside function

**Location:** `jxl_photo.py` line 2209

**Problem:** `from pathlib import Path` was imported inside `_execute_manifest_workflow()` function, even though `Path` was already imported at module level (line 18). Redundant and confusing.

**Fix:** Removed the redundant import. The function uses the module-level `Path`.

---

### Bug #33 — Rich markup leaking in non-Rich fallback

**Location:** `jxl_photo.py` lines 1431-1439, 1485-1487, 1608-1618

**Problem:** When Rich library is not available, fallback code printed strings containing Rich markup tags like `[green]`, `[cyan]`, `[bold]`, etc. These appeared literally in terminal output (e.g., `[green]converted_jxl[/green]`).

**Fix:** Enhanced all `.replace()` chains to clean all Rich markup tags (`[green]`, `[cyan]`, `[red]`, `[yellow]`, `[dim]`, `[bold]`, and closing tags) before printing. Also fixed order — compound tags like `[bold green]` are replaced before simple tags like `[bold`.

---

### Bug #34 — Type hint `str | None` (Python < 3.10 incompatible)

**Location:** `jxl_jpeg_transcoder.py` line 332

**Problem:** Function signature `def read_md5_db(jxl_path: Path) -> str | None:` uses the `|` union syntax which requires Python 3.10+. The project supports Python 3.8+.

**Fix:** Changed to `-> Optional[str]` and added `from typing import Optional` import. This syntax is compatible with Python 3.8+.

---

### Additional v1.3 Improvements

- **Type hint fixed:** `_show_mode_details_and_select` now correctly typed as `-> bool`
- **Script path resolution:** All script references now use absolute paths via `SCRIPT_DIR`
- **Error handling:** Timeout handling added in both `_run_subprocess` and `execute_workflow`

---
## v1.4 Bug Fixes (Detailed)

### Bug #36 — Quality/sRGB Asked for Lossless JXL→JPEG

**Location:** `jxl_photo.py` Step 6 (lines ~1793, ~1852)

**Problem:** The wizard was asking for Quality and sRGB conversion even when "JPEG Lossless Transcode" was selected. These settings are irrelevant for lossless transcoding.

**Fix:** Added check for `conversion_type == 'jxl_to_jpeg_force'` before asking quality/sRGB questions.

```python
# Only ask for lossy mode
if origin == 'jxl' and dest == 'jpeg' and workflow.get('conversion_type') == 'jxl_to_jpeg_force':
    # ask quality and sRGB
```

---

### Bug #37 — Repeat Last Workflow Not Saving Destination Format

**Location:** `jxl_photo.py` `save_last_session()`, Repeat workflow section

**Problem:** When using "Repeat last workflow", the system didn't remember if the last JXL→? conversion was to JPEG, PNG, or TIFF. It would always default to TIFF for JXL source.

**Fix:** 
1. Added `last_dest_format` and `last_conversion_type` fields to ToolConfig
2. Updated `save_last_session()` to save these fields
3. Updated Repeat workflow to use saved values

---

### Bug #38 — Step 2 TIFF Option Misnumbered

**Location:** `jxl_photo.py` Step 2 options

**Problem:** When JXL source was selected, the TIFF option had the same number [3] as PNG, causing confusion.

**Fix:** Renumbered options:
- [1] JPEG Lossless Transcode
- [2] JPEG Lossy Convert  
- [3] AUTO (in development) - shown as grayed out
- [4] PNG
- [5] TIFF

---

## v1.5 Features and Fixes

### Bug #35 — JXL→JPEG Auto Mode for Directories (FIXED)

**Location:** `jxl_jpeg_transcoder.py`, `jxl_photo.py`

**Problem:** The transcoder's auto-detect feature only worked for single files, not directories. Users had to choose between force-lossless (fails without jbrd) or force-lossy (always recompresses).

**Solution Implemented:**

**In `jxl_jpeg_transcoder.py`:**
- Added `cmd_auto()` function that implements per-file auto-detection for batch processing:
  1. Scans all JXL files in directory
  2. Checks each file for jbrd box
  3. Separates into two lists: with_jbrd (lossless) and without_jbrd (lossy)
  4. Processes each group with appropriate method

**In `jxl_photo.py`:**
- Replaced "In development" placeholder with working "JPEG Auto-Detect" option
- Moved AUTO to option [1] as the recommended choice
- Quality and sRGB settings apply to files that will be lossy-converted

**New Step 2 Options:**
- [1] JPEG Auto-Detect — Recommended (auto: lossless if jbrd, else lossy)
- [2] JPEG Lossless — Force lossless transcoding (requires jbrd)
- [3] JPEG Lossy — Force lossy conversion with quality/ICC control
- [4] PNG
- [5] TIFF

---


### Bug #39 — JXL→TIFF Preview Not Configurable (FIXED)

**Location:** `jxl_tiff_decoder.py`, `jxl_photo.py`

**Problem:** The TIFF decoder always added an embedded JPEG preview (hardcoded `ADD_JPEG_PREVIEW = True`). Users who wanted smaller TIFF files without preview had no option to disable it.

**Solution Implemented:**

**In `jxl_tiff_decoder.py`:**
- Added `--no-preview` CLI flag
- When passed, sets `ADD_JPEG_PREVIEW = False`
- Default behavior unchanged (creates preview for compatibility)

**In `jxl_photo.py`:**
- Added question in Step 6: "Add JPEG preview? (for faster viewing)"
- Default: Yes (maintains backward compatibility)
- Shows preview status in Step 7 Summary
- Passes `--no-preview` to decoder when user selects No

**Usage:**
```bash
# With preview (default)
python jxl_tiff_decoder.py folder/ --mode 1

# Without preview (smaller files)
python jxl_tiff_decoder.py folder/ --mode 1 --no-preview
```

---


### Bug #40 — TIFF Encoder Mode 3 Using Wrong Folder Constant (FIXED)

**Location:** `jxl_tiff_encoder.py` line 392

**Problem:** Mode 3 was using `CONVERTED_JXL_FOLDER` ("converted_jxl") instead of `JXL_FOLDER_NAME` ("JXL_16bits"). This caused Mode 3 and Mode 1 to output to the same folder, breaking the intended folder structure.

**Fix:** Changed line 392 from:
```python
return tiff_path.parent / CONVERTED_JXL_FOLDER / tiff_path.with_suffix(".jxl").name
```
to:
```python
return tiff_path.parent / JXL_FOLDER_NAME / tiff_path.with_suffix(".jxl").name
```

---

### Bug #41 — TIFF Encoder Mode 0 Searching JPEG Files (FIXED)

**Location:** `jxl_tiff_encoder.py` line 1119

**Problem:** `find_files_mode0()` was searching for both `*.jpg` and `*.tif` files. Since this is a TIFF→JXL encoder, attempting to open JPEG files with `tifffile.TiffFile()` would crash.

**Fix:** Removed JPEG extensions from the search pattern:
```python
# Before:
for ext in ("*.jpg", "*.jpeg", "*.tif", "*.tiff"):

# After:
for ext in ("*.tif", "*.tiff"):
```

---

### Bug #42 — Variable 'skipped' Overwritten in Encoder Summary (FIXED)

**Location:** `jxl_tiff_encoder.py` line 1350

**Problem:** The variable `skipped` (file skip counter) was being overwritten with `_d50_patch_count["skipped"]` after the summary was already printed. While this didn't affect output (print happened first), it was confusing and error-prone.

**Fix:** Renamed the D50 variable to `d50_skipped` to avoid shadowing:
```python
# Before:
skipped = _d50_patch_count["skipped"]

# After:
d50_skipped = _d50_patch_count["skipped"]
```

---

### Bug #43 — Manifest Missing Flags vs execute_workflow (FIXED)

**Location:** `jxl_photo.py` `_build_manifest_entry_cmd()`

**Problem:** The manifest workflow builder was missing several flags that existed in `execute_workflow()`:
- `--embed-thumbnail` (TIFF→JXL)
- `--delete-source` (TIFF→JXL)
- `--delete-source` (JXL→TIFF)
- `--no-preview` (JXL→TIFF)
- `--staging`
- `--encode-tag`
- `--d50-patch`

This caused inconsistent behavior between interactive and manifest modes.

**Fix:** Added all missing flags to `_build_manifest_entry_cmd()` matching the logic in `execute_workflow()`:
```python
if workflow.get('staging'):
    parts.append(f'--staging "{staging}"')
if advanced.get('encode_tag'):
    parts.append(f'--encode-tag {encode_tag}')
if advanced.get('d50_patch'):
    parts.append(f'--d50-patch {d50_patch}')
```

---

### Bug #44 — Duplicate --format Flag in Transcoder (FIXED)

**Location:** `jxl_photo.py` lines 2560-2592

**Problem:** When `conv_type == 'jxl_to_png'`, `--format png` was added at line 2562, then again at lines 2589-2592 based on `dest`. This resulted in duplicate `--format png --format png` arguments.

**Fix:** Removed the duplicate `--format` addition in the conversion type block, keeping only the final destination-based logic.

---

### Bug #45 — Thumbnail Fallback Using Undefined PIL (FIXED)

**Location:** `jxl_tiff_encoder.py` lines 985-1009

**Problem:** The thumbnail generation fallback code used `Image.fromarray()` inside an exception handler. If the PIL import had failed, `Image` would be undefined and the fallback would crash too.

**Fix:** Added check for PIL availability before the fallback:
```python
if 'Image' not in globals():
    logger.debug("  >PIL not available, skipping thumbnail fallback")
    return
```

## Claude Code Audit Fixes (v1.5)

### Bug #46 — D50 Summary Uses Wrong Variable (FIXED)

**Location:** `jxl_tiff_encoder.py` lines 1358, 1365, 1367

**Problem:** The D50 patch summary used `skipped` (file skip counter) instead of `d50_skipped` (D50 skip counter), showing incorrect statistics.

**Fix:** Changed all three occurrences to use `d50_skipped`:
```python
total_processed = applied + d50_skipped + skipped_needed  # was: skipped
logger.info(f"D50 patch: {applied} applied | {d50_skipped} skipped")  # was: skipped
```

---

### Bug #47 — Thumbnail Return Aborts Conversion (FIXED)

**Location:** `jxl_tiff_encoder.py` line 988

**Problem:** The thumbnail fallback used `return` which aborted the entire `convert_one()` function. Should use `pass` to skip thumbnail but continue with conversion.

**Fix:** Changed `return` to `pass`.

---

### Bug #48 — ICC Comments Misplaced (FIXED)

**Location:** `jxl_tiff_encoder.py` lines 166-171

**Problem:** Comments about ICC embedding were under `D50_PATCH_SOFTWARE_LIST` instead of under `EMBED_ICC_IN_JXL`.

**Fix:** Moved ICC-related comments to the correct variable block.

---

### Bug #49 — TIFF Docstring Order Wrong (FIXED)

**Location:** `jxl_tiff_decoder.py` line 7

**Problem:** Docstring said "JPEG as page 0, 16-bit as page 1" but actual code does the opposite (16-bit=page0, JPEG=page1).

**Fix:** Corrected docstring to match actual behavior:
```python
# Before:
"TIFF structure: JPEG as page 0 (thumbnail flag), 16-bit as page 1"

# After:
"TIFF structure: 16-bit as page 0 (primary), JPEG preview as page 1 (thumbnail flag)"
```

---

### Bug #50 — OVERWRITE == False Should Be 'is' (FIXED)

**Location:** `jxl_tiff_encoder.py` line 854, `jxl_tiff_decoder.py` line 1103

**Problem:** Used `== False` which could match `0` or other falsy values. Should use `is False` for explicit boolean comparison.

**Fix:** Changed both occurrences to `if OVERWRITE is False:`.

---

### Bug #51 — --effort CLI Ignored in Transcode (FIXED)

**Location:** `jxl_jpeg_transcoder.py` line 818

**Problem:** The `encode_one_transcode()` function used global `CJXL_EFFORT` instead of the CLI argument `args.effort`.

**Fix:** Changed to `args.effort` and added `effort` parameter to `process_group_transcode()`.

---

### Bug #52 — bit_depth Hardcoded in Auto Mode (FIXED)

**Location:** `jxl_jpeg_transcoder.py` line 1515

**Problem:** Auto mode used `bit_depth=8` hardcoded, ignoring `--bit-depth` CLI argument.

**Fix:** Changed to `bit_depth=args.bit_depth`.

---

### Bug #53 — ICC Ignored with --no-ram (FIXED)

**Location:** `jxl_jpeg_transcoder.py` lines 1152-1170

**Problem:** When `--no-ram` was used with `--icc-profile`, the ICC conversion was silently ignored for PNG output. JPEG path already had temp file fallback, but PNG didn't.

**Fix:** Added temp file fallback for PNG ICC conversion (same logic as JPEG):
```python
if use_ram:
    # RAM pipeline (djxl | magick)
else:
    # Temp file pipeline (djxl → temp.png → magick)
```

---

### Bug #54 — has_jbrd_box Naive Detection (FIXED)

**Location:** `jxl_jpeg_transcoder.py` lines 356-366

**Problem:** Used `b'jbrd' in header` which could match false positives if those bytes appeared in metadata.

**Fix:** Implemented proper ISOBMFF box parsing:
- Skip JXL signature
- Parse box size and type
- Only match exact `jbrd` box type
- Handle extended sizes correctly

---

### Bug #55 — jxl_to_jpeg_lossless Missing --decode (FIXED)

**Location:** `jxl_photo.py` lines 2562-2565

**Problem:** The wrapper didn't pass `--decode` for `jxl_to_jpeg_lossless`, causing the transcoder to search for JPEGs instead of JXLs when processing directories.

**Fix:** Added `cmd.append('--decode')` for this conversion type.

---

### Bug #56 — jxl_to_jpeg_force Missing --decode (FIXED)

**Location:** `jxl_photo.py` lines 2566-2569

**Problem:** Similar to #55, `jxl_to_jpeg_force` didn't pass `--decode`, causing direction confusion.

**Fix:** Added `cmd.append('--decode')` for this conversion type.

---

### Bug #57 — jxl_to_png Generates JPEG with jbrd (FIXED)

**Location:** `jxl_photo.py` (new elif block after jxl_to_jpeg_force)

**Problem:** When user selected PNG output for JXL files with jbrd, the auto mode would transcode to JPEG instead of converting to PNG, silently ignoring the user's format choice.

**Fix:** Added explicit handling for `jxl_to_png`:
```python
elif conv_type == 'jxl_to_png':
    # Force convert (don't transcode even if jbrd present)
    cmd.append('--force-convert')
    cmd.append('--decode')
```

---

### Bug #58 — Repeat Workflow Loses Thumbnail (FIXED)

**Location:** `jxl_photo.py` lines 2915-2920

**Problem:** When using "Repeat last workflow", the `embed_thumbnail` setting wasn't being restored from the saved session.

**Fix:** Added to `advanced_options`:
```python
'embed_thumbnail': config.config.last_jpeg_thumbnail if origin == 'tiff' else None
```

---

## Second Pass Fixes (v1.5 Final)

### Bug #59 — Log Shows Wrong Effort Value (FIXED)

**Location:** `jxl_jpeg_transcoder.py` line 932

**Problem:** The log message showed `CJXL_EFFORT` (global constant = 7) instead of `args.effort` (actual CLI value). When user passed `--effort 9`, the log showed "Effort: 7" but the encode actually used 9. Only the log was wrong.

**Fix:** Changed from:
```python
logger.info(f"{op_type} | Mode: {args.mode} | Effort: {CJXL_EFFORT} | ...")
```
to:
```python
logger.info(f"{op_type} | Mode: {args.mode} | Effort: {args.effort} | ...")
```

---

### Bug #60 — bit_depth=None in Auto Mode (FIXED)

**Location:** `jxl_jpeg_transcoder.py` line 1556

**Problem:** When using auto mode with `--format png` but without explicit `--bit-depth`, `args.bit_depth` was `None`. This could cause `djxl` to receive `--bits_per_sample=None` which is invalid.

**Fix:** Added fallback to default:
```python
bit_depth=args.bit_depth or PNG_DEFAULT_BIT_DEPTH,
```

Now defaults to 16-bit when not specified.

---
### Bug #61 — Thumbnail Fallback Double Except (FIXED, Second Pass)

**Location:** `jxl_tiff_encoder.py` lines 982-1045

**Problem:** The thumbnail fallback code had three issues:
1. Double `except` blocks (second one unreachable in Python)
2. Fallback executed even when PIL not available (NameError crash)
3. No separate try/except for fallback path

**Fix:** Restructured to:
1. Single outer except for PIL approach failure
2. Check PIL availability before attempting fallback
3. Wrap fallback in its own try/except
4. Continue conversion gracefully if both approaches fail

```python
except Exception as e:
    logger.debug(f"Thumbnail PIL approach failed: {e}")
    if 'Image' not in locals():
        logger.debug("PIL not available, skipping thumbnail entirely")
    else:
        try:
            # tifffile fallback here...
        except Exception as e2:
            logger.debug(f"Thumbnail fallback also failed: {e2}")
```


---

### Bug #62 — Extended Size Box Not Appended (CRITICAL, Second Pass)

**Location:** `jxl_tiff_encoder.py` and `jxl_jpeg_transcoder.py` — `reorder_jxl_boxes()`

**Related to:** Bug #6 (Integer Overflow) - different but related issue

**Problem:** When a JXL box has `size == 1`, it indicates an extended 64-bit size follows. The code correctly calculated the extended size and extracted header/payload, but **never appended the box to the list**:

```python
# BEFORE (bug):
if size == 1:
    ext_size = int.from_bytes(data[i+8:i+16], "big")
    header, payload = data[i:i+16], data[i+16:i+ext_size]
    size = ext_size
    # ← FALTA: boxes.append((name, header, payload))
```

**Impact:** Any JXL file with boxes larger than 4GB (requiring extended size) would have those boxes **silently dropped**, corrupting the output file.

**Fix:** Added the missing `boxes.append()`:
```python
# AFTER (fixed):
if size == 1:
    ext_size = int.from_bytes(data[i+8:i+16], "big")
    header, payload = data[i:i+16], data[i+16:i+ext_size]
    size = ext_size
    boxes.append((name, header, payload))  # ← ADICIONADO
```

**Note:** This is different from Bug #6 which added size validation. Bug #6 prevented crashes from invalid sizes; Bug #62 fixes the missing append for valid extended sizes.

---

### Bug #63 — EXIF Binary Corrupted by text=True (CRITICAL, Second Pass)

**Location:** `jxl_jpeg_transcoder.py` — `inject_exif_to_jxl_from_jpeg()`

**Problem:** The function used `text=True` when extracting EXIF binary data from JPEG:

```python
# BEFORE (bug):
r = subprocess.run([...], capture_output=True, text=True, encoding="utf-8", ...)
if r.returncode == 0 or len(r.stdout) > 8:
    exif_bin.write_bytes(r.stdout)  # r.stdout is str, not bytes!
```

Two issues:
1. `text=True` with `encoding="utf-8"` and `errors="replace"` **corrupts binary EXIF data**
2. `write_bytes()` expects `bytes`, receives `str` → TypeError or data corruption

**Fix:** Removed `text=True` to keep output as bytes:
```python
# AFTER (fixed):
r = subprocess.run([...], capture_output=True, timeout=60)  # r.stdout is bytes
if r.returncode == 0 or len(r.stdout) > 8:
    exif_bin.write_bytes(r.stdout)  # bytes -> bytes ✓
```

---

### Bug #64 — Mode 1 Directory Behavior Wrong (MEDIUM, Second Pass)

**Location:** `jxl_tiff_encoder.py` — `main()` lines 1291-1296

**Problem:** According to documentation, Mode 1 should create `converted_jxl/` subfolder for **both** files and directories. But the code only did this for single files:

```python
# BEFORE (bug):
elif args.mode in (1, 2):
    if args.input.is_file():
        jxl = t.parent / CONVERTED_JXL_FOLDER / t.with_suffix(".jxl").name
    else:
        jxl = output_root / t.with_suffix(".jxl").name  # ← Flat, wrong!
```

**Fix:** Separated Mode 1 and Mode 2 logic:
```python
# AFTER (fixed):
elif args.mode == 1:
    # Mode 1: Create converted_jxl/ subfolder (file or directory)
    jxl = t.parent / CONVERTED_JXL_FOLDER / t.with_suffix(".jxl").name
elif args.mode == 2:
    if args.input.is_file():
        jxl = t.parent / CONVERTED_JXL_FOLDER / t.with_suffix(".jxl").name
    else:
        jxl = output_root / t.with_suffix(".jxl").name  # Flat for Mode 2 directory
```

**Documentation updated:** Changed Mode 1 description from "Single file" to "File or directory" in README.

---
### Bug #65 — extract_exif_raw r.stdout None Check (MEDIUM, Second Pass)

**Location:** `jxl_tiff_encoder.py` — `extract_exif_raw()`

**Problem:** If subprocess times out, `r.stdout` could be `None`, causing `len(None)` to crash:

```python
# BEFORE (bug):
if r.returncode == 0 and len(r.stdout) > 8:  # Crash if r.stdout is None
```

**Fix:** Added null check:
```python
# AFTER (fixed):
if r.returncode == 0 and r.stdout and len(r.stdout) > 8:
```

---

### Bug #66 — _d50_patch_count["skipped"] Never Incremented (MINOR, Second Pass)

**Location:** `jxl_tiff_encoder.py` — D50 patch tracking

**Problem:** The counter `skipped` in `_d50_patch_count` is initialized to 0 and read in the summary, but **never incremented** by any code path. The value is always 0.

This appears to be dead code — the actual tracking uses `skipped_needed` and `already_correct` instead.

**Status:** Code works correctly (always shows 0 skipped), but the counter is unnecessary. Could be removed in future cleanup.
## Third Pass Fixes (v1.5 Final Polish)

### Bug #67 — --no-ram Never Works (encoder) [REAL]

**Location:** `jxl_tiff_encoder.py` lines 1220-1223

**Problem:** `args.ram` is `store_true` with `default=True`, so it's never `None`. The check `if args.ram is not None` is always True, preventing the `elif args.no_ram` branch from executing.

```python
# BEFORE (bug):
if args.ram is not None:  # Always True (default=True)
    USE_RAM_FOR_PNG = args.ram
elif args.no_ram is not None:  # Never reached
    USE_RAM_FOR_PNG = not args.no_ram
```

**Fix:** Check `--no-ram` first:
```python
# AFTER (fixed):
if args.no_ram:
    USE_RAM_FOR_PNG = False
elif args.ram is not None:
    USE_RAM_FOR_PNG = args.ram
```

---

### Bug #68 — strip_metadata Deletes Description (encoder) [REAL]

**Location:** `jxl_tiff_encoder.py` — `build_metadata_injection_args()`

**Problem:** When `strip_metadata=True`, the order of exiftool arguments was wrong:
1. Set `-xmp-dc:Description=...`
2. Then `-xmp:all=` (deletes ALL XMP, including the Description just set!)

Exiftool processes arguments sequentially, so the Description was set then immediately deleted.

**Fix:** Reorder: strip first, then set Description:
```python
# AFTER (fixed):
args_lines.append("-exif:all=")  # Strip EXIF first
args_lines.append("-xmp:all=")   # Strip XMP second
args_lines.append(f"-xmp-dc:Description={encoding_desc}")  # Set Description last
```

---

### Bug #69 — PNG 8-bit Shift Results in Zeros (decoder) [POTENTIAL]

**Location:** `jxl_tiff_decoder.py` — multiple locations

**Problem:** When `DJXL_OUTPUT_DEPTH == 8`, the code does `pixels >> 8` to convert 16-bit to 8-bit. But if the input is already 8-bit (uint8), shifting right by 8 results in all zeros.

```python
# BEFORE (bug):
if DJXL_OUTPUT_DEPTH == 8:
    pixels = (pixels >> 8).astype(np.uint8)  # If pixels is uint8, result is 0!
```

**Fix:** Check dtype before shifting:
```python
# AFTER (fixed):
if DJXL_OUTPUT_DEPTH == 8 and pixels.dtype == np.uint16:
    pixels = (pixels >> 8).astype(np.uint8)
```

**Note:** This is only a potential issue if djxl generates 8-bit PNGs in Basic mode. If djxl always generates 16-bit, this is theoretical.

---

### Bug #70 — cleanup_xmp_icc Duplicates Label (decoder) [REAL]

**Location:** `jxl_tiff_decoder.py` — `cleanup_xmp_icc()`

**Problem:** Exiftool returns output with labels like `"CreatorTool : Capture One Windows | ICC:ABC..."`. The regex removes `ICC:...` but keeps the label. When writing back, it becomes `"CreatorTool : CreatorTool : Capture One Windows"`.

```python
# BEFORE (bug):
r = subprocess.run([..., "-XMP-xmp:CreatorTool", ...], ...)  # Output: "CreatorTool : value"
content = r.stdout.strip()  # "CreatorTool : Capture One | ICC:ABC"
clean = re.sub(r'ICC:[A-Za-z0-9+/=]+', '', content)  # "CreatorTool : Capture One | "
# Written back: "CreatorTool : CreatorTool : Capture One | "
```

**Fix:** Use `-s -s -s` for raw output without labels:
```python
# AFTER (fixed):
r = subprocess.run([..., "-s", "-s", "-s", "-XMP-xmp:CreatorTool", ...], ...)  # Output: "value"
```

---

### Bug #71 — --format jpg Falls Through to PNG Path (transcoder) [REAL]

**Location:** `jxl_jpeg_transcoder.py` — parser and usage

**Problem:** The argparse accepts `"jpeg"`, `"jpg"`, and `"png"` as choices. But the code only checks for `"jpeg"`, so `"jpg"` falls through to the PNG path:

```python
# BEFORE (bug):
if args.format == "jpeg":  # jpg doesn't match!
    # JPEG path
else:
    # PNG path (jpg ends up here!)
```

**Fix:** Normalize format after parsing:
```python
# AFTER (fixed):
if args.format == "jpg":
    args.format = "jpeg"
```

---

## Additional Fixes (Post v1.5.2)

### Bug #88 — OVERWRITE Log Reports "no" When Default Is "smart"

**Location:** `jxl_tiff_encoder.py` and `jxl_tiff_decoder.py`

**Problem:** Both scripts set `OVERWRITE = "smart"` as the default behavior, but the startup log only checked CLI flags (`args.sync` and `args.overwrite`) when building the log string. If the user ran the script without any overwrite-related flags, the log would show `Overwrite: no` even though the actual behavior was smart sync (comparing file timestamps to skip up-to-date files). This was misleading and could cause confusion about why files were being skipped.

**Fix:** Updated the log string to also check the global `OVERWRITE` variable:
```python
_overwrite_str = "sync" if args.sync else (
    "yes" if args.overwrite else (
        "smart" if OVERWRITE == "smart" else "no"
    )
)
```

---

### Bug #89 — PIL_MAX_IMAGE_PIXELS Config Ignored in Decoder

**Location:** `jxl_tiff_decoder.py`

**Problem:** The script hardcoded `Image.MAX_IMAGE_PIXELS = None` immediately after importing PIL (line 29), but also provided a user-configurable variable `PIL_MAX_IMAGE_PIXELS = None` later in the settings block (line 145). If a user changed the value in the settings block, it had no effect because the hardcoded assignment was never updated.

**Fix:** Added `Image.MAX_IMAGE_PIXELS = PIL_MAX_IMAGE_PIXELS` after the user setting, matching the pattern already used in `jxl_tiff_encoder.py`.

---

### Bug #90 — 8-bit PNG Not Scaled to 16-bit in Decoder

**Location:** `jxl_tiff_decoder.py` — `read_png_to_numpy()`

**Problem:** When Basic decode mode extracted a PNG from `djxl`, the function returned 8-bit pixel data as `np.uint8` without scaling to 16-bit. If `DJXL_OUTPUT_DEPTH` was 16 (the default), the resulting TIFF would contain values 0-255 in a 16-bit container — producing a very dark image, similar to the critical Bug #87 in the encoder.

**Fix:** Updated `read_png_to_numpy()` to accept a `target_depth` parameter. When `target_depth == 16` and the array dtype is `uint8`, it now applies the same `* 257` scaling used elsewhere in the codebase:
```python
if target_depth == 16 and rgb.dtype == np.uint8:
    rgb = rgb.astype(np.uint16) * 257  # 0-255 → 0-65535
```
The Basic mode call was also updated to pass `DJXL_OUTPUT_DEPTH` explicitly.

---

## Final Summary (All Bugs Fixed)

**Total bugs fixed: 78**
- 45 bugs from v1.0-v1.4
- 13 bugs from Claude Code audit (1st pass)
- 3 bugs from first pass additions
- 5 bugs from second pass (critical)
- 5 bugs from third pass (polish)
- 15 bugs from v1.5.1 final pass
- 3 bugs from post-v1.5.2 audit

**Critical bugs (would cause data loss/corruption):**
- #6, #62: Integer overflow / Extended size
- #4, #17: Deadlock in pipeline
- #16, #22, #23: Race conditions in deletion
- #63: EXIF binary corruption
- #68: strip_metadata deletes Description
- #70: cleanup_xmp_icc duplicates label
- #75: MD5 checksums with UUID (staging)
- #84: resolve_output_convert parameters swapped
- #87, #90: 8-bit scaling (TIFF encoder and PNG decoder)

**All 78 bugs documented, fixed, and tested.**


---

## Scripts Affected

- `jxl_jpeg_transcoder.py` — Bug fixes #1, #2, #3, #4, #6, #8, #11, #17, #19, #21, #23, #24, #26
- `jxl_tiff_encoder.py` — Bug fixes #1, #5, #6, #7, #12, #16, #21, #24, #25, #26, #88
- `jxl_tiff_decoder.py` — Bug fixes #1, #5, #6, #13, #21, #22, #24, #69, #73, #74, #81, #88, #89, #90, Improvement #14 (merged v2 features)
- `jxl_photo.py` — Bug fixes #2, #9, #10, #21
- `jxl_photo_v2.py` — Bug fixes #15, #18, #20, #21

**Deprecated (reference only):**
- `deprecated/jxl_tiff_decoder_v1_old.py` — Original v1 decoder
- `deprecated/jxl_tiff_decoder_old.py` — Previous version
- `deprecated/jxl_to_jpg_png.py` — Legacy script

---

## New Features Added

### D50 Illuminant Patch (TIFF→JXL)
- Configurable via `--d50-patch` CLI flag (on/off/auto)
- `D50_PATCH_MODE` setting in encoder (default: "auto")
- Auto-detects Capture One exports via EXIF Software field
- Fixes ICC rounding errors that cause cjxl warnings
- Statistics shown in conversion summary (applied/skipped count)

### Lossy JPEG→JXL Conversion
- Now works correctly with cjxl 0.11.2
- Added `--lossless_jpeg=0` when distance>0

### Improved Basic Decode Mode (v1.2)
- Now preserves ICC profile generated by djxl
- Uses PNG intermediate to capture ICC data
- Renamed old behavior to "None" mode (`--none` flag)

### JXL Integrity Verification (v1.3)
- Added `_verify_jxl_integrity()` function to encoder
- Validates JXL header before deleting source TIFFs
- Prevents data loss from corrupted conversions

### Safe Pipeline Execution (v1.3)
- Added `_run_pipeline_safe()` helper for subprocess pipelines
- Prevents deadlock when piping djxl to ImageMagick
- Proper timeout handling and cleanup

### Python 3.8 Compatibility (v1.3)
- Backported `Path.is_relative_to()` for Python 3.8+
- Toolkit now works on older Python versions
- Tested with Unicode paths

### File Integrity Verification (v1.3)
- Added `_verify_jxl_integrity()` to encoder
- Added `_verify_tiff_integrity()` to decoder  
- Added `_verify_file_integrity()` to transcoder
- Prevents data loss from corrupted conversions

### Robustness Improvements (v1.3)
- All subprocess calls now have timeouts
- ExifTool operations timeout after 60 seconds
- CJXL_DISTANCE validated (0-15 range)
- All bare except clauses fixed

---

## v1.5.2 Critical Bug Fix

### Bug #87 — 8-bit TIFF to JXL Produces Black Images (CRITICAL)

**Location:** `jxl_tiff_encoder.py` - `convert_one()` and `make_png_bytes()`

**Problem:** When converting 8-bit TIFF files to JXL, the resulting images were completely black and extremely small (~25 KB instead of ~25 MB). This affected users converting TIFFs from:
- NX Studio (Nikon)
- GIMP (8-bit export)
- Adobe Lightroom (8-bit export)
- Any software generating 8-bit TIFFs

**Root Cause:** The 8→16 bit conversion did not scale pixel values correctly:
```python
# BROKEN (v1.5.1 and earlier)
img = tif.series[0].asarray().astype(np.uint16)
# uint8 255 → uint16 255 (0.39% brightness in 0-65535 range)
```

When an 8-bit value (0-255) is cast to 16-bit without scaling:
- White (255) becomes 255 in a 0-65535 range = effectively black
- The JXL compressor efficiently encodes these near-zero values
- Result: 25 KB "black" image instead of 25 MB proper image

**Fix:** Applied proper scaling (multiply by 257 = 65535/255):
```python
# FIXED (v1.5.2)
img = tif.series[0].asarray()
if img.dtype == np.uint8:
    img = img.astype(np.uint16) * 257  # 0-255 → 0-65535
else:
    img = img.astype(np.uint16)
```

Same fix applied to `make_png_bytes()` as a fallback:
```python
if img.dtype == np.uint8:
    img_16 = img.astype(np.uint16) * 257
    img_be = img_16.astype(">u2")
```

**Impact:** This was a **critical data corruption bug** affecting all 8-bit TIFF conversions. Users could unknowingly convert their images to black JXLs. The fix ensures proper brightness preservation regardless of source bit depth.

**Reported by:** WiseTomCat (NX Studio 8-bit LZW TIFFs)

**Tested with:**
- 8-bit GIMP TIFF exports (50 MB → 26 MB JXL)
- 8-bit BigTIFF files
- 8-bit LZW compressed TIFFs
- 8-bit with ICC profiles
- EXIF preservation verified on all conversions

**Files changed:**
- `jxl_tiff_encoder.py` lines 752-765, 898-903

---

### Bug #91 — `cmd_auto` Routes JPEG Folders to Decode Instead of Encode

**Location:** `jxl_jpeg_transcoder.py` — `_process_file_group()`

**Problem:** When `cmd_auto` received a folder containing only JPEG files, it classified them correctly as "lossless encode", but then called `process_group_transcode(..., decode=True)`. That routed every JPEG through `decode_one_transcode()`, which runs `djxl` and produces the error `can't decode to the file extension '.jxl'`. The encode path (`cjxl --lossless_jpeg=1`) was never reached.

**Fix:** Split each transcode group into encode pairs (JPEG inputs) and decode pairs (JXL inputs) and call `process_group_transcode()` once per direction:

```python
encode_pairs = [...]  # .jpg/.jpeg inputs -> cjxl
process_group_transcode(encode_pairs, ..., decode=False)

decode_pairs = [...]   # .jxl inputs -> djxl
process_group_transcode(decode_pairs, ..., decode=True)
```

**Files changed:**
- `jxl_jpeg_transcoder.py` `_process_file_group()`

---

### Bug #92 — Staging Moves Failed Outputs to Final Destination

**Location:** `process_group()` in `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

**Problem:** After worker threads finished, the staging move loop only checked that the result tuple was non-`None`. A failed conversion still returned a result tuple (e.g. `(path, "error", ...)`), so the partial or missing staging file was moved to the output directory. For example, a corrupt `.jxl` could leave a zero-byte TIFF next to valid outputs.

**Fix:** Move the staging file to the final path only when the status string is in the success set (`"ok"`, `"reconvert"`, `"overwritten"`, `"skipped"`). On failure, the staging file is discarded and the final path is left untouched.

**Files changed:**
- `jxl_tiff_encoder.py` `process_group()`
- `jxl_tiff_decoder.py` `process_group()`
- `jxl_jpeg_transcoder.py` `process_group_transcode()`

---

### Bug #93 — `cmd_auto --delete-source` Deletes Without Confirmation

**Location:** `cmd_auto()` in `jxl_jpeg_transcoder.py` and `jxl_jpeg_transcoder_HDR.py`

**Problem:** The `cmd_auto` entry point checked `DELETE_SOURCE` and called `delete_source_files()` directly without any confirmation dialog. This made an irreversible, recursive source deletion a single flag away from running.

**Fix:** Added explicit confirmation before processing:
- Lossless JPEG↔JXL transcode uses `confirm_deletion_jpeg()` (type `yes`).
- Lossy JXL→JPEG convert uses `confirm_deletion_lossy()` (type current HHMM).

If the user does not confirm, the command exits cleanly before touching any file.

**Files changed:**
- `jxl_jpeg_transcoder.py` `cmd_auto()`
- `jxl_jpeg_transcoder_HDR.py` `cmd_auto()`

---

### Bug #94 — Basic Mode PNG Reader Downgrades 16-bit to 8-bit

**Location:** `jxl_tiff_decoder.py` — `read_png_to_numpy()`

**Problem:** The fallback path used `imagecodecs.png_decode(str(png_path))`, which returned a downgraded 8-bit array (only 256 unique values) for 16-bit PNGs. This broke the promise of lossless 16-bit roundtrip in Basic mode.

**Fix:** Use the bytes-based API and keep a PIL fallback:

```python
try:
    import imagecodecs
    return imagecodecs.png_decode(Path(png_path).read_bytes())
except Exception:
    pass

# PIL fallback with full 16-bit support
from PIL import Image
...
```

`imagecodecs.png_decode(Path.read_bytes())` preserves all 65,536 levels, making Basic mode pixel-perfect.

**Verification:**
- `np.array_equal(orig, basic)` now returns `True` for 16-bit ProPhoto TIFFs.

**Files changed:**
- `jxl_tiff_decoder.py` `read_png_to_numpy()`

---

### Bug #95 — Matrix Mode `--color_space` Token Incompatible with libjxl v0.11.x

**Location:** `jxl_tiff_decoder.py` — Matrix mode conversion

**Problem:** Matrix mode used `--color_space=RGB_D65_2020_Per_Lin`, but libjxl v0.11.x expects the Rec.2020 primaries token to be written as `202`, not `2020`. This caused `Failed to set color space` errors during Matrix decode.

**Fix:** Changed the Matrix mode argument to the token recognized by libjxl v0.11.x:

```bash
djxl input.jxl temp.png --color_space=RGB_D65_202_Per_Lin
```

This is confirmed compatible with `djxl v0.11.2 332feb1` and preserves Rec.2020 primaries.

**Files changed:**
- `jxl_tiff_decoder.py` Matrix mode conversion path
- `docs/README_jxl_tiff_decoder.md`

---

### Bug #96 — `--export-marker` Case Mismatch and Missing Propagation

**Location:** `jxl_photo.py`, `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

**Problem:** The wrapper exposed an `--export-marker` setting, but it was not passed to any of the worker scripts. In addition, marker matching used substring checks that were case-sensitive (`if marker in name`), so "ProPhoto-g22" did not match "prophoto".

**Fix:**
1. Added `--export-marker` CLI argument to encoder, decoder, and transcoder.
2. Propagated the configured marker from `jxl_photo.py` to each script.
3. Made all marker comparisons case-insensitive across the pipeline.

**Files changed:**
- `jxl_tiff_encoder.py`
- `jxl_tiff_decoder.py`
- `jxl_jpeg_transcoder.py`
- `jxl_photo.py`

---

### Bug #97 — Mode 2 Output Folder Handling Inconsistent

**Location:** `jxl_photo.py` and all worker scripts

**Problem:** In mode 2 (save to parent of input), the wrapper sometimes passed the parent path while the worker scripts expected a folder name or the parent itself. The result was files scattered or written to the wrong location.

**Fix:** Standardized mode 2 handling: the wrapper resolves `output_dir` to the parent of the input directory and passes it explicitly. Each worker script respects the provided `--output` path, creating it if necessary.

**Files changed:**
- `jxl_tiff_encoder.py`
- `jxl_tiff_decoder.py`
- `jxl_jpeg_transcoder.py`
- `jxl_photo.py`

---

### Bug #98 — Manifest Modes 6/7 Reset to 0/2 by Wrapper

**Location:** `jxl_photo.py` — `detect_mode_for_entry()`, `_generate_manifest()`, `_wizard_run_from_manifest()`

**Problem:** When a manifest entry used modes 6 or 7, the wrapper mapped them to 0 or 2 before launching the worker. The original workflow intention (specific output naming modes) was lost. The root cause was that the manifest CSV only stored `Source` and `Destination`, so by the time `detect_mode_for_entry()` ran, the generation mode had been overwritten by the special manifest execution mode (99).

**Fix (v1.5.3b):** Changed the manifest CSV schema to include a third `Mode` column:

```csv
Source,Destination,Mode
F:\2025\Tokyo\_Export\TIFF,F:\2025\Tokyo\_Export\TIFF,6
```

- `generate_manifest()` now writes `(source, destination, count, mode)`.
- `_generate_manifest()` writes the `Mode` column.
- `_wizard_run_from_manifest()` reads the mode and passes it as `original_mode` to `detect_mode_for_entry()`.
- `_view_manifest()` displays the mode column.

**Compatibility note:** Manifests are guaranteed to work with the version that generated them. Older 2-column manifests are not supported after this change.

**Files changed:**
- `jxl_photo.py` `detect_mode_for_entry()`
- `jxl_photo.py` `_generate_manifest()`
- `jxl_photo.py` `_wizard_run_from_manifest()`
- `jxl_photo.py` `_view_manifest()`
- `README.md` manifest example

---

### Bug #99 — `logger` Undefined in Wrapper Aborts Workflow

**Location:** `jxl_photo.py`

**Problem:** A code path in `jxl_photo.py` referenced `logger` before the logger was initialized, raising a `NameError` and aborting the workflow.

**Fix:** Ensured the logger is initialized before any code path can log, or guarded the call so the undefined variable cannot be reached.

**Files changed:**
- `jxl_photo.py`

---

### Bug #100 — Extended Size Box Validation Rejects Valid JXL Containers

**Location:** `reorder_jxl_boxes()` in `jxl_tiff_encoder.py` and `jxl_jpeg_transcoder.py`

**Problem:** The JXL box parser validated extended-size boxes with `extended_size < len(payload)`, but the correct check is `extended_size < len(payload) + 8` because the extended size field itself replaces the 8-byte size header. This caused valid containers with large boxes to fail reordering/injection.

**Fix (v1.5.3):** Corrected the interior comparison:

```python
if extended_size < len(payload) + 8:
    raise ValueError("Extended size too small")
```

However, the guard `if size < 8 and size != 0:` still rejected `size == 1` *before* the extended-size branch, so that branch remained unreachable. The guard was therefore wrong.

**Fix (v1.5.3a):** Changed the guard to allow `size == 1` to reach the extended-size branch:

```python
if 1 < size < 8:
    raise RuntimeError(f"Invalid JXL box size {size} at offset {i}, minimum is 8")
```

**Files changed:**
- `jxl_tiff_encoder.py` `reorder_jxl_boxes()`
- `jxl_jpeg_transcoder.py` `reorder_jxl_boxes()`

---

### Bug #101 — `--jpeg_quality` Ignored in Direct JXL→JPEG Path

**Location:** `jxl_jpeg_transcoder.py`

**Problem:** When decoding JXL directly to JPEG in the non-jbrd/lossy path, the configured `--jpeg_quality` value was not passed to `djxl`, so the output used the decoder's default quality.

**Fix:** Added `quality` parameter to the `djxl` call for JPEG output:

```bash
djxl input.jxl output.jpg --jpeg_quality=95
```

**Files changed:**
- `jxl_jpeg_transcoder.py`

---

### Bug #102 — Orphaned Alternate-Extension Staging File Left on Failure

**Location:** `jxl_jpeg_transcoder.py`

**Problem:** During decode, the code renamed the staging file from `.png`/`.jpg` to the final extension. If the conversion failed after the rename, the leftover file stayed in the staging directory.

**Fix:** Track the renamed staging path and remove it on error paths, keeping the staging directory clean on failure.

**Files changed:**
- `jxl_jpeg_transcoder.py`

---

### Bug #103 — `make_png_bytes` Crashes on Float/CMYK/Unsupported Shapes

**Location:** `jxl_tiff_encoder.py`

**Problem:** `make_png_bytes()` assumed `uint8`/`uint16` RGB(A) input and used `shape[2]` without checking. Float arrays or CMYK images caused an `IndexError` or produced invalid PNG bytes.

**Fix:** Added guards at the start of the function to reject unsupported dtypes, channel counts, and color spaces with a clear error message instead of crashing.

**Files changed:**
- `jxl_tiff_encoder.py` `make_png_bytes()`

---

### Bug #104 — D50 Patch Statistics Printed Wrong Count

**Location:** `jxl_tiff_encoder.py`

**Problem:** The encoder's summary reported D50-patched files using an incorrect variable, so the count did not match the number of profiles actually patched.

**Fix:** Updated the summary print statement to use the correct counter variable.

**Files changed:**
- `jxl_tiff_encoder.py`

---

### Bug #105 — Encoder Staging `status_map` Mismatches Under Concurrency

**Location:** `jxl_tiff_encoder.py` — `process_group()`

**Problem:** `results` is filled from `as_completed()` (completion order), but the staging move loop built `status_map` by zipping it positionally against `tasks` (submission order):

```python
status_map = {str(t): r[1] for t, r in zip([task[0] for task in tasks], results)}
```

With `workers > 1`, statuses crossed over. A failing file could be moved to the destination while a successful file was withheld in staging.

**Fix:** Use the source-path key that each worker result already carries, matching the decoder/transcoder:

```python
status_map = {r[0]: r[1] for r in results}
```

**Files changed:**
- `jxl_tiff_encoder.py` `process_group()`

---

### Bug #106 — Basic Mode Grayscale 8-bit PNG Not Scaled to 16-bit

**Location:** `jxl_tiff_decoder.py` — `read_png_to_numpy()`

**Problem:** The new `imagecodecs` path scaled RGB 8-bit up by `×257`, but the grayscale branch stacked the raw 8-bit plane into RGB and returned it unchanged. The resulting "16-bit" TIFF had max value 255 and looked nearly black.

**Fix:** Apply the same `×257` scaling in the grayscale branch before stacking to RGB.

**Files changed:**
- `jxl_tiff_decoder.py` `read_png_to_numpy()`

---

### Bug #107 — `--format jpeg` in Auto Mode Forces PNG / Double-Dot Filename

**Location:** `jxl_jpeg_transcoder.py` — `_process_file_group()`

**Problem (Part 1):** `_process_file_group` passed `out_ext = ".png" / ".jpg"` (with leading dot) to `resolve_output_convert()`, which builds names as `f"{stem}.{ext}"`. Result: `name..png` / `name..jpg`.

**Problem (Part 2):** The auto convert path always defaulted to `PNG_DEFAULT_BIT_DEPTH` (16), even for JPEG. `djxl` refuses 16-bit JPEG output, so the code fell back to PNG.

**Fix:**
1. Pass extension without dot: `out_ext = "jpg" if args.format == "jpeg" else "png"`.
2. Mirror `cmd_convert` bit-depth defaulting: `8` for JPEG, `PNG_DEFAULT_BIT_DEPTH` otherwise.

**Files changed:**
- `jxl_jpeg_transcoder.py` `_process_file_group()`

---

### Bug #108 — Skipped Files Spam "KEEP in Staging" Warning

**Location:** staging move loops in `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

**Problem:** Files with status `"skipped"` never wrote anything to staging, but the new staging safety loops warned `KEEP in staging (skipped)` for every one of them. On a re-run of a large synced archive this produced thousands of spurious warnings.

**Fix:** Only emit the staging warning for non-skipped non-success statuses.

**Files changed:**
- `jxl_tiff_encoder.py`
- `jxl_tiff_decoder.py`
- `jxl_jpeg_transcoder.py`

---

### Bug #109 — Wizard Offers Non-Functional "Skip" Existing-File Option

**Location:** `jxl_photo.py`

**Problem:** The wizard presented `[0] skip` for existing-file handling, but there was no true "skip" implementation — the choice silently behaved the same as sync or produced no-op behavior. This was confusing and inconsistent with the CLI, which only supports `--overwrite` and `--sync`.

**Fix:** Removed the "skip" option from both the Rich and fallback prompts. Existing-file handling is now `overwrite` or `sync` only, matching the scripts.

**Files changed:**
- `jxl_photo.py`

---

### Bug #110 — Adobe RGB / ProPhoto RGB ICC Aliases Fail

**Location:** `jxl_tiff_decoder.py` and `jxl_photo.py`

**Problem:** `load_target_icc()` advertised built-in aliases for `Adobe RGB` and `ProPhoto RGB`, but `ImageCms.createProfile()` from Pillow only supports `sRGB`, `Lab`, and `XYZ`. Choosing either alias always raised `PyCMSError`.

**Fix:** Removed the non-functional aliases. Only `sRGB` is supported as a built-in alias; other ICC profiles must be supplied as file paths. Updated prompts and help text accordingly.

**Files changed:**
- `jxl_tiff_decoder.py` `load_target_icc()`
- `jxl_tiff_decoder.py` `--target-icc` help text
- `jxl_photo.py` target-ICC prompts

---

### Bug #111 — Wrapper Detects Marker as Substring While Scripts Use Prefix/Suffix

**Location:** `jxl_photo.py` — `FolderAnalyzer.analyze()` and `detect_mode_for_entry()`

**Problem:** The wrapper used `re.search()` to detect the export marker anywhere in a folder name (substring match). The worker scripts (`jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`) only match folders that **start or end** with the marker. This caused a mismatch: a folder like `MY_EXPORT_OLD` was detected by the wizard and added to the manifest, but the scripts ignored it and processed 0 files.

**Fix:** Changed the wrapper to use the same prefix/suffix matching as the scripts:

```python
name_lower = folder.name.lower()
if name_lower.startswith(marker_lower) or name_lower.endswith(marker_lower):
    ...
```

`detect_mode_for_entry()` was updated the same way when checking whether a source path contains the marker.

**Files changed:**
- `jxl_photo.py` `FolderAnalyzer.analyze()`
- `jxl_photo.py` `detect_mode_for_entry()`

---

### Bug #112 — CMYK TIFFs Silently Treated as RGBA

**Location:** `jxl_tiff_encoder.py` — `convert_one()`

**Problem:** A CMYK TIFF has 4 channels. `make_png_bytes()` treated any 4-channel image as RGBA (`color_type=6`), producing a silently corrupted PNG/JXL with wrong colors.

**Fix:** Added an explicit check right after opening the TIFF:

```python
photometric = tif.pages[0].photometric
if photometric == tifffile.PHOTOMETRIC.SEPARATED:
    raise ValueError("CMYK TIFFs are not supported")
```

The encoder now aborts with a clear error message instead of producing incorrect output.

**Files changed:**
- `jxl_tiff_encoder.py` `convert_one()`

---

### Bug #113 — Dead `global _counter` in `_process_file_group`

**Location:** `jxl_jpeg_transcoder.py` — `_process_file_group()`

**Problem:** The function declared `global _counter` but never read from or wrote to the variable. It was leftover code from an earlier refactor.

**Fix:** Removed the unused `global _counter` declaration.

**Files changed:**
- `jxl_jpeg_transcoder.py` `_process_file_group()`

---

### Bug #114 — Grayscale Standalone TIFFs Reconstructed as RGB

**Location:** `jxl_tiff_encoder.py` — `convert_one()`

**Problem:** The `jxlphoto-grayscale` XMP marker was only written when `multipage_group` was set. Single-page grayscale TIFFs were therefore encoded as grayscale but decoded without the marker, and `read_png_to_numpy()` always returns a 3-channel RGB array, so the reconstructed TIFF came out as RGB instead of 2D grayscale.

**Fix:** Write the grayscale marker outside the `if multipage_group:` block, so every grayscale page gets the flag.

**Files changed:**
- `jxl_tiff_encoder.py` `convert_one()`

---

### Bug #115 — `--none` Mode Ignored for Standalone Files with `_pageN`/`_thumbnail` Suffix

**Location:** `jxl_tiff_decoder.py` — `write_multipage_tiff()`

**Problem:** `reason` and `strategy` were only captured when `page_idx == 0 and not is_thumb`. A standalone file named `photo_page2.jxl` (no group marker) was parsed with `page_idx = 2`, so `strategy` stayed `"unknown"` and the `--none` contract was violated: preview JPEG was added and full XMP/IPTC metadata was copied.

**Fix:** Use the first non-thumbnail page of the group as the anchor, regardless of its page index: `if not is_thumb and page_icc is None:`.

**Files changed:**
- `jxl_tiff_decoder.py` `write_multipage_tiff()`

---

### Bug #116 — `min(os.cpu_count(), 16)` Crashes When `cpu_count()` is `None`

**Location:** `jxl_jpeg_transcoder.py`, `jxl_tiff_decoder.py`, `jxl_tiff_encoder.py` — argument parsers

**Problem:** In some containers/sandboxes `os.cpu_count()` returns `None`, making `min(None, 16)` raise `TypeError` during argument parsing.

**Fix:** Use `min(os.cpu_count() or 4, 16)` in all three scripts.

**Files changed:**
- `jxl_jpeg_transcoder.py` `--workers` default
- `jxl_tiff_decoder.py` `--workers` default
- `jxl_tiff_encoder.py` `--workers` default

---

### Bug #117 — RGBA TIFFs Rejected by the Encoder

**Location:** `jxl_tiff_encoder.py` — `convert_one()`

**Problem:** The encoder raised `ValueError` for `page.samplesperpixel == 4` even though `make_png_bytes()` supports 4-channel PNGs and the decoder reconstructs alpha with `extrasamples=UNASSALPHA`. This broke the round-trip for any TIFF with an alpha channel.

**Fix:** Removed the RGBA rejection. The encoder now passes the 4-channel page through the existing PNG/JXL pipeline.

**Files changed:**
- `jxl_tiff_encoder.py` `convert_one()`

---

### Bug #118 — D50 Patch Summary Under-Counts Actually-Patched Files

**Location:** `jxl_tiff_encoder.py` — D50 summary

**Problem:** `actually_patched = applied - already_correct` mixed applied-correct files with skipped-correct files, so the summary could under-report (or report zero) actually-patched files.

**Fix:** Split the counter into `applied_already_correct` and `skipped_already_correct`. Summary now uses `actually_patched = applied - applied_already_correct`.

**Files changed:**
- `jxl_tiff_encoder.py` `_d50_patch_count` initialization, `apply_d50_policy()`, D50 summary

---

### Bug #119 — Manifest Auto-Mode Routes Any Folder Containing Substring `jxl` as Mode 6/7

**Location:** `jxl_photo.py` — `detect_mode_for_entry()`

**Problem:** The check `'jxl' in dest_str_lower` meant that a folder like `JXL_archive` was treated as an export-style destination, forcing mode 6 or 7 for manifest entries that were really mode 0.

**Fix:** Removed the `jxl` substring check; only the configured export marker is used for this heuristic.

**Files changed:**
- `jxl_photo.py` `detect_mode_for_entry()`

---

### Bug #120 — Embedded JPEG Thumbnail Always Generated from Page 0

**Location:** `jxl_tiff_encoder.py` — `convert_one()`

**Problem:** When `EMBED_JPEG_THUMBNAIL` was enabled and a multi-page page with `page_idx > 0` was encoded, the thumbnail was read from `tif.pages[0]` (or `img` pointing to page 0) instead of the page being encoded.

**Fix:** Both the PIL path (`img.seek(page_idx)`) and the tifffile fallback (`tif.pages[page_idx]`) now use the page being encoded.

**Files changed:**
- `jxl_tiff_encoder.py` `convert_one()`

---

### Bug #121 — `reorder_jxl_boxes()` Fails on Bare Codestreams

**Location:** `jxl_tiff_encoder.py` and `jxl_jpeg_transcoder.py` — `reorder_jxl_boxes()`

**Problem:** A bare JXL codestream (`0xFF 0x0A`) has no ISOBMFF boxes. The function tried to parse boxes from the raw codestream and raised `RuntimeError`.

**Fix:** Added an early return when `data[:2] == b'\xff\x0a'`.

**Files changed:**
- `jxl_tiff_encoder.py` `reorder_jxl_boxes()`
- `jxl_jpeg_transcoder.py` `reorder_jxl_boxes()`

---

### Bug #122 — Obsolete Comments in `add_jpeg_preview()`

**Location:** `jxl_tiff_decoder.py` — `add_jpeg_preview()`

**Problem:** Comments described page 0 as the JPEG preview and page 1 as the main image, while the code writes the main image on page 0 and the preview on page 1 (matching the Capture One structure).

**Fix:** Updated comments to match the actual page order.

**Files changed:**
- `jxl_tiff_decoder.py` `add_jpeg_preview()`

---

### Bug #123 — `jxl_jpeg_transcoder.py` Uses Invalid `--output_format=png` Flag

**Location:** `jxl_jpeg_transcoder.py` — `decode_to_image()`

**Problem:** When `output_icc` was set and `use_ram=True` (default), the `djxl` command included `"--output_format=png"`. `djxl` does not support this flag and fails with "Unknown flag". This broke `--to-srgb` and `--icc-profile` for JXL→JPEG/PNG conversions in RAM mode, which is the default.

**Fix:** Replaced the RAM pipeline for ICC conversions with the same temp-PNG path used by `--no-ram`: `djxl input tmp.png` (format inferred by extension), then `magick tmp.png ... output`.

**Files changed:**
- `jxl_jpeg_transcoder.py` `decode_to_image()`

---

### Bug #124 — Staging Orphan with `--format jpeg --bit-depth 16`

**Location:** `jxl_jpeg_transcoder.py` — `cmd_convert()` and `decode_to_image()`

**Problem:** JPEG does not support 16-bit, so `decode_to_image()` silently switched the output to PNG (`actual_out`). In staging mode, the staging file had already been created with `.jpg`, so the final `.png` was never moved from staging and the `.jpg` staging file was orphaned.

**Fix:** Moved the JPEG+16-bit detection to `cmd_convert()` before building output pairs. The format is switched to PNG early, so staging files and final paths get the correct `.png` extension.

**Files changed:**
- `jxl_jpeg_transcoder.py` `cmd_convert()`

---

### Bug #125 — `make_png_bytes()` Always Writes 16-Bit PNGs

**Location:** `jxl_tiff_encoder.py` — `make_png_bytes()` / `_cautious_test_icc_depth()`

**Problem:** `make_png_bytes()` always converted uint8 input to uint16 and wrote a 16-bit PNG. The "cautious" ICC test therefore ran two 16-bit round-trips instead of the documented 8-bit and 16-bit tests.

**Fix:** Made `make_png_bytes()` preserve the input bit depth: uint8 input now writes a true 8-bit PNG, and uint16 input writes a 16-bit PNG.

**Files changed:**
- `jxl_tiff_encoder.py` `make_png_bytes()`

---

### Bug #126 — `add_jpeg_preview()` Still Has Obsolete Comment and Dead Code

**Location:** `jxl_tiff_decoder.py` — `add_jpeg_preview()`

**Problem:** One leftover comment still described page 0 as the JPEG preview and page 1 as the main image, and the JPEG bytes were read into `jpeg_bytes` but never used.

**Fix:** Corrected the comment and removed the unused `jpeg_bytes` read.

**Files changed:**
- `jxl_tiff_decoder.py` `add_jpeg_preview()`

---

### Bug #127 — `read_ppm_to_numpy()` Fails When Dimensions Are on Separate Lines

**Location:** `jxl_tiff_decoder.py` — `read_ppm_to_numpy()`

**Problem:** The parser expected width and height on the same line. Valid PPM headers that split dimensions across lines or include comments would fail.

**Fix:** Read header tokens (skipping comments) until magic, width, height, and maxval are all available, regardless of line breaks.

**Files changed:**
- `jxl_tiff_decoder.py` `read_ppm_to_numpy()`

---

### Bug #128 — Transcoder `--force-transcode --decode` Crashes on Missing `jbrd` Box

**Location:** `jxl_jpeg_transcoder.py` — `decode_one_transcode()` / `process_group_transcode()`

**Problem:** When a JXL file had no `jbrd` box, the function raised `RuntimeError` **outside** the `try` block. The exception propagated out of the `ThreadPoolExecutor` worker and aborted the entire batch with a traceback instead of logging a per-file error.

**Fix:** Moved the `jbrd` check inside the function's `try` block so the existing `except` handler returns a `("error", ...)` tuple for that file. The `write_path.parent.mkdir()` call was also moved inside the `try`.

**Files changed:**
- `jxl_jpeg_transcoder.py` `decode_one_transcode()`

---

### Bug #129 — Wrapper `--delete-source` Confirmation Can Be Invisible / Blocked

**Location:** `jxl_photo.py` — `_run_subprocess()`

**Problem:** The wrapper runs the backend scripts with `stdout=PIPE`. When the backend asked for `--delete-source` confirmation with `print()` + `input()`, the child Python process could buffer the prompt and never flush it to the pipe, so the user saw nothing while the process blocked waiting for input.

**Fix:** The subprocess environment now includes `PYTHONUNBUFFERED=1`, forcing the child Python process to flush stdout line-by-line.

**Files changed:**
- `jxl_photo.py` `_run_subprocess()`

---

### Bug #130 — Lossy Convert Paths Drop EXIF/XMP/IPTC Metadata

**Location:** `jxl_jpeg_transcoder.py` — `encode_to_jxl()` and `decode_to_image()`

**Problem:** The lossy `convert` paths (`--force-convert`, JPEG lossy, PNG) relied on `cjxl`/`djxl` to preserve metadata, but those paths often drop EXIF/XMP/IPTC because the image is re-encoded rather than losslessly wrapped.

**Fix:** Added a best-effort `_copy_metadata()` helper that uses exiftool to copy metadata from the source file to the output file. Called after `encode_to_jxl()` (JPEG/PNG → JXL) and after `decode_to_image()` (JXL → JPEG/PNG). Failures are silently ignored so the conversion itself always succeeds.

**Files changed:**
- `jxl_jpeg_transcoder.py` `_get_exiftool_cmd()`, `_copy_metadata()`, `encode_to_jxl()`, `decode_to_image()`

---

### Bug #131 — `decode_one_transcode()` Never Reports `reconvert` on Overwrite

**Location:** `jxl_jpeg_transcoder.py` — `decode_one_transcode()`

**Problem:** The function computed `overwritten = final_path.exists()` but always returned `"ok"`, so overwrite/reconvert counters in the JXL→JPEG direction stayed at zero.

**Fix:** Return `"reconvert"` when the destination file already exists, matching the encoder behaviour.

**Files changed:**
- `jxl_jpeg_transcoder.py` `decode_one_transcode()`

---

### Bug #132 — `--multipage-mode skip` Always Encodes Page 0

**Location:** `jxl_tiff_encoder.py` — `_plan_multipage()`

**Problem:** When `--multipage-mode skip` found exactly one real page, it always encoded page 0. If the real image was on a different page and page 0 was only a thumbnail, the thumbnail was encoded as the main output.

**Fix:** Use the detected real page index when there is exactly one real page; fall back to page 0 only if there are no real pages at all.

**Files changed:**
- `jxl_tiff_encoder.py` `_plan_multipage()`

---

### Bug #133 — JPEG Scanners Ignore `.jfif` / `.jpe` Files

**Location:** `jxl_jpeg_transcoder.py` — `find_jpegs_flat()` / `find_jpegs_recursive()`; `jxl_photo.py` — `_get_extensions()`

**Problem:** `determine_command()` already accepted `.jfif` and `.jpe` for single files, but directory scans only looked for `.jpg`/`.jpeg`, so those files were silently ignored in batch modes. The wrapper's extension mapping also omitted them.

**Fix:** Added `*.jfif`, `*.JFIF`, `*.jpe`, and `*.JPE` to both recursive/flat scanners and to the wrapper's `jpeg` extension set.

**Files changed:**
- `jxl_jpeg_transcoder.py` `find_jpegs_flat()`, `find_jpegs_recursive()`
- `jxl_photo.py` `_get_extensions()`

---

### Bug #134 — `_copy_metadata()` Runs After `reorder_jxl_boxes()`, Undoing the Reorder

**Location:** `jxl_jpeg_transcoder.py` — `encode_to_jxl()`

**Problem:** The lossy convert path called `reorder_jxl_boxes(write_path)` first and `_copy_metadata(src_path, write_path)` second. Since `_copy_metadata()` uses exiftool, which re-appends metadata boxes at the end of the file, the reorder was silently undone: Exif/XMP boxes ended up after the codestream, breaking IrfanView compatibility (IrfanView reads boxes linearly and stops at the codestream). The TIFF encoder already documents that `reorder_jxl_boxes()` must be the **last** mutation on the file.

**Fix:** Swapped the order: `_copy_metadata()` runs first and `reorder_jxl_boxes()` is now the last mutation on the JXL file, matching the encoder. Verified: metadata boxes (`brob`/`xml `) now precede the codestream boxes in lossy convert output.

**Files changed:**
- `jxl_jpeg_transcoder.py` `encode_to_jxl()`

---

### Bug #135 — `execute_workflow()` Missing `PYTHONUNBUFFERED` (Main Wizard Path)

**Location:** `jxl_photo.py` — `execute_workflow()`

**Problem:** Bug #129 fixed `_run_subprocess()` (used by the manifest path), but the main wizard path ("New workflow") builds its own `subprocess.Popen` without the unbuffered environment. With `--delete-source` (mode 8), the child script's interactive confirmation prompt (`print()` + `input()`) could stay block-buffered in the pipe — the process looked hung while waiting for input the user never saw.

**Fix:** Pass `env={**os.environ, "PYTHONUNBUFFERED": "1"}` to the `Popen` call in `execute_workflow()`, mirroring `_run_subprocess()`. Verified: a child blocked on `input()` now has its prompt line delivered through the pipe immediately.

**Files changed:**
- `jxl_photo.py` `execute_workflow()`

---

### Bug #136 — `--dry-run` Ignored on Transcode/Auto Paths

**Location:** `jxl_jpeg_transcoder.py` — `cmd_transcode()`, `cmd_auto()` / `_process_file_group()`

**Problem:** `args.dry_run` was only checked in `cmd_convert()`. The transcode (`--force-transcode`) and auto paths performed real conversions even with `--dry-run`. Since the wrapper offers "Dry run?" for every workflow and forwards the flag to all scripts, a user simulating a JPEG↔JXL lossless batch got real conversions.

**Fix:** Added the same `DRY | src -> dst` listing + early return to `cmd_transcode()` and `_process_file_group()` (used by `cmd_auto`), and skipped the mode-8 deletion confirmation on dry runs. Verified: zero files created, no conversion subprocess runs (covered by `tests/test_transcoder_fixes.py`).

**Files changed:**
- `jxl_jpeg_transcoder.py` `cmd_transcode()`, `_process_file_group()`, `cmd_auto()`

---

### Bug #137 — Wizard Mode 8 `--delete-source` Dropped on Most Paths

**Location:** `jxl_photo.py` — wizard Step 6A (`_wizard_parameters_advanced`)

**Problem:** Choosing mode 8 sets `workflow['delete_source'] = True`, but `execute_workflow()` only reads `advanced_options['delete_source']`. The no-advanced-options early return never copied the flag, and the JXL→TIFF / transcoder advanced branches re-asked the question with a "No" default, ignoring the earlier choice. Only the TIFF→JXL advanced branch read `workflow['delete_source']`. Result: the user confirmed deletion (even typing HHMM) and nothing was deleted — the advertised mode-8 behavior did not work via the wizard.

**Fix:** Propagate `workflow['delete_source']` in the early return, and use it as the default (skip re-asking) in all three advanced branches, mirroring the TIFF→JXL branch.

**Files changed:**
- `jxl_photo.py` `_wizard_parameters_advanced()`

---

### Bug #138 — Auto Mode + Staging + 16-bit: Output Stranded in Staging

**Location:** `jxl_jpeg_transcoder.py` — `_process_file_group()` / `decode_to_image()` / `process_group_convert()`

**Problem:** In auto mode without an explicit `--format`, the pre-switch for 16-bit JPEG output never fired (`args.format` is `None`, not `"jpeg"`). `decode_to_image()` then switched the extension `.jpg → .png` at runtime, but the staging promotion looked for the original `.jpg` staging path — which didn't exist — and silently moved nothing (no warning either). The converted file stayed in the staging dir with a UUID name while the log said OK.

**Fix:** Compute the effective bit depth and switch the output extension to `.png` when JPEG+16-bit **before** building output pairs (same pattern as the #124 fix in `cmd_convert`), so staging names, final names, and promotion all agree. Verified end-to-end: PNG promoted to destination, staging left clean.

**Files changed:**
- `jxl_jpeg_transcoder.py` `_process_file_group()`

---

### Bug #139 — Transcoder Mode 1 Was Recursive (Docs and Decoder Say Flat)

**Location:** `jxl_jpeg_transcoder.py` — file collection in `cmd_transcode()`, `cmd_convert()`, `cmd_auto()`

**Problem:** Only mode 0 used the flat scanners; mode 1 scanned recursively. The transcoder README documents mode 1 as "Flat (non-recursive)", and the TIFF decoder treats modes 0 and 1 as flat — so sibling scripts disagreed, and outputs from nested folders were flattened into a single `converted/` dir (possible name collisions).

**Fix:** Modes 0 and 1 both use the flat scanners in all collection sites, matching the docs and the decoder. Recursive collection remains available in modes 2/3/8.

**Files changed:**
- `jxl_jpeg_transcoder.py` `cmd_transcode()`, `cmd_convert()`, `cmd_auto()`

---

### Bug #140 — Transcoder Modes 4/5 Inverted vs Encoder/Decoder (+ Wrong Wrapper Labels)

**Location:** `jxl_jpeg_transcoder.py` — `resolve_output_transcode()` / `resolve_output_convert()`; `jxl_photo.py` — mode labels

**Problem:** The TIFF encoder/decoder use mode 4 = folder rename (suffix swap) and mode 5 = sibling folder; the transcoder had them swapped (4 = sibling, 5 = rename). The wrapper presented a single mode table that could not be correct for both — and in practice its labels described the old transcoder-only semantics for every direction. **Breaking change (accepted):** the transcoder was renumbered to match the encoder/decoder.

**Fix:** Swapped mode 4/5 in both transcoder resolve functions, updated all wrapper labels/descriptions (including the auto-mode recommender, which now recommends mode 4 for folder names containing the source type), and updated the docs. Users with saved transcoder commands using modes 4/5 must swap them.

**Files changed:**
- `jxl_jpeg_transcoder.py` `resolve_output_transcode()`, `resolve_output_convert()`
- `jxl_photo.py` mode tables/labels, auto-mode recommender

---

### Bug #141 — Auto Mode Does Nothing on PNG-Only Folders

**Location:** `jxl_jpeg_transcoder.py` — `cmd_auto()`

**Problem:** `cmd_auto()` collected only JPEG and JXL files, so a folder of PNGs reported "No JPEG or JXL files found" — despite single-file auto-detection routing PNG → convert encode.

**Fix:** `cmd_auto()` also collects PNGs and routes them through `_process_file_group(..., direction="to_jxl")` (new direction parameter). PNG→JXL is treated as lossy for deletion-confirmation purposes.

**Files changed:**
- `jxl_jpeg_transcoder.py` `cmd_auto()`, `_process_file_group()`

---

### Bug #142 — Wizard Asks Decode Mode Twice; Second Pass Discards the First

**Location:** `jxl_photo.py` — wizard Step 6 vs Step 6A (JXL→TIFF)

**Problem:** Step 6 asks decode mode (roundtrip/basic/matrix/none) and target ICC for JXL→TIFF. Entering advanced options (Step 6A) re-asked everything with factory defaults and rebuilt `advanced_options` from scratch, discarding the Step 6 answers (the merge preserving them existed only on the no-advanced path). Choosing "matrix" in Step 6 and accepting defaults in Step 6A silently produced roundtrip. Additionally, target ICC was asked even for non-matrix modes, where the decoder ignores it.

**Fix:** Step 6A uses the Step 6 answers as prompt defaults (matrix/basic/none/target ICC), and target ICC is only asked when matrix is selected (both in Step 6 and Step 6A).

**Files changed:**
- `jxl_photo.py` `_wizard_parameters()` / `_wizard_parameters_advanced()`

---

### Bug #143 — Repeat Workflow Loses `distance` for Lossy Conversions

**Location:** `jxl_photo.py` — session save / repeat workflow

**Problem:** For conversion types containing "lossy", only `quality` was saved (`saved_distance = None`); the repeat rebuilt the workflow without `distance`, so the command used the default `d=1.0`. A lossy batch done at d=0.5 repeated at d=1.0, silently.

**Fix:** Save `distance` alongside `quality` for lossy workflows and restore it when repeating (when present).

**Files changed:**
- `jxl_photo.py` session save / repeat workflow

---

### Bug #144 — HHMM Lossy Confirmation Required for Lossless Transcode Decode

**Location:** `jxl_jpeg_transcoder.py` — `cmd_transcode()`

**Problem:** `is_lossy_decode = decode and not args.force_transcode` treated an auto-detected transcode decode as "lossy", requiring the HHMM time confirmation. But transcode decode requires the `jbrd` box per file (enforced in `decode_one_transcode`), so it is always lossless — the docs state lossless operations only require "yes".

**Fix:** `cmd_transcode()` always uses the simple `confirm_deletion_jpeg()` confirmation; the HHMM confirmation remains for genuinely lossy paths (`cmd_convert`/`cmd_auto`).

**Files changed:**
- `jxl_jpeg_transcoder.py` `cmd_transcode()`

---

### Bug #145 — Progress Total Counts Files Filtered Out by Modes 6/7

**Location:** `jxl_jpeg_transcoder.py` — `cmd_transcode()` / `cmd_convert()`

**Problem:** `_counter["total"]` was set to `len(files)` before the output-pair loop filtered out files outside `EXPORT_MARKER`, so progress in modes 6/7 ended below the stated total (e.g. `[1/2]` and never reaching 2).

**Fix:** `_counter["total"] = len(pairs)` after the filtering loop (both commands).

**Files changed:**
- `jxl_jpeg_transcoder.py` `cmd_transcode()`, `cmd_convert()`

---

### Bug #146 — Wizard Mode 2 Output Positional After Flags Breaks Argparse on Python < 3.12.7

**Location:** `jxl_photo.py` — `execute_workflow()` (encoder/decoder/transcoder branches)

**Problem:** In mode 2 the wrapper appended the output directory **after** the flags (`script input --mode 2 --workers 4 output`). That intermixed-positional pattern hit the 12-year-old argparse bug gh-59317 on CPython < 3.13.1 / < 3.12.7 (never backported to 3.9–3.11 or 3.12.0–3.12.6): `error: unrecognized arguments: <output>`, killing every wizard mode-2 workflow on those versions. On Python 3.12.7+/3.13.1+ the same command parses fine, so the failure depends on the interpreter version — the README promises Python 3.9+.

**Fix:** Insert the output positional right after the input (`cmd.insert(3, output_dir)`), giving the classic `[input, output, ...flags]` ordering that parses on every Python version — the same ordering the manifest path already used.

**Files changed:**
- `jxl_photo.py` `execute_workflow()`

---

### Bug #147 — D50 Patch Stats Count Per Page in Multipage Splits

**Location:** `jxl_tiff_encoder.py` — `apply_d50_policy()` + D50 summary

**Problem:** The `_d50_patch_count` counters increment per `apply_d50_policy()` call, which runs once per page in multipage split mode. A 3-page TIFF sharing one ICC profile counted 3 "applied" in the summary, which reads as a file count. Cosmetic (log only).

**Fix:** Added `_d50_patched_hashes`, a set of md5 hashes of original ICC bytes that actually needed the patch, updated under the same lock. The summary now appends "(N unique profiles)" when the unique count differs from the per-page applied count.

**Files changed:**
- `jxl_tiff_encoder.py` `apply_d50_policy()`, D50 summary

---

### Bug #148 — `check_dependencies(force=...)` Ignores the `force` Parameter

**Location:** `jxl_photo.py` — `DependencyChecker.check_dependencies()`

**Problem:** The `force` parameter was accepted but never read — the method always ran full detection (one subprocess per tool) and rewrote the config. Callers pass `force=True` for the menu "re-check" option, so behavior was safe, but the parameter was misleading.

**Fix:** Implemented the intended semantics: the result is cached on the instance; `force=False` returns the cache when present, `force=True` bypasses it and re-detects.

**Files changed:**
- `jxl_photo.py` `DependencyChecker`

---

### Bug #149 — Mode 8 Delete Flag Lost on Manual/Detail Selection Paths

**Location:** `jxl_photo.py` — `_wizard_select_mode_manual()` / `_show_mode_details_and_select()`

**Problem:** Follow-up of #137. `workflow['delete_source'] = True` was only set in `_wizard_select_mode()` (direct number input). The other two mode-8 selection paths — "choose manually" from Auto Mode and the `?` details view — asked the HHMM confirmation but never set the flag, so `--delete-source` never reached the scripts (nothing was deleted despite the user confirming twice).

**Fix:** Set `workflow['delete_source'] = True` right after `_confirm_archive_mode()` in both paths.

**Files changed:**
- `jxl_photo.py` `_wizard_select_mode_manual()`, `_show_mode_details_and_select()`

---

### Bug #150 — `cmd_auto` Ignores Script-Level `DELETE_SOURCE`/`DELETE_CONFIRM`

**Location:** `jxl_jpeg_transcoder.py` — `cmd_auto()`

**Problem:** The deletion confirmation in auto mode was gated on `args.delete_source` (CLI flag) instead of the `DELETE_SOURCE` global, unlike `cmd_transcode`/`cmd_convert`. Setting `DELETE_SOURCE = True` in the script made auto mode delete sources **without any confirmation**; conversely, `DELETE_CONFIRM = False` (automation) still prompted and blocked non-interactive runs.

**Fix:** `cmd_auto` now sets `DELETE_SOURCE` from `args.delete_source` up front (like the other commands) and gates the confirmation on `args.mode == 8 and DELETE_SOURCE and not args.dry_run and DELETE_CONFIRM`.

**Files changed:**
- `jxl_jpeg_transcoder.py` `cmd_auto()`

---

### Bug #151 — Multipage Marker Batch: 32k Command-Line Limit + `[` Wildcards + Silent Fallback

**Location:** `jxl_tiff_decoder.py` — `_read_multipage_markers_batch()`

**Problem:** Up to 400 file paths were passed as arguments to a single exiftool call. With typical photo paths (~90 chars) that exceeds the ~32k Windows `CreateProcess` limit; the exception hit a silent `except: continue`, quietly disabling multipage reconstruction for the whole chunk. Paths containing `[ ]` were also interpreted as wildcards by exiftool.

**Fix:** The batch now passes the file list via an exiftool argfile (`-@`, with `-charset FileName=UTF8` for non-ASCII paths) — removing both the command-line limit and the wildcard problem — and logs a warning when a batch fails instead of silently continuing.

**Files changed:**
- `jxl_tiff_decoder.py` `_read_multipage_markers_batch()`

---

### Bug #152 — Mode 6 Output Collision Across `_EXPORT` Subfolders

**Location:** all 3 scripts — output resolution for mode 6

**Problem:** Mode 6 drops the first subfolder level under `EXPORT_MARKER`, so `_EXPORT/sRGB/img.jpg` and `_EXPORT/AdobeRGB/img.jpg` (typical multi-recipe Capture One exports) both mapped to `_EXPORT/<out>/img.jxl`, silently overwriting each other — with mode 8 + delete, one validated output could justify deleting both distinct sources.

**Fix:** Added `_abort_on_duplicate_outputs()` at planning time in all 3 scripts: duplicate destinations are listed and the run aborts with a clear error before converting anything. Verified end-to-end (exit 2, no outputs created).

**Files changed:**
- `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

---

### Bug #153 — Decoder Partial Output + Smart Sync Skips Forever

**Location:** `jxl_tiff_decoder.py` — `convert_multipage_jxl_group()`

**Problem:** Without staging, the TIFF was written directly to its final destination. If writing failed mid-way (disk full, crash), the partial file stayed with a fresh mtime; the next run with the default smart sync compared mtimes and skipped it as "up to date" — a corrupt TIFF, never reprocessed.

**Fix:** The `except` handler now deletes the partial output (if the file pre-existed, `TiffWriter` had already truncated it, so the partial on disk is never the original).

**Files changed:**
- `jxl_tiff_decoder.py` `convert_multipage_jxl_group()`

---

### Bug #154 — `shlex.split` Posix Mangles Windows Paths in Expert Flags

**Location:** `jxl_photo.py` — `execute_workflow()`

**Problem:** `shlex.split(expert_flags)` with the default posix mode turned `--staging E:\temp_jxl` into `E:temp_jxl`. The manifest path already used `posix=(os.name != 'nt')`.

**Fix:** Same `posix=(os.name != 'nt')` in `execute_workflow()`.

**Files changed:**
- `jxl_photo.py` `execute_workflow()`

---

### Bug #155 — Repeat Workflow Loses Quality for `jxl_to_jpeg_force/auto`

**Location:** `jxl_photo.py` — session save on wizard completion

**Problem:** Only conversion types containing "lossy" saved the chosen quality; `jxl_to_jpeg_force`/`jxl_to_jpeg_auto` fell into the `else` branch and saved `default_quality` (95), so a repeat ran with 95 instead of the user's choice.

**Fix:** The quality-preserving branch now also covers `jxl_to_jpeg_force`/`jxl_to_jpeg_auto`.

**Files changed:**
- `jxl_photo.py` session save

---

### Bug #156 — `--decode` Ignored for Directories

**Location:** `jxl_jpeg_transcoder.py` — `main()` routing

**Problem:** Directories always routed to `cmd_auto`, which never reads `args.decode`. `--decode` on a folder re-encoded JPEGs to JXL and lossy-converted non-jbrd JXLs — the opposite of the documented "force decode direction".

**Fix:** Directory + `--decode` routes to `cmd_transcode(auto_decode=True)`: JXL-only, jbrd-gated lossless recovery; non-jbrd files fail per-file.

**Files changed:**
- `jxl_jpeg_transcoder.py` `main()`

---

### Bug #157 — `--ram`/`--no-ram` Is a No-Op in Transcoder Decode

**Location:** `jxl_jpeg_transcoder.py` — `decode_to_image()` + CLI help

**Problem:** `decode_to_image()` accepted `use_ram` but never used it; all decodes use temporary files. The CLI help implied a working RAM pipeline.

**Fix:** Help text and the Performance section of the transcoder README now state that `--ram`/`--no-ram` are accepted but currently without effect. (A real in-RAM decode pipeline is a possible future enhancement.)

**Files changed:**
- `jxl_jpeg_transcoder.py` CLI help; `docs/README_jxl_jpeg_transcoder.md`

---

### Bug #158 — `cmd_auto` Progress Total Counts Files Filtered by Modes 6/7

**Location:** `jxl_jpeg_transcoder.py` — `_process_file_group()`

**Problem:** Same class as #145 (fixed for `cmd_transcode`/`cmd_convert`): `_counter["total"]` included files that modes 6/7 later filter out (`out is None`).

**Fix:** The total is decremented by the number of filtered files when pairs are built.

**Files changed:**
- `jxl_jpeg_transcoder.py` `_process_file_group()`

---

### Bug #159 — `--container=1` Applied on Lossless (d=0) in `encode_to_jxl`

**Location:** `jxl_jpeg_transcoder.py` — `encode_to_jxl()`

**Problem:** `FORCE_CONTAINER_FOR_LOSSY` appended `--container=1` unconditionally. On lossless encodes the container changes how the ICC is stored and breaks color display in IrfanView — the reason the TIFF encoder only passes it for `d>0`.

**Fix:** The flag is now only appended when `distance > 0`.

**Files changed:**
- `jxl_jpeg_transcoder.py` `encode_to_jxl()`

---

### Bug #160 — `cleanup_xmp_icc` Leaves Leading `| ` When ICC Marker Is Mid-String

**Location:** `jxl_tiff_decoder.py` — `cleanup_xmp_icc()`

**Problem:** The cleanup only stripped a trailing pipe. With `CreatorTool = "ICC:xxx | Capture One 23"` (marker not last, e.g. written by another tool), the result was `"| Capture One 23"`.

**Fix:** Also strip a leading pipe after the ICC marker removal. Covered by test.

**Files changed:**
- `jxl_tiff_decoder.py` `cleanup_xmp_icc()`

---

### Bug #161 — Wizard Texts Promise Wrong Folder Names

**Location:** `jxl_photo.py` — mode tables, Step 5, auto-mode mappings

**Problem:** The wizard advertised `{dest}_files` for mode 3 and `converted_{dest}` for mode 1, but the scripts create `JXL_16bits`/`TIFF_16bits`/`converted_jxl`/`converted_tiff`/`recovered_jpeg`. The auto-mode mode-2 preview also showed `output_{dest}` while Step 5 defaults to `<parent>/output`.

**Fix:** New `_dest_folder_names(origin, dest)` helper returns the real names per direction and is used by all labels, Step 5, and the auto-mode mapping preview.

**Files changed:**
- `jxl_photo.py`

---

### Bug #162 — Step 7 Summary Shows Quality for Distance-Driven Lossy

**Location:** `jxl_photo.py` — wizard Step 7 summary

**Problem:** For `convert_lossy` (JPEG→JXL lossy, which is distance-driven) the summary displayed "Quality: 95" and never showed the distance.

**Fix:** The branch now displays the `Distance` row.

**Files changed:**
- `jxl_photo.py` Step 7 summary

---

### Bug #163 — Pure-Text Wizard Fallback Asks Target ICC Outside Matrix Mode

**Location:** `jxl_photo.py` — wizard Step 6 (non-Rich fallback)

**Problem:** Follow-up of #142: the plain-text fallback still asked for a target ICC unconditionally, although the decoder only honors it in matrix mode.

**Fix:** The question is only asked when `decode_mode == "matrix"`, matching the Rich branch.

**Files changed:**
- `jxl_photo.py` Step 6 fallback

---

### Bug #164 — `decode_auto` Dead Code in Decoder

**Location:** `jxl_tiff_decoder.py` — `decode_auto()`

**Problem:** Never called — all paths use `decode_auto_png()` or `decode_rec2020_linear()`.

**Fix:** Removed.

**Files changed:**
- `jxl_tiff_decoder.py`

---

### Bug #165 — Lowercase-Only Globs Miss `.TIF`/`.JXL` on Case-Sensitive Filesystems

**Location:** `jxl_tiff_encoder.py` — `find_files_mode0()` / `find_tiffs_recursive()`; `jxl_tiff_decoder.py` — `find_jxls_*()`; `jxl_jpeg_transcoder.py` — `find_jxls_*()`

**Problem:** Globs only matched lowercase extensions (`.tif`, `.tiff`, `.jxl`, `.jif`), missing uppercase files on Linux/macOS filesystems. (JPEG finders already covered uppercase.)

**Fix:** Added uppercase variants to all glob lists.

**Files changed:**
- `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

---

### Bug #166 — ICC Verify Commands Reference Swapped XMP Fields (Docs)

**Location:** `README.md` and `docs/README_jxl_tiff_encoder.md`

**Problem:** The verification snippets checked for the ICC in `XMP-dc:Description` and for encoding params in `XMP-xmp:CreatorTool` — exactly inverted relative to what the encoder writes (ICC → `CreatorTool`, params → `dc:Description`).

**Fix:** Commands corrected in both files.

**Files changed:**
- `README.md`, `docs/README_jxl_tiff_encoder.md`

---

### Bug #167 — Mode 7 Default + `HHMMSS` Format Wrong in Tools README (Docs)

**Location:** `docs/README_jxl_tools.md`

**Problem:** Documented the mode 7 default as `_EXPORT/JXL`, but the real default subfolder is empty (all subfolders processed, like mode 6). Also said the delete confirmation format is `HHMMSS` where it is `HHMM`.

**Fix:** Both entries corrected.

**Files changed:**
- `docs/README_jxl_tools.md`

---

### Bug #168 — Step 7 Plain-Text Summary Shows Quality for Distance-Driven Lossy

**Location:** `jxl_photo.py` — wizard Step 7 summary (non-Rich fallback)

**Problem:** Follow-up of #162 — the Distance fix was applied only to the Rich branch; the plain-text fallback still printed `Quality: {quality}` for `convert_lossy` (distance-driven).

**Fix:** The fallback now prints the `Distance` line, matching the Rich branch.

**Files changed:**
- `jxl_photo.py` Step 7 summary (plain-text fallback)

---

### Bug #169 — Mode 1 Detail Example Hardcodes `converted_{dest}`

**Location:** `jxl_photo.py` — `_show_mode_details_and_select()` mode 1 entry

**Problem:** Follow-up of #161 — the title line was fixed to `_dest_folder_names()`, but the "Example:" line below still hardcoded `converted_{dest}` (wrong for JPEG decode, which creates `recovered_jpeg`).

**Fix:** The example now uses `_dest_folder_names(origin, dest)[0]`.

**Files changed:**
- `jxl_photo.py` `_show_mode_details_and_select()`

---

### Bug #170 — "KEEP in staging" Logged for Partial Outputs Already Discarded

**Location:** `jxl_tiff_decoder.py` — `process_group()` staging promotion

**Problem:** After the #153 fix (partial outputs deleted on error), the staging loop still logged `KEEP in staging (error)` for files that no longer existed — a misleading message.

**Fix:** When the staging file is gone, the log now says `Partial output discarded (status)`; `KEEP in staging` is only logged when the file actually remains.

**Files changed:**
- `jxl_tiff_decoder.py` `process_group()`

---

### Bug #171 — Cross-Group Collision Invisible in Auto Mode

**Location:** `jxl_jpeg_transcoder.py` — `cmd_auto()` / `_process_file_group()`

**Problem:** The #152 duplicate check ran per processing group, but auto mode processes JPEGs, PNGs, and JXLs in separate `_process_file_group()` calls — so `photo.jpg` and `photo.png` in the same folder (both mapping to `photo.jxl`) collided undetected (sequential processing avoided corruption, but the protection was bypassed).

**Fix:** `_process_file_group()` gained a `collect_only` pre-pass mode; `cmd_auto()` collects all output pairs across the four groups and runs the duplicate check on the union before processing anything. Verified end-to-end (abort, exit 2).

**Files changed:**
- `jxl_jpeg_transcoder.py` `cmd_auto()`, `_process_file_group()`

---


