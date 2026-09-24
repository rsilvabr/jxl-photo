# Version history

Detailed notes for superseded releases, newest first. The current release is
documented in the [main README](../README.md) — which also carries a one-line
summary table of every release — and per-release notes are published as
[GitHub Releases](https://github.com/rsilvabr/jxl-photo/releases).

For the complete list of individual fixes see
[bug_tracking_since_v1.0.md](bug_tracking_since_v1.0.md) and
[new_features_since_v1.0.md](new_features_since_v1.0.md).

---

## v2.1.1_beta1

**Released 2026-09-20 as a pre-release, superseded by v2.2.0** (which ships all of it). Maintenance beta on top of v2.1.0 — ten fixes from the second audit of the recompressor release (round 37, bugs #347–#356), all in the safety/reporting layer. No command line and no file format changes.

- **Dry runs no longer lie.** The decoder and recompressor skipped the provenance refusal gate in dry runs — the simulation promised outputs the real run refuses, with `errors: 0` in the summary. Both now preview the refusals (`DRY | would REFUSE`, counted as predicted errors), and the recompressor dry run exits 0.
- **Outputs are never written under their final name.** A run killed externally used to leave a truncated file at the final path with a fresh mtime — which the next smart-sync run then treated as up to date forever. All four scripts now write a uuid temp beside the final and swap it in with an atomic same-folder `os.replace` only after the integrity check.
- **`checksums.md5` appends are serialized across processes.** Two manifest entries targeting one folder are two child processes; the thread lock only serialized one, and appends interleaved mid-line. A sibling `.lock` file (fail-closed: an untaken lock skips the line, never a torn write).
- **Wrapper: recompressor policies survive the wizard.** Step 6A rebuilt the advanced options from scratch and dropped `on_downgrade`/`on_regeneration`/`on_unknown`/`jbrd_policy`/`no_keep_smaller` (the child fell back to `ask` — a silent skip on the wrapper's pipe); the manifest builder never emitted `--on-unknown`/`--jbrd-policy` at all. The wizard now asks both on the recompressor path and carries the rest through every branch.
- **Encoder: `--encode-tag xmp` merges the EXIF Software chain.** A TIFF recovered from a `--encode-tag software` JXL carries the lineage chain in EXIF Software; the xmp branch left it there beside the new dc:Description record, and the recompressor trusted the stale one. Both fields are now merged into dc:Description and the machine block is stripped from Software (unrelated text kept).
- **Recompressor: keep-smaller fallback passes the MD5 gate.** The verbatim-copy proof keyed on `action == "copy"`, but the keep-smaller fallback reports status `"copied"` with action still `"convert"` — a corrupt copy certified the deletion of its source.
- Smaller: log filenames carry the pid (two runs in the same second shared one log); the decoder counts a missing final output as a KEEP instead of leaving the gate silently; jbrd repair temps no longer wear a `.jxl` name (a crash left a fake input for the next scan) and honor `TEMP_DIR`; `--repair-jbrd` no longer requires cjxl (repair only needs djxl ≥ 0.12 + exiftool); the wrapper's mode-6 collision mirror matches the real finder's decoder-output skip.

Every fix has a regression test proven to fail against the pre-fix code (`tests/test_audit_round37.py`, 23 tests). **1357 tests** in the suite.

---

## v2.1.0

**Released 2026-09-20, superseded by v2.2.0 (v2.1.1_beta1 was a pre-release in between) and kept here for reference.** A new script joins the toolkit: **`jxl_recompressor.py`** — and a new destination in the wrapper ("JXL (smaller)"). No existing command line changes; nothing about TIFF/JPEG conversion moved.

### Why

Archives were written at `d=0.05–0.1` when disk was cheap. When storage runs short, the right move is a **single** generation of lossy re-encode to `d=1.0–2.0` — not several, and never blind. The recompressor is the batch tool for that move.

### What it guarantees

- **Metadata survives.** cjxl carries nothing across a JXL→JXL re-encode, so the script copies EXIF/XMP/IPTC with exiftool, keeps the base64 ICC and every `jxlphoto-*` provenance marker verbatim, and restamps `cjxl d=/e=` with the **new** parameters (the old tag is replaced wherever it lived; unrelated text is kept).
- **Counterproductive requests are caught.** Each file's recorded parameters are compared against the request: same distance, or a *lower* distance than an already-lossy source, cannot gain anything. The default policy asks once per batch (unattended runs fail closed to *skip*); `copy`/`skip`/`convert` are selectable per policy (`--on-downgrade`, `--on-unknown`).
- **JPEG-recoverable JXLs (jbrd) are copied verbatim by default** — recompressing would destroy the bit-exact JPEG recovery and the MD5 binding the transcoder's delete gates rely on.
- **Keep-smaller net.** A re-encode that comes out not-smaller than the source is replaced by the original bytes (in place: the original is simply kept).
- **Same delete machinery as v2.0.x**: `--delete-source`, `--delete-skipped`, `--verify-roundtrip`, provenance checks, three confirmations — and in the wrapper, the same execution-time token gate.

### Generation counter in the encode record

The encode record is now an append-only lineage chain with a generation counter: `gen=N | cjxl d=X e=Y | cjxl d=... e=...` (any user caption stays first — the field is visible in Windows Properties). Every encode or recompression **appends** one entry (the recompressor used to *replace* the record, erasing the history), and `gen=N` counts the **lossy** (`d>0`) entries — reconciled from the chain on every write via `max(stored, count)`, never incremented, so a hand-edited field self-corrects on the next pass. The encoder also no longer deduplicates: re-encoding a decoder-produced TIFF at identical d/e appends a second entry, because decode-then-re-encode is exactly where a generation of loss happens.

Why it matters: controlled chain tests showed that at a fixed byte budget each extra lossy generation costs ~0.2–0.6 dB of PSNR on top of what the byte reduction alone costs — and the marginal cost grows with the number of generations — while the recorded nominal `d` stops describing the result: a 19-generation chain landed 9 dB below a single direct encode at the same file size (nominal d≈1.5, perceptual quality of d≈4–7). The **`--on-regeneration`** policy (`ask`/`copy`/`skip`/`convert`, default `ask`, unattended = skip) fires when a file has **already been lossy-recompressed at least once** (`gen >= 2`) and the request adds another — closing the hole where a slow drip of `d=0.1 → 1.0 → 1.5 → 2.0` runs years apart passed every per-step check. The threshold is 2, not 1, because every lossy file this toolkit's encoder produces is born at `gen=1`: guarding at 1 turned the recompressor's main use case (encoder previews → final archive) into an `ask` that silently skipped everything headless. It sits beside `--on-downgrade`/`--on-unknown`, unchanged, and when two policies fire the more conservative action wins. The wrapper asks the question up front, like the other policies.

### ⚠️ `--encode-tag off` now strips the record (encoder)

The encoder's `off` previously only *omitted* the record — but a TIFF produced by the decoder carries the JXL's `dc:Description` along, so the stale `cjxl d=/e=` chain survived into a file it did not describe, and the recompressor would trust it. Now `off` matches the recompressor: it records nothing **and** strips any `gen=`/`cjxl` record from the copied Description/Software (unrelated text is kept). It remains the only way to deliberately discard the lineage. If you relied on `off` carrying old metadata through, that no longer happens.

Legacy archives need no migration: a chain with no `gen=` reads as `gen =` (lossy entry count), exactly what it always meant.

### New: `--modular on|off` (encoder) — measured: not for photos

The lossy encoder is now selectable: `--modular on` forces the Modular encoder for lossy output (default off — cjxl's VarDCT decides). We measured before shipping: 7 real masters (Nikon Zf/Z8 ProPhoto 16-bit TIFFs, medium-format film scan, IR dust-channel scan, negative scan), SSIMULACRA2 at d=0.05/0.10 — quality is a wash (every margin ≤ 0.39), VarDCT smaller in 14/14 files (modular up to +33%) and 20–100× faster. So the flag exists for what Modular was built for (screenshots/graphics batches), the wrapper only asks inside Step 6A (advanced options), the default behavior is untouched, and the disk-space preflight estimate now matches the chosen encoder.

### New: decode-side remedies for the jbrd marker damage

A failed `djxl --reconstruct_jpeg` on the JXL→JPEG path now names both remedies instead of a bare djxl error. **`--auto-repair-jbrd`** repairs a copy in the system temp and decodes from it — the JXL is never modified, and the delete gate never deletes that source in the same run (the recovered JPEG has identical image data but re-serialized XMP bytes). In the wrapper: the auto-repair is a Step 6A question on JXL→JPEG, and `--repair-jbrd` is **main menu option 8** (audit by default).

Verified against real files, end to end: Capture One ProPhoto 16-bit exports and film scans (including RGB+IR scans where the IR channel is its own grayscale page) encoded to `d=0.1`, recompressed to `d=1.0` (archive 264 MB → 44 MB), every marker (multi-page group, grayscale, provenance, ICC) carried over verbatim, the recompressed archive decoded back to multi-page TIFFs with page structure, dtype, photometric and ICC placement identical to the originals (33–53 dB PSNR, no brightness shift), and `--delete-source --verify-roundtrip` deleting only after per-file pixel verification passed. **1334 tests** in the suite.

---

## v2.0.3

**Released 2026-08-23.** Bug fixes only. No command line and no file format changes: v2.0.2 commands keep working exactly as written. Three audit rounds (32–34) across all four scripts, verified against the real fixtures — the 16-bit Capture One exports and the RGB+IR film scans — not just the mocked suite.

### The JXL → JPEG delete gates trusted the JXL's name

`checksums.md5` holds the original JPEG's hash keyed by the JXL's **filename**. A delete-skipped run compared that stored hash against the recovered JPEG on disk — and the JXL being deleted never entered the comparison. Replace `photo.jxl` with a different, same-named JXL (a re-export, a restore mix-up) and the old archive still matched, so the replacement was deleted having never been archived. This was documented as "provenance PROVEN"; it was proven for the name, not the bytes.

The gates now bind the file's **content**. The encoder stores the JXL's own MD5 beside the original's (a `<name>.jxl-md5` companion line; old databases keep working), and a delete run compares it against the JXL in front of it. Archives written before this release fall back to a real `djxl --reconstruct_jpeg` comparison. When neither proof can run, the source is kept. Verified end to end with the real tools: a swapped JXL is kept with a message saying exactly why, on both the new and the legacy path.

### An RGB ICC was attached to grayscale output

On the `--to-srgb` / `--icc-profile` paths (transcoder and decoder), grayscale output received the **RGB** profile — which PNG rejects as a mismatched `iCCP` and which is equally wrong on a 1-component JPEG. Every film-scan IR page took this path. Grayscale images now keep their pixels and skip the profile. *(Round 32.)*

### The rest

- **The wrapper's manifest collision scan compares its two entry families against each other again.** A round-31 performance change bucketed entries into two families and only compared within each — a mode-6 entry and an in-place entry aimed at the marker's output folder could race on the same outputs from two child processes. Cross-family containment now forces the full scan; disjoint libraries keep the fast path.
- **A failed move out of staging no longer deletes a good pre-existing destination.** The cleanup assumed "staging copy survived ⇒ destination is partial", which is wrong when the move failed before writing (a locked or read-only destination) or after the copy but before the unlink (a complete copy). The destination's identity is snapshotted before the move; only a provably-written, provably-incomplete result is removed. All three backends, kept identical by the parity test.
- **Re-encoding a lost page of a pre-v2.0.2 multi-page archive heals it again.** v2.0.2's group-id change made the id cover the page set, so the re-encoded page landed in a different group than its surviving siblings — the decoder saw two truncated groups and sent the user hunting for a page that is not missing. The encoder now adopts the siblings' legacy id when they prove it (unanimous, matching the old formula, all within the planned page set), and the decoder recognizes the mixed-version shape and advises a full re-encode instead.
- **The decoder treats a failed metadata copy as an error that blocks the delete.** exiftool's exit code was never checked: a failed copy silently dropped the metadata, the pixel-valid TIFF passed the integrity gate, and `--delete-source` removed the JXL — the only remaining copy of that metadata.
- **The decoder's multi-page marker reader normalizes path case**, like its sibling already did; an exiftool reply with a differently-cased drive letter silently dropped the group markers, and a delete run then peeled a split apart page by page.
- **The `--delete-source is ARMED` dry-run notice now prints in all three transcoder entry points** — v2.0.1's changelog claimed it did; only one of the three had it.
- Plus a per-script batch of lows: the wrapper charges the delete token only after checking the child script exists, `--list-presets` works without codecs, Ctrl+C cancels cleanly (exit 130, summary still printed), hand-written mode-7 manifests get their `--export-subfolder`, corrupt session files are refused field by field; the encoder counts skipped files in the real-run summary, validates `TEMP2_DIR`, classifies zero-page TIFFs as corrupt in every multipage mode, and no longer lets mode 6 honor the mode-7 subfolder exemption; the transcoder asks for the requested bit depth on the ICC paths and stops charging the lossy token for PNG → JXL at `--distance 0`.

**1129 tests**, up from 1027 — every fix-targeted test verified failing against the pre-fix code. Round trips re-verified on the real fixtures: TIFF → JXL → TIFF pixel-identical (16-bit ProPhoto exports; the RGB+IR scan, thumbnail excluded, IR page grayscale), JPEG ↔ JXL MD5-exact, and the swapped-JXL scenario above run end to end. Full detail in [bug tracking](bug_tracking_since_v1.0.md) (rounds 32–34).

---

## v2.0.2

**Released 2026-08-19, superseded by v2.0.3 and kept here for reference.** Bug fixes only. No command line and no file format changes, and JXLs from earlier versions are read the same way.

Converting a file has been clean in every audit round. This one looked at converting it a **second** time, and that was not.

### Re-archiving a scan merged a page from the previous round

A film scan is `[image, thumbnail, IR]`, and `--thumbnail-mode exclude` leaves the real pages on their original indices — so the archive is pages `{0, 2}`: `scan.jxl` and `scan_page2.jxl`. Decode that and the TIFF has two pages, `[image, IR]`. Encode it again in the same folder and the IR page is index 1, so you get `scan.jxl` and `scan_page1.jxl` while `scan_page2.jxl` from the first round stays where it was.

The multi-page group id was a hash of the source *path*, identical both times, so all three files claimed the same group and the decoder wrote them into one TIFF with the IR page twice — a structurally valid file, reported as `0 errors`.

Nothing was destroyed: the group was flagged as incomplete and `--delete-source` kept the sources. But the TIFF was wrong and the run called itself a success. Three changes close it — the group id now covers the pages a split produces, so a leftover cannot join a later one; the decoder refuses to merge a group holding more members than the split recorded, telling them apart by the source-bytes id each output carries and failing closed when it cannot; and the encoder names leftovers it finds in the destination. The second of those also repairs folders **already** in this state, where every member shares one id.

### The rest

- A manifest that deletes now asks the same three questions as the `[D]` menu: verify each output against its source, whether to cover originals already converted, and how an existing output is matched to the source replacing it. A manifest does not go through that menu, so it reached the deletion with the structural check alone.
- "Incomplete group" gave one message and one piece of advice for two opposite problems — pages missing and pages extra.
- Mode-6 manifests no longer walk every library before starting. Entries writing inside their own Source cannot collide when the Sources are disjoint, so `G:\2024` / `G:\2025` / `G:\2026` begins converting immediately. Sources are now checked for overlap rather than assumed disjoint.
- `Added JPEG preview ... with ICC` was logged with no ICC attached — every film-scan IR page, where the profile is inherited and deliberately not written.
- `CreatorTool` went into an exiftool argfile unsanitised in the transcoder, which had no `_argfile_safe` at all.
- One of two readers split a single-value `dc:Relation` on commas, enough to tear `Smith, John` in half.
- A JXL → JPEG preset showed a `d=` it never uses, left over from an earlier TIFF run.
- The cautious ICC test held its lock across the probe, stopping every worker the first time a profile appeared.
- The `output` positional was documented "mode 0 only"; mode 2 takes it too.

**1017 tests**, up from 994 — eighteen of the twenty new ones verified failing against the pre-fix code. Full detail in [bug tracking](bug_tracking_since_v1.0.md) (round 31).

---

## v2.0.1

**Released 2026-08-13, superseded by v2.0.2 and kept here for reference.** A maintenance release on top of v2.0.0. Nothing here changes a command line or a file format: v2.0.0 commands keep working exactly as written.

v2.0.0 shipped a lot of new machinery around the moment a file is deleted, so this round audited it the way the [AGENTS notes](../AGENTS.md) ask for — against real photos rather than the synthetic test suite. The fixtures were 16-bit Capture One exports, a 260 MB Z8 export, and the 756 MB RGB+IR film scans (RGB page + embedded preview + IR `MASK` page, carrying a 217 KB scanner ICC).

**The conversion path came out clean, end to end.** Every lossless round trip was pixel-identical with the ICC, `SubfileType` and page structure intact — the IR `MASK` page included. JPEG ↔ JXL recovered byte-identical with MD5 PASS. Alpha, pure grayscale, 8-bit and CJK/accented paths all survived. The v2.0.0 gates held under real files too: the cross-run provenance refusal, the incomplete-split KEEP, staging + delete, and the scanner-ICC lossy workaround each did what the READMEs promise.

The six defects are all *around* that core:

- **The delete confirmation counted the wrong files in mode 7.** The wizard's "About to delete originals" panel applied the export marker but not the `--export-subfolder`, so it counted every subfolder under the marker for a run that converts one of them. With `_EXPORT` holding `16B_TIFF`/`AdobeRGB`/`sRGB` it announced 2 files (TIFF → JXL) or 3 (JXL → TIFF) for a run that touches 1 — and a *disjoint* set, not a superset. That count is the documented way a wrong folder is caught before the HHMM token is charged, in the mode the READMEs call the most common Capture One workflow. The run itself always converted the right files; only the preview lied.
- **A manifest run leaked its export marker into the rest of the menu session**, which made the count above depend on what had been run earlier. Both sites now share one helper so they cannot drift apart again.
- **`--multipage-mode split_all` reported `Thumbnail: exclude`** in the opening banner while encoding every thumbnail, two lines above its own log of a written `*_thumbnail.jxl`.
- **The decoder README still documented the `SubfileType=4` (MASK) downgrade** that v2.0.0 had already fixed — so someone reading it about their own film scans was told the IR page is demoted when it is not.
- **The dependency status bar was unreadable in a redirected log**: `✓` and `✗` both became `?`, so a scheduled-task log could not say which tool was missing.
- One comment in the integrity gate understated its cost by a whole page.

**994 tests**, up from 981. Ten of the thirteen new ones were verified failing against the pre-fix code; the other three are controls that must pass on both sides. Full detail in [bug tracking](bug_tracking_since_v1.0.md) (round 30).

---

## v2.0.0

**Released 2026-08-09, superseded by v2.0.1 and kept here for reference.** Everything below landed after v1.9.1, across seven internal audit rounds. Ordinary conversion is unchanged — same pixels, same ICC, same metadata, validated again on real Capture One exports and the 756 MB RGB+IR film scans. What changed is everything around the moment a file is **deleted**.

### Upgrading from v1.9.1

Two behaviour changes can affect a command line you already have. Both are in the destructive path; nothing else needs attention.

**1. `--delete-source` is honoured in every mode.** v1.9.1 had `if DELETE_SOURCE and mode == 8` in all three scripts — outside mode 8 the flag was accepted and ignored. If you have a saved command, preset, manifest or scheduled task using `--delete-source` with any other mode, it deleted nothing before and deletes the originals now.

*What to do:* re-read any stored command that carries `--delete-source`. If the intent was "keep both", drop the flag. If the intent was archival, it now works as written — try it on one folder with `--dry-run` first, which reports exactly which sources would go.

**2. Outputs written before v2.0.0 can be refused.** In the folder-collapsing modes — 2, 4, 5, 6, 7, and now mode 0 when given an output folder — two sources in different folders can resolve to the same output. A run that overwrites an existing output *and* deletes the source that produced it would destroy the earlier photo, whose own original is already gone. So every conversion now records which source made it, and a delete run refuses an output it cannot tie to the source in front of it. An archive built before this release carries no such record.

*What to do, by direction:*

| Direction | Migration |
|---|---|
| TIFF → JXL | `--provenance adopt` once. Each unrecorded output is decoded, compared against its source, and stamped — a one-time healing pass, after which the strict check applies again. `--no-adopt-scan` skips the verification if you would rather trust the pairing. |
| JXL → TIFF, JPEG ↔ JXL | No adopt yet. Use a structure-preserving mode (0/1/3/8) for those folders, where nothing can collide and nothing is refused. |

**Also worth knowing if you script the tools:** a provenance refusal is a failure — it counts into the error total and exits `1`, where a refused run used to exit `0`. And outputs now carry `jxlphoto-*` markers in `XMP-dc:Relation`; they are stripped from anything this toolkit reconstructs, so a round trip is unaffected.

### Archive and replace

- **`--delete-source` works in every mode (0–8)**, in all three scripts. Convert into a separate tree and drop the originals — the workflow that was previously impossible without doing the move yourself.
- A source is deleted only when: every page of it converted **this run**; no page was dropped by the multi-page policy; the output exists at its **final** path (and if staging was used, the move there actually succeeded — a stale file already sitting there does not count); it passes the integrity check **there**; and, with `--verify-roundtrip`, it decodes back to the source pixels.
- **`--verify-roundtrip`** *(TIFF → JXL)* — the only gate that looks at pixels rather than at file structure. At `--distance 0` the decode must be identical; on a lossy run it is a brightness + PSNR sanity check that catches a black or scrambled encode. Opt-in: it costs one full decode per output.
- **`--delete-skipped`** — also delete sources whose output already exists, so an archive interrupted between the conversion and the unlink can be finished without re-encoding the library. Never acts on the timestamp: the output must exist and pass its checks.
- **Three gates before anything goes**, each more specific than the last: a plain y/N, then the concrete consequence (how many files, from which folder, to where), then a time token that cannot be answered by reflex. A dry run never charges the token.

### Provenance

- Every conversion records **which source produced it** — `jxlphoto-src` (the location) and `jxlphoto-srcsum` (the bytes), in the XMP `dc:Relation` bag.
- `--provenance path` (default, free) compares the recorded location · `content` also accepts matching source bytes, so it survives folders you moved · `adopt` (**TIFF → JXL only**) verifies and stamps an archive built before the markers existed.
- A mismatch always fails closed: not converted, nothing overwritten, nothing deleted. `adopt` relaxes "I cannot tell", never "I can tell it is wrong".
- The check also covers **mode 0 with an output folder**, which flattens every source into that folder exactly like mode 2.

### Multi-page and film scans

- A split now records **how many JXLs it produced** (`jxlphoto-pages`). A group that arrives with pages missing is reported and its sources kept — the short TIFF it produces is a perfectly valid file, so no integrity check, round trip or checksum downstream could tell it was incomplete.
- `--allow-incomplete-groups` deletes anyway, for a page that is genuinely lost.
- A gap in the page numbers is **not** treated as a missing page: `--thumbnail-mode exclude` drops the thumbnail and leaves the real pages on their original indices, so the ordinary `[real, thumb, real]` scan archives completely as pages `{0, 2}`.
- Scanner IR pages keep their role — `SubfileType=4` was being downgraded to 2 on decode.

### Robustness

- **A failed move out of staging no longer leaves a truncated file** at the destination with a fresh timestamp, which smart-sync would then skip forever. A destination volume that is full stops the run instead of producing one failure line per remaining file.
- **`--dry-run` touches nothing.** It no longer creates the staging or temp folders while validating them, and no longer stamped provenance markers into real files.
- **Refusals reach the exit code and the summary** in all three scripts and all their commands — an auto-mode run that refused every file used to exit 0 with an empty failure list.
- **A rejected command line says so**: the wrapper tells a usage error apart from a safety abort instead of reporting both as "aborted by a safety check".
- A manifest stops when a child aborts, `--clean-staging` never sweeps during a dry run, and messages emitted before the logger was configured now reach the log.
- Flags that do nothing in the given combination say so instead of looking effective.

### Under the hood

- 121 new regression tests (830 → 973), every one verified failing against the code before its fix.
- A parity test pins the helpers the four scripts deliberately duplicated, so a fix applied to one copy cannot silently miss the others.

---

## v1.9.1

**Released 2026-08-02, superseded by v2.0.0 and kept here for reference.** This is the release where the tool stops flying blind: a run now measures how much space it will need before it starts, stops cleanly instead of grinding when a disk fills, says something during a slow folder scan instead of looking frozen, and reports what it left behind in staging.

v1.9.1 itself is a small fix on top of v1.9.0: **manifest runs no longer stall silently before starting.** The cross-entry collision guard skips its full recursive scan for the per-source output modes (0/1/3/6/7/8), where every entry writes inside its own Source tree and a cross-entry collision is impossible — mode 6/7 manifests over large libraries now start immediately. When the scan does run (modes 2/4/5, where entries can share an output folder), each entry prints `Collision check: scanning <folder> ...` so the wait is visible. No changes to conversion logic; safe update for everyone.

> **Behaviour change in v1.9.0 — read this if you script the tools.** A run that fills its output volume now **aborts and exits 2** instead of failing every remaining file and exiting 1. Automation that treats "non-zero" as one bucket is unaffected; automation that distinguishes `1` (some files failed) from `2` (aborted) will now see `2` for a full disk — which is the retryable case.

Highlights (v1.9.0):

- **Space estimate before the run starts** *(TIFF → JXL only)* — `jxl_tiff_encoder.py` encodes three crops of your own files to measure this batch, then warns if the output will not fit staging or destination. Warns only, never blocks. `--no-preflight` skips it.
- **A full disk stops the run** — instead of failing every remaining file one by one. Queued files are reported as *not attempted*, and the run exits `2`.
- **Slow folder scans show progress** — a 25-second walk on an external drive no longer looks frozen. Fast local scans stay silent.
- **Staging leftovers are reported** — and `--clean-staging` sweeps the old ones.
- **Distances below 0.05 do nothing** — cjxl clamps them; `0.02` and `0.05` give byte-identical files. Use `--distance 0` for real lossless.
- **Three delete-gate bypasses closed** — `--delete-source` in the wrapper's expert-flags field could delete originals unattended.
- **Corrupt saved workflows are refused** — a hand-edited config no longer ends the run in a traceback.
- **Manifest entries with `..` refuse the whole file** — they used to be skipped while the run carried on.

Full release notes: [v1.9.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.9.1) · [v1.9.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.9.0) · [v1.8.4](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.4)

---

## v1.8.4

**Released 2026-07-28, superseded by v2.0.0 and kept here for reference.** Adds `--run-preset NAME`, which runs a saved preset without the menu and exits, so a recurring sync can live in Task Scheduler or cron. Sync is the default (`--overwrite` to redo everything) and `--dry-run` is never inherited from the stored run; presets that delete sources are refused unattended. `--list-presets` shows what is saved. Everything below came with v1.8.3 and is unchanged.

> **Breaking change (inherited from v1.8.0):** in `jxl_jpeg_transcoder.py`, modes **4 and 5 were swapped** — **4 = folder rename**, **5 = sibling folder**. Swap them in saved commands and manifests.

Highlights:

- **Your own distance in the menu** — entry `[2]` of Step 2 now shows the distance you set in option 4 (e.g. `d=0.05 — Your default`), instead of forcing a trip through `[4] Custom` on every single run. Ships as `0.1`, so nothing changes until you set it. `[4] Custom` comes pre-filled with the last value you used.
- **Manifest runs are repeatable** — `Repeat last workflow` now handles them: it re-reads the CSV (so edits you made in Excel count), skips the pointless input-folder question and goes straight to overwrite/sync and dry-run. Keeping a library in sync is two keystrokes; it used to mean walking through the whole wizard again.
- **Presets (option 7)** — save the last workflow under a name and run it later. Several recurring jobs (a nightly manifest sync, a per-shoot conversion) can coexist instead of overwriting one another in the single "last workflow" slot.
- **Settings that actually apply** — changing workers/quality/effort/distance in option 4 now also drives the next run, repeats included. They used to write a separate value that no run ever read, so editing them appeared to do nothing.
- **Manifest runs end with a real summary** — a per-folder table, a file-level TOTAL across every entry, and the **paths** of the files that failed. A multi-hour run over three folders used to end with `3 OK` (counting folders), the per-folder numbers already scrolled away into three separate logs. Also saved to `Logs/jxl_photo/<timestamp>.log`.
- **Corrupt files no longer hide in the `skipped` count** — a TIFF with no readable pages was reported as "skipped by multipage policy", a policy that never asked for it. It now has its own count and its own section. Exit codes are unchanged: a damaged input is not a failed run.
- **No silent data loss** — 16-bit decode failures, a "repaired" corrupt JXL, and weak delete verification now all fail loudly instead of quietly proceeding.
- **Multi-page TIFFs are safe by default** — every page is kept (`--multipage-mode split`) instead of just page 0, and mode 8 refuses to delete a source whose pages were dropped.
- **Every output is verified before being trusted** — a corrupt or partial JXL/JPEG/PNG/TIFF is caught and cleaned up instead of reported OK.
- **Manifests are Excel-safe and collision-checked** — UTF-8 BOM for non-ASCII paths, cross-entry output collisions refused up front.
- **Better throughput on large libraries** — the thread pool no longer stalls at folder boundaries.

Full release notes: [v1.8.4](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.4) · [v1.8.3](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.3) · [v1.8.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.2)

---

## What's New in v1.7

### Multi-Page TIFF Support
TIFFs with more than one page are now handled explicitly instead of silently discarding extra pages.

**TIFF → JXL encoder:**
- `--multipage-mode ignore` — encode only page 0 (original behavior, default)
- `--multipage-mode skip` — skip files that have more than one "real" page
- `--multipage-mode split` — encode each real page to a separate JXL (`photo.jxl`, `photo_page2.jxl`, ...)
- `--multipage-mode split_all` — encode every page, including thumbnails

Thumbnails are detected via standard TIFF `SubfileType` flags (`is_reduced` / `is_subifd`). When splitting, thumbnails can be excluded or included with a configurable suffix (`_thumbnail` by default).

**JXL → TIFF decoder:**
- Reconstructs multi-page TIFFs from pages that carry the encoder's XMP group marker (`jxlphoto-mpg:` in `XMP-dc:Relation`). Grouping is marker-based, not name-based, so independently-named files such as `scan.jxl` + `scan_page2.jxl` are never merged unless they were split by this encoder.
- Preserves per-page ICC profiles: each page is restored with its own ICC tag; pages that inherited ICC from IFD0 are reconstructed without an ICC tag, matching the original TIFF structure.
- Preserves grayscale pages and `SubfileType` role: single-channel pages are reconstructed as 2D grayscale, and inherited RGB ICC is not forced onto them. Non-standard `SubfileType` values (e.g. scanner IR/mask pages) are restored as `PAGE` semantics.
- Per-page bit depth policy: main pages stay 16-bit while 8-bit thumbnails are restored as 8-bit by default (`--depth-policy preserve_thumbnails`). Use `force16` for all 16-bit output or `preserve_original` to keep every page at its original bit depth.
- `--thumbnail-handling ignore` — ignore `_thumbnail.jxl` files
- `--thumbnail-handling include` — include thumbnails in the reconstructed TIFF (default)
- `--thumbnail-handling generate` — not yet implemented; falls back to `include`
- `--no-reconstruct-multipage` — disable multi-page reconstruction entirely

```bash
# Photos with main image + thumbnail → split JXLs
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode split --thumbnail-mode include --distance 0

# Reconstruct the original multi-page TIFF
python jxl_tiff_decoder.py "E:\photos_jxl" "E:\photos_reconstructed" --mode 2 --thumbnail-handling include

# Film scanner workflow with IR/mask page (grayscale)
python jxl_tiff_encoder.py "E:\film_scans" "E:\film_scans_jxl" --mode 2 --multipage-mode split_all --thumbnail-mode include
python jxl_tiff_decoder.py "E:\film_scans_jxl" "E:\film_scans_tiff" --mode 2 --thumbnail-handling include
```

> **⚠️ IR channel / Digital ICE warning:** If your scanner software (e.g. SilverFast, VueScan) uses the IR page as a hidden channel for Digital ICE / dust & scratch removal, converting the TIFF to JXL and back may break that feature. Those programs often rely on vendor-specific tags and exact page ordering beyond the standard TIFF `SubfileType`. This tool preserves the page as a standard grayscale `PAGE`, but the original scanner software may no longer recognize it as an IR mask. Test with one file before batch-processing important film scans.

JPEG previews are automatically skipped when reconstructing multi-page TIFFs.

> **Scanner color profile note:** Scanner ICC profiles (e.g. SilverFast `SFprofT`) can cause `cjxl` to produce very dark images in lossy mode. The encoder works around this by not embedding the ICC in the intermediate PNG and restoring it into the reconstructed TIFF. The JXL file may therefore display with shifted colors in some viewers, but the TIFF round-trip is accurate. For scanner workflows, treat JXL as the backup container and the reconstructed TIFF as the final image.

### v1.7.1 / v1.7.2

- **Cautious ICC strategy (v1.7.1)** — the default `--icc-png-strategy cautious` round-trip-tests each unseen ICC profile through cjxl+djxl and caches the verdict, so scanner profiles that darken lossy encodes are skipped automatically.
- **Audit fixes (v1.7.1)** — a batch crash on JXLs without `jbrd` became a per-file error, lossy convert preserves EXIF/XMP/IPTC via exiftool, `.jfif`/`.jpe` support, `--multipage-mode skip` uses the detected real page.
- **v1.7.2** — the wrapper's `--delete-source` confirmation no longer gets stuck on the main wizard path, and lossy convert keeps Exif/XMP before the codestream after the metadata copy (IrfanView-compatible).

Full release notes: [v1.7.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.1) · [v1.7.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.2)

---

## What's New in v1.6

### Audit-Driven Fixes
This release fixes issues found during an independent audit of v1.5.3:

- **Encoder staging concurrency fixed** — failed outputs no longer get promoted when using multiple workers
- **Auto mode routing fixed** — JPEG folders are now encoded, not decoded; `--format jpeg` actually produces JPEGs
- **Manifest modes preserved** — generated manifests now include a `Mode` column, so modes 6 and 7 survive execution
- **Basic mode 16-bit fidelity** — PNG decode now preserves full 16-bit data
- **Matrix mode for libjxl v0.11.x** — uses the correct `--color_space` token
- **CMYK TIFFs rejected early** — no more silent RGBA mis-encoding
- **Wizard cleanup** — removed non-functional "skip" option; custom Target ICC asks for the real file path

### ICC Alias Cleanup
Only `sRGB` remains as a built-in ICC alias. `Adobe RGB` and `ProPhoto RGB` aliases were removed because Pillow cannot generate them on the fly; use actual `.icc` profile files instead.

---

## What's New in v1.5

### JXL → JPEG Auto-Detect Mode
**Smart per-file detection** for mixed JXL archives:
- Files **with jbrd box** → lossless transcoding (original JPEG recovered)
- Files **without jbrd** → lossy conversion with configurable quality
- Processes entire folders automatically, routing each file to the optimal method

### Optional JPEG Preview in TIFF Output
JXL → TIFF conversion now supports **disabling the embedded JPEG preview**:
```bash
python jxl_tiff_decoder.py folder/ --no-preview  # Smaller files, no preview
```
Default behavior unchanged (preview enabled for compatibility).

### Refined JXL → JPEG Options
Step 2 now offers three clear choices:
1. **JPEG Auto-Detect** — Recommended (auto-routes based on jbrd presence)
2. **JPEG Lossless** — Force lossless transcoding (requires jbrd)
3. **JPEG Lossy** — Force lossy conversion with quality/ICC control

---

## What's New in v1.3

### Auto Mode + Manifest System

> **Beta:** Auto Mode is functional but still being tested. If you encounter issues, use manual mode selection (options 0-8) which is fully stable.

**[A] Auto Mode** analyzes your folder structure and recommends the best organization mode automatically:
- Detects `_EXPORT`, `Export_Lightroom`, etc. (case-insensitive)
- Shows folder mapping preview before running
- Recommends mode with confidence level (high/medium/low)

**[P] Manifest CSV** — Generate, edit in Excel, then run:
```
[A] Auto Mode → [P] Generate manifest → Edit in Excel → [M] Run from manifest
```

- Edit paths, delete rows, reorder before running
- Comment out lines with `#` to skip temporarily
- Manifests saved in `manifests/` folder — rerun anytime
- Use with `--sync` to re-process only changed files

### Embedded JPEG Thumbnail in JXL (Optional)
Optional embedded 256px sRGB thumbnail in JXL files for fast preview in IrfanView, XnView, digiKam.
```bash
python jxl_tiff_encoder.py folder/ --embed-thumbnail
```
Adds ~20KB per file.

**Windows Explorer Note:** The current JXL WIC codec from Microsoft Store generates its own thumbnail and **ignores the embedded EXIF thumbnail**. Worse, it does so **without color management** — so if your image uses ProPhoto RGB or Adobe RGB, the thumbnail will show wrong/washed-out colors. This is a **Windows codec limitation, not a bug in this software**. Use IrfanView, XnView MP, or digiKam for accurate thumbnails.

**IrfanView Note:** EXIF display in JXL has limitations with this software:

| Source | JXL Type | EXIF in IrfanView | Why |
|--------|----------|-------------------|-----|
| **TIFF → JXL** | Lossless | ✅ Shows | Boxes reordered (Exif before codestream) |
| **JPEG → JXL** | Lossless | ❌ Hidden | Brotli compression (`brob` box) - IrfanView can't read |
| **JPEG → JXL** | Lossy | ❌ Hidden | Brotli compression (`brob` box) - IrfanView can't read |

For reliable EXIF viewing regardless of source, use **XnView MP** or **digiKam**.

**Wide-gamut Note:** On an Adobe RGB calibrated monitor, IrfanView and XnView MP may display wide-gamut JXL files (e.g. ProPhoto RGB) with a slightly muted appearance — comparable to the difference between an original Adobe RGB file and the same file properly converted to sRGB. The most vibrant colors that extend beyond sRGB may appear dulled. This is a **subtle viewer rendering limitation**, not data loss — the JXL file still holds the full gamut intact. If the image looks *heavily* desaturated, that is a real bug. You can verify preservation by decoding the JXL back to TIFF: the round-trip TIFF will show the original vibrant colors again in any color-managed editor. See [docs/jxl_color_internals.md](jxl_color_internals.md) for details.

---
