# Bug Tracking Since v1.0

Every bug fixed since v1.0, one numbered row each, newest round first. Bugs
#1–#171 (v1.0 up to the first part of the v1.8.1 audit) keep their original
long-form write-ups in [bug_tracking_archive.md](bug_tracking_archive.md).
New features and behaviour changes are in
[new_features_since_v1.0.md](new_features_since_v1.0.md); release notes in
[version_history.md](version_history.md).

**Numbering.** One sequence for the whole project, shared with the archive: a
new bug takes the next free number, and a number is never reused.
**Next free number: #529.** `tests/test_bug_tracker_numbers.py` fails
on a repeated number, a stale "next free" line, or sections out of date order.
#14 (an improvement, not a bug) and #78 have no row. #481–#495 are the fifteen entries that had been
numbered #195–#209 a second time (Post-v1.8.1, v1.8.3 and the first
v1.9.0_beta2 row) and were renumbered on 2026-10-07 — each of those sections
says so.

**Rounds are not releases.** A round is one audit or fix batch; the *Shipped
in* column says which release carried it. The version numbers some rounds
carried while in progress (v1.9.2, v1.9.3, v1.9.4, v1.10.1, v1.10.2, v1.10.3)
were never tagged and never shipped.

| Round / release | Date | Shipped in | Bugs |
|---|---|---|---|
| [Round-51 — the wizard's defaults are the scripts' settings](#round-51--the-wizards-defaults-are-the-scripts-settings-2026-10-09) | 2026-10-09 | v2.8.2 | #528 (1) |
| [Round-50 — what the 261008 audit left open](#round-50--what-the-261008-audit-left-open-2026-10-08) | 2026-10-08 | v2.8.1 | #521–#527 (7) |
| [Round-49 — the 261008 audit](#round-49--the-261008-audit-2026-10-08) | 2026-10-08 | v2.8.0 | #496–#520 (25) |
| [Round-48 — what `--buffering 1` really costs](#round-48--what---buffering-1-really-costs-2026-10-06) | 2026-10-06 | v2.8.0 | #480 (1) |
| [Round-47 — per-file exiftool timeouts, planning progress](#round-47--per-file-exiftool-timeouts-planning-progress-2026-10-06) | 2026-10-06 | v2.7.0 | #478–#479 (2) |
| [Round-46 — the recompressor's silent planning phase](#round-46--the-recompressors-silent-planning-phase-2026-10-05) | 2026-10-05 | v2.6.2 | #477 (1) |
| [Round-45 — subprocess without reader threads, the ignore-mode page size, logs out of the repository](#round-45--subprocess-without-reader-threads-the-ignore-mode-page-size-logs-out-of-the-repository-2026-10-05) | 2026-10-05 | v2.6.1 | #473–#476 (4) |
| [Round-44 — memory](#round-44--memory-2026-10-04) | 2026-10-04 | v2.6.0 | #470–#472 (3) |
| [Round-43 — the 261001 audit](#round-43--the-261001-audit-2026-10-01) | 2026-10-01 | v2.5.0 | #439–#469 (31) |
| [Round-42 - the lossy ICC-blob decode](#round-42---the-lossy-icc-blob-decode-2026-09-26) | 2026-09-26 | v2.3.0 | #437–#438 (2) |
| [Round-41 — resize/sharpen round](#round-41--resizesharpen-round-2026-09-25) | 2026-09-25 | v2.3.0 | #436 (1) |
| [v2.2.0 — the distance floor is per cjxl version](#v220--the-distance-floor-is-per-cjxl-version-2026-09-24) | 2026-09-24 | v2.2.0 | #435 (1) |
| [Round-40 audit](#round-40-audit-2026-09-23) | 2026-09-23 | v2.2.0 | #419–#434 (16) |
| [Round-39 audit](#round-39-audit-2026-09-21) | 2026-09-21 | v2.2.0 | #385–#418 (34) |
| [Round-38 audit](#round-38-audit-2026-09-21) | 2026-09-21 | v2.2.0 | #357–#384 (28) |
| [Round-37 audit](#round-37-audit-2026-09-20) | 2026-09-20 | v2.1.1_beta1, v2.2.0 | #347–#356 (10) |
| [Round-36 audit](#round-36-audit-2026-09-19) | 2026-09-19 | v2.1.0 | #339–#346 (8) |
| [Round-35 audit](#round-35-audit-2026-09-18) | 2026-09-18 | v2.1.0 | #323–#338 (16) |
| [Round-34 audit](#round-34-audit-2026-08-23) | 2026-08-23 | v2.0.3 | #319–#322 (4) |
| [Round-33 audit](#round-33-audit-2026-08-23) | 2026-08-23 | v2.0.3 | #314–#318 (5) |
| [Round-32 audit](#round-32-audit-2026-08-19) | 2026-08-19 | v2.0.3 | #313 (1) |
| [Round-31 audit](#round-31-audit-2026-08-19) | 2026-08-19 | v2.0.2 | #303–#312 (10) |
| [Round-30 audit](#round-30-audit-2026-08-13) | 2026-08-13 | v2.0.1 | #297–#302 (6) |
| [Round-29 audit](#round-29-audit-2026-08-08) | 2026-08-08 | v2.0.0 | #277–#296 (20) |
| [Round-28 audit](#round-28-audit-2026-08-07) | 2026-08-07 | v2.0.0 | #271–#276 (6) |
| [Round-27 audit](#round-27-audit-2026-08-07) | 2026-08-07 | v2.0.0 | #266–#270 (5) |
| [Round-26 audit](#round-26-audit-2026-08-07) | 2026-08-07 | v2.0.0 | #260–#265 (6) |
| [Round-25 audit](#round-25-audit-2026-08-06) | 2026-08-06 | v2.0.0 | #252–#259 (8) |
| [Round-24 audit](#round-24-audit-2026-08-06) | 2026-08-06 | v2.0.0 | #242–#251 (10) |
| [Round-23 audit — post-v1.9.1](#round-23-audit--post-v191-2026-08-05) | 2026-08-05 | v2.0.0 | #234–#241 (8) |
| [v1.9.0_beta2 — Full-repo audit](#v190_beta2--full-repo-audit-2026-08-01) | 2026-08-01 | v1.9.0 | #210–#233, #495 (25) |
| [v1.8.3 — Manifest run reporting](#v183--manifest-run-reporting-2026-07-27) | 2026-07-27 | v1.8.3 | #493–#494 (2) |
| [Post-v1.8.1 — Real-batch usability fixes](#post-v181--real-batch-usability-fixes-2026-07-27) | 2026-07-27 | — | #481–#492 (12) |
| [v1.8.1 — The Audit Release](#v181--the-audit-release-2026-07) | 2026-07 | v1.8.1 | #172–#209 (38) |
| [Archive: v1.0 → v1.8.1, original format](bug_tracking_archive.md) | 2026-04 – 2026-07 | v1.0 – v1.8.1 | #1–#171 |

---

## Round-51 — the wizard's defaults are the scripts' settings (2026-10-09)

Found while closing round 50 (`AI_tools/261008_Claude_report_audit-leftovers.md`).
Harmless with the shipped settings, which the literals matched. Regression
tests: `tests/test_wrapper_child_defaults.py` (every scenario fails against the
v2.8.1 wrapper via `JXLPHOTO_SCRIPTS_UNDER_TEST`). Nothing a codec or exiftool
writes is involved, so there is no real-codec test.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 528 | **The wizard's defaults were literal copies of the scripts' settings.** For the options it puts on a child's command line — multi-page and thumbnail mode, thumbnail suffix, D50 patch, encode tag, RAM for the PNG intermediate, TIFF compression, bit depth, JPEG preview, depth policy, multi-page reconstruction, provenance check, the recompressor's distance — the wizard fell back to the shipped value (`last_multipage_mode or 'split'`) whenever it had no remembered answer. An edit at the top of a script (`MULTIPAGE_TIFF_MODE = "split_all"`) was overridden by a fresh wizard run, by Step 6A answered "no", and by every preset that stored no answer. Three `"_EXPORT"` literals repeated the wrapper's own marker setting. | wrapper | ✅ FIXED (`_child_default` reads each option's setting from its script through one table, `_CHILD_OPTION_DEFAULTS`, whose fallbacks a test pins to the shipped settings; a remembered `last_*` answer still comes first, and a typo falls back to the setting; the marker literals read `ToolConfig.export_marker`. Unchanged on purpose: the wrapper's own settings (workers, quality, effort, the TIFF → JXL distance, set in menu option 4) and the transcoder's lossy distance, which has no setting at the top of its script) |

---

## Round-50 — what the 261008 audit left open (2026-10-08)

The low-severity findings v2.8.0 left for later (`AI_tools/261008_Claude_report_audit-fixes.md`,
"O que NÃO foi feito"). None of them lost a photo; most are edge cases a
normal library never meets. Regression tests:
`tests/test_audit_261008_leftovers.py` (real cjxl/djxl/exiftool where the
item touches what they write; every scenario test fails against the v2.8.0
scripts via `JXLPHOTO_SCRIPTS_UNDER_TEST`). The real-photo battery gained
two decoder checks (an edited decode on a real 16-bit TIFF).

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 521 | **A 4th channel came back as transparency.** JPEG XL stores a TIFF's 2nd/4th channel as alpha and keeps no TIFF role for it, so the decoder wrote every such channel as unassociated alpha (ExtraSamples=2): an RGB+IR scan's IR channel (ExtraSamples=0) or an associated alpha (1) changed role in the round trip. The pixels were intact (#514 keeps them so in lossy encodes). | encoder, decoder | ✅ FIXED (the encoder records the page's ExtraSamples in `jxlphoto-extrasamples:`, and the decoder restores it on the main writer and on the JPEG-preview rewrite; JXLs without the marker keep the old default. The marker never reaches the TIFF, and a re-encode writes it once) |
| 522 | **A decoded TIFF edited afterwards was decoded over.** Photoshop keeps the XMP, so a retouched decode still carried the `jxlphoto-src` marker naming its JXL: once the JXL was newer (recompressed, re-encoded), the smart sync decoded over the edit, and `--delete-skipped` could delete the JXL on the strength of the edited TIFF. | decoder | ✅ FIXED (every decode records its pixels in `jxlphoto-pixsum:<pages>:<md5>`; before overwriting one of its own TIFFs, and before a skipped TIFF certifies a deletion, the decoder compares — an edit is refused (status "refused"; `--overwrite` still replaces it) and keeps the JXL. TIFFs decoded before v2.8.1 carry no record and are handled as before. The dry run previews both) |
| 523 | **A JXL cut exactly at a `jxlp` box boundary passed the integrity check.** A codestream split into `jxlp` boxes is complete only when its last box carries the "last" bit; the box walk checked the chain's shape and length, not that bit, so a file truncated between two `jxlp` boxes passed every delete gate. Never seen in a real run (cjxl and exiftool write `jxlc`); hardening. | encoder, recompressor, transcoder | ✅ FIXED (`_verify_jxl_integrity` (parity-pinned) and the transcoder's `_verify_file_integrity` read each `jxlp` index: they must count up from 0, the last must carry the high bit, and `jxlc` and `jxlp` must not mix) |
| 524 | **The decoder's dry run promised deletions a degraded decode would not make.** v2.8.0 keeps the JXL of a `--depth 8` decode of a deeper master and of a `--basic` decode that drops the profile recorded in XMP (#505), but `--dry-run --delete-source` still counted them as "would be DELETED". | decoder | ✅ FIXED (the depth-8 keeps are counted from the depth marker; the `--basic` ones — decided only after djxl runs — are announced as "may be KEPT" from one batched read of the XMP profile) |
| 525 | **The transcoder converted INTO a scanner profile.** `--icc-profile <file>` accepted a profile with A2B tables and no B2A (an input profile such as SilverFast's `SFprofT`), which cannot be a faithful conversion target (#502: 27 dB on a real scan); only the "keep the source profile" path refused it. | transcoder | ✅ FIXED (refused up front, exit 2, like the recompressor's `--output-icc`) |
| 526 | **Step 6 offered a derivative after `[D]`.** With delete originals already chosen in Step 4, the wizard still asked for a colour conversion, resize or sharpening (JXL → JXL, JXL → JPEG/PNG); the combination was refused only after the Step 7 YES. | wrapper | ✅ FIXED (Step 6 skips those questions with `[D]` on, says why, and drops earlier answers) |
| 527 | **The manifest collision scan modelled only page 0 of a split TIFF.** A split writes `foto_page1.jxl` next to `foto.jxl`; another entry's `foto_page1.tif` writes the same name. The scan compared stems only, so it passed; the child refused the overwrite (#496) and nothing was lost, but the manifest started anyway. | wrapper, encoder | ✅ FIXED (for TIFF → JXL, a page-shaped stem next to its base stem in one output folder is checked against the encoder's own page plan (`_planned_page_names`, pinned to `convert_multipage`), reading only those TIFF headers; the run's multi-page settings are honoured) |

---

## Round-49 — the 261008 audit (2026-10-08)

Found by a read-only audit of the four backends and the wrapper
(`AI_tools/261008_Claude_audit_scripts.md`), every data-loss finding
reproduced with real photos before the fix. Regression tests:
`tests/test_audit_261008_x1.py` (real cjxl/djxl/exiftool, the scripts run as
subprocesses; the scenario tests fail against the pre-fix scripts via
`JXLPHOTO_SCRIPTS_UNDER_TEST`), `tests/test_audit_261008_r1.py` (colour),
`tests/test_audit_261008_x2.py` (source identity) and
`tests/test_audit_261008_gates.py` (delete gates) and
`tests/test_audit_261008_wrapper.py` (wrapper),
`tests/test_audit_261008_item6.py`, `tests/test_staging_promotion.py` and
`tests/test_audit_261008_item7.py`.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 496 | **#268 was only half fixed: a plain sync overwrote the archive of a photo an earlier run had deleted.** The cross-run provenance guard ran only when the CURRENT run had `--delete-source` in a folder-collapsing mode ("without deletion an overwrite is recoverable"). That holds for the new source only: the existing output's own TIFF may have been deleted by an earlier run, and then the overwrite is the loss. Reproduced with real 45 MP TIFFs: mode 2, run 1 archives and deletes `A/foto.tif`, run 2 (no delete flag — every scheduled preset is one) re-encodes `B/foto.tif` over `out/foto.jxl`. And every mode collapses names: in mode 8 `foto.tif` and `foto.tiff` both write `foto.jxl` (lost with `--delete-source` on both runs). A jbrd JXL (the transcoder's lossless JPEG archive) at the output path was overwritten the same way. | encoder | ✅ FIXED (every run now reads, in one batch, the markers of each existing output it is about to OVERWRITE and refuses — exit 1, listed in the failures, previewed by `--dry-run` — when the marker names a different source (`--provenance content` still accepts matching bytes) or the output is a jbrd JPEG archive; a markerless output is overwritten as before. The strict delete-time guard is unchanged) |
| 497 | **The decoder's twin of #496, and the smart sync trusted any marker.** The guard ran only with `--delete-source` in a collapsing mode, and the smart sync treated any TIFF carrying a `jxlphoto-src` marker as "ours" — including the decode of a DIFFERENT same-named JXL. Reproduced: mode 2, run 1 decodes `A/foto.jxl` and deletes it, run 2 (no delete) overwrites `out/foto.tif` — the only copy of photo A — with `B/foto.jxl`'s decode. | decoder | ✅ FIXED (`_decode_output_is_ours(tiff, src_paths)` requires the marker to MATCH these JXLs (`_provenance_ok`) in both smart-sync directions; a TIFF this run would overwrite (`--overwrite`, or the JXL is newer) whose marker names other JXLs is refused at planning time as an error, `--overwrite` included; the up-to-date direction reports it as refused, never as "up to date") |
| 498 | **The recompressor overwrote another photo's archive, warning only in modes 1/3.** No provenance check without `--delete-source`; the warning `_warn_foreign_overwrite` ran only in the folder-preserving modes, so in modes 2/4/5/6/7 — where same-named sources meet — a foreign archive was replaced in silence (reproduced with real JXLs in mode 2). | recompressor | ✅ FIXED (`_foreign_overwrite_refusals` replaces the warning: in every mode, an output about to be overwritten whose markers name a different origin is refused — exit 1, failures, dry-run preview — and a refused page still vetoes its multi-page siblings' deletion. The battery's "foreign output is warned (and proceeds)" check now expects the refusal) |
| 499 | **The transcoder's twin of #496.** `_provenance_filter` returned early without `--delete-source` in a collapsing mode, so `--sync`/`--overwrite` replaced the lossless archive of a JPEG an earlier run had deleted (reproduced with a real JPEG, mode 2: `RECONVERT` over `out/foto.jxl`). | transcoder | ✅ FIXED (`_foreign_overwrite_filter`: every `--sync`/`--overwrite` run refuses to overwrite an output that is provably another source's — a marker naming a different file, a jbrd archive whose recorded `checksums.md5` hash is not this JPEG's, or on the JXL→JPEG recovery an existing JPEG that is not the one the JXL recorded. No record either way keeps the old behaviour; a hash that cannot be computed refuses) |
| 500 | **`--provenance content` in the recompressor was not a superset of `path`.** `_markers_match` compared only the srcsum in content mode, so a master re-exported in place (same path, new bytes) was refused with `content` and accepted with `path` — the opposite of the help ("also accepts") and of `_provenance_ok` in the other three scripts. Fail-closed, but confusing. | recompressor | ✅ FIXED (content accepts a matching path OR a matching srcsum; `tests/test_recompressor.py::TestMarkersMatch` updated) |
| 501 | **Recompressing a scan master changed its colours.** A profile with no native JPEG XL form (table curves, scanner LUTs — the owner's film scans use `SFprofT`, A2B tables and no B2A) was handled as `cjxl src.jxl out.jxl`: a LOSSLESS master re-encoded lossy became a "lossy ICC blob", which the toolkit decodes through linear sRGB and converts back — measured on a real scan at **27 dB** (the A2B-only profile has no faithful way back) — and recompressing that file again had cjxl read it as linear sRGB and write native linear sRGB under the old XMP profile: **18 dB**. The battery ran exactly this chain on the real scan and passed (it checked markers, not colours). | recompressor | ✅ FIXED (`_recompress_encode`: `jxlinfo` triage (header only) sends native-colour files down the old path; for an ICC source a real decode decides — a lossy-blob source is never re-encoded (`COPY (lossy ICC blob)`, in place left as it is), a profile with no native form (probed once per run on a 16x16 image, `_icc_has_native_form`) is encoded tagged sRGB with the profile in XMP CreatorTool, the encoder's "skip". Real scan crop: 27.2 / 18.0 dB → 48.7 / 46.6 dB; camera files byte-identical to before. New battery check: the in-place multi-page scan is decoded and compared with the original scan (≥ 40 dB)) |
| 502 | **Derivatives of such masters had the same problem, and an A2B-only profile was used as a conversion target.** A "keep" derivative (resize/sharpen) of a lossless table-curve master was written as a lossy ICC blob; a "keep" derivative of a lossy blob converted linear sRGB back INTO the master's profile — impossible to do faithfully for a scanner profile without B2A; `--output-icc` accepted such a profile. | recompressor, transcoder | ✅ FIXED (`_derive_pixels` encodes a profile with no native form tagged sRGB with the profile in XMP and returns it as `skip_icc`; a conversion INTO a profile with A2B and no B2A (`_icc_a2b_only`, parity-pinned) is refused — the derivative errors, `--output-icc` exits 2, the transcoder refuses the "keep" conversion of such a blob) |
| 503 | **The decoder deleted the JXL after an approximate decode.** A lossy ICC blob of an A2B-only (scanner) profile can only be converted back approximately (27 dB on a real scan), yet `--delete-source` treated the TIFF as a copy of the master. | decoder | ✅ FIXED (the decode is still written, with a warning; such a JXL is recorded in `_unfaithful_decodes` and the delete gate keeps it: `KEPT ... store(s) a scanner profile (no B2A table) as a lossy ICC blob`) |
| 504 | **A source re-exported during the run was deleted without ever being read.** The delete gate runs only after the whole pool drains — hours on a big batch — and nothing recorded which file the run had read: a TIFF re-exported by the editor in that window (or a JXL replaced by another tool) was unlinked on the strength of the output made from its old version. Reproduced with a real TIFF. The lossless JPEG transcode was safe (it reconstructs and compares with the file on disk at delete time). | encoder, decoder, recompressor, transcoder (lossy) | ✅ FIXED (`_record_source_identity` stores each source's `(st_size, st_mtime_ns)` when the work is handed to the pool; `_source_changed_since_read` re-stats it right before the unlink and keeps the source when it changed or vanished: `KEEP (source changed since this run read it ...)`. Parity-pinned in the four backends; a multi-page group / recompressor group is kept whole) |
| 505 | **The decoder deleted the master after a degraded decode.** Only `--matrix` was blocked; `--depth 8` from a 16-bit master (TIFF uint8), `--none` (no profile, no XMP/IPTC) and `--basic` on a file whose real profile lives in XMP (an encoder "skip" file: the TIFF gets djxl's sRGB tag) all deleted the JXL. Reproduced with real photos for `--depth 8` and `--none`. | decoder | ✅ FIXED (`--none` + `--delete-source` exits 2 before anything runs; a page decoded at 8 bits from a deeper — or unrecorded — source, or by `--basic` with a profile different from the XMP one, is recorded in `_degraded_decodes` and the gate keeps the group: "the TIFF is a degraded copy ..., not the master") |
| 506 | **Lossy `--delete-skipped` deleted the master for ANY same-named JPEG/PNG.** The lossy gate was "structural check only — nothing can prove it", although every lossy output the transcoder writes carries its source's `jxlphoto-src`/`srcsum`. Reproduced: a master JXL (TIFF already deleted) next to an unrelated `foto.jpg` was deleted as "already archived". | transcoder | ✅ FIXED (both lossy gates — `cmd_convert` and auto mode — read the skipped outputs' markers in one batch and require `_provenance_ok`; a jbrd output keeps its reconstruction proof. Setting comment, `--help`, the run's NOTE and the wrapper/READMEs no longer say "nothing can prove it") |
| 507 | **A JPEG cut in half passed the JPEG integrity check.** `_verify_file_integrity` accepted an `FF D9` anywhere in the file — and the EXIF thumbnail inside APP1 is a whole JPEG with its own EOI near the start. A real camera JPEG truncated at 50 % passed (the only structural gate of the lossy deletes, and of a lossless decode without an MD5). | transcoder | ✅ FIXED (`_jpeg_main_eoi` walks the marker chain from the SOI — length-prefixed segments skipped, entropy-coded data scanned for its terminating marker — and passes only on the EOI of the MAIN image; data after it (Motion Photos) is still fine) |
| 508 | **The delete-skipped texts still said "a file with the same name would pass".** The encoder's warning and help, the decoder's warning and the wrapper's `[D]` panel described the gate as structural-only, although the encoder (E-1), the decoder (D-2) and the recompressor (#419) already required the matching provenance marker (and the transcoder's lossy gate does now, #506). The transcoder's and the recompressor's "`--provenance` has no effect without `--delete-source`" became wrong with #496–#499. | encoder, decoder, transcoder, recompressor, wrapper, docs | ✅ FIXED (texts say what backs the delete — the marker names the source FILE, not its pixels; the transcoder warns about `--provenance` only when nothing can overwrite or delete, the recompressor's warning is gone: its smart-sync default overwrites) |
| 509 | **The `[D]` panel counted a different set of files than the run deletes.** "About to delete originals" — the count that makes a wrong folder visible before the HHMM token — is drawn in Step 4, before Step 5 sets the export marker, the mode-7 subfolder and the output folder. Reproduced with real photos: the panel said 3 TIFFs, the mode-7 run with subfolder `TIFF16` converted and deleted 2. | wrapper | ✅ FIXED (Step 7 recounts with the final settings when `--delete-source` is armed — `DELETE SOURCE: ON (!) — N TIFF file(s) in scope` — and says when the `[D]` panel's count differed) |
| 510 | **"Convert to sRGB?" + `[D]` deleted the master after a colour conversion.** The manifest's OutputICC rows never received `--delete-source` (a colour conversion is a derivative), but the direct route sent `--icc-profile sRGB` with it, and the transcoder deleted the 16-bit wide-gamut master after writing an 8-bit sRGB JPEG; the recompressor's `--output-icc` never deletes. | wrapper, transcoder | ✅ FIXED (the transcoder refuses `--icc-profile`/`--to-srgb` with `--delete-source`/`--delete-skipped` outside the lossless transcode route (exit 2); the wrapper refuses the pair before the HHMM token and never emits it from a manifest row) |
| 511 | **A master encoded from a decoded derivative was marked as a derivative.** `jxlphoto-derived:` was not among the encoder's internal dc:Relation prefixes nor the decoder's internal markers: master → sRGB derivative → decoded TIFF → encoded again gave a NEW master carrying `jxlphoto-derived:sRGB/...` AND `jxlphoto-src` — and the derivative guards ("only overwrite our own derivatives") would treat that master as one and overwrite it when the recipe changed. Reproduced with real photos. | encoder, decoder | ✅ FIXED (`DERIVED_XMP_PREFIX` defined in both, matching the recompressor's and transcoder's; the encoder never copies it into a master, the decoder never into a TIFF) |
| 512 | **An image kept in a SubIFD was deleted unarchived.** tifffile's `pages` is the main IFD chain only; a TIFF/EP- or DNG-style file keeps a small preview in IFD0 and the full image in a SubIFD. `--multipage-mode skip/ignore/split_all` archived the preview and `--delete-source` deleted the file. | encoder | ✅ FIXED (`_note_subifd_images`: a source with SubIFDs is recorded as having discarded real pages, so no gate deletes it, and the run warns `SubIFD image(s) NOT encoded`. The SubIFDs are still not encoded) |
| 513 | **Promoting an output out of staging over an existing one was not atomic.** `shutil.move` onto an existing file is copy2 + unlink on Windows — even on one volume — and copy2 truncates the destination first: a kill or power cut mid-copy destroyed the old archive and left a partial file with a fresh mtime that smart sync then treats as up to date. A failed copy over an existing output was even deleted by the cleanup. | encoder, decoder, transcoder, recompressor | ✅ FIXED (`_promote_from_staging`, all four copies: move to `<uuid>_<name>.tmp` in the destination folder, then `os.replace`; on failure a complete temp is put in place, a partial one removed — the final name holds the old file or the complete new one, never a mix) |
| 514 | **Lossy encodes rewrote the colour under a zero 4th channel.** A 2nd/4th channel reaches cjxl as alpha, and lossy cjxl defaults to `--keep_invisible=0`: for an RGB+IR scan stored as 4 channels the image under the dust (IR = 0) came back 80x worse than asked (mean error 1601 vs 19 elsewhere). | encoder, recompressor | ✅ FIXED (`--keep_invisible=1` on lossy pages with 2/4 channels in the encoder, on every lossy encode in the recompressor — a no-op without such a channel; derivative bytes unchanged. The ExtraSamples type (IR vs alpha) was still written back as unassociated alpha — fixed in v2.8.1, #521) |
| 515 | **The encoder skipped the decoder's output folders by a literal copy of their default names.** `_DECODER_OUTPUT_FOLDERS` repeated `16B_TIFF`/`TIFF_16bits`/`converted_tiff`: a user who renamed them in `jxl_tiff_decoder.py` had modes 6/7 pick the decoded TIFFs up again — a duplicate-output abort while the original existed, or a lossy re-encode of a decode once it was gone. | encoder | ✅ FIXED (the names are read from the decoder's settings with `ast` — nothing imported or executed — plus the shipped defaults; the wrapper's mode-6 preview asks the encoder for the same set) |
| 516 | **The wrapper named the children's default output folders, not their settings.** `_dest_folder_names` (the "About to delete originals" panel, the previews, the manifest generator) and the mode-6 preview's skip list were literals: an edited script showed the shipped folder name. | wrapper | ✅ FIXED (`_child_setting` reads CONVERTED_*_FOLDER / *_FOLDER_NAME / RECOVERED_JPEG_FOLDER from the child; the literals are only fallbacks) |
| 517 | **The idle-timeout kill left the child's codecs running.** On Windows `Popen.kill()` terminates the Python child alone: cjxl/djxl/exiftool kept running and left their `<uuid>_name.tmp` files beside the finals, where no sweep looks. | wrapper | ✅ FIXED (`_kill_process_tree`: `taskkill /F /T` on the child's process tree, then `kill()`; used by the idle timeout, Ctrl+C and the final wait) |
| 518 | **JXL → TIFF with decode mode 'none' and `[D]` was not refused up front.** The wizard offered it and the run went as far as the child (which now refuses it, #505); bit depth 8 / 'basic' with `[D]` gave no hint that the JXLs may be kept. | wrapper | ✅ FIXED (`execute_workflow` refuses 'none' + delete before the HHMM token and notes that depth 8 / 'basic' keep the JXLs of a degraded decode) |
| 519 | **The manifest recap undercounted deletions without saying so.** "SOURCES DELETED" adds up the children's `##JXLSUM##` lines; a child killed by the idle timeout, crashed or interrupted emits none, so what its delete gate had already removed was missing from the total. | wrapper | ✅ FIXED (entries that ran with `--delete-source` and ended without a summary are listed: `NOT COUNTED: N entries with --delete-source ended without a summary (killed) ...`) |
| 520 | **Auto Mode recommended mode 7 after looking at three export folders only.** Mode 7 takes ONE subfolder name for the whole run; a 4th export folder keeping its files under another name was then silently left out. | wrapper | ✅ FIXED (every export folder is examined, stopping at the second subfolder name, when the answer is mode 6) |

---

## Round-48 — what `--buffering 1` really costs (2026-10-06)

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 480 | **The `--buffering 1` advice was wrong at effort 8–9.** Since v2.6.0 the memory-cap warning, the docs and the wrapper README recommended `--buffering 1` for any whole-image setting, "files ~2 % larger" — a size measured at effort 7 only; quality was never measured. Measured now with cjxl 0.12.0 on four real photos (SSIMULACRA2 + Butteraugli): at effort 7 / d=3, streaming is the same image ~1.5 % larger (the advice holds); at effort 9 / d=3, a streamed encode is **effort 7's file** (identical metrics, sizes within 0.3 %) — the extra effort is not used — while the default whole-image effort-9 encode is 7–10 % smaller. The scheduled MOBILE preset (d=3, e=9, `--buffering 1`) was paying for effort 9 and getting effort 7. | encoder, recompressor, docs | ✅ FIXED (the memory-cap warning is effort-aware: at effort 8+ it says a streamed encode is effort 7's file and suggests effort 7; at effort 7 it keeps the `--buffering 1` advice with the measured cost. Docs: [Streaming vs whole-image](README_jxl_recompressor.md#streaming-vs-whole-image-what---buffering-1-costs-measured) with the full results and the cjxl version; `tools/buffering_benchmark.py` re-runs the measurement. Test: `tests/test_memory_workers.py::test_cap_hint_depends_on_effort`, which fails against the pre-fix code) |

---

## Round-47 — per-file exiftool timeouts, planning progress (2026-10-06)

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 478 | **A good file became an error when exiftool stalled under load, and the error blamed the codec.** Every per-file exiftool call (metadata copy, source-profile read, markers, thumbnail) ran with a fixed 15–180 s limit, while the codecs get 900 s. The scheduled MOBILE run of 2026-10-06 (17 workers at d=3 e=9 on 45 MP files, `G:` hard disk) lost twelve consecutive derivatives within 40 s of each other, about 3 minutes after they started, while throughput dropped from ~17 to 3–5 files a minute: a transient stall, not the files (same size as their neighbours, which converted). The recompressor reported each one as `codec timed out after 900s`, whatever had actually timed out — here exiftool at 60/120 s. No partial output was left. | all four backends | ✅ FIXED (new setting `EXIFTOOL_TIMEOUT`, defined as the script's codec timeout, used by all 52 per-file exiftool calls; the planning-time batch reads keep their per-batch limit. The recompressor's TIMEOUT line and error summary name the tool and the limit that fired: `exiftool timed out after 120s`. Test: `tests/test_exiftool_timeouts.py`, which fails against the pre-fix code) |
| 479 | **The planning phase was announced, but still silent for minutes.** #477 added the `Planning N file(s)` line and the closing `Planned in X` timings; in between, the encode records were read in exiftool batches of 400 with nothing logged, so a hard-disk run of 681 files still sat ~4 minutes on one line (the folder scan before it, by contrast, counts as it goes). | recompressor | ✅ FIXED (`Encode records: N/total read (Xs, ~Ym left)` after each batch, the estimate extrapolated from the rate so far, paced like the folder scan: quiet while it is fast, then at a growing interval capped at 60 s. Batches are now 100 files so the count moves every ~20–30 s on a hard disk; the extra exiftool starts cost ~0.14 s each, ~3.5 s over 3 327 files. Test: `tests/test_planning_progress.py`, the slow-disk case fails against the pre-fix code) |

---

## Round-46 — the recompressor's silent planning phase (2026-10-05)

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 477 | **The recompressor planned for minutes with nothing on screen.** Between `JXLs found: N` and the first `[n/total]` line it reads every source's encode record with exiftool, walks its boxes for a `jbrd` and checks the existing outputs, opening each file at least twice. On a hard disk that is ~0.15–0.25 s per file, and the scheduled MOBILE runs on `G:` sat silent for 105 s (681 files), 285 s (1 248) and 466 s (3 327): it looks like a hang. | recompressor | ✅ FIXED (a `Planning N file(s): ...` line announces the phase, and `Planned in X (encode records A, jbrd check B, output checks C)` reports where the time went; a batch-prompt wait is not counted. Measured on the same disk: 1 355 files planned in 3m58s, all of it the exiftool read of the encode records. The encoder and decoder logs showed no silent planning gap of 15 s or more and are unchanged. Test: `tests/test_planning_progress.py`, which fails against the pre-fix code) |

---

## Round-45 — subprocess without reader threads, the ignore-mode page size, logs out of the repository (2026-10-05)

The follow-up round to Round 44: #471's `_run_captured` (temp-file capture, no
`communicate()` reader threads) is extended to every remaining `subprocess.run`
call in the four backends and to the transcoder's `check=True` decode calls,
the encoder's `--multipage-mode ignore` records the page size so the memory cap
stops assuming the 60 MP fallback, and the test suite plus the real-photo
battery route their logs out of the repository with `JXLPHOTO_LOG_DIR`
(`tests/test_subprocess_capture.py`, `tests/test_log_dir.py` pin it). #476
corrects the *Start in* note the docs got wrong. Nothing here changes what the
tools convert; without the environment variable set, behaviour is identical.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 473 | **Codec calls still used `subprocess.run(capture_output=True)` — the Round-44 fix covered only `_run_exiftool_argfile` and the recompressor.** `capture_output` makes `communicate()` start reader threads (the #471 hang class) in every backend's djxl/magick/cjxl path, and the transcoder's `decode_to_image` ran three calls with `check=True` whose `CalledProcessError` handler kept the FIRST 200 characters of stderr — djxl's version banner — so the line that said what failed was cut off. | encoder, decoder, transcoder, recompressor | ✅ FIXED (every call goes through `_run_captured`, which now also accepts `input=` (written from the calling thread — stdin never starts a reader thread), so the `convert_one` stdin path keeps `--ram` behaviour; `_stderr_tail` is shared by the four parity-pinned copies and the codec error messages keep the LAST 200 characters; the three `check=True` calls in `decode_to_image` became plain `_run_captured` + `returncode` checks raising `RuntimeError("djxl: ...")`/`("magick: ...")`. The version probes (`_tool_version`, `_warn_if_libjxl_too_old`) keep `capture_output` deliberately — an unusable temp capture must fail the probe into "version unknown", never silently disable the libjxl 0.12 paths. Test: `tests/test_subprocess_capture.py` — the AST sweep `test_no_subprocess_call_outside_the_capture_helper` and the real-run `test_a_real_run_starts_no_reader_thread` fail against the pre-fix code) |
| 474 | **The memory cap read the fallback 60 MP in `--multipage-mode ignore`.** `convert_multipage`'s ignore branch never recorded a page's pixel count into `_PAGE_PIXELS`, so every ignore-mode worker was estimated at `_UNKNOWN_IMAGE_PIXELS` and `--workers` was reduced below what the real (small) pages need. | encoder | ✅ FIXED (the ignore branch records `imagewidth × imagelength` for page 0 like the other modes. Test: `tests/test_memory_workers.py::test_encoder_ignore_mode_records_page_pixels` — fails against the pre-fix code with a KeyError) |
| 475 | **The test suite wrote its logs into the repository.** `LOG_DIR` is computed at import relative to the script, and scripts run as subprocesses could not be redirected by monkeypatching — every test run grew `Logs\` next to the scripts (~25 700 files, synced by OneDrive), and `rejected_files.log` ignored the script's own `LOG_DIR` and sat in `SCRIPT_DIR / "Logs"` by name. | encoder, decoder, transcoder, recompressor, wrapper | ✅ FIXED (`JXLPHOTO_LOG_DIR` (environment variable) moves every log folder — set in `tests/conftest.py` at import time to a temp folder removed at session end, after `logging.shutdown()` closes the log files Windows would not let it delete (before any test imports a script; subprocesses inherit it) and by the real-photo battery's `run()`; `rejected_files.log` writes to `LOG_DIR`. Unset, behaviour is identical. Tests: `tests/test_log_dir.py` — each script's `LOG_DIR` in a subprocess with the variable set, `_log_rejected_file` under a monkeypatched `LOG_DIR`, and a suite-level check that the log dir is outside the repository) |
| 476 | **The scheduled-run docs explained *Start in* wrongly: they claimed a blank field "is where the logs then land".** Logs are relative to the SCRIPT folder, never to the working directory — what a blank *Start in* really does is start the task in `C:\Windows\System32`, where `py jxl_photo.py` cannot find the script and every run dies with `can't open file ...` and exit code 2, indistinguishable from "no such preset". | wrapper (docs) | ✅ FIXED (`docs/README_jxl_tools.md`'s *Start in* bullet and the manifest README's step 4 now say what happens — the `.cmd` example with `cd /d "%~dp0"` and its bullet were already correct) |

---

## Round-44 — memory (2026-10-04)

The scheduled MOBILE preset recompressed 45 MP JXLs at d=3 effort 7 with 30
workers. That distance/effort is the "whole image at once" path of libjxl 0.12:
each cjxl peaked at ~3.6 GiB, 30 of them (~107 GiB) did not fit the machine's
76.6 GiB commit limit, 314 files failed with `JxlEncoderProcessOutput failed` /
`WinError 1455`, and one worker hung forever when the MemoryError landed in the
`capture_output` reader thread's bootstrap (the run had to be killed by the
wrapper). `tests/test_memory_workers.py` pins both fixes.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 470 | **`--workers` was never limited by memory.** cjxl encodes the WHOLE image at once (2.5–8× the usual streaming peak) at effort 7 with distance ≥ 3, effort 8–9 with distance > 0.5, effort 10+, or `--buffering 0`; the run started the requested count regardless, so a large `--workers` on that path could exhaust the commit limit and fail file after file. | encoder, recompressor | ✅ FIXED (`_memory_capped_workers` estimates each worker's peak from the largest image and the settings and lowers the count to fit the memory the system can still commit times `WORKER_MEMORY_FRACTION` (0.8; 0 = off); the encoder reads each page's pixel count (`_PAGE_PIXELS`) and the recompressor reads width×height in its batched exiftool call. It logs `Memory: ...` and warns `--workers N reduced to K` with the `--buffering 1` remedy on the whole-image path. Tests: `tests/test_memory_workers.py`) |
| 471 | **A worker could hang forever before its subprocess timeout.** `capture_output=True` makes `communicate()` start two reader threads per call on Windows; under memory exhaustion a thread can die in its bootstrap (MemoryError) before it signals "started", and `Thread.start()` then waits with no timeout — `subprocess.run` never reaches its own timeout arm, so the manifest entry never returns (the 2026-10-04 run hung 60 min on one file until the wrapper killed the child). | recompressor (all four backends' `_run_exiftool_argfile` + the recompressor's codec calls) | ✅ FIXED (`_run_captured` captures stdout/stderr through temp FILES — no reader thread can be created, and a failed spawn raises OSError. Used by `_run_exiftool_argfile` in all four backends and by the recompressor's djxl/magick/cjxl calls. The other three scripts' codec calls still use `capture_output` — follow-up. Test: `tests/test_memory_workers.py::test_run_captured_starts_no_thread`, which monkeypatches `threading.Thread.start` to fail) |
| 472 | **A failed encode was logged without the line that said why.** Every codec error message kept the FIRST 200 characters of stderr, and cjxl prints its version banner and an `Encoding [...]` line before the failure — 97 of the 314 errors of the 2026-10-04 run were logged as just the banner. A bare `MemoryError` (empty message) was logged as an empty string, and exiftool's `Out of memory!` was not recognised as a memory failure. | recompressor | ✅ FIXED (`_stderr_tail` keeps the LAST 200 characters, CRLF folded, in the djxl/magick/cjxl/exiftool error messages of the encode path (`convert_one`, `_derive_pixels`; the parity-pinned `_decode_jxl_for_verify` keeps its copy's form); an empty exception message is recorded as its type name in `convert_one` and in the worker-crash handler; `"out of memory"` joins the signatures of the end-of-run memory hint — on that run's log the hint recognised only 215 of the 314 errors. Tests: `tests/test_memory_workers.py::test_memory_failures_are_recognised`, `::test_memory_hint_in_summary`) |

---

## Round-43 — the 261001 audit (2026-10-01)

The consolidated audit of the `--exclude-folders` feature (commit `59ec602`) and
the whole repo, from `261001_audit_consolidated.md` plus its second opinion
(`261001_audit_consolidated_AI2.md`). The headline is the last unbounded member
of the #419/#387 skipped-path provenance family: `--delete-skipped` in the
encoder and the decoder could delete a master on the strength of a same-named
output written from a different photo. The rest are the #432/#433 ports that
never reached their siblings, the run-scoped-global leak in all four children,
and a wrapper/manifest batch — most of it ours, in the just-committed feature.
The `tests/test_round43_*.py` files pin this audit.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 439 | **`--delete-skipped` certified a pre-existing output by existence+integrity, not by a provenance match (E-1/D-2, the #419 family's last two members).** The encoder's skipped path never read a marker at all — a valid, newer same-named JXL of ANOTHER photo (a camera name counter resetting across cards, or any existing JXL under `OVERWRITE=False`) certified deleting the master TIFF; the decoder read marker PRESENCE, not a match. #419 closed only the recompressor — exactly the sibling-port gap Appendix B named. | encoder, decoder | ✅ FIXED (the skipped branch now requires a provenance MATCH: the encoder batch-reads the outputs once (`_skipped_archive_proof` + `_provenance_ok`) and the decoder requires `_markers_match(..., PROVENANCE_CHECK)`; an unreadable, absent or foreign marker fails CLOSED and the source is KEPT. Design: a legacy markerless archive is KEPT with a healing hint (re-encode, or `--provenance adopt` in a collapsing mode) — it is never silently overwritten or deleted; a whole folder moved with the default `--provenance path` stops matching, so `--provenance content` is the remedy. The dry-run preview runs the SAME predicate, so it can no longer promise a deletion the gate refuses. Tests: `tests/test_round43_encoder_gate.py`, `tests/test_round43_decoder_gate.py`) |
| 440 | **The delete confirmation was charged on an empty / all-skip plan (the #432 port).** A no-TTY re-run of an already-archived folder with `--delete-source` (no `--delete-skipped`) has a plan of pure skips — nothing would be deleted — yet the HHMM/yes token was demanded, stdin was EOF, and the run exited 3 forever. #432 had fixed only the transcoder and the recompressor. | encoder, decoder | ✅ FIXED (`_plan_would_delete_source` (encoder) and `_plan_touches_sources` (decoder) charge the prompt only when the plan can actually delete; fail-closed — any item this run would write, or a deletion-eligible skip under `--delete-skipped`, keeps the prompt armed, and `--delete-confirm-off` is unchanged. Tests: `tests/test_round43_encoder_gate.py`, `tests/test_round43_decoder_gate.py`) |
| 441 | **`--export-marker ""` silently kept the default (the #433 port).** `if args.export_marker:` treated an explicit empty string (export NOTHING) as absent, so the encoder and the decoder ran with `_EXPORT` while the recompressor and the transcoder honoured the empty marker. #433 had fixed only those two — and the first pass of this round ported it to the encoder alone, missing the decoder (caught in review). | encoder, decoder | ✅ FIXED (`is not None`, mirroring `--export-subfolder`; the empty marker then matches nothing, fail-closed via `_marker_matches`, and the decoder's next in-process run gets the script setting back. Tests: `tests/test_round43_encoder_gate.py`, `tests/test_round43_decoder_gate.py`) |
| 442 | **CLI-mutated run-scoped globals leaked one-way in all four children.** `main()` assigned `DELETE_SOURCE`/`DELETE_CONFIRM`/`DELETE_SKIPPED`/`OVERWRITE`/`TEMP2_DIR`/`PROVENANCE_CHECK`/`EXPORT_MARKER` and friends only when their flag was passed, so a second in-process run inherited the first run's armed delete state (and `--staging` leaked `TEMP2_DIR`). It was also the cause of the round-37/40 cross-file test failures (`dec.DELETE_SOURCE` left armed). #431 had fixed only the encoder's counters. | encoder, decoder, transcoder, recompressor | ✅ FIXED (the encoder, the recompressor and the transcoder reset every run-scoped global at `main()` entry to an import-time `_RUN_DEFAULTS` snapshot of the settings at the top of the script — never to hardcoded literals, so a setting the user edited in the file (`EXPORT_MARKER`, `DELETE_CONFIRM`, `TEMP2_DIR`, ...) stays in force; the first pass of the transcoder fix assigned `"_EXPORT"`/`"path"`/`True` literally and was corrected in review. The decoder restores, at each entry, only the values a previous run actually CHANGED (`_prev_run_globals`). The suite-wide autouse fixture `tests/conftest.py::_isolate_run_globals` snapshots and restores every child module's run-scoped globals around each test and clears `_tool_version`'s cache before and after it. Tests: `tests/test_round43_encoder_gate.py`, `tests/test_round43_decoder_gate.py`, `tests/test_round43_transcoder_fixes.py`, `tests/test_round43_recompressor.py`) |
| 443 | **The transcoder's `--dry-run` spawned `cjxl --version`.** `_warn_distance_clamp(args.distance, _min_effective_distance("cjxl"))` evaluated the floor eagerly in `cmd_convert`/`cmd_auto`, so every invocation — including a simulation — ran the version probe (up to 10 s on a slow or antivirus-scanned exe) and broke the "a dry run runs no subprocess" contract. A module-wide `_tool_version` cache warmed by earlier tests masked it in the full suite. | transcoder | ✅ FIXED (the floor is resolved only on a real run: `--dry-run` spawns nothing and skips the clamp warning, while a non-dry run warns exactly as before; the regression is pinned order-independently by clearing the cache in the test fixture. Test: `tests/test_round43_transcoder_fixes.py`) |
| 444 | **`-d 0 --force-convert` JPEG (jbrd) outputs never recorded checksums.** At distance 0 a JPEG goes through `encode_to_jxl` with cjxl's default `--lossless_jpeg=1` (a jbrd container) but, unlike `encode_one_transcode`, never called `store_md5_db`/`store_jxl_self_hash_db`; a second run in a collapsing mode with `--delete-source` then failed closed with the misleading "no checksum to prove it (was it written with --no-md5?)". | transcoder | ✅ FIXED (`encode_to_jxl` stores the source md5 and the JXL self-hash exactly like the transcode path, honouring `--no-md5`; `process_group_convert` redistributes the staged checksums to the destination like `process_group_transcode`. Tests: `tests/test_round43_transcoder_fixes.py`) |
| 445 | **`cmd_auto` silently ignored `--icc-profile`/`--to-srgb` for JPEG/PNG → JXL encode groups.** `encode_to_jxl` has no ICC conversion step, and `cmd_auto` warned only about ignored `--resize-*`/`--sharpen` (never about the ICC flags), so `--to-srgb` on a JPEG-only folder encoded lossless while appearing to honour it. | transcoder | ✅ FIXED (warns once, mirroring `cmd_convert`'s to_jxl warning and `cmd_auto`'s own resize/sharpen warning; a decode/lossy-only run that does honour the profile stays silent. Test: `tests/test_round43_transcoder_fixes.py`) |
| 446 | **A folder-preserving overwrite of an output whose provenance names a different origin was silent.** In modes 1/3 the overwrite path replaces an existing output with no provenance check — the #419 gate only covers `status == "skipped"` — so an output belonging to another origin could be overwritten without a word. | recompressor | ✅ FIXED (warn-only BY DESIGN: `_warn_foreign_overwrite` batch-reads output and source markers and logs a loud warning when they name different origins, then proceeds — on the overwrite path the source is authoritative (the smart-sync contract regenerates it) and a block would refuse every legitimate re-encode; a markerless or matching output stays silent. Test: `tests/test_round43_recompressor.py`) |
| 447 | **Mode-8 multi-page (`jxlphoto-mpg`) groups were replaced page-by-page with no group veto.** The in-place path built `deletables = [it for it in items if not it["in_place"]]`, so each sibling was replaced independently and a failed (or policy-skipped) page still let its siblings be replaced — contradicting the README's "no page is deleted" promise. | recompressor | ✅ FIXED (`_hold_incomplete_in_place_groups` holds every runnable page of a group when a sibling is policy-skipped; at conversion time a group's pages wait until the LAST sibling settles and are then replaced together, or — when any page did not convert — none of them, temps discarded. Only group pages wait: every other in-place file is replaced the moment it settles. The first pass deferred EVERY in-place file to the end of the run — a whole tree's verified re-encodes on disk at once, all work lost and the temps orphaned on Ctrl+C — and was corrected in review; an interrupt now also removes the temps of groups still waiting. Tests: `tests/test_round43_recompressor.py`) |
| 448 | **`_counter["done"]` was never reset between in-process runs.** `_reset_abort()` cleared the delete stats but not the module progress counter, so a second `main()` in the same process started at `[N+1/total]` (the encoder and decoder already zeroed theirs). | recompressor | ✅ FIXED (`main()` zeroes `_counter["done"]` at entry. Test: `tests/test_round43_recompressor.py`) |
| 449 | **`_decoded_in_original_space` returning `None` fell through to pasting the original ICC.** If djxl wrote neither `--icc_out` nor `--orig_icc_out` (an old or odd build), a lossy ICC-blob JXL took the paste-the-original-ICC path — exactly the linear-sRGB trap — behind a DEBUG line, with only `_warn_if_libjxl_too_old` as a safeguard. | decoder | ✅ FIXED (fail-CLOSED: the roundtrip refuses with an explicit libjxl ≥ 0.11.2 / `icc_out` requirement instead of pasting, so a wrong TIFF is never written and the delete gate never fires. Test: `tests/test_round43_decoder_gate.py`) |
| 450 | **Dead `_pre_identity` machinery in the decoder.** `_promote_local` rewrites `write_path` to a uuid `.tmp` immediately, so the pre-identity snapshot condition was unreachable and its cleanup branches were dead code — documenting a protection that did not exist and inviting a regression if the order were changed. | decoder | ✅ FIXED (the dead machinery is removed; the uuid-temp + atomic `os.replace` design is what protects the pre-existing file. Test: the round-43 decoder suite `tests/test_round43_decoder_gate.py`, which drives `process_group`) |
| 451 | **`_delete_stats["kept"]` mixed per-group and per-source units.** The "final missing / never left staging / failed integrity" branches counted per GROUP while the markerless-skipped / incomplete-group / unread-marker branches counted per SOURCE, so the summary line "kept by a gate: N" (documented as files) mixed units. | decoder | ✅ FIXED (every branch counts per SOURCE. Test: `tests/test_round43_decoder_gate.py`) |
| 452 | **Legacy group-id adoption compared sibling names case-sensitively.** `_parse_output_page_suffix(f.stem)[0] == tiff.stem` was a raw comparison while the stale-split check normcases; on Windows a recased sibling (`scan_page2.jxl` vs `Scan.tif`) failed the candidate test, the re-encoded page was stamped with a new id, and the archive split into two truncated groups — the exact failure #318 heals. | encoder | ✅ FIXED (normcase both sides, like the stale-split check. Test: `tests/test_round43_encoder_gate.py`) |
| 453 | **`read_existing_description` parsed exiftool's `-s` output with a line-prefix filter and colon-split heuristics.** The audit (E-4) reported a caption starting with `[minor]`/`[major]`/`Warning:` being dropped; with the real tool that does not reproduce — `-s` prints `Description : value`, so the line never starts with the caption. The first pass of the fix assumed `-s` printed the bare value and returned the line verbatim, seeding the TAG NAME into every new `dc:Description` — passed by the mocked tests, caught in review against real exiftool. | encoder | ✅ FIXED (`-s3`, which prints the bare value; no prefix filter, no colon split — the tool's warnings are on stderr. Pinned by a real-exiftool test that reads `[minor] dust on scan` / `Captured: autumn 1958` back verbatim, and the stubbed tests assert the `-s3` argument. Test: `tests/test_round43_encoder_gate.py`) |
| 454 | **The manifest collision walk applied `--exclude-folders` in directions whose child never receives the flag.** `_excl_names` was computed per row and applied in `_resolve_all` without a direction gate, while `_build_manifest_entry_cmd` emits `--exclude-folders` only for tiff↔jxl. A stale `last_exclude_folders` (saved by a TIFF run, replayed top-level and inherited by every row copy) shrank the walk below the recompressor/transcoder's real file set, hiding a REAL cross-entry collision; replaying it in every direction also raised the spurious "ExcludeFolders ... IGNORED" warning on presets that never asked. | wrapper | ✅ FIXED (the walk fills `_excl_names` only for `(origin, dest) in {('tiff','jxl'), ('jxl','tiff')}`, mirroring the builder, and the session replay carries `last_exclude_folders` only for those directions — killing both the hidden collision and the spurious warning. Test: `tests/test_round43_wrapper.py`) |
| 455 | **The `--exclude-folders` feature-fix batch (F-2..F-8).** F-2: the #297-class test pinned the same exclusion value in the monkeypatch and the workflow, so it could not fail (monkeypatch dropped). F-3: the non-rich wizard prompt ignored its displayed default, so Enter dropped a saved exclusion the docs say is reused. W1-3: `_wizard_select_files` wrote the answer straight into `self.config.config.last_exclude_folders` mid-wizard, so a cancelled wizard persisted the exclusion — violating the #319f staging invariant (now staged on the workflow only). F-4: the legacy no-Mode collision branch did not filter while the child received `--exclude-folders` (a phantom abort); it now shares the direction-gated filter. F-5: a `;`-only / whitespace-only ExcludeFolders cell was an explicit removal; it is now empty-cell semantics (keeps the run's value). F-6: `_session_number_error` only checked the type; it now rejects `\`/`/` paths and requires folder NAMES, refusing at replay (the #319e pattern). F-7: `_exclude_folder_warned` was per menu session, so a second unsupported-direction run stayed mute; it now resets per run as documented. F-8 (the "Excluding folders" cross-reference/indent nit) is docs-only and not part of this fix. | wrapper | ✅ FIXED (Tests: `tests/test_round43_wrapper.py`; F-2 in the updated `tests/test_exclude_folders.py`) |
| 456 | **Hand-edited `last_origin_format`/`last_dest_format` silently routed to the wrong child.** `_session_number_error` never validated the two fields that choose the script: `"Tiff"` (capital T) is truthy, so the `or "tiff"` fallback never fired, every `== 'tiff'` comparison was False, the run routed to the jxl→jpeg branch, and the panel still showed "Source: TIFF / Destination: JXL" (the display uppercases) — indistinguishable from a correct choice, with a run that "succeeded" doing zero conversions. | wrapper | ✅ FIXED (refused at session validation against the known format set — refuse-not-default, the #319e pattern. Test: `tests/test_round43_wrapper.py`) |
| 457 | **A filled `ExportMarker`/`ExportJxlFolder` on a row with an explicit Mode ∉ {6,7} was accepted but dead.** Per-row validation refused `ExportSubfolder` on Mode ≠ 7 yet accepted the other two export columns on any explicit Mode; the builder emitted the flags, but the child's finder in modes 0-5 never consults them. The confirmation panel advertised a scope the child never applied — dangerous with delete, where the user believes a run was limited to `_PRINT`. | wrapper | ✅ FIXED (refused like `ExportSubfolder`, fail-closed; a Mode-less legacy row still accepts. Test: `tests/test_round43_wrapper.py`) |
| 458 | **A commented manifest row with an invalid Mode cell refused the whole manifest.** The Mode cell was parsed before the comment check (the Direction cell was correctly skipped), so `# old row,dest,9.5,dir` aborted the load. | wrapper | ✅ FIXED (comment rows are fully inert; a real row with a fractional or out-of-range Mode still refuses. Test: `tests/test_round43_wrapper.py`) |
| 459 | **The wizard's resize questions accepted `nan`/`inf`/negative values the manifest regex refuses.** `_ask_output_shaping` used raw `float()`/`int()`: `nan`/`inf`/negative percent and non-positive long/short edges were emitted verbatim and failed per file in the child instead of refusing up front. | wrapper | ✅ FIXED (validated the same way as `_MANIFEST_RESIZE_RE` — re-prompt on bad values, while unparseable text still disables resize. Test: `tests/test_round43_wrapper.py`) |
| 460 | **Auto Mode / manifest-generator counts included the recompressor's own output folders and its configured `EXPORT_JXL_FOLDER`.** The mode-6 filter knew only the decoder's names (`16b_tiff`/`tiff_16bits`/`converted_tiff`), so a jxl2jxl preview over an export folder with a past run counted folders the child filters out — the #269/#297 over-count class, fixed only in `_count_origin_files`. | wrapper | ✅ FIXED (`_mode6_preview_skips` asks the recompressor's own `_is_own_output_path` — its output names, its configured `EXPORT_JXL_FOLDER`, the `_JXL_small` suffix — and ONLY for jxl→jxl: the first pass applied those names in every direction, so a jxl→tiff preview under-counted the JXLs the decoder really decodes inside `JXL_small/` and could drop an export folder from a generated manifest (caught in review). Below the root only. Tests: `tests/test_round43_wrapper.py`) |
| 461 | **The collision scan modelled `--rename-from` where the child ignores it.** The scan resolved every jxl→jpeg/png row with rename, but the builder emits the flags only for `jxl_to_jpeg_auto`/`force`/`png`: `jxl_to_jpeg_lossless` never renames and in `auto` a jbrd file is recovered under its original stem. Guard and run disagreed — a silent overwrite (two rows renamed apart that the child writes to the same name) and a refused legitimate run. | wrapper | ✅ FIXED (rename is now refused up front on `jxl_to_jpeg_lossless`, as resize/sharpen already were; in `auto` the scan probes `has_jbrd_box` per file and keeps the original stem for jbrd files. Test: `tests/test_round43_wrapper.py`) |
| 462 | **The derivative-in-place guard read raw manifest modes, not the resolved ones.** `_derivative_in_place_rows(manifest_entries)` saw `Mode=None` on a legacy row whose Destination == Source resolves to mode 0/in-place, so the guard missed it and the run reached the child's exit 2 mid-manifest — after the HHMM token was charged. | wrapper | ✅ FIXED (iterates `resolved_entries`, like the other guards. Test: `tests/test_round43_wrapper.py`) |
| 463 | **A child exiting 130 (SIGINT) was an ordinary failure and the manifest kept launching entries.** Only `rc == 2` and `rc == -1` were fatal; 130 (the recompressor's documented Ctrl+C path) fell into the `else` and the loop continued — an interrupt relaunched children instead of stopping them. | wrapper | ✅ FIXED (130 joins `{2, -1}` as stop-everything. Test: `tests/test_round43_wrapper.py`) |
| 464 | **The abort recap header's buckets did not sum to the total.** Not-started entries counted in `len(entry_reports)` but in no bucket, so a 5-row run aborted at #2 read "5 entries - 1 ok, 1 with failures, 0 cancelled". | wrapper | ✅ FIXED (a separate "N not started" bucket; the parts now sum. Test: `tests/test_round43_wrapper.py`) |
| 465 | **Duplicated helpers were unpinned, defeating the "fix in ALL copies" rule.** `_provenance_marker_args` (encoder/decoder/transcoder), `_read_derived_markers_batch`, `_capture_output_identity` and `_delete_partial_if_written` (transcoder/recompressor) were byte-identical duplicates absent from `SHARED_HELPERS`, so deleting a marker line from one copy left the suite green. | tests | ✅ FIXED (all four added to `SHARED_HELPERS`; `_derivative_metadata_args` documented as a deliberate divergence — different signatures, and only the recompressor has `--output-icc` — and `_move_dest_from_staging` remains unpinnable (nested, with same-name twins). Test: `tests/test_helper_parity.py`) |
| 466 | **The scan-progress behavior contract never ran against the recompressor.** `BACKENDS = [enc, dec, tr]` omitted the recompressor, which defines the same `_scan_state`/`_scan_tick`/`_scan_done` and wires them in `_iter_jxls`/`find_jxls_recursive`, so the silent-25-second-freeze regression the test exists to prevent was behaviorally untested for 1 of 4 backends. | tests | ✅ FIXED (the recompressor is added to `BACKENDS`. Test: `tests/test_scan_progress.py`) |
| 467 | **Export marker and output-folder names were shadowed by literal copies in the wrapper.** The cmd builders passed `--export-marker` only when it differed from `"_EXPORT"`, so a child whose `EXPORT_MARKER` was edited at the top of its script anchored on a DIFFERENT marker than the one the wrapper detected modes and counted files with; and `_export_folder_name` hardcoded `16B_TIFF`/`16B_JXL`/`16B_JXL_small`/`JXL_jpeg`/`JPEG_recovered`, so the delete panel announced the shipped folder name instead of the one an edited script really writes. | wrapper | ✅ FIXED (the wrapper's marker is ALWAYS passed — every child accepts the flag; `_export_folder_name` reads each child's own `EXPORT_*_FOLDER` setting via `_child_setting`, the shipped names surviving only as the fallback when a child cannot be imported. Tests: `tests/test_round43_wrapper.py`, `tests/test_manifest_export_columns.py`) |
| 468 | **Every JPEG/PNG → JXL `--force-convert` run crashed after converting (regression of #444, never released).** #444 made `encode_to_jxl` return the source md5 and the JXL self-hash as two extra tuple members, but `cmd_convert`'s summary loop still unpacked four names: the run raised `ValueError` right after its last file converted — before the summary and the delete gate. The stubbed tests called `encode_to_jxl` alone; the AGENTS.md rule "list every caller before changing a function's signature" was not followed. Caught by the real-photo battery before v2.5.0. | transcoder | ✅ FIXED (the loop reads the result by index, like every other consumer. Test: `tests/test_round43_transcoder_fixes.py::test_real_force_convert_d0_run_completes`, REAL cjxl, the whole script as a subprocess — fails against the pre-fix code) |
| 469 | **The manifest load note said an empty cell meant "not applied" for every option column.** True only for the five derivative columns; an empty `Export*` or `ExcludeFolders` cell keeps the run's value — the opposite. A tiff2jxl manifest with the new columns (all empty, behaviour unchanged) printed that its exclusion and export settings were "not applied". | wrapper | ✅ FIXED (the note names each family with its own empty-cell meaning, and ExcludeFolders' `-`. Tests: `tests/test_manifest_export_columns.py`) |

---

## Round-42 - the lossy ICC-blob decode (2026-09-26)

The open bug the colour measurements of 2026-09-25 ended with: a profile whose
tone curve is a TABLE (ROMM with its linear toe, eciRGB v2, scanner LUTs) has
no native JPEG XL form, so a lossy cjxl stores the whole ICC blob and djxl
returns the pixels in LINEAR sRGB - and three places in the toolkit pasted or
assigned the original ICC on those pixels. Full measurement tables:
`docs/jxl_color_internals.md`, "Lossy JXL with an ICC blob".

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 437 | **A lossy JXL carrying a whole ICC blob decoded with wrong colours, logged as OK.** The decoder's Roundtrip mode pasted the XMP ICC on djxl's linear-sRGB output (15.4 dB on the ROMM-toe test photo, vs 51.9 dB for a correct float decode + conversion); the recompressor's `_derive_pixels` and the transcoder's `_source_profile_args` made the same mistake in every derivative/colour conversion of such a master; and the encoder's cautious test only checked the mean brightness, so a table-curve profile could slip through as "embed" | decoder, recompressor, transcoder, encoder | ✅ FIXED (the case is detected from djxl itself: `--icc_out` vs `--orig_icc_out` on the SAME decode are byte-identical in every correct case and differ only here - shared `_djxl_icc_args`/`_decoded_in_original_space`, parity-pinned in all four scripts. Roundtrip then decodes to a float PFM and CONVERTS from djxl's profile to the original one (alpha, not colour-managed, comes from the integer decode; no magick on PATH fails closed, so a wrong TIFF is never written and the delete gate never fires). The derivative paths switch their magick input to the float PFM with djxl's own profile assigned, and a keep-the-space derivative is converted back to the original ICC instead of being re-assigned linear sRGB; an ICC blob with an alpha channel is refused there. The cautious test refuses "embed" for a lossy ICC blob whatever the brightness, and the icc_cache key gained `:t=2` so every profile is retested once. Tests: `tests/test_icc_blob_decode.py` + `tests/_icc_fixtures.py` (a ROMM-toe profile built from scratch) - 14 real-codec tests, the six bug-facing ones verified failing against the pre-fix scripts (bug path 15.3 dB, fixed 52.4 dB on the fixture; blob derivatives at parity with the native-path control) and Elle-g2.2/"skip" controls byte-identical to before) |
| 438 | **The same bug on GREY masters, in the derivative paths.** A grey master with a table-curve grey profile (Photoshop "Dot Gain"-style, scanner LUTs) also decodes to LINEAR grey, but the #437 derivative fix only covered RGB: the recompressor encoded djxl's linear grey and kept the copied XMP ICC (a later decode pasted it on those pixels), and the transcoder delivered the PNG/JPEG in djxl's linear grey instead of the master's profile (8-bit linear bands). Measured on the fixture: 14.7 dB off | recompressor, transcoder | ✅ FIXED (the grey blob case takes the same float PFM path and converts back to the file's own grey profile - djxl's `--orig_icc_out`, always single-channel; grey is still never colour-converted to `--output-icc`. The decoder already handled grey. Tests: 4 grey real-codec tests in `tests/test_icc_blob_decode.py` (`grey_toe_icc` in `_icc_fixtures.py`); the three derivative ones verified failing on the pre-fix scripts) |

---

## Round-41 — resize/sharpen round (2026-09-25)

The output-shaping round: `--resize-long/-short/-percent` and `--sharpen
none|screen|print` on the transcoder's JXL → JPEG/PNG direction and on the
recompressor's derivatives. The first fix below is an independent colour bug
found while measuring the new pipeline against real photos.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 436 | **A colour conversion could silently re-tag instead of converting (trap B1).** For a JXL encoded as sRGB, djxl writes a PNG with an `sRGB` chunk and NO `iCCP`; `magick -profile <target>` then ATTRIBUTES the target profile to already-sRGB pixels instead of converting from them. Measured on a synthetic saturated gradient (IM 7.1.2 Q16-HDRI): the output was identical to a plain assignment (PSNR 120) and 35.7 dB away from the correct conversion — every `--to-srgb`/`--icc-profile` delivery of an sRGB-encoded master was wrong in silence. The recompressor already assigned the source profile explicitly; the transcoder had only ever been exercised against ProPhoto/XMP masters, where `_copy_metadata`'s CreatorTool blob masked the hole | transcoder | ✅ FIXED (`_source_profile_args()` ported from the recompressor's rule — XMP CreatorTool ICC > the decoded PNG's `iCCP` > its `sRGB` chunk > refuse to guess — and prepended to the target profile in both magick branches; the output must now carry its profile (`iCCP` for PNG, `ICC_Profile:ProfileDescription` for JPEG) or the file is an error. The three helpers were added to `SHARED_HELPERS`. Test: `tests/test_transcoder_source_profile.py`, real codec — PSNR ≥ 60 dB against the correct conversion, < 50 dB against a plain re-tag; verified failing against the pre-fix script) |

---

## v2.2.0 — the distance floor is per cjxl version (2026-09-24)

Re-measured at the user's request: the "floor at 0.05" documented since v1.9.0
was measured on cjxl 0.12 only. libjxl 0.12 added it
([PR #4238](https://github.com/libjxl/libjxl/pull/4238): below 0.05 the DC
coefficients leave `int16` and the bitstream leaves Level 5); cjxl 0.11.2 clamps
only below 0.01.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 435 | **The lossy distance floor was hard-coded at 0.05 for every cjxl.** Measured on two real 16-bit photos: cjxl 0.12.0 writes byte-identical files from `--distance 0.005` to `0.05`, but cjxl 0.11.2 only merges 0.005 and 0.01 — 0.02/0.03/0.04 are real steps (d=0.01: 60.4 dB PSNR vs 51.7 dB at 0.05, 1.75× the size). On 0.11 the encoder/recompressor/transcoder warned that 0.02 "behaves exactly like 0.05" (false), the encoder's space preflight modelled a 0.05 file for a 0.02 request, and the recompressor's `_classify` read a 0.05 request over a real d=0.02 source as "same distance" (policy copy/ask instead of a legitimate shrink) | encoder, recompressor, transcoder | ✅ FIXED (`_min_effective_distance(exe)`, parity-pinned in all three backends: 0.05 for cjxl ≥ 0.12 or an unknown version, `_MIN_EFFECTIVE_DISTANCE_PRE_012 = 0.01` before it. `_warn_distance_clamp` takes the floor as a parameter; `_classify(..., floor=)` defaults to 0.05 and `main()` passes the installed cjxl's. The record does not say which cjxl wrote a source, so an old d=0.02 file under 0.12 still reads as "same distance" — the conservative side. Tests: `tests/test_distance_floor.py`, including a real-codec check that the detected floor matches the installed binary, run against cjxl 0.11.2 and 0.12.0) |

---

## Round-40 audit (2026-09-23)

The fifth audit of v2.1.1_beta1, from `bug_report_260923.md`. Sixteen fixes in
two batches: batch A below (419-423) covers the high/medium findings, batch B
(424-434) the low-severity ones. Headline: the recompressor could delete
an original JXL under `--delete-skipped` on the strength of a valid, newer,
same-named output written by an UNRELATED photo — the only reproduced data loss
of the audit, and it happened in the folder-preserving modes (1/3) that never
had a provenance gate. The other three: `--repair-jbrd` gave up after stripping
one marker pair, leaving repairable files reported STILL BROKEN; the decoder
treated an ordinary photo whose stem ends in `_thumbnail` as a reduced-resolution
thumbnail under `--no-reconstruct-multipage`; and the dry runs still labelled
would-SKIP pairs as conversions (the encoder's #409 fix had not reached the
decoder, and the transcoder/recompressor never applied it to the per-file tag and
topline).

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 419 | **`--delete-skipped` deleted a source on the strength of an unrelated same-named output (real data loss).** For `status == "skipped"` with `action == "convert"` the only gate in the non-collapsing modes was `exists() + _verify_jxl_integrity`; the provenance gate ran only when `_run_collapses_structure()` was true (modes 0-with-output/2/4-8). Modes 1/3 deleted the source even when the pre-existing output was a valid, newer archive of a DIFFERENT photo | recompressor | ✅ FIXED (the skipped-convert path in `_delete_gate` now batch-reads the `jxlphoto-src`/`jxlphoto-srcsum` markers of output and source — one exiftool call for the whole run — and requires `_markers_match(..., PROVENANCE_CHECK)`; an unreadable/absent marker fails CLOSED, keeping the source. A skipped COPY is still proven byte-for-byte by the MD5 gate. The dry-run `--delete-skipped` preview mirrors the new gate, so a refused delete is no longer counted as "would delete". The README documents the new proof and its fail-closed behaviour) |
| 420 | **`--repair-jbrd` stripped only the last marker pair.** `_read_source_markers_batch` keeps only the LAST `jxlphoto-src`/`srcsum` value, and the strip routine removed exactly that pair and checked the same id — a second pair with a different id survived, the reconstruction still failed, and a repairable file was reported STILL BROKEN (defeating `--auto-repair-jbrd` too) | transcoder | ✅ FIXED (a local `_read_all_source_marker_values` returns EVERY marker value; `_strip_provenance_markers` removes one `-XMP-dc:Relation-=<token>` per distinct value and the post-check asserts NO `jxlphoto-src`/`srcsum` token remains. `_read_source_markers_batch` is untouched, so the pinned helper parity holds) |
| 421 | **A normal photo named `*_thumbnail` decoded as a thumbnail under `--no-reconstruct-multipage`.** `_has_internal_markers` is true for ANY `jxlphoto-*` marker — and `jxlphoto-depth` is written to every encoder output — so the filename suffix decided the page role alone: `photo_thumbnail.jxl` became SubFileType=1, and with `--thumbnail-handling ignore` nothing was written at all | decoder | ✅ FIXED (the suffix is trusted only when the file also carries a `jxlphoto-page`/`jxlphoto-group` marker — a legacy split page; a standalone with only depth/grayscale decodes as an ordinary photo, mirroring the reconstruction branch. `_has_internal_markers` is left intact; docs updated) |
| 422 | **Dry runs labelled would-SKIP pairs as conversions** (recompressor + transcoder `cmd_transcode`/`cmd_convert`/`cmd_auto`). The per-file tag and the `ok`/`skipped` topline counted the planned action, not the SKIP `should_process`/`_would_skip` would report, so a simulation over a folder with existing outputs was an upper bound the real run never reached — and the wrapper sums those toplines | recompressor, transcoder | ✅ FIXED (all four previews apply the same skip predicate the worker uses, log `DRY \| SKIP (exists/up to date)` and count the pair as skipped, not converted) |
| 423 | **`cmd_auto` counted provenance-refused pairs in `ok`** — `ok=len(all_pairs)` used the pre-`_provenance_filter` list (bug #15); **and the decoder's dry-run topline ignored smart-sync / existing-output skips**, so `ok=1, skipped=0` where the real run reports `ok=0, skipped=1` (the encoder's #409 fix never reached the decoder). Contaminated the wrapper's manifest recap, which sums the toplines | transcoder, decoder | ✅ FIXED (cmd_auto reports the post-filter per-group tally; the decoder's dry run computes `_sync_skips` with the existing `_would_skip_group`, subtracts them from `ok` and adds them to `skipped`, excluding provenance-refused groups) |

Batch B — the audit's LOW-severity items (report items #6–#22; #15 was folded
into 423 above). Left for a later round by design: #8, #12, #13, #14, #20, #23
and the wrapper minors W1–W3.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 424 | **A single-page TIFF whose only page is SubFileType=4 (MASK) lost the role on round-trip** — the `jxlphoto-subfiletype` writer was gated on `page_idx > 0` and the group block only ran for multi-output groups, so the JXL carried no marker and the decoder wrote SubfileType=0 back (also under `--multipage-mode ignore`) | encoder | ✅ FIXED (page 0's marker is written whenever the subfiletype differs from the role default; real-codec round-trip test proves SubFileType=4 survives) |
| 425 | **Spurious `Output outside input tree` warning for a single-FILE run in decoder modes 4/5** — the anchor was the file's own parent, whose designed output IS a sibling; the encoder's item-29 fix never reached this copy | decoder | ✅ FIXED (single-file input anchors on the parent's parent, mirroring the encoder) |
| 426 | **`_verify_tiff_integrity` only force-decoded the LAST page** — external damage in an earlier page of a multi-page TIFF passed the delete gate | decoder | ✅ FIXED (every page is decoded; decoder-only helper, no parity impact) |
| 427 | **`_preflight_space` TOCTOU: `stat()` without a guard after `exists()`** — a source vanishing between the plan and the staging estimate escaped `main()` as a raw traceback; `convert_one`'s error handler re-`stat()`ed a vanished source out of its own except block | recompressor | ✅ FIXED (both sites guard OSError, mirroring the first preflight loop) |
| 428 | **`--provenance` silently inert without `--delete-source` (transcoder)** — the recompressor's round-39 #413 warning never reached this copy | transcoder | ✅ FIXED (same explicit warning, same condition) |
| 429 | **The output positional was silently discarded in transcoder modes 3-8** (mode 1 already warned) — parity gap with the recompressor, which warns in every non-0/2 mode | transcoder | ✅ FIXED (warning mirrored to every applicable mode) |
| 430 | **The encoder's `reorder_jxl_boxes` lacked the `OverflowError` guard** its transcoder/recompressor copies carry for a re-headered size-0 box near 4 GiB | encoder | ✅ FIXED (guard ported; the three copies are identical again. The helper stays out of `SHARED_HELPERS` — the parity test's normalisation does not cover it) |
| 431 | **The encoder never reset per-run module state** — a second run in the SAME process inherited the progress counter, discard counters and the discard source sets (fail-closed: KEEP, never a wrong delete) | encoder | ✅ FIXED (`main()` resets all per-run state, mirroring the decoder) |
| 432 | **The delete confirmation was charged on an empty / all-skip plan** — a no-TTY re-run of an already-archived folder with `--delete-source` (without `--delete-skipped`) asked for a token it could not answer and exited 3 forever, deleting nothing | transcoder, recompressor | ✅ FIXED (the prompt only fires when the plan actually holds a deletion; fail-closed — any doubt still prompts; `--delete-confirm-off` behaviour unchanged, so the wrapper is unaffected) |
| 433 | **`--export-marker ""` silently kept the default** — `if args.export_marker:` treated an explicit empty string (export NOTHING) as absent | recompressor, transcoder | ✅ FIXED (`is not None`, mirroring `--export-subfolder`; the empty marker then matches nothing, fail-closed via `_marker_matches`) |
| 434 | **`_parse_encode_params` was dead code** — superseded by `_read_encode_params_batch`; only a test still called it | recompressor | ✅ FIXED (function removed; the test now exercises the batch reader) |

Regression tests: `tests/test_audit_round40.py` (18 tests, all mocked) plus three
real-codec tests in `tests/test_audit_round36.py` (#1 encode/decode,
#2 recompressor delete-skip, #3 multi-pair repair; skipped without
cjxl/djxl/exiftool). Proven against the pre-fix code (`git show HEAD:<script>`
copies run from a temp dir): the four positive controls pass and all fourteen
regression tests fail; the three real-codec tests fail as well (the #3 one with
`assert 'broken' == 'repaired'`). Batch B: `tests/test_audit_round40b.py`
(21 tests), two #9 tests in `tests/test_audit_round30.py` and one real-codec
#6 test in `tests/test_audit_round36.py`; proven against the same HEAD
extraction — 16 failed / 5 passed (positive controls), the #9 pair fails, and
the #6 real-codec test fails.

---

## Round-39 audit (2026-09-21)

Fourth audit of v2.1.1_beta1 — two independent reports (`20260921_audit2.md`,
`20260921_audit3.md`) consolidated into `20260921_audit_consolidated.md` with
every claim re-verified against the working tree (five reproduced by execution
with cjxl/djxl 0.12.0 + exiftool 13.59). 34 findings. Headline: `--force-convert
--distance 0` broke jbrd JPEG recovery with a real Lightroom XMP payload AND its
delete gate never tested reconstruction — the original was deleted and the
archive left unrecoverable, the exact v2.0.0 data-loss class the markers were
introduced to prevent. Two claims were investigated and REJECTED (the decoder
preview `shutil.move` targets a temp, not the final TIFF; `_strip_encode_params`
preserves a user caption ending in `gen=N`). All fixes verified against the
pre-fix code and against real photos (`E:\TESTE` copies: the 3-page RGB+IR scan,
Camera-One 16-bit exports) — 1447→1454 tests passing, helper parity green.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 385 | **`--force-convert --distance 0` destroyed the original with a broken reconstruction.** `_copy_metadata` re-serialized XMP INSIDE a jbrd container (breaking `djxl --reconstruct_jpeg` bit-exactness), and the convert delete gate was structural-only — it never ran the reconstruction proof the transcode gate has. Reproduced: jbrd True → reconstructs False → gate deletes anyway | transcoder | ✅ FIXED (`_copy_metadata` now sits inside the same `if not has_jbrd_box(...)` guard as the provenance markers; the full transcode gate — jbrd + `_jxl_reconstructs_to` bit-exact + fail-closed with djxl < 0.12 — ported to the convert gate and to `cmd_auto`. Real-codec proof: Lightroom XMP written by exiftool survives the whole path bit-exact; a deliberately re-serialized jbrd fails `_jxl_reconstructs_to` and the gate refuses) |
| 386 | **A JPEG with a >64 KiB trailer was deleted by the encode gate and then rejected by the decoder itself** — the EOI scan only looked at the last 65536 bytes while the comment named exactly the files that overflow it (Motion Photos, appended thumbnails) | transcoder | ✅ FIXED (the JPEG branch walks BACKWARD from EOF in 1 MiB chunks until it finds EOI — trailer-after-EOI is legitimate, no EOI anywhere is not. Tested: truncated → refused, 3 MB without EOI → refused, EOI straddling the chunk boundary → accepted, 200 KB trailer → full round trip bit-exact) |
| 387 | **The smart-sync skip never checked the `jxlphoto-src` marker** — a master TIFF (no marker) with mtime ≥ its JXL was "up to date", admitted to `--delete-skipped`, and the JXL — which is NOT its decode — was deleted on the strength of an unrelated file | decoder | ✅ FIXED (the skip branch runs `_decode_output_is_ours`; no marker ⇒ status `refused`, never `skipped` — plus a belt in the delete gate itself: a `skipped` source whose TIFF carries no marker is kept fail-closed. Real-codec proof: the master TIFF is refused in dry-run preview AND in the real run, JXL survives) |
| 388 | **Multi-page marker reads were fail-open** — timeout, rc≠0 and exceptions all degraded the batch to standalone pages; every page decoded to a valid own TIFF and the gate deleted the group, losing SubfileType (the IR page became a common page), inherited ICC, grayscale/depth. The recompressor's twin was closed fail-closed in #363; the decoder stayed open | decoder | ✅ FIXED (a run-level `_mpg_marker_failures` set marks every unread batch — rc≠0 counts even when exiftool emits no JSON `Error` key — and the gate keeps ALL JXLs of affected groups with a warning; the dry-run preview mirrors it. Real-codec proof: with a failing exiftool, `kept by a gate: 1`, nothing deleted; with a real exiftool the RGB+IR group merges and `--delete-skipped` deletes legitimately) |
| 389 | **TIFF MINISWHITE was archived tonally inverted, silently** — photometric 0 missed the rejection list while its siblings (SEPARATED, PALETTE, Lab, YCbCr) were all refused; `page.asarray()` returns raw samples (0=white) and the JXL came out MINISBLACK | encoder | ✅ FIXED (MINISWHITE rejected like the others via `_log_rejected_file`; inversion in the reader was rejected as the riskier fix. Real-codec proof: a real photometric-0 TIFF is refused with a clear reason; MINISBLACK control still encodes) |
| 390 | **`cmd_auto` PNG-only + `--distance 0` deleted with no confirmation at all** — `has_lossy` and `has_lossless` were both False, so no `confirm_deletion_*` ran | transcoder | ✅ FIXED (d=0 counts as lossless like `cmd_convert` already did: PNG-only d=0 asks `confirm_deletion_jpeg`. Real-codec proof: the run cancels at the prompt instead of deleting silently) |
| 391 | **`--delete-skipped` + action `copy` skipped the MD5 proof** — for a pre-existing output (`processed_this_run=False`) the source was deleted on the integrity check alone, though proving the copy byte-exact is trivial exactly there | recompressor | ✅ FIXED (the copy gate fires on the action regardless of who produced the output; no proof ⇒ keep, fail-closed) |
| 392 | **A `Software` read failure left the OLD lineage chain migrate into the JXL** — rc≠0 → `original_sw = ""` → no `-Software=` cleanup → `-tagsfromfile` copied the stale chain beside the fresh Description; the recompressor later merged the two contradictory records and inflated `gen` | encoder | ✅ FIXED (a failed read raises and the file errors out — fail-closed like the rest of the lineage path. The "empty stdout with rc=0" case is deliberately NOT a failure: that is a normal new TIFF with no Software tag) |
| 393 | **A bare-codestream source + `d=0` errored per file, guaranteed** — `--container=1` was conditional on distance, the naked output made the restamp's exiftool refuse every file | recompressor | ✅ FIXED (`--container=1` unconditional. Real-codec proof: a real `ff0a` codestream recompresses to a container, restamps, verifies — 0 errors. The keep-smaller copy of a bare SOURCE still refuses at the delete gate, which is the documented rule, not a regression) |
| 394 | **The preflight did not know `exiftool(-k).exe`** — `shutil.which("exiftool")` only, while `_get_exiftool_cmd()` accepts the stock Windows download names; the script exited "Missing required tool(s)" with the tool on PATH | recompressor | ✅ FIXED (the preflight resolves through `_get_exiftool_cmd()`, same as the other three scripts) |
| 395 | **The mode menu lied about JXL→JXL in-place** — mode 0 said "side by side in same folder" and mode 8 "originals kept" while the recompressor REPLACES the source; the correct text existed only in the `[?]` screen, after the decision point | wrapper | ✅ FIXED (modes 0/8 say the source is replaced when the conversion is JXL→JXL, in both the wizard and the manual list; the stale "nothing destroys anything" comment rewritten) |
| 396 | **The manifest continued after `rc == -1`** — a child killed by the idle-timeout or a failed launch was just "failed", and every following entry relaunched into the same fault mid-delete/mid-encode | wrapper | ✅ FIXED (`rc == -1` aborts the rest of the manifest via the same fatal path as exit 2, with its own `killed` state and a message that does NOT promise "nothing was deleted" — the child may have died mid-delete) |
| 397 | **Manifest CSV relative paths resolved against the wrapper's CWD** — under Task Scheduler that is `C:\Windows\System32`; the run scanned/deleted there or found nothing and reported `ok=0, exit 0` | wrapper | ✅ FIXED (source/dest are anchored on the CSV's folder in the loader, `..` still refused, and the error message no longer suggests relative paths are fine) |
| 398 | **The cross-entry collision guard never compared A's output with B's Source** — output×output and Source×Source were walked, but an entry whose Source IS another entry's (still nonexistent) output folder recompressed freshly-created files, losing a generation | wrapper | ✅ FIXED (`_manifest_output_source_collisions` runs the same walk with the output-tree resolver and refuses/warns like the other guards; the canonical case from the report is flagged) |
| 399 | **The distance dead zone was not applied in `_classify`** — nominal comparison let `--distance 0.05` "upgrade" a `d=0.02` source that cjxl clamps to a byte-identical output, paying a lossy generation for nothing | recompressor | ✅ FIXED (effective distances: `max(d, _MIN_EFFECTIVE_DISTANCE)` for d>0, d=0 stays 0 — same-distance now falls back to a verbatim copy as the README promises, with a clamp note in the message) |
| 400 | **The mode-4 fallback output folder escaped the own-output skip** — `<nome>_JXL_small` is not one of the four exact names in `_RECOMPRESSOR_OUTPUT_FOLDERS`, so a recursive re-run ate its own output | recompressor | ✅ FIXED (`_is_own_output_path` also recognizes the `_jxl_small` suffix, below the input root only; the wrapper mirror got the same shape locally so it stays closed even if the child regresses) |
| 401 | **A nonexistent input exited 0** — `is_file()` false → treated as a folder → rglob over nothing → warning + success, so a typo in an unattended preset read as a clean run | recompressor | ✅ FIXED (`parser.error("input path does not exist...")`, same contract as the decoder and encoder) |
| 402 | **`_merge_lineage_blocks` read only the first `gen=`** — the round-38 fix (#373) reached `_reconcile_gen` but not the merge used by the reader and the restamp; a restamp rebuilt the chain with `gen=1` and the append rewrote `gen=5` out of the file — loss in the downward direction | recompressor, encoder | ✅ FIXED (`max()` over all tokens per field in BOTH copies, AST-identical — parity test green; verified by execution: `'gen=1 | ... | gen=5 | ...'` now merges to stored=5, matching `_reconcile_gen`) |
| 403 | **A mode-99 manifest repeat discarded `last_mode_config` instead of validating it** — a preset saved with a custom export marker silently ran with the current global marker | wrapper | ✅ FIXED (mode 99 preserves `export_marker`/`export_subfolder`; the stale "always empty on a mode-99 run" comment removed) |
| 404 | **A single-FILE run warned "Output outside input tree" per file in modes 4/5** — the encoder and recompressor had the anchor fix, the transcoder inferred `single_file` from `input_root.is_file()` which never fired (the cmds pass the file's PARENT), and `resolve_output_convert` never got any fix | transcoder | ✅ FIXED (explicit `single_file` flag on both resolvers — recompressor pattern — because the callers disagree on what root means: cmd_transcode/cmd_convert pass the parent, cmd_auto passes the file. Proven against the pre-fix code and via the real CLI on both paths; folder runs still warn) |
| 405 | **The provenance-marker write ignored exiftool's rc** — a failed marker write still returned `copied=True` at debug level, so the TIFF read as an original master next run and the decoder refused to re-sync it | decoder | ✅ FIXED (rc checked: a failed write is a warning and `copied=False`; the except path degrades the same way) |
| 406 | **A metadata-failed output was promoted to the final path BEFORE the `meta_ok` verdict** — the gate held the JXL that run, but the fresh-mtime TIFF made the next run classify `SKIP (sync)` and `--delete-skipped` deleted the JXL, the only copy of those metadata | decoder | ✅ FIXED (the verdict runs before promotion: a failed-metadata output is discarded, the previous final keeps its old mtime, the JXL is retained) |
| 407 | **`checksums.md5` reads were unguarded** — no `try`, no `utf-8-sig` (a Notepad BOM mismatched the first token forever), and the `md5_of_file` in `_jxl_binds_to_archived_jpeg` sat OUTSIDE the function's try: one locked or non-UTF8 file killed the whole run without a summary | transcoder | ✅ FIXED (both readers: try/except + `utf-8-sig`, failure reads as None — fail-closed KEEP; the stray `md5_of_file` moved inside the try, the spot rounds 37/38 missed) |
| 408 | **`--rename-from/--rename-to` were silently ignored on transcode paths** — the convert applied them, the transcode accepted-and-dropped them, and the docs listed them unscoped | transcoder | ✅ FIXED (explicit warning on both transcode call sites, scoped per group in `cmd_auto`; the README now says the flags are convert-only) |
| 409 | **The "dry-run lies" family, round-38 leftovers** — the recompressor counted would-be-skipped outputs as deletions (`_would_skip` had one call site), the transcoder's convert/auto reported `errors=0` under provenance refusals, the decoder's preview ignored `_would_refuse`/`--matrix` and counted an incomplete group as 1 kept, the encoder's `ok=len(all_items)` ignored smart-sync skips | all four scripts | ✅ FIXED (each topline now models what the real run does: recompressor excludes `_would_skip` unless `--delete-skipped` widens the gate; transcoder convert/auto carry `errors=len(_refused)`; decoder preview excludes refusals/matrix and counts real kept JXLs, batch-reading markers; encoder counts sync skips via the same condition `convert_one` uses) |
| 410 | **The "temp with its real name in a scanned folder" family** — beside-final temps `<32hex>_<final>.<ext>` that an external kill orphaned were adoptable as real inputs next run; no sweep covered them (only `TEMP2_DIR`), and the recompressor's own docstring admitted it | all four scripts | ✅ FIXED (every beside-final/in-place temp wears a non-final `.tmp` suffix — `os.replace` does not care, readers are content-driven; the recompressor's REPLACE-FAILED paths clean the temp up; `cmd_auto`'s encode temp goes through the same rule) |
| 411 | **`--repair-jbrd --dry-run` wrote beside the source** — the proof copy was written BEFORE the dry-run branch, so a read-only archive marked every file STILL BROKEN despite "nothing is written" | transcoder | ✅ FIXED (the dry run writes its copy to `TEMP_DIR`, like the reconstruction temp) |
| 412 | **`_auto_repair_copy` ignored `TEMP_DIR` and named its temp `.jxl`** — the v2.1.1 changelog claimed both were fixed, but only `_repair_one_jbrd` was | transcoder | ✅ FIXED (`dir=TEMP_DIR` with system-temp fallback, `.tmp` name) |
| 413 | **`--provenance` was silently inert without `--delete-source`** — assigned, read only inside the delete branch, no warning (unlike `--delete-skipped`, which has one) | recompressor | ✅ FIXED (explicit warning at startup, mirroring the `--delete-skipped` one) |
| 414 | **The repeat/preset panel showed `Quality` for presets that never receive it and omitted `Distance` for JXL→JXL** — the dead knob advertised, the live one hidden, right before "Proceed?" | wrapper | ✅ FIXED (the panel reuses `_describe_session`'s direction filter: Distance for JXL→JXL, Quality only where it is sent) |
| 415 | **`save_config()` was non-atomic and `--list-presets` rewrote the documented read-only config** — two instances or a mid-dump crash corrupted config+presets; the loader then dropped everything to defaults with one warning. The children got temp+replace+lock in v2.1.1; the wrapper did not | wrapper | ✅ FIXED (`save_config` writes to a temp and `os.replace`s it; `check_dependencies` takes a `persist` flag and `--list-presets` runs with `persist=False` — verified byte-identical after a real run) |
| 416 | **`repair_jbrd_flow` did not strip quotes from the typed path** — Explorer's "Copy as path" (which quotes) gave "Folder not found" for a folder that exists, while every other wizard prompt strips | wrapper | ✅ FIXED (both prompts pass through `_strip_surrounding_quotes`) |
| 417 | **The mode-99 fix left stale comments and an incomplete panel** — covered by #395, #403, #414 above | wrapper | ✅ FIXED (see those entries) |
| 418 | **Doc↔code divergences (all confirmed):** `gen≥1` vs `gen >= 2`; positional output "modes 0, 2, 5" vs 0-and-2; a `--jpeg_quality` forwarding claim in the jbrd path that does not exist; decoder positional "mode 0 only" vs 0-and-2; `DELETE_CONFIRM` described as mode-8-only; log names without the `_<pid>` suffix | docs | ✅ FIXED (all four script READMEs + the tools README corrected; `tests/test_docs_cover_the_flags.py` green. The round-38 #372 gen≥2 text was re-checked and stands) |

Regression tests: `tests/test_audit_round39_decoder.py` (21), 
`tests/test_audit_round39_transcoder.py` (30), `tests/test_audit_round39_item29.py`
(7) — every behavioral fix proven against the pre-fix code (extracted with
`git show HEAD:<script>` / file snapshots, never `git stash`), plus real-codec
verification for items 1, 2, 3, 4, 5, 6, 9 and 22 against actual photos
(Lightroom-style XMP via exiftool, a 200 KB-trailer JPEG, the E:\TESTE 3-page
RGB+IR scan with a simulated exiftool failure, a real MINISWHITE TIFF, a real
bare codestream). The reviewer's post-fix re-verification (by reading and by
execution) confirmed all 34 closed; its one new finding — the decoder's
`_would_skip_group` dry-run mirror missing the #387 fix — was closed the same
day with a pre-fix proof, and item 29's half-fix (the `is_file()` inference
that never fired) was replaced by the explicit `single_file` flag.

---

## Round-38 audit (2026-09-21)

Third audit of v2.1.0, from `bug_report_260921.md` — 28 findings across all
four scripts plus the wrapper, several reproduced by execution against the
real fixtures. Headline: the encoder archived TIFF Lab/YCbCr as silently
inverted RGB, and the decoder's `--matrix --delete-source` discarded the alpha
channel and then deleted the source. All fixes ship in v2.2.0 (the v2.1.1 beta line).

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 357 | **TIFF Lab/YCbCr was archived as silently inverted RGB** — tifffile returns raw samples; the encoder fed them to cjxl as if they were RGB | encoder | ✅ FIXED (the photometric gate rejects CIELAB/ICCLAB/ITULAB, SEPARATED, PALETTE and YCBCR with a per-file reason, same fail-closed shape as every other unsupported photometric) |
| 358 | **`--matrix --delete-source` discarded the alpha and deleted the source** — the matrix pipeline decodes through PPM, which has no alpha channel, yet the delete gate certified the output | decoder | ✅ FIXED (matrix mode cannot prove alpha reached the TIFF, so `--delete-source` under `--matrix` ALWAYS keeps the sources — fail-closed, with the remedy named: use roundtrip/basic for deletable decodes. Verified against a real RGBA encode: dry run says "would DELETE NO source", real run keeps the JXL; documented in the decoder README) |
| 359 | **A source named `*_page<N>.tif` came back with the wrong name** — the split-name heuristic treated the source itself as a page of a split | decoder | ✅ FIXED (page detection distinguishes real split members from a source that merely carries the suffix; the decode writes the source's own stem) |
| 360 | **`--provenance path\|content` was inert in the recompressor** — accepted, never consulted | recompressor | ✅ FIXED (wired into the delete path; without `--delete-source` it now warns explicitly instead of silently doing nothing) |
| 361 | **A grayscale page with its own RGB ICC failed every encode** — cjxl refuses an RGB profile on grayscale samples | encoder | ✅ FIXED (the page's ICC is checked against its channel count and dropped/handled before cjxl runs, with a log) |
| 362 | **`--none`: an EXIF copy failure was ignored and the gate deleted the source** | decoder | ✅ FIXED (the none-mode metadata copy checks its result; a failed copy is an error and retains the JXL) |
| 363 | **A multi-page marker read failure zeroed the group veto** — unreadable markers degraded the group to standalone pages, each deletable on its own | recompressor | ✅ FIXED (`mpg_complete=False` on any marker-read failure: nothing in the affected run is deleted — the same recipe the decoder adopted in #399) |
| 364 | **A JXL→JXL manifest preset hit the HHMM gate in unattended runs** | wrapper | ✅ FIXED (unattended manifest runs carry the confirmation state instead of falling into the interactive gate) |
| 365 | **A single output derived from page > 0 lost its markers** | encoder | ✅ FIXED (the encode record carries the provenance markers regardless of which page seeded the output) |
| 366 | **`--export-marker ""` raised IndexError** | recompressor | ✅ FIXED (empty marker is guarded — `if not marker_lower` short-circuits the match logic) |
| 367 | **`--workers 0` raised a traceback** | recompressor | ✅ FIXED (validated with `args.workers < 1` at argument time) |
| 368 | **`--jbrd-policy convert` had no per-file log** (the README promises one) | recompressor | ✅ FIXED (the convert decision logs per file) |
| 369 | **The dry run counted refused sources as converted/deleted** | recompressor | ✅ FIXED (refusals are excluded from the simulated toplines) |
| 370 | **The deletion count never reached the wrapper's red panel** (it fell into the dim generic line) | wrapper | ✅ FIXED (the recap carries the deletion count into the panel) |
| 371 | **False "Output outside input tree" in modes 4/5 with a single-FILE input** — the resolver anchored on the file itself | recompressor, encoder | ✅ FIXED (`anchor = input_root.parent if single_file` / `input_root.parent.parent` when the resolver receives the file — the transcoder got the same shape only in round 39, see #404) |
| 372 | **The `--on-regeneration` help said `gen>=1`** (the code uses 2, deliberately — encoder outputs are born at gen=1) | recompressor | ✅ FIXED (docs corrected to gen ≥ 2; re-checked in round 39's docs sweep) |
| 373 | **`_reconcile_gen` read only the first `gen=` token** — a chain with multiple gen entries sub-counted generations downward, the direction `--on-regeneration` does not forgive | recompressor | ✅ FIXED (`findall + max` over every token; the round-39 sweep extended the same fix to `_merge_lineage_blocks` in both copies, see #402) |
| 374 | **Manifest mode 0 with a file Source and Destination pointing at the file became an output positional** | wrapper | ✅ FIXED (the manifest builder no longer turns that pair into a positional) |
| 375 | **`_manifest_output_collisions` ignored rows with a file Source** | wrapper | ✅ FIXED (file Sources take part in the collision walk) |
| 376 | **Auto mode picked the wrong sibling folder for JXL→JXL in modes 4/5, and a JPEG→JXL lossless preset carried `q=`** | wrapper | ✅ FIXED (both builder bugs corrected) |
| 377 | **`native.icc` was reused across group pages** — page 2 inherited page 1's profile | decoder | ✅ FIXED (the native profile is resolved per page) |
| 378 | **The `--delete-source` dry run counted groups that would be refused; ignored thumbnails stayed out of the kept count** | decoder | ✅ FIXED (the preview excludes would-be refusals and counts kept JXLs; round 39's #409 completed the mirror) |
| 379 | **`--repair-jbrd` had no per-file try/except and no `--summary-json`** | transcoder | ✅ FIXED (per-file exception handling and a summary payload) |
| 380 | **`md5_of_file` unprotected in the delete gate — one locked file killed the whole run** | transcoder | ✅ FIXED (partially here, the last spot in `_jxl_binds_to_archived_jpeg` moved inside the try in round 39's #407) |
| 381 | **The transcode dry run reported `errors=0` for provenance refusals** (the `_delete_extras` variant without `--delete-source`) | transcoder | ✅ FIXED (`errors=len(refused)` in cmd_transcode; the convert/auto variants were still zero — completed by round 39's #410) |
| 382 | **`--to-srgb`/`--icc-profile` silently ignored when routing fell to transcode** | transcoder | ✅ FIXED (warned explicitly — transcode is bit-exact by definition, there is nothing to apply) |
| 383 | **`--force-transcode` on PNG only errored inside the worker** (late, cryptic) | transcoder | ✅ FIXED (the error surfaces at plan time) |
| 384 | **`%TEMP%\jxl_photo_sRGB.icc` was written non-atomically and unvalidated** | transcoder | ✅ FIXED (cache + lock + atomic write with validation) |

Regression tests: `tests/test_audit_round38.py` and the per-script suites
touched at the time. Spot-reverified during the round-39 session by code
inspection (#357, #359, #366, #367, #384) and by the consolidated audit's own
cross-references (#363, #371, #373).

---

## Round-37 audit (2026-09-20)

The second audit of v2.1.0, from `bugs_to_fix_260920.md`. Ten bugs, all in the
safety/reporting layer around the codecs — the conversion core is again
untouched. Headline: a dry run could promise outputs the real run refuses, and
every script wrote its output under the FINAL name, so a run killed externally
left a truncated file with a fresh mtime that the next smart-sync run trusted
forever. All fixes ship in v2.1.1.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 347 | **The wrapper dropped the recompressor policies.** Step 6A rebuilt `advanced_options` from scratch, discarding the `on_downgrade`/`on_regeneration`/`on_unknown`/`jbrd_policy`/`no_keep_smaller` already chosen in Step 6 — the child fell back to `ask`, a SILENT SKIP on the wrapper's pipe. The manifest builder never emitted `--on-unknown`/`--jbrd-policy` at all, and the recompressor wizard branch never asked them | wrapper | ✅ FIXED (`_carry_recompress_policies()` carries the five keys through every branch of Step 6A, including the advanced-declined path; the recompressor branch asks `on_unknown` (default convert) and `jbrd_policy` (default copy) in rich and plain modes and no longer asks the transcoder's no-md5/no-verify/output-suffix; both builders emit `--on-unknown` and `--jbrd-policy`) |
| 348 | **`--encode-tag xmp` ignored a lineage chain sitting in EXIF Software.** A TIFF recovered by the decoder from a `--encode-tag software` JXL carries the `gen=/cjxl d= e=` chain in EXIF Software; `-tagsfromfile -exif:all` copied it into the new JXL next to the fresh dc:Description record, and the recompressor — which merges both fields — trusted the STALE one (wrong d/e, inflated gen) | encoder | ✅ FIXED (the xmp branch mirrors the software branch in reverse: `_merge_lineage_blocks` over both fields seeds the dc:Description record, then the machine block is stripped from Software — unrelated user text, e.g. an editor name, is kept. Real-codec regression test: Software chain → merged Description, clean Software) |
| 349 | **The keep-smaller fallback skipped the copy MD5 proof.** `_delete_gate` gated the byte-for-byte check on `action == "copy"`, but the KEEP_SMALLER fallback reports status `"copied"` with action still `"convert"` — a CORRUPT copy certified the deletion of its source | recompressor | ✅ FIXED (the gate fires on `action == "copy" or status == "copied"` — a verbatim copy is the strongest proof there is, so it is required, not optional) |
| 350 | **Outputs were written under their FINAL name.** A run killed externally (idle-timeout kill, Ctrl+C, power loss) left a TRUNCATED file at the final path with a NEW mtime, which the next smart-sync run then treated as up to date forever | all four scripts | ✅ FIXED (uuid temp BESIDE the final, promoted with an atomic same-folder `os.replace` only after the integrity check — the final name only ever names a verified, complete file. Encoder/decoder/transcoder redirect inside the workers when `write_path == final_path`; the recompressor assigns the temp in `main()` and promotes in a new `process_group` branch — `_promote_from_staging`'s `shutil.move`/`os.rename` refuse an existing destination on Windows, so a keep-smaller re-run over an existing output needs `os.replace`) |
| 351 | **`checksums.md5` appends raced between child processes.** `_md5_db_lock` is a thread lock — per process. Two manifest entries targeting the same folder are two child processes, and their appends interleaved mid-line, corrupting the db | transcoder | ✅ FIXED (`_append_checksum_line`: a sibling `<db>.lock` created O_EXCL with backoff, 10 s timeout, 120 s stale reclaim, fail-CLOSED on every path — a lock that cannot be taken means the line is NOT written, since a missing entry reads as "no provenance recorded" and blocks deletions, while a torn line is worse) |
| 352 | **Dry runs skipped the provenance refusal gate** — the simulation promised outputs the real run refuses, and the summary reported errors=0 for a run that will fail. The recompressor dry run also exited nonzero | decoder, recompressor | ✅ FIXED (the gate runs in dry runs as a PREVIEW: `DRY \| would REFUSE` per file, counted in the summary's errors, no item leaves the plan; the recompressor dry run exits 0 — a simulation with predicted failures is still a successful simulation, same contract as the encoder's) |
| 353 | **Two runs started in the same second opened the SAME log file** — timestamp-only names, so two manifest children appended interleaved into one log | all four scripts + wrapper | ✅ FIXED (`{timestamp}_{pid}.log`, with a counter bump for same-process repeats inside one second) |
| 354 | **A missing final output left the decoder's delete gate silently** — `continue` with no `kept++` and no KEEP line, invisible in the summary | decoder | ✅ FIXED (`_delete_stats["kept"] += 1` + a `KEEP (final output missing)` warning) |
| 355 | **The jbrd repair wrote its working copy as `*.jxl`** — a crash between the strip and the cleanup left a fake input the next recursive scan would eat — and `_jxl_binds_to_archived_jpeg`'s reconstruction `mkstemp` ignored `TEMP_DIR` | transcoder | ✅ FIXED (`<uuid>_repair_<stem>.tmp`; `mkstemp(dir=TEMP_DIR)`) |
| 356 | **(a) The wrapper's mode-6 collision mirror exempted the requested subfolder** the real finder does NOT exempt (mode 6 has no requested subfolder — the exemption is mode-7 semantics), reporting collisions the child would never produce; **(b) `--repair-jbrd` demanded cjxl** it never invokes — repair only decodes (djxl ≥ 0.12) and edits metadata (exiftool) | wrapper, transcoder | ✅ FIXED ((a) the mirror passes `honor_requested_subfolder=(mode != 6)`, matching the finder exactly; (b) repair is routed before the full tool check with its own two-tool requirement) |

Regression tests: `tests/test_audit_round37.py` (23 tests; the #348 lineage
test is real-codec, skipped without cjxl/djxl/exiftool). Proven against the
pre-fix code (`git show HEAD:<script>` copies): 20 of 23 fail. The three that
pass are the #349 positive control (a faithful copy must still delete), the
#350 recompressor promotion pin (pre-fix `shutil.move` also landed the bytes,
just non-atomically), and the #351 concurrency test (torn appends are
timing-dependent — the fail-closed lock test is the discriminator).

---

## Round-36 audit (2026-09-19)

A review of the round-35 fixes against the real fixtures (`E:\TESTE`: Capture One
exports and the RGB+IR film scans). The round-35 suite passed in full; five of its
fixes had nevertheless opened new holes, two of them data-losing, and every one
invisible to the mocked tests. `tests/test_audit_round36.py` adds real-codec tests
(cjxl/djxl/exiftool, skipped when absent) for exactly that reason.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 339 | **Refusing a master TIFF reported a SKIP — and `--delete-skipped` then deleted the JXL.** #326 returned `"skipped"` for the refusal. A skip admits the source to `--delete-skipped`, so with `--delete-source --delete-skipped` the JXL was deleted on the strength of a TIFF that is NOT its decode (it is older than the JXL and never went through this tool). Reproduced: `photoA.jxl` next to a `photoA.tif` holding a different photo — the log printed `KEEP (refusing to overwrite ...)` and the very next line `DELETED source (already archived)`. The dry-run preview (`_would_skip_group`) predicted the same deletion | decoder | ✅ FIXED (own status `"refused"`: never a skip, never deletable, silent in the staging mover, counted apart — a WARNING block lists the refused TIFFs at the end and the summary carries `Refused (existing TIFF is an original master)`; the exit code is unchanged, since refusing is the intended safe outcome and a folder where the encoder left TIFF + JXL side by side would otherwise fail every scheduled sync. `_would_skip_group` returns False for it, and the dry run now previews the refusals) |
| 340 | **The lineage merge collapsed real generations.** #328's `_merge_lineage_blocks` deduplicated entries with a set, also INSIDE one field — but `cjxl d=0.1 e=7 \| cjxl d=0.1 e=7` (decode, then re-encode at the same settings) is two generations, the very case the append-only chain exists to record. A legacy two-generation file read as gen=1, so the gen≥2 regeneration guard stayed silent; a restamp deleted one history entry and wrote gen=2 instead of 3 | encoder, recompressor | ✅ FIXED (no dedup inside a field. Across the two fields: a mirror, or one chain being a prefix of the other, is ONE history (the longer one); anything else is split history and both are kept, dc:Description first — the older side in every shape this toolkit produces. Parity-pinned copies stay identical) |
| 341 | **The multi-page veto never saw the page that failed.** #331 built the groups only from pages that were deletable this run; a page whose conversion FAILED (or was policy-skipped, or refused by provenance) was left out, the group looked one page long, and page 0 was deleted alone — reproduced with the film scan's IR page failing. Also: the group key was the id alone, so a copy of the same split in another folder could veto (or be vetoed by) this one | recompressor | ✅ FIXED (every planned page is recorded — not-settled ones as silent blockers that are never deleted nor counted as KEEP; `main()` passes all items, provenance refusals included; groups are keyed by (folder, id), like the decoder's) |
| 342 | **Every mode-0 manifest row of JXL→JXL exited 2.** #332 refused an output equal to the input in mode 0 too — but mode 0 is flat, so that is exactly an in-place run, and a manifest row always sends it (the loader fills an empty Destination with the Source). The wrapper's in-place gate from #329 only matched an EMPTY Destination, which a manifest never has, so mode-8 rows fell into the child's hidden HHMM prompt again (headless: exit 3) | recompressor, wrapper | ✅ FIXED (the refusal applies to mode 2 only; the wrapper's new `_recompress_entry_in_place()` treats mode 8, and mode 0 with no Destination or Destination == Source, as in place — both for the HHMM gate and for `--delete-confirm-off`) |
| 343 | **In-place detection compared unnormalized paths.** A relative input with an absolute output of the same folder (or the reverse) was not recognized as in place, so `cjxl` was pointed at its own input file as the output instead of taking the temp-file + atomic replace path | recompressor | ✅ FIXED (`abspath` + `normcase` on both sides) |
| 344 | **`--repair-jbrd` could claim a repair it never made.** #323's strip ignored exiftool's exit code: on a JXL exiftool refuses to edit (a `[minor]` Exif oddity) it logged "markers stripped, reconstruction still fails" although nothing was stripped. The edit ran on the archive itself and stayed there when the reconstruction still failed. After a real repair `checksums.md5` still held the ORIGINAL JPEG's md5 and the old self-hash, so every later lossless decode failed MD5 verification and the delete gates refused the file forever | transcoder | ✅ FIXED (the repair works on a copy next to the file and replaces it only when the copy provably reconstructs — a failure leaves the archive byte-for-byte untouched; the exit code is checked, retried with `-m`, and the markers are re-read to confirm they are gone; after a repair the db gets the reconstructed JPEG's md5 and the new self-hash; a dry run performs the same test on the copy, so WOULD REPAIR is a tested prediction) |
| 345 | **Recompressed metadata stayed Brotli-compressed.** #333 reordered the boxes, but cjxl ≥ 0.12 carries the source's Exif/XMP across as `brob` boxes and exiftool edits them in that form — IrfanView cannot read `brob` (README, *Viewer quirks*), so the EXIF stayed invisible | recompressor | ✅ FIXED (the restamp runs with `-api Compress=0`: plain `Exif`/`xml ` boxes, before the codestream, exactly like the encoder's outputs) |
| 346 | **The round-35 entries were filed as #172–#187** at the end of this document, reusing numbers that already belong to v1.8.1 bugs (the table rows 172–187 below) and breaking the cross-references this file relies on | docs | ✅ FIXED (re-filed as #323–#338 in a Round-35 section at the top, in the file's table format) |

---

## Round-35 audit (2026-09-18)

The first audit of v2.1.0, run against the real fixtures. Headline: since
v2.0.0 the JPEG → JXL transcode was no longer bit-exact recoverable for any JPEG
that already carried XMP, and `--delete-source` deleted those JPEGs anyway.
Several of these fixes were reworked in round 36 (noted per row).

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 323 | **CRITICAL — XMP markers in jbrd containers broke bit-exact JPEG recovery.** Since v2.0.0 (#268) the `jxlphoto-src:`/`jxlphoto-srcsum:` markers were written into every JXL, jbrd containers included. For a source JPEG that already had XMP (Lightroom / Capture One exports) the appended XMP makes `djxl --reconstruct_jpeg` fail, while the delete gate (MD5 recorded + jbrd present + integrity) still certified the output — `--delete-source` destroyed originals that could no longer be recovered. The same JPEG through v1.9.1 reconstructs with an identical MD5 | transcoder | ✅ FIXED (no marker is ever written into a jbrd container — provenance there is `checksums.md5` plus the content-binding check; the encode-direction delete gate runs a REAL `--reconstruct_jpeg` and compares bytes before unlinking a JPEG, failing closed with djxl < 0.12; new `--repair-jbrd` audit/repair mode — reworked in #344) |
| 324 | **CRITICAL — `--delete-skipped` alone deleted sources** with no confirmation and no provenance check: in mode 2 a same-named output from a DIFFERENT photo was enough to destroy the only copy of a source, exit 0 | recompressor | ✅ FIXED (inert without `--delete-source`, with a warning, like the other three scripts) |
| 325 | **CRITICAL — failed recompressions left their output behind**: at the FINAL path (smart sync then skipped the file forever), or as `<uuid>_name.jxl` next to the source in place (picked up as a new input next run) | recompressor | ✅ FIXED (the encoder's `output_dirty`/identity-checked cleanup, ported) |
| 326 | **CRITICAL — the decoder overwrote the original TIFF master** in the default smart sync when the encoder's mode 0 had left `foto.tif` + `foto.jxl` side by side: "JXL newer" → OVERWRITE with the lossy decode, no delete flag involved | decoder | ✅ FIXED (an existing TIFF without the decoder's own `jxlphoto-src` marker is refused; `--overwrite` is the explicit override; TIFFs written in `--none` mode carry no XMP, so they need `--overwrite` to be re-decoded — the safe direction — reworked in #339) |
| 327 | **HIGH — the regeneration guard fired on the main use case**: encoder outputs are born at gen=1, so `gen >= 1` turned every first recompression into `ask` — headless runs skipped everything, the wrapper's `copy` default copied without compressing | recompressor, wrapper | ✅ FIXED (threshold `gen >= 2`; wrapper default `convert`; README table corrected) |
| 328 | **HIGH — lineage lost when the record changed fields**: the restamp stripped the other field's chain instead of merging it (gen=1 written where gen=2 was true); the encoder's software mode left a stale chain in dc:Description and seeded a bare `cjxl` segment | encoder, recompressor | ✅ FIXED (parity-pinned `_merge_lineage_blocks()`; reading reconciles gen over both fields — reworked in #340) |
| 329 | **HIGH — wrapper out of step with the recompressor**: wrong folder names (`converted_jxl` for `recompressed_jxl`/`JXL_recompressed`) in previews and in the delete panel; modes 0/8 described as "side by side" where the recompressor REPLACES; in-place recompression not gated as destructive (no HHMM, `--run-preset` let it through, the child's prompt appeared invisibly mid-stream) | wrapper | ✅ FIXED (real names, REPLACE wording, HHMM gate + `--delete-confirm-off`, preset refusal — manifest detection reworked in #342) |
| 330 | **MEDIUM — cross-volume in-place promotion could destroy the only copy**: the staging move copied ONTO the original non-atomically; on failure the only good copy stayed in staging under a UUID name, swept by `--clean-staging` an hour later | recompressor | ✅ FIXED (temp file in the destination folder, then an atomic `os.replace`) |
| 331 | **MEDIUM — multi-page groups could be deleted page by page**, spreading a document across two folders | recompressor | ✅ FIXED (all-or-nothing per `jxlphoto-mpg:` group — reworked in #341) |
| 332 | **MEDIUM — mode 2 with the output equal to the input** replaced root files in place and flattened subfolders into the root, mixed with the originals | recompressor | ✅ FIXED (refused with exit 2 — narrowed to mode 2 in #342) |
| 333 | **MEDIUM — recompressed outputs hid their EXIF from IrfanView**: no `reorder_jxl_boxes` after the restamp | recompressor | ✅ FIXED (reorder after the restamp, before the integrity check — completed by #345) |
| 334 | **MEDIUM — doc examples used modes that ignore the output positional** (mode 5 in the README, mode 3 in the recompressor README) | docs | ✅ FIXED |
| 335 | **LOW — encoder software mode seeded the chain with a bare `cjxl` segment** when the TIFF had no Software tag | encoder | ✅ FIXED |
| 336 | **LOW — a caption containing `cjxl d=1 e=7` counted as a generation** and was duplicated into the chain | encoder, recompressor | ✅ FIXED (chain entries read from whole machine-block segments only) |
| 337 | **LOW — recompressor summary/progress**: aborted counted as errors; failure reasons reached the summary as a bare "error"; the dry run named `--delete-source` when only `--delete-skipped` was armed; `[n/total]` used the raw scan count; an exception outside `convert_one` killed the run | recompressor | ✅ FIXED |
| 338 | **LOW — `_classify` called a gen≥1 chain ending in a `d=0` pass "the FIRST lossy generation"** | recompressor | ✅ FIXED |

---

## Round-34 audit (2026-08-23)

The low-severity sweep over the same tree, grouped per script. None of these
destroys data on its own; each makes the tool lie a little, charge a
confirmation for a run that cannot start, or die with a raw traceback where a
clean error belongs.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 319 | **The wrapper batch.** (a) A JXL→TIFF preset advertised `q=95` — the second direction of #310: `save_last_session` only overwrites `last_quality` when a run supplies one, so the preset carried the quality of whatever JPEG run came before it, a knob the decoder never receives. (b) `execute_workflow` charged the HHMM delete token and pre-created the mode-2 output folder BEFORE noticing the child script is not installed. (c) `--delete-source=1` in expert flags was read as a delete request, but the children declare the flag `store_true` and argparse rejects an explicit argument (exit 2) — a token charged for a run that never starts. (d) `--list-presets`, a read-only listing, was unreachable on a machine with no codecs installed. (e) `_session_number_error` validated only numbers; a hand-edited `provenance` / `multipage_mode` / `compression` / `depth_policy` / `conversion_type` / `icc_profile` / `bit_depth` reached the child's argparse as a command line the user never typed. (f) `_ask_delete_options` wrote its answers straight into the live config, so cancelling the wizard still persisted them via the next unrelated `save_config()`. (g) A hand-written manifest with Mode=7 and a Source ABOVE the marker ran with an empty `--export-subfolder` — mode 6 wearing a mode-7 label, converting every subfolder of the marker. (h) The mode-2 `mkdir` died with a raw traceback on an uncreatable folder. (i) Ctrl+C in a manifest run skipped the end-of-run summary and traceback'd out of `main()`. (j) The manifest-repeat label/disable compared the STRING `"99"` only, so a hand-edited config storing the NUMBER `99` bypassed it | wrapper | ✅ FIXED (each with a regression test in `tests/test_round33_lows.py`. (a) `_describe_session` shows quality only where the child actually receives `--quality`; (b) the script-existence check runs before any gate; (c) a valued delete flag is reported as unrunnable and refused ahead of the token, like #263's ambiguous abbreviations; (d) the listing runs before the cjxl/djxl gate; (e) the enumerated fields are validated against their allowed values — corrupt is REFUSED, not defaulted; (f) the answers are staged on the workflow and persisted only by the save that follows a run; (g) the subfolder is derived from the manifest's Source paths and passed explicitly — entries naming different subfolders need no flag since each Source scopes its own child — and when it cannot be derived, attended runs warn and unattended presets are refused, fail-closed like the overlap guard; (i) the cancelled entry and everything after it are recorded, the summary renders, and `main()` exits 130) |
| 320 | **The encoder batch.** (a) The real-run summary undercounted skipped: mode 6/7 files outside the export marker were counted in the DRY-RUN summary but not the real one. (b) The script-set `TEMP2_DIR` staging path was never validated — only `--staging` was — so an invalid value crashed mid-run with a raw traceback. (c) Zero-page TIFFs: `split` classified a header that opens but yields no pages as corrupt, while `skip`/`ignore` fell through to a per-file ERROR (exit 1). (d) The adopt-scan refusal verified against the CURRENT `--distance` without naming that as the likely cause of a mass refusal. (e) Single-file mode-4/5 runs warned "Output outside input tree" on every legitimate output — the anchor was the file itself. (f) `--thumbnail-suffix` accepted path separators and `..`, writing thumbnails outside the destination. (g) Mode 6's decoder-output skip honored `EXPORT_TIFF_SUBFOLDER` — a mode-7 setting — so a leftover value made mode 6 re-encode decoded TIFFs (generational loss at d>0). (h) Stale-split detection stored the source stem in original case while the compared names were normcased — no match on Windows. (i) `_measure_batch_ratio`'s sort key statted without a guard: one TIFF vanishing between scan and preflight silently killed the whole estimate | encoder | ✅ FIXED (each with a regression test in `tests/test_round34_lows.py`. (c) all multipage modes now classify the zero-page shape as `UnreadableTiff` — corrupt, with the exit code unchanged, per #494's rule that a damaged input is not a failed run; (d) the refusal says the scan ran against this run's distance and to re-run with the archive's; (f) the suffix must be a plain filename suffix; (g) mode 6 has no requested subfolder, so it skips ALL decoder-output folders — the exemption is mode-7 semantics; (i) the vanished file is dropped from the estimate) |
| 321 | **The decoder batch.** (a) `copy_metadata` never checked exiftool's exit code — a failed metadata copy (corrupt tag, write failure) was silently dropped, the TIFF passed the pixel gate anyway, and `--delete-source` removed the JXL holding the only copy of that metadata. (b) A group containing ONLY a thumbnail page kept `strategy="unknown"`, and `"unknown" != 'none'`: under `--none` it got the full XMP/IPTC copy, provenance markers and a JPEG preview that None mode explicitly forbids. (c) A duplicate (page, thumb) entry demoted to standalone kept `is_thumb=True`, so its single-page output TIFF's primary image was tagged `subfiletype=1` (reduced-resolution), which some readers hide. (d) `_counter["done"]` was never reset between runs in the same process, so the second run's progress started at `[N+1/total]`. (e) `process_group`'s `mode` parameter was dead (the delete gate is mode-independent) but every caller still passed it. (f) The script-set `TEMP2_DIR` staging path was never validated — #320b's twin | decoder | ✅ FIXED (each with a regression test in `tests/test_decoder_lows.py`. (a) a copy failure is now a per-file ERROR — the pixels are fine, so the TIFF stays — and the delete gate fails CLOSED on it; (b) a thumb-only group falls back to the first page's strategy, so `--none` keeps its minimal-metadata contract; (e) the parameter is removed, not left to rot) |
| 322 | **The transcoder batch.** (a) #267 was incompletely fixed: the dry-run `--delete-source is ARMED` preview only existed in `_process_file_group` (cmd_auto) — `cmd_transcode` and `cmd_convert` ran the same armed simulation without ever mentioning it, despite the v2.0.1 notes claiming all three. (b) The ICC-conversion paths decoded the intermediate PNG without `--bits_per_sample`, so a 16-bit request relied on `magick -depth 16` upscaling whatever djxl defaulted to instead of decoding at 16-bit. (c) `cmd_auto` charged the strict HHMM lossy token for a PNG→JXL group even at `--distance 0` (lossless modular); `cmd_convert` gates on `distance > 0`. (d) `reorder_jxl_boxes` died with a raw OverflowError when a size-0 box moved off the tail of a ~4 GiB file computed a real size past the 32-bit field. (e) Two silent gate bypasses: the transcode delete gate's missing-final branch `continue`d with no KEEP log or count, and the convert staging mover silently dropped an "ok" result whose staged file was gone | transcoder | ✅ FIXED (each with a regression test in `tests/test_round34_transcoder_lows.py`. (a) the ARMED notice now prints in all three entry points — what #267 always claimed; (b) the intermediate decode passes the requested depth; (c) `cmd_auto` follows `cmd_convert`'s rule; (d) a clear RuntimeError naming the cause; (e) both KEEP, log it, and count it) |

Regression tests: `tests/test_round33_lows.py` (17, wrapper),
`tests/test_round34_lows.py` (13, encoder), `tests/test_decoder_lows.py` (9,
decoder), `tests/test_round34_transcoder_lows.py` (10, transcoder) — 49 total.
The encoder and transcoder files document their pre-fix proof (run the same
file against an extracted HEAD copy of the script); the decoder's
copy_metadata tests fail against the pre-fix code, which returned success on
a nonzero exiftool exit.

---

## Round-33 audit (2026-08-23)

Full-repo audit after round 32, weighted towards the paths that can delete or
overwrite an archive: the decode-side delete gates, the wrapper's manifest
collision model, the staging promotion, and the pre-v2.0.2 group ids. Five
mediums. **The conversion core is again untouched** — every finding sits in
the safety layer around it.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 314 | **The decode-side provenance gates were name-keyed, not content-bound — a swapped same-named JXL could be deleted without ever being archived.** In the JXL→JPEG lossless direction, `checksums.md5` stores the ORIGINAL JPEG's md5 keyed by the JXL's NAME, and both delete gates — the `--delete-skipped` gate in `process_group_transcode` and `_provenance_filter(decode_lossless=True)` — compared that stored hash against the JPEG on disk and never looked at the JXL's own bytes. Replace `photo.jxl` with a DIFFERENT same-named JXL and the old JPEG still matches the stored hash: both gates pass, and `--delete-source` then destroys a JXL that was never archived | transcoder | ✅ FIXED (the gate now binds the JXL's CONTENT, via `_jxl_binds_to_archived_jpeg`. The encoder additionally stores the JXL's own md5 under `<name>.jxl-md5` in `checksums.md5` — a backward-compatible companion line: `read_md5_db` matches names exactly, so the suffixed key can never shadow the plain lookup, and databases written before it existed or read by older versions behave unchanged. When the self-hash is present it is compared directly — an exact identity proof, no decode needed. Legacy databases fall back to `djxl --reconstruct_jpeg` into a temp file (djxl ≥ 0.12), compared against the archived JPEG and the stored hash. A mismatch — or the proof being UNAVAILABLE — returns False: the gate fails CLOSED, the source is KEPT) |
| 315 | **The manifest collision scan never compared its two families against each other (regression from #306).** `_manifest_needs_collision_scan` buckets entries into `marker_dirs` (modes 6/7 whose export marker is an ANCESTOR of the Source — outputs land in a constant subfolder of the marker dir, shared by every entry under it) and `within_source` (everything whose outputs stay inside its own tree), and then only looked for collisions WITHIN each family. A mode-6 entry anchored on `G:\_EXPORT` (outputs in `G:\_EXPORT\16B_JXL`) next to an in-place entry whose Source IS `G:\_EXPORT\16B_JXL` has disjoint Sources in both families — and one shared output folder, written by two child processes that can each only see their own entry | wrapper | ✅ FIXED (a cross-family containment check: because the marker family's output folder sits strictly inside the marker dir, any containment between a `within_source` Source and a marker dir — either way — forces the scan. The function does not know the run's direction, so it cannot cheaply name that subfolder and fails SAFE on anything ambiguous; the cost is a false-positive scan for harmless mixes, which is what #306 was buying back) |
| 316 | **`_read_multipage_markers_batch` matched exiftool's `SourceFile` paths exact-case — the fix #290(a) gave its sibling reader never reached this one.** A differently-cased path (a recased mount point, `8.3` fallout) silently dropped the multipage group markers, so the pages of a split were decoded STANDALONE — and with `--delete-source`, deleted page by page, each one passing every gate because a standalone JXL is the ordinary single-output case | decoder | ✅ FIXED (a normcased index, like `_read_source_markers_batch` and every other path comparison in the provenance layer) |
| 317 | **`_promote_from_staging` deleted the destination after ANY failed move, on the assumption it was partial.** #283's cleanup was right for the case it named (ENOSPC mid-copy) but "the move raised" does not mean "the destination is partial": the move can fail BEFORE touching the destination (a locked or read-only PRE-EXISTING file — a perfectly good archive), and the copy can SUCCEED with only the staging unlink failing (the destination then holds the COMPLETE new output). Both were deleted | encoder, decoder, transcoder | ✅ FIXED (a pre-move identity snapshot — `(mtime_ns, size)` — of the destination. On a failed move the destination is removed only when the move PROVABLY wrote to it AND what it wrote is incomplete; when the state cannot be told apart, the file is kept. The disk-full latch and the staging-kept behaviour are unchanged) |
| 318 | **Re-encoding a lost page of a pre-v2.0.2 archive stamped the NEW group id beside LEGACY-id siblings.** v2.0.2's formula change (#303a) made the id cover the page set, so the re-encoded page landed in a different group than its surviving siblings — the decoder saw two TRUNCATED groups and advised "point the run at the folder holding every page", sending the user hunting for a page that is not missing | encoder + decoder | ✅ FIXED (two parts. The encoder ADOPTS the legacy id when the on-disk siblings prove it — unanimous, matching the legacy formula exactly, and all within the planned page set; a mixed or out-of-set sibling keeps the new formula, since adopting there would invite #303's own shape. Adoption is logged as INFO, and the archive heals into one group. The decoder recognizes the mixed-version shape structurally — several truncated groups in one folder, one source stem, one source checksum, disjoint pages, together exactly the split size every group recorded — and advises a full re-encode (e.g. `--overwrite`) instead of a page hunt; a genuinely truncated group keeps the old advice. The decoder cannot recompute either hash — the source path is not in the markers — which is why the detection is structural) |

Regression tests: `tests/test_jxl_content_binding.py` (9, #314 — swapped
JXL kept on a self-hash mismatch, legacy-db reconstruct fallback matching and
mismatching, djxl<0.12 failing closed, and the suffixed key never shadowing
the plain lookup), `tests/test_audit_round33.py` (6, #315 — cross-family
collisions force the scan, disjoint mixes still skip it), 
`tests/test_multipage_marker_normcase.py` (2, #316), `tests/test_staging_promotion.py`
(3 new, #317 — an untouched pre-existing output and a complete copy with a
failed unlink both survive; a genuinely partial overwrite is still removed),
`tests/test_legacy_group_id_heal.py` (6, #318 — adoption heals the archive
into one group; mixed or out-of-set siblings keep the new formula; the
decoder's mixed-version advice vs a genuinely truncated group).
`tests/test_parity_round29.py` was updated for the `.jxl-md5` companion line.

---

## Round-32 audit (2026-08-19)

Second sweep the same day, aimed at what round 31 did NOT exercise: the decoder's
alternative colour paths (`--matrix`, `--basic`, `--none`, `--target-icc`), the
transcoder's convert direction (PNG output, `--to-srgb`, `--icc-profile`, auto
mode on a mixed folder), encoder modes 1/3/4/5, non-ASCII and bracketed paths,
`--provenance adopt`, and a re-review of everything round 31 had just changed.

**Almost all of it held.** Matrix/basic/none behave as documented (alpha dropped
on the PPM path, 8-bit precision in the LittleCMS transform, no ICC under
`--none`); `--provenance adopt` verified, stamped, and correctly REFUSED an
archive that did not come from its source; the v1.8.2 ignored-thumbnail delete
guard still keeps those sources; a CJK + `[bracket]` + accented path round-tripped
pixel-identical with markers intact; and every `--flag` the wrapper can emit was
checked mechanically against the three child parsers — no orphans.

One real defect, in the one direction round 31 never ran.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 313 | **An RGB ICC was attached to single-channel outputs.** `--to-srgb` and `--icc-profile` hand every decoded image to `magick -profile <sRGB.icc>`, which attaches the profile without converting the image — so a grayscale source came out as a grayscale file carrying an RGB profile. PNG requires the iCCP profile's data colour space to match the colour type, so libpng warns on every read (`iCCP: profile 'icc': 'RGB ': RGB color space not permitted on grayscale PNG`), and a 1-component JPEG with an sRGB profile is wrong the same way. **Reproduced** on a grayscale TIFF and on a film scan's IR page, in both output formats: `b_gray16.png` / `f_scan_page2.png` grayscale with `iCCP data-space='RGB '`, and the matching JPEGs with `ColorComponents=1` + `ColorSpaceData=RGB`. Without the flag the same files correctly carry no profile at all. The encoder learned this first and its README says so — "Inherited RGB ICC is not applied to grayscale pages, which prevents libpng iCCP errors on scanner IR/mask pages" — but the transcoder's convert path never did | transcoder | ✅ FIXED (`_png_is_grayscale` reads the 26-byte IHDR of the decoded intermediate — colour type 0 or 4 — and `_icc_args_for` drops the profile argument for those, keeping it for RGB/RGBA. Decided AFTER the decode, since only the intermediate knows the channel count. Deliberately does NOT widen grey to RGB to make the profile fit: there is no gamut to map, and it would triple every IR page) |

Also noted, not a code defect: `PIP/` holds a fourth copy of all four scripts,
frozen at v1.8.1 and ~2 000 lines behind. It is gitignored, so it cannot drift
into a release by accident, but it was not listed in `AGENTS.md` next to
`claude/` and a repo-wide grep hits it. Added there.

Regression tests: `tests/test_audit_round32.py` (10). Nine fail against the
pre-fix code; the tenth is a control that pins the shape of the defect (an RGB
profile inside a colour-type-0 PNG) and passes on both sides.

---

## Round-31 audit (2026-08-19)

Full-repo audit of HEAD after round 30: all four scripts read end to end, then
exercised against the real fixtures and a 12-file matrix of synthetic TIFFs
built to cover the shapes the mocked suite cannot (RGB 16/8, grayscale, RGBA,
gray+alpha, a 3-page scan with an IR `MASK` page, a thumbnail at page 0, sources
named `*_page3` / `*_thumbnail`, mixed per-page depths).

**Single-file conversion came out clean again**: every lossless round trip was
pixel-identical with ICC, photometric and `SubfileType` preserved, JPEG↔JXL
recovered byte-identical, and each delete gate refused when it should
(cross-run provenance, incomplete split, discarded pages, un-promoted staging,
an impostor archive caught by `--verify-roundtrip`).

**What was not clean is the SECOND cycle.** #303 is the first structural loss
since v2.0.0 and it lands exactly on the film-scan shape: a scan is
`[real, thumbnail, IR]`, `--thumbnail-mode exclude` archives it as pages
`{0, 2}` (excluded thumbnails do not renumber real pages), and the decoded TIFF
is `[real, IR]` — so re-encoding it in place writes `{0, 1}` and strands
`_page2.jxl`. The group id was a hash of the SOURCE PATH alone, identical across
both runs, so the decoder pulled all three into one group and wrote a TIFF with
the IR page twice, reporting `0 errors` and exiting 0.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 303 | **A second archive cycle of a multi-page scan merges a leftover page.** `_make_group_id` hashed only the resolved source path, so two DIFFERENT splits of the same file claimed one group. **Reproduced end to end on a real `raw_scan_2` crop**: encode → decode → re-encode in place leaves `scan.jxl` + `scan_page1.jxl` + a stale `scan_page2.jxl`, and the decode of that folder produced `[real, IR, IR]` — a valid TIFF, so no integrity check, round-trip or checksum downstream can notice. Two existing defences did fire (the INCOMPLETE warning, and `--delete-source` keeping the sources), which is why nothing was destroyed, but the written TIFF was wrong and the run reported success | encoder + decoder | ✅ FIXED (three parts. (a) the group id now hashes the set of pages the split actually produces, so two runs whose output NAMES differ get different ids while a re-encode of an unchanged structure stays stable; (b) the decoder refuses to merge a group carrying more members than the split recorded — resolving it through `jxlphoto-srcsum`, which differs between encodes, and failing closed to standalone decodes + a real error when it cannot tell. That second half is what repairs archives ALREADY written by the old encoder, verified against one built with the pre-fix code; (c) the encoder names leftovers of a previous split at archive time, before the folder is ever decoded. Nothing is deleted automatically) |
| 304 | **"Incomplete group" covered two opposite cases with one message.** The check was `len(entries) != declared`, so *missing* pages and *extra* pages both became `truncated` with the same wording and the same advice — "point the run at the folder holding every page", which is the wrong instruction when the problem is a file that should not be there | decoder | ✅ FIXED (`< declared` stays `truncated`; `> declared` is its own path, handled by #303. The message for a resolved leftover names the file and says where it came from) |
| 305 | **A manifest that deletes never offered `--verify-roundtrip` or `--provenance`.** The `[D]` gateway is where those are asked, and `_wizard_run_from_manifest` does not go through it — it asks "delete originals?" for mode-8 rows and turns `delete_source` on for every entry. So the largest run the wrapper starts reached the unlink with the structural check alone. Step 7 disclosed `Verify round-trip: off`, which is honest but not the same as being able to turn it on | wrapper | ✅ FIXED (the block is now `_ask_delete_options(workflow, collapses, scope_label)`, shared by both paths. For a manifest the provenance question fires as soon as ONE entry collapses folder structure, and a legacy entry with no Mode cell counts as collapsing) |
| 306 | **Mode-6 manifests always paid a full recursive scan.** `_manifest_needs_collision_scan` returned `True` whenever the export marker sat BELOW the Source, which is the shape of the auto-generated "sync the whole library" manifest (`G:\2024`, `G:\2025`, `G:\2026`). Every run walked all three trees and resolved an output per TIFF before converting anything — to look for a collision that disjoint trees cannot produce, since those entries write inside their own Source | wrapper | ✅ FIXED (6/7 with the marker below the Source join the "writes inside its own Source" family, and that family is now checked for overlap rather than assumed disjoint — which is also strictly safer than before for modes 1/3/8, whose Sources were only WARNED about upstream) |
| 307 | **`Added JPEG preview ... with ICC` was printed unconditionally.** A page whose ICC was inherited is passed `None` on purpose, so the original's missing ICC tag is not invented — and the line claimed an ICC on every film-scan IR page | decoder | ✅ FIXED (the suffix follows `icc_data`) |
| 308 | **The transcoder wrote `CreatorTool` into an argfile unsanitised.** The encoder and decoder both wrap it in `_argfile_safe`; the transcoder had no such helper at all, so a multi-line `CreatorTool` copied from another program would split one argfile line into several bogus arguments | transcoder | ✅ FIXED (`_argfile_safe` added and applied. Also added to `SHARED_HELPERS` so the parity test keeps the three copies together) |
| 309 | **The `output` positional was documented as "mode 0 only".** Mode 2 honours it too — the README's own mode table shows it, and the wrapper's manifest warning says "only modes 0/2" | docs | ✅ FIXED (argparse help and README line corrected, and both now say what the other modes do instead) |
| 310 | **A decode preset advertised a distance it never uses.** `save_last_session` only overwrites `last_distance` when a run supplies one, so a JXL→JPEG preset carries the distance of whatever TIFF run came before it — and `_describe_session` tested `distance is not None` before quality, printing `d=0.05` for a decode | wrapper | ✅ FIXED (the choice follows the DIRECTION: distance for TIFF sources and `convert_lossy`, quality otherwise) |
| 311 | **Two readers of `dc:Relation` disagreed on a scalar value.** `_read_multipage_markers_batch` did `str(rel).replace(";", ",").split(",")` while `_read_source_markers_batch` — reading the same tag in all four scripts — used the whole string. A single user Relation like `Smith, John` was torn in two on the way in | decoder | ✅ FIXED (aligned on `[str(rel)]`) |
| 312 | **The cautious ICC test held its lock across the probe.** `_icc_test_lock` covered the cache read, two `cjxl` runs, two `djxl` runs (120 s timeout each) and the cache write, so every worker stopped dead the first time each profile appeared | encoder | ✅ FIXED (the lock covers only the cache's read-modify-write; the probe runs outside it, with a re-read before the store. Two threads racing on one unseen profile reach the same verdict — the benign race `_content_id_cache` already accepts) |

Regression tests: `tests/test_audit_round31.py` (20). Eighteen fail against the
pre-fix HEAD; the other two are controls that must pass on both sides (a group
id staying stable when the split shape does not change, and a TIFF preset still
showing its distance). `tests/test_audit_round23.py` was updated for #306: the
test that pinned "marker below the Source always needs the scan" now pins the
narrower rule, plus the nested-Sources case that still does.

---

## Round-30 audit (2026-08-13)

Full-repo audit of HEAD after v2.0.0, run against the real fixtures rather than
reasoned about: 16-bit Capture One exports, a 260 MB Z8 export, and the 756 MB
RGB+IR film scans (3 pages — RGB, thumbnail, IR `MASK`, 217 KB scanner ICC).

**The conversion core came out clean once more, and this time end to end.** Every
lossless round trip was pixel-identical with ICC, `SubfileType` and page
structure preserved, including the IR `MASK` page; JPEG↔JXL recovered
byte-identical with MD5 PASS; alpha, pure grayscale, 8-bit and CJK/accented
paths all survived. The v2.0.0 machinery held under real files too — the
cross-run provenance refusal, the incomplete-split KEEP, staging+delete, and the
scanner-ICC lossy workaround each did what the READMEs promise. All six findings
are around that core, and none of them deletes or corrupts a file.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 297 | **The delete confirmation counts the wrong FILES in mode 7.** `_count_origin_files` applied the export MARKER to the imported child module but never the SUBFOLDER, so `find_tiffs_mode7`/`find_jxls_mode7` ran with the script default (`""` = every subfolder). **Reproduced**: an `_EXPORT` holding `16B_TIFF`/`AdobeRGB`/`sRGB` with `--export-subfolder 16B_TIFF` announced 2 files (encoder) and 3 (decoder) for a run that converts 1 — and not a subset, a *disjoint* set, since `16B_TIFF` is itself a decoder-output name the unfiltered finder skips. This is the count printed in the "About to delete originals" panel, the second of the three gates, whose visible number is the documented way a wrong folder is caught before the HHMM token. Mode 7 is the Capture One workflow the READMEs call the most common. `_manifest_output_collisions` already applied both — the two sites disagreed | wrapper | ✅ FIXED (new `_with_child_marker(child, marker, subfolder)` context manager, used by BOTH sites so they cannot drift again. All three directions covered, including the transcoder branch, which filters inside `resolve_output_transcode` rather than in a finder) |
| 298 | **The manifest collision scan leaks the child's globals for the rest of the session.** It assigned `EXPORT_MARKER` and all three `EXPORT_*_SUBFOLDER` on the imported child modules and restored none of them — only `logger.disabled` was put back. The wrapper is a long-lived interactive process, so a manifest run with a custom marker left every later in-process use of that child reading the leaked value, which made #297's count depend on what had been run earlier in the same menu | wrapper | ✅ FIXED (the same `_with_child_marker`, entered on an `ExitStack` closed in the existing `finally`, so a resolver that raises cannot leave the module rewritten either) |
| 299 | **The opening banner announces the opposite of what the run does.** `--multipage-mode split_all` ignores `THUMBNAIL_MODE` by design and encodes every page, but the banner printed the raw setting — so a `split_all` run reported `Thumbnail: exclude` two lines above its own log of a written `*_thumbnail.jxl`. The README documents that line as showing the settings that are ACTIVE | encoder | ✅ FIXED (the label is derived from the effective policy: `include (forced by split_all)`. Every other mode still reports `THUMBNAIL_MODE` unchanged) |
| 300 | **The decoder README still documented the `MASK` demotion that was already fixed.** The film-scan section promised `SubfileType=4` (MASK) "is mapped to `PAGE` (`2`)". `_page_subfiletype_kwargs` has written the raw TIFF tag 254 for values `tifffile`'s enum rejects since that was corrected, so a scanner's IR page keeps its role — verified on the real scan, `4 → 4`. Missed by "docs: bring the READMEs up to v2.0.0" | docs | ✅ FIXED (the section states the real behaviour and marks the change, so someone reading it about their own film scans is not told their IR page is downgraded when it is not. The local `docs/RELEASE_v1.7.0.md` draft repeated the old claim and was corrected too rather than kept as a historical record — no copy of it survives to be found later. Those drafts are untracked: `chore: unpublish release notes from repo` (854e101) moved every one of them out of version control, so they live only on the author's machine and in the published GitHub Releases) |
| 301 | **The integrity gate's comment understates it by a whole page.** `_verify_tiff_integrity` says "Only the last strip/tile is decoded" while `tif.pages[-1].asarray()` decodes the entire page — measured at 187 MB and a full decode for a 93 MP scan page. Comment only; the behaviour is right and deliberate | decoder | ✅ FIXED (the comment says what it costs and why that is accepted: the gate runs once per output, serially, after the pool, and truncation lands on the last page, which is the one read) |
| 302 | **Redirected output makes every dependency icon identical.** The wrapper reconfigured its streams with `errors="replace"` and no `encoding`, so a REDIRECTED stdout fell back to the ANSI codepage where `✓`, `✗` and `⚠` are all unencodable — and all three became the same `?`. The status bar in a scheduled-task log therefore said nothing at all, which is the one place its only job is to be read. The three backend scripts already passed `encoding="utf-8"`; the wrapper's own comment claimed it did the same | wrapper | ✅ FIXED (`encoding="utf-8", errors="replace"`, matching the backends. The `errors` fallback still protects against anything else unencodable) |

Regression tests: `tests/test_audit_round30.py` (13). Ten of them fail against
the pre-fix HEAD; the other three are controls that must pass on both sides
(no-subfolder counting, the banner outside `split_all`, and the `MASK` tag
behaviour, which #300 only mis-documented).

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

| 281 | **`cmd_auto` swallows provenance refusals (#272 was fixed in two of three commands).** `_process_file_group` subtracts `_refused` from the progress total and nothing else: `tally` starts at `err: 0, failures: []`, and the `if not pairs` early return hands back zeros. An auto run that refused every file exited 0 with an empty failure list, so a scheduled job saw a clean run and only the log said otherwise. `cmd_transcode` and `cmd_convert` both do `err = len(_refused)` | transcoder | ✅ FIXED (the refusals seed the tally and the empty-`pairs` return, exactly as the two sibling commands do, so they reach the exit code and the `failures` list the wrapper's recap reads. The dry-run return keeps reporting zeros, matching the siblings — whether a simulation should report the refusals it PREDICTS is a separate open question for all three) |

| 282 | **A multi-page split arriving with pages MISSING is decoded and its sources deleted.** Only a group of exactly ONE member that was not page 0 was detected (`len(entries_sorted) == 1`). A three-page split arriving as pages `{0,1}` decoded to a valid two-page TIFF — nothing downstream can tell it is short — and `--delete-source` deleted both JXLs. **Reproduced against the shipped code**: `Done: 1 OK, 0 errors`, `Sources DELETED: 2`, third page gone for good. Also reproduced on the real 756 MB RGB+IR scan by removing its IR page | encoder, decoder | ✅ FIXED (new `jxlphoto-pages:<N>` marker: every page of a split records how many the split produced, and a group whose members do not add up is reported and its sources KEPT. **The count is the only sound test** — a GAP in the page numbers is not evidence, because `--thumbnail-mode exclude` leaves the real pages on their ORIGINAL indices, so the ordinary film-scan shape `[real, thumb, real]` archives completely and correctly as pages `{0, 2}`; treating that as incomplete would refuse the commonest scan there is (verified on the real fixture, which is exactly that shape). Archives with no count are NOT refused — "cannot tell" is not "incomplete", per #271 — and the count-free lone-fragment check is unchanged. `--allow-incomplete-groups` deletes anyway, for a page that is genuinely gone; it never changes what is DECODED, and the warning naming the missing pages prints either way) |

| 283 | **A failed move out of staging leaves a truncated file at the destination, with a fresh mtime.** Cross-volume `shutil.move` is copy-then-unlink, so ENOSPC part way through leaves a partial output — and smart-sync compares timestamps, sees something newer than the source, and skips the reconversion forever. The complete copy was in staging the whole time. A destination volume that is simply full also produced one `MOVE FAILED` line per remaining file instead of latching the disk-full abort the scripts already have | encoder, decoder, transcoder | ✅ FIXED (new shared `_promote_from_staging`, in `SHARED_HELPERS`, replacing all four hand-written copies. It removes whatever landed at the destination — only when the staging copy survived, since an empty staging means the move actually completed — and calls `_abort_if_disk_full`. A locked or read-only destination still costs one file, not the run: "cannot tell" is never reported as "disk full") |

| 284 | **The lossy `--delete-skipped` confirmation never fires on a manifest run.** `execute_workflow` dispatches mode 99 BEFORE calling `_confirm_lossy_delete_skipped`, and `_execute_manifest_workflow` never called it — so the extra gate for the one combination with no provenance of any kind vanished for exactly the runs that touch the most files | wrapper | ✅ FIXED (called on the manifest path too, before the HHMM token. The gate is idempotent via `_lossy_skip_confirmed`, so it is still asked once per run, and it turns `delete_skipped` off in the very dict the cmd builder reads) |
| 285 | **The manifest delete offer asks about mode 8 and applies to every entry.** There is one `delete_source` for the whole workflow and the cmd builder appends `--delete-source` to EVERY entry, which the children now honour in every mode — so a manifest of mode-8 rows plus mode-3 rows asked "Manifest contains DELETE-mode (8) entries" and then deleted the mode-3 sources as well | wrapper | ✅ FIXED (the question describes what actually happens: it names the entry count and the other modes whose originals go too. Per-entry delete flags were deliberately NOT added — the manifest format has no column for it, and inventing one to match a mis-worded prompt is the wrong way round) |
| 286 | **The `[D]` count preview ignores the export marker for the transcoder directions.** #269 fixed the over-count by asking the child's own finder, but the transcoder has no mode-6/7 finder — it scans everything and drops non-marker files by returning None from `resolve_output_transcode` — so JPEG↔JXL kept the raw extension count and announced 23 files for a run that touches 3 | wrapper | ✅ FIXED (the extension scan is filtered through the child's own resolver, which is what actually drops them. It returns a path in every other mode, so nothing changes there; the module's `EXPORT_MARKER` is restored afterwards) |

| 287 | **Staging checksums are filed by output FILENAME.** `dest_map[final_out.name]` collapses two sources with the same basename — `a/photo.jpg` and `b/photo.jpg`, both becoming `photo.jxl` in different destination folders — to one key: both hashes were filed under one folder and the other got NONE (verified: folder `a` ended with an empty `checksums.md5`). The "unmatched" fallback then appended lines to the first successful task's folder, claiming coverage of a file that folder never received, so a later decode there verifies a good file against a foreign hash and reports MD5-FAIL | transcoder | ✅ FIXED (the md5 is already in each result, so nothing is parsed back: each checksum is written to the folder its own output landed in, and only for outputs that actually reached `moved_finals` — a checksum must never claim coverage of a file still in staging) |
| 288 | **Two gates the three scripts disagreed on.** The transcoder's libjxl check tested `(0, 11)` while its message named 0.11.2, so cjxl 0.11.0/0.11.1 passed in silence there and were warned about by the other two; and its `_get_exiftool_cmd` ordered the candidates differently, so a machine carrying both `exiftool-k` and `exiftool(-k)` got a different binary from this script than from the others | transcoder | ✅ FIXED (patch level checked, for djxl as well as cjxl; candidate order aligned. `_get_exiftool_cmd`, `_tool_at_least` and `_promote_from_staging` added to `SHARED_HELPERS`. `_cjxl_buffering_flag` is deliberately NOT unified — the transcoder invokes the bare `cjxl` everywhere and must version-check the binary it actually runs, while the encoder has a configurable path; both copies now say so, per the parity test's own rule for deliberate differences) |
| 289 | `--mode 1` ignores the output positional. The encoder and decoder have warned about that since v1.9.3; the transcoder silently discarded it, which looked like the destination had been honored | transcoder | ✅ FIXED (same warning, naming the folder the outputs actually go to) |

| 290 | **The low-severity batch.** None destroys data; each makes the tool lie a little. (a) `_read_source_markers_batch` matched exiftool's `SourceFile` without normcase, so a recased path left both markers None — read downstream as "no marker at all" and refused as "written by an older version", a reason that sends the user after the wrong problem. (b) `--dry-run` CREATED `TEMP_DIR` and the staging folder while validating them. (c) `--provenance` and `--no-adopt-scan` were inert without `--delete-source` (and `--provenance` outside a collapsing mode) with nothing said. (d) The dry-run summary reported `errors: 0` beside a non-empty `failures` list. (e) `_delete_stats` was never reset between runs in one process, so a second run inherited the first one's deletion totals. (f) The transcoder logged `Sources DELETED: 0 \| kept by a gate: 0` at the TOP of every run, from `setup_logger`. (g) `_file_content_id` re-hashed the whole source once per PAGE — 2.1 GB of reads for a 700 MB three-page scan. (h) The delete gate's mode-6/7 line hardcoded `16B_JXL` for every direction, so a JXL→TIFF run was told its originals would land somewhere they never do. (i) A hand-edited config whose `presets` is a list, or one preset saved as a string, took down the preset menu, `--list-presets` and the main-menu counter with a traceback. (j) `_last_child_summary` was only cleared on the manifest path, so a single run whose child died before emitting one reported the PREVIOUS run's numbers. (k) The manifest collision guard used the lossless resolver for the lossy convert directions | all four | ✅ FIXED (each with a regression test in `tests/test_round29_lows.py`; the content-id cache is keyed on path+size+mtime so a source edited mid-run is never served a stale hash. (k) never produced a wrong VERDICT — both entries were resolved by the same wrong function and the difference is a constant folder name — but it is fixed so the next per-direction rule added to either resolver does not inherit a guard that disagrees with the run) |
| — | **Declined:** a missing external tool exits 1 in all three scripts, and the audit suggested 2 ("nothing ran"). Left at 1: all three already agree, and after #277 the wrapper reports exit 2 as a safety abort, so the change would swap one misleading message for another. The exit-code contract deserves its own pass, not a drive-by in a low-severity batch | — | ⏸️ NOT CHANGED (deliberate) |

| 291 | **The new mode-1 warning names the wrong folder.** It picked the subfolder from `auto_decode`, which `determine_command` returns False for the CONVERT command even when converting a JXL, so a lossy JXL→JPEG run was told `converted_jxl/` while it writes `recovered_jpeg/`. It also ignored `--output-name`, which overrides the folder for convert modes 1 and 3 | transcoder | ✅ FIXED (and the deeper problem it exposed: for a DIRECTORY input the convert direction is not knowable in `main()` at all — `cmd_convert` scans first and falls back to `from_jxl` only when the folder holds no JPEG/PNG. It now names the folder exactly where it can (transcode, single file, `--output-name`) and says the content decides where it cannot, instead of guessing) |
| 292 | **The new inert-flag warnings hand-wrote the collapse test.** `mode == 0 and args.output is not None` instead of `_run_collapses_structure`, the predicate added in #280 two commits earlier. Mode 0 with an output folder EQUAL to the source writes in place and collapses nothing, so the ad-hoc test warned about `--strip`/`--none` where there was nothing to warn about and stayed silent about an inert `--provenance` | encoder, decoder | ✅ FIXED (all three sites ask the predicate) |
| 293 | **The rewritten staging-checksum block deletes the staging `checksums.md5` even when a move failed.** #287 correctly refuses to file a checksum for an output still in staging — and then threw away the only other copy of that hash, so a file recovered from staging by hand had lost its provenance for good | transcoder | ✅ FIXED (the staging db is kept, and its location logged, when any output is stranded; still swept when every one was promoted) |
| 294 | **The `--allow-incomplete-groups` banner points the wrong way, and the advice does not fit every case.** It said the affected groups are "named above" when they are named below (the banner is printed during flag validation, the groups are built later); and both the group warning and the delete gate's KEEP line told the disagreeing-markers case to "re-run against the folder holding every page", which sends the user after a page that may not be missing at all | decoder | ✅ FIXED (`_incomplete_groups` carries the KIND — truncated vs inconsistent — so the group warning and the KEEP line both give advice that matches. The KEEP line was a second site the audit did not name) |
| 295 | **`_confirm_lossy_delete_skipped` documents a contract it does not have.** Annotated `-> bool` and documented as returning False on cancel, which no path ever did, so `if not ...: return False` at both call sites was dead code stating a rule that does not exist. Its `input()` had no `EOFError` guard either, so a closed stdin raised out of a DELETE gate instead of declining | wrapper | ✅ FIXED (`-> None`, the docstring states what it really does, the dead guards are gone, and EOF/Ctrl+C count as declining — which turns `delete_skipped` off and keeps the originals, like every other gate here) |
| 296 | `_DEFAULT_INFO` lost the `pages` key added to the marker dicts in #282. Harmless today only because that one key is read with `.get()` while every other is read by index — a `KeyError` waiting for whoever follows the surrounding pattern | decoder | ✅ FIXED (found while verifying the audit's list, not reported by it) |

**On #290's claim of coverage:** the row above said "each with a regression
test", and two items did not have one — (j) `_last_child_summary` and (k) the
resolver-by-direction in the collision guard. Both were verified by reading and
neither was pinned by a test, which is exactly the gap that lets a fix rot. They
have tests now; the claim is true as written. Flagged by the same follow-up
audit that found #291-#295.

**Still open after this round:** the decoder and the transcoder have no
migration path for an archive written before the markers existed (#271's gap,
now stated honestly instead of pointing at a flag that does not exist). Porting
adopt to the decoder is mechanical — decode the JXL, compare against the
existing TIFF, which is real proof. The transcoder's lossless JXL→JPEG path has
proof too (jbrd/MD5), but its lossy convert directions have none, so what
"adopt" would even mean there is a design question, not a port.

---

## Round-28 audit (2026-08-07)

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

## Round-27 audit (2026-08-07)

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

## Round-26 audit (2026-08-07)

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

## Round-25 audit (2026-08-06)

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

## Round-24 audit (2026-08-06)

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

## Round-23 audit — post-v1.9.1 (2026-08-05)

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

*Renumbered on 2026-10-07: the first row was numbered #209, a number the [v1.8.1 audit](#v181--the-audit-release-2026-07) section already used; it is now #495. The rest of this section kept its numbers.*

A fresh four-script audit (wrapper last), every finding reproduced against the
pre-fix code before the fix was accepted. 47 new regression tests
(`tests/test_audit_priority1-4.py`); full suite went 490 → 537. Two of the
audit's suspected bugs were **proven false positives with real files** and are
listed as such at the bottom.

### Critical / data-safety

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 495 | **Mode-8 delete gate certified a file the run did not deliver.** With staging + overwrite of a pre-existing output, a FAILED `shutil.move` (locked destination, ACL) left the stale old file at the final path: it passed `exists()` + integrity, and the source was deleted while the fresh, verified output sat in staging under a UUID name. Same hole in all three children (decoder JXL→TIFF, encoder TIFF→JXL, both transcoder transcode and convert gates) | all 3 | ✅ FIXED (each group tracks which finals actually left staging — `moved_finals` — and the delete gate requires membership; `process_group_convert` now returns `(results, moved_finals)`) |
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

## v1.8.3 — Manifest run reporting (2026-07-27)

*Renumbered on 2026-10-07: these entries were first numbered #207–#208, numbers the [v1.8.1 audit](#v181--the-audit-release-2026-07) section already used. New number = old + 286.*

Found by running a 3-entry manifest over folders holding thousands of files each:
the user starts it, leaves, and comes back hours later to find out whether
anything broke.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 493 | A manifest run reported only `3 OK \| 0 skipped \| 0 errors` — **entries**, not files. Each entry is a separate child with its own log file, so the per-folder totals scrolled away and the only surviving number counted folders. Answering "did any file fail?" meant opening N logs by hand | wrapper | ✅ FIXED (children emit a machine-readable summary under `--summary-json`; the wrapper aggregates it into a per-entry table, a file-level TOTAL, and the list of failed files with their paths) |
| 494 | **Miscounted:** a corrupt or truncated TIFF was reported as `skipped by multipage policy`. `convert_multipage()` returns an empty list both when a policy asked for the file to be dropped (`--multipage-mode skip`, `--thumbnail-mode exclude`) and when the file has no readable pages at all — the caller could not tell them apart, so a damaged file was filed under a policy that never asked for it, and vanished into the `skipped` count | encoder | ✅ FIXED (new `UnreadableTiff`, raised when the analyzer found neither a real page nor a thumbnail; counted, logged and reported in its own bucket) |

**Why "corrupt" is not an error.** A damaged input is not a failed run: exit codes
keep their current meaning (`1` = the process failed on a file), so automation
reading them does not start firing on broken photos. The count gets its own
column and its own section in the summary instead.

**How the split is decided.** No heuristic: a healthy TIFF always has at least one
page, so "the analyzer returned neither a real page nor a thumbnail" is a reliable
signal. In `split_all` it is stronger still — that mode encodes every page there
is, so an empty result can only mean the file has none.

---

## Post-v1.8.1 — Real-batch usability fixes (2026-07-27)

*Renumbered on 2026-10-07: these entries were first numbered #195–#206, numbers the [v1.8.1 audit](#v181--the-audit-release-2026-07) section already used. New number = old + 286.*

Found by running a 4762-file Capture One library through mode 6 — the kind of
scale the synthetic test suite never reproduces.

| # | Bug | Script | Status |
|---|-----|--------|--------|
| 481 | Planning phase opened every TIFF serially with no output at all: on an external drive a large library sat silent for minutes and looked hung | encoder | ✅ FIXED (scan runs on a thread pool capped at 16, logs `Analyzing TIFF pages (N workers)`, progress every ~5%, and the elapsed time; plan order still follows input order) |
| 482 | `--thumbnail-mode exclude` logged one WARNING per file — an export library where every TIFF has a preview page produced thousands of lines and buried the real errors, for a drop the user had explicitly requested | encoder | ✅ FIXED (counted and reported once in the run summary; `--warn-thumbnail-discard` restores the per-file lines) |
| 483 | Thumbnails dropped in `split` mode were counted in `_multipage_ignored`, so the run summary blamed `--multipage-mode ignore` and told the user to "re-run with --multipage-mode split" — which is what they had already done | encoder | ✅ FIXED (separate `_thumbnails_dropped` counter and its own summary line) |
| 484 | Per-file discard warnings were uncapped in `ignore` mode too | encoder | ✅ FIXED (`DISCARD_WARN_LIMIT = 20`, then a suppression notice; totals always in the summary) |
| 485 | `--dry-run` returned before the discard summary, so a dry run never reported dropped pages once the per-file warnings were quieted | encoder | ✅ FIXED (`_log_discard_summary()` called from both exits) |
| 486 | Wizard asked "Thumbnail handling when splitting" after `split_all`, where the encoder ignores the answer | wrapper | ✅ FIXED (question asked only for `split`; both prompts now list what each mode does, and the Step 7 summary states `split_all` always includes thumbnails) |
| 487 | Duplicate-output abort did not explain the cause; nested marker folders (`Untitled Export/Untitled Export/`) collapse onto one destination in modes 6/7 | all 3 | ✅ FIXED (abort prints a hint naming the one-level collapse rule) |
| 488 | **Data loss:** mode 8 + `--delete-source` deleted a multi-page TIFF after encoding page 0 only. Every existing gate passed — the single JXL written *is* valid and complete — so nothing downstream could notice the other pages existed only in the source. Reproduced: 3-real-page TIFF in, 1 JXL out, source unlinked | encoder | ✅ FIXED (planning records which sources lost real pages; the delete gate refuses them: `KEEP source (pages were discarded...)`) |
| 489 | `--multipage-mode` defaulted to `ignore`, i.e. the default silently dropped pages | encoder + wrapper | ✅ CHANGED (default is now `split`; a single-real-page TIFF still yields exactly `photo.jxl`, so ordinary photos are unaffected) |

| 490 | **Throughput:** the thread pool was fed one OUTPUT FOLDER at a time. A folder with fewer files than `--workers` could never fill the pool, and the pool drained at every folder boundary — modes 3/5/6/7 create one output folder per shoot, so a photo library ran at roughly one worker (CPU ~4% with `--workers 12`) | encoder | ✅ FIXED (one pool for the whole run; staging still flushes per folder, fired when that folder's last file lands). Measured on 8 real 45 MP TIFFs, 8 workers: **33s → 10s** when spread over 8 folders (10s in a single folder, 47s fully serial) |
| 491 | Manifest CSV written as UTF-8 **without BOM**: Excel opens it with the system ANSI codepage, so `240419_山羊公園_長瀞岩畳` displayed as 文字化け — and saving from there wrote the broken bytes back | wrapper | ✅ FIXED (written as `utf-8-sig`; readers use `utf-8-sig`, which also strips a BOM that would otherwise land in the first header cell and turn the header into a data row) |
| 492 | A manifest re-saved by Excel in the ANSI codepage crashed the reader with `UnicodeDecodeError` | wrapper | ✅ FIXED (refused with an actionable message). **Deliberately not** decoded with a guessed codepage: a wrong guess yields a plausible path pointing elsewhere, and these paths drive a converter that deletes sources in mode 8. Pure-ASCII manifests are valid UTF-8, so hand-written files are unaffected |

**Thumbnail pages are deliberately NOT covered by fix 488.** A thumbnail is a
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
