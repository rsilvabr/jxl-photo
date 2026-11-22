# Pre-release notes (archive)

The test releases of jxl-photo (`vX.Y.Z_betaN`, since v2.30.1-rc1
`vX.Y.Z-rcN`) were published as GitHub pre-releases. To keep the
[Releases page](https://github.com/rsilvabr/jxl-photo/releases) short, their
release pages were removed on 2026-11-23 — except the ones someone reacted to
(v2.31.0-rc2) and the current test release. Their notes are kept
here as they were published, with the tag and the commit each points to.

The git tags stay: `git checkout <tag>` — or the commit — gives the files of
each one, and a link to `releases/tag/<tag>` still opens the tag. The
technical notes of every release are in [version_history.md](version_history.md).

| Tag | Published | Commit | Title |
|---|---|---|---|
| [v2.31.0-rc7](#v2310-rc7) | 2026-11-22 | `9fa90a4` | v2.31.0-rc7 — Targeted Review Fixes |
| [v2.31.0-rc6](#v2310-rc6) | 2026-11-22 | `aac57f1` | v2.31.0-rc6 — Second Sweep Fixes |
| [v2.31.0-rc5](#v2310-rc5) | 2026-11-22 | `f23f2e7` | v2.31.0-rc5 — Sweep Fixes and Faster Archiving |
| [v2.31.0-rc4](#v2310-rc4) | 2026-11-21 | `fbbfae2` | v2.31.0-rc4 — Error Messages on One Line, a Long TMP Refused |
| [v2.31.0-rc3](#v2310-rc3) | 2026-11-21 | `43eebde` | v2.31.0-rc3 — Faster Archiving and Decoding, Exact EXIF |
| [v2.31.0-rc1](#v2310-rc1) | 2026-11-19 | `0f7b30a` | v2.31.0-rc1 — Round-Trip Check Without Deleting (Pre-release) |
| [v2.30.2-rc2](#v2302-rc2) | 2026-11-14 | `dacc939` | v2.30.2-rc2 — Fewer Temp Files, Faster Copies (Pre-release) |
| [v2.30.2-rc1](#v2302-rc1) | 2026-11-14 | `85a357c` | v2.30.2-rc1 — Explorer Thumbnails Without Temp Files (Pre-release) |
| [v2.30.1-rc2](#v2301-rc2) | 2026-11-13 | `899f7bc` | v2.30.1-rc2 — Ready for libjxl 0.13 (Pre-release) |
| [v2.30.1-rc1](#v2301-rc1) | 2026-11-13 | `e613564` | v2.30.1-rc1 — Manifest Checks Read Every Option (Pre-release) |
| [v2.30.0_beta1](#v2300_beta1) | 2026-11-13 | `f7ee16b` | v2.30.0_beta1 — Faster Metadata and Flags in Full (Pre-release) |
| [v2.29.0_beta1](#v2290_beta1) | 2026-11-12 | `f31f5ca` | v2.29.0_beta1 — The Shared Code in One File (Pre-release) |
| [v2.24.0_beta1](#v2240_beta1) | 2026-11-05 | `da21da8` | v2.24.0_beta1 — 8-bit TIFFs Lossless at Low Distances, File Sizes in the Wizard (pre-release) |
| [v2.22.0_beta1](#v2220_beta1) | 2026-11-04 | `f7cbcba` | v2.22.0_beta1 — Every Option From The Wizard (Pre-release) |
| [v2.18.0_beta1](#v2180_beta1) | 2026-10-29 | `34882ab` | v2.18.0_beta1 — Pixel Check Before Deleting TIFFs, Hardening |
| [v2.17.0_beta1](#v2170_beta1) | 2026-10-23 | `546b82d` | v2.17.0_beta1 (pre-release) |
| [v2.1.1_beta1](#v211_beta1) | 2026-09-20 | `f4e0d0f` | v2.1.1_beta1 |

---

## v2.31.0-rc7

**v2.31.0-rc7 — Targeted Review Fixes** — published 2026-11-22 15:01:33 UTC, tag `v2.31.0-rc7`, commit `9fa90a46ac95c4df40a354d1979fa881a32ce6d7`.

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included. It follows the stable releases: a test release like this one has no Windows build.

The seventh test release (pre-release) of v2.31.0; the last stable release is [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2). Everything in [v2.31.0-rc6](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.31.0-rc6) is in it. A targeted review of the parts the two sweeps had not read in full — the JPEG transcoder's auto mode and archive repair, and the copies, the adopt pass and the restamp of the other scripts — found 16 bugs, and the review of the fixes one more; this release fixes them. Most close unusual setups; three concern film scans.

### What you may notice

- **Film scans.** VueScan's RGB + infrared scans no longer have their infrared taken for transparency (their copies were see-through and their Explorer thumbnails nearly white); a grey colour profile is really tested before a lossy encode (it was always kept in XMP only, so other programs showed such masters with the sRGB curve); no copy is made of a SilverFast scan's infrared page. The masters' pixels were never touched. If you archived such scans before, the [known issues](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc7/docs/known_issues.md) page shows how to find them and fix them by hand.
- **Deletes, hardened.** The JPEG recovery (JXL → the original JPEG) deletes an archive only when the JPEG matched its checksum — an older djxl can rebuild a slightly different JPEG from some archives; a 16-bit PNG export keeps a master that carries its TIFF's own tags (IPTC, scanner data), which a PNG cannot hold, and a decode to TIFF keeps a JPEG archive; `--provenance adopt` no longer takes a burst frame of the same name for the archived photo; the toolkit's own JPEG exports are not archived as photos.
- **Auto mode.** The documented `--sync` resume of a folder of JPEGs and their archives runs (it stopped with "Output would overwrite another input"), and "convert to sRGB" converts the JPEG archives too (they came back in their own profile).
- **Smaller fixes:** `--repair-jbrd` checks its "OK" against the checksums and reports a file it cannot read, the wizard's archive repair accepts a single file, and a few edge cases.

### Upgrading

Copy the six files together. If you archived VueScan RGB + infrared scans or grey scans with a lossy distance, see the [known issues](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc7/docs/known_issues.md). The [upgrading notes](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc7/docs/upgrading.md) list what a command line does differently now — for example, a JPEG archive without its checksum line is kept after its recovery, with a line saying why.

### Testing

With libjxl 0.13.0: 4563 tests pass, and so does the real-photo battery (183 checks), in folders named with Japanese characters, `[ ]`, a space and `%`. A side-by-side run against rc6 on real photos differs only in the version written into the logs and the history of each file. photo-backup's contract test passes (33 tests, items JP-1 to JP-24). Every fix has a test that fails without it. Two independent reviews checked the fixes; everything they found is fixed.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc7/docs/version_history.md#v2310-rc7)
- [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc7/docs/upgrading.md)
- [Known issues](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc7/docs/known_issues.md)
- [Bug tracking](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc7/docs/bug_tracking_since_v1.0.md) (#729–#745)
- Previous test release: [v2.31.0-rc6](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.31.0-rc6)
- Last stable: [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2)

---

## v2.31.0-rc6

**v2.31.0-rc6 — Second Sweep Fixes** — published 2026-11-22 10:59:29 UTC, tag `v2.31.0-rc6`, commit `aac57f138a4323d8e7dc5aa4e6f19bf17e05d9c8`.

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included. It follows the stable releases: a test release like this one has no Windows build.

The sixth test release (pre-release) of v2.31.0; the last stable release is [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2). Everything in [v2.31.0-rc5](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.31.0-rc5) is in it. A second independent sweep, of rc5, found 17 bugs — one of them in rc5 itself, most of the others in the wizard's manifests and menus; this release fixes them, and makes a plain JXL → JXL recompression faster with the same files.

### What you may notice

- **A fix for rc5.** The JXL of page 1 and after of a multi-page TIFF (a camera TIFF's thumbnail page, for example) could come out with a wrong EXIF orientation ("Rotate 180", or the first page's) and the first page's resolution unit. If you archived multi-page TIFFs with rc5, the [known issues](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc6/docs/known_issues.md) page has an ExifTool command that lists those masters; encode their TIFFs again with `--overwrite`. The whole TIFF the decoder rebuilds was right.
- **Manifests.** A "no" to deleting the already-converted originals now reaches every row (each row had kept the earlier answer). A row whose Source is missing — a drive not plugged in — is an error of its own, and the other rows still run. Two file rows that would write one output (`a.tif` and `a.tiff`) are caught before the run.
- **The wizard.** A setting changed in Settings reaches the next job of the same session; a preset keeps the export marker it ran with; a `--delete-source` typed in the expert flags gets the same checks as [D], before the confirmation instead of a refusal by the script after it.
- **Smaller fixes:** the transcoder's resized or colour-converted copies of scans with an infrared channel, the colours of some Explorer thumbnails of lossy scans, a distance typed as `nan`, and a menu question that did nothing.
- **Faster, the same files.** A plain JXL → JXL recompression now shares the cores among its encodes, as the copies already did: 40 masters with 8 workers, 23.3 → 19.7 s.

### Upgrading

Copy the six files together. If you archived multi-page TIFFs with rc5, see the [known issues](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc6/docs/known_issues.md). The [upgrading notes](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc6/docs/upgrading.md) list what a manifest or a preset does differently now.

### Testing

With libjxl 0.13.0: 4526 tests pass, and so does the real-photo battery (183 checks, one of them new: it fails on rc5's later TIFF pages), in folders named with Japanese characters, `[ ]`, a space and `%`. A side-by-side run against rc5 on real photos differs only in the version written into the logs and the history of each file. photo-backup's contract test passes (33 tests, items JP-1 to JP-24). Every fix has a test that fails without it. Two independent reviews checked the fixes; everything they found is fixed.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc6/docs/version_history.md#v2310-rc6)
- [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc6/docs/upgrading.md)
- [Known issues](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc6/docs/known_issues.md)
- [Bug tracking](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc6/docs/bug_tracking_since_v1.0.md) (#712–#728)
- Previous test release: [v2.31.0-rc5](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.31.0-rc5)
- Last stable: [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2)

---

## v2.31.0-rc5

**v2.31.0-rc5 — Sweep Fixes and Faster Archiving** — published 2026-11-22 07:33:39 UTC, tag `v2.31.0-rc5`, commit `f23f2e7d7ed6b490ebed333d76d6c3c6bc351b3d`.

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included. It follows the stable releases: a test release like this one has no Windows build.

The fifth test release (pre-release) of v2.31.0; the last stable release is [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2). Everything in [v2.31.0-rc4](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.31.0-rc4) is in it. An independent full sweep of rc4 found 22 bugs, most of them in unusual combinations; this release fixes them all, and makes archiving and sRGB copies about a fifth faster with the same files.

### What you may notice

- **Safer deletes in a few unusual setups.** A JPEG or PNG export with its colour converted (`--to-srgb`, `--icc-profile`) is a derivative now and never deletes its master, and a 16-bit PNG an older version converted this way no longer passes for a copy of its master. A JPEG archive is never deleted by a decode of its pixels. The recompressor leaves alone a file whose encode record it could not read, instead of re-encoding it.
- **A failed check before a delete is an error.** With `--delete-source`, a JXL that fails its round-trip check used to keep its final name while the run said OK. Now the run exits 1, the TIFF is kept, and the JXL stays beside it as `<name>_jxlphoto-error.jxl`, so the next run encodes the TIFF again.
- **Ctrl+C finishes the files in progress**, deletes nothing after it, prints the summary and exits 130, in every script; a second Ctrl+C stops at once. The encoder, the decoder and the transcoder used to work through their whole queue first.
- **The wizard remembers each task's own values.** An 8-bit PNG export no longer sets the bit depth of the next "Restore TIFFs", nor the copies' folder that of the masters, and "Repeat last job" replays only what that job ran. A script setting changed in menu 9 reaches the next job.
- **Faster, the same files.** cjxl's threads are shared among the encodes running at once, and the Explorer thumbnail is made from the image already in memory instead of a second decode of the finished file; the decoder's preview uses a table. Measured with the PC quiet on 37 varied exports, 8 workers: archiving 22.3 → 17.6 s, their sRGB copies 32.5 → 26.0 s, decoding them 13.5 → 11.8 s. Only the thumbnail's bytes differ from rc4's.
- Smaller fixes for film scans with an infrared channel, the EXIF of multi-page TIFFs, captions with `|`, private TIFF tags, an ICC test that could not run, and a TIFF re-exported during its encode.

### Upgrading

Copy the six files together. Check the [upgrading notes](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc5/docs/upgrading.md) if a command line of yours combines a colour conversion with `--delete-source`, or reads the exit code of a deleting run. The [known issues](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc5/docs/known_issues.md) page lists the files older versions wrote that these fixes concern, and what to do.

### Testing

With libjxl 0.13.0: 4464 tests pass, and so does the real-photo battery (182 checks), in folders named with Japanese characters, `[ ]`, a space and `%`. A side-by-side run against rc4 on real photos differs only in the version written into the logs and the history of each file. photo-backup's contract test passes (33 tests, items JP-1 to JP-24). Every fix has a test that fails without it. Two independent reviews checked the fixes; everything they found is fixed.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc5/docs/version_history.md#v2310-rc5)
- [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc5/docs/upgrading.md)
- [Known issues](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc5/docs/known_issues.md)
- [Bug tracking](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc5/docs/bug_tracking_since_v1.0.md) (#690–#711)
- Previous test release: [v2.31.0-rc4](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.31.0-rc4)
- Last stable: [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2)

---

## v2.31.0-rc4

**v2.31.0-rc4 — Error Messages on One Line, a Long TMP Refused** — published 2026-11-21 23:03:34 UTC, tag `v2.31.0-rc4`, commit `fbbfae2a2320c40c03d75017072ec6d959b3ff90`.

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included. It follows the stable releases: a test release like this one has no Windows build.

The fourth test release (pre-release) of v2.31.0; the last stable release is [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2). Everything in [v2.31.0-rc3](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.31.0-rc3) is in it. Three small changes; none changes a file the scripts write.

### What you may notice

- **Error messages say why, on one line.** When cjxl, djxl, ImageMagick or ExifTool fail, the file's ERROR line now carries their whole explanation. It often started in the middle of a sentence or showed only cjxl's version banner, with the cause on the lines below.
- **A TMP of 261 characters or more is refused.** On Windows, a process whose TMP is that long hangs when it starts any program, so such a run waited forever on its first cjxl or djxl. Every script now stops at its start with exit code 2 and says what to change. Only a program that points TMP at a very deep folder could meet it.
- **`jxl_photo.py encode` and the other script commands start a little sooner**: 35–40 ms a call, because the wizard no longer loads first.

### Upgrading

Copy the six files together. Nothing to do, unless a program of yours starts the scripts with a TMP of 261 characters or more ([upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc4/docs/upgrading.md)).

### Testing

With libjxl 0.13.0: 4394 tests pass, and so does the real-photo battery (182 checks), in folders named with Japanese characters, `[ ]`, a space and `%`. A side-by-side run against rc3 on real photos differs only where a tool's error is quoted. photo-backup's contract test passes (31 tests, items JP-1 to JP-23). An independent review checked the changes; its three findings are fixed.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc4/docs/version_history.md#v2310-rc4)
- [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc4/docs/upgrading.md)
- [Bug tracking](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc4/docs/bug_tracking_since_v1.0.md) (#688–#689)
- Previous test release: [v2.31.0-rc3](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.31.0-rc3)
- Last stable: [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2)

---

## v2.31.0-rc3

**v2.31.0-rc3 — Faster Archiving and Decoding, Exact EXIF** — published 2026-11-21 15:09:11 UTC, tag `v2.31.0-rc3`, commit `43eebdec952a718d136ff4847a13bd3a5e09efb7`.

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included. It follows the stable releases: a test release like this one has no Windows build.

The third test release (pre-release) of v2.31.0; the last stable release is [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2). Everything in [v2.31.0-rc2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.31.0-rc2) and [v2.31.0-rc1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.31.0-rc1) is in it.

### What you may notice

- **Faster archiving, decoding and deleting.** Each script now keeps one ExifTool open for its metadata work instead of starting one per step (each start cost about 0.2 s), and the decoder writes a TIFF's metadata in one pass instead of four. Measured against rc2 on copies of real photos: archiving TIFFs one call each with every page checked 22 % faster, decoding masters to TIFF 18 %, a run with `--delete-source` 21 %, resized copies 11 %. Pixels and metadata are unchanged, apart from the exact EXIF values below.
- **Exact EXIF values.** A few EXIF numbers were rounded on the way: a lens's maximum aperture of f/2.83 became f/2.8, a phone's focal length of 6.62 mm became 6.6 mm. New files keep the exact values. The files you have are fine as they are and are not redone.
- **A temp folder with `%` in its name is refused.** ExifTool reads a `%` in a file name as a format code, so a run whose temp folder (`TEMP_DIR`, or Windows' TEMP when it is unset) has one now stops before its first file and says what to change.
- **The failed files are listed again at the end** of a run with errors, for whoever reads only the last lines.
- **For programs: `--serve`.** The encoder, the decoder and the JPEG transcoder can stay open and convert files sent to them one per line, exactly as single calls: a program that converts photo by photo saves the start of every call (30 JPEGs in 4.8 s instead of 8.9). The wizard does not use it; each script's page has a "Serve mode" section.
- **[Known issues](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc3/docs/known_issues.md):** a new page with the bugs that changed files earlier versions wrote, how to find those files and what to do. Most need nothing.

### Upgrading

Copy the six files together. One thing to check: a temp folder with `%` in its path is now refused ([upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc3/docs/upgrading.md)). `EXIFTOOL_STAY_OPEN = False`, at the top of a script or in the settings overlay, goes back to one ExifTool per step.

### Testing

With libjxl 0.13.0: 4369 tests pass, and so does the real-photo battery (182 checks), in folders named with Japanese characters, `[ ]`, a space and `%`. A side-by-side run against rc2 on real photos shows only the intended differences. Two independent reviews, and a focused review of the delete gate, the kept-open ExifTool, the decoder's single write and the serve mode, checked the round; what they found is fixed.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc3/docs/version_history.md#v2310-rc3)
- [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc3/docs/upgrading.md)
- [Bug tracking](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc3/docs/bug_tracking_since_v1.0.md) (#684–#687)
- [Performance](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc3/docs/performance_details.md)
- Previous test release: [v2.31.0-rc2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.31.0-rc2)
- Last stable: [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2)

---

## v2.31.0-rc1

**v2.31.0-rc1 — Round-Trip Check Without Deleting (Pre-release)** — published 2026-11-19 16:06:29 UTC, tag `v2.31.0-rc1`, commit `0f7b30a9113a3b3d0ccd4da0419942af2be236e5`.

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included. It follows the stable releases: a test release like this one has no Windows build.

A test release (pre-release) of v2.31.0; the last stable release is [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2).

### What you may notice

- **`--verify-roundtrip` now also checks when nothing is deleted.** In a TIFF → JXL run without `--delete-source`, every page is decoded and compared with its TIFF page before it gets its final name. A page that does not match, or cannot be checked, gets no JXL and the run reports an error. Until now the flag checked only before a deletion and otherwise did nothing. [photo-backup](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc1/docs/related_projects.md) uses it for every TIFF it keeps as JXL.
- **Nothing changes** for runs without the flag, for runs that delete (checked exactly as before), or for the wizard and its presets, which send the flag only together with `--delete-source`.

### Upgrading

Copy the six files together. A command line of your own that passes `--verify-roundtrip` without `--delete-source` now takes about a fifth longer on TIFFs and needs djxl on PATH.

### Testing

With libjxl 0.13.0: 4217 tests pass, and so does the real-photo battery (176 checks), in folders named with Japanese characters, `[ ]`, a space and `%`. The battery now runs the check on every TIFF fixture — camera exports, film scans (8/16-bit, gray, RGB+IR, 84 MP), SilverFast originals, tagless and multi-page TIFFs: 91 TIFFs, 107 pages, all verified (40 with identical pixels, 67 lossy). On 69 of them, 8 at a time, the check added 18 % to the time.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc1/docs/version_history.md#v2310-rc1)
- [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.31.0-rc1/docs/upgrading.md)
- Last stable: [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2)

---

## v2.30.2-rc2

**v2.30.2-rc2 — Fewer Temp Files, Faster Copies (Pre-release)** — published 2026-11-14 11:19:52 UTC, tag `v2.30.2-rc2`, commit `dacc939786148f97faf23b0dc43e135f423127cf`.

> **Superseded by [v2.30.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2)**, the stable release (everything in rc2 is in it).

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included.

The second test release (pre-release) of v2.30.2; the last stable release is [v2.30.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.1). Everything in [v2.30.2-rc1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2-rc1) is in it.

### What you may notice

- **Copies and decodes are faster.** Making a smaller copy of a master (a resized JPEG, a resized JXL, a JXL converted to sRGB), decoding a master back to TIFF and checking a master before a TIFF is deleted used to save the whole photo as a temporary 16-bit PNG first: about 230 MB for a 45 MP photo, written, read back and thrown away. That image now stays in memory. On a batch of 99 varied photos (2 to 45 MP, 8 at a time): resized JPEGs 91 s instead of 137 s, resized JXLs 80 s instead of 127 s, sRGB JXLs 165 s instead of 238 s; decoding to TIFF takes about half the time.
- **Less written to the disk when archiving.** A new 48 MB master used to cost about 340 MB of disk writes while its metadata was added; it now costs about 100 MB.
- **A sync with nothing to do starts much sooner.** To find what is already done, the scripts read a few KB of each JXL instead of the whole file.
- **The same files, byte for byte.** Every output was compared with the previous version on real photos. One fix: a resized black-and-white JPEG copy (the infrared page of a film scan) is now marked as a copy; before, the next run refused to overwrite it.

### Upgrading

Copy the six files together. Nothing else to do. A copy now holds the decoded photo in memory (about 260 MB for a 45 MP photo) instead of reading it back from the temp folder; that fits in the memory each worker already reserves, and resized copies need less memory than before.

### Testing

With libjxl 0.13.0: 4197 tests pass, and so does the real-photo battery (174 checks in 611 s), in folders named with Japanese characters, `[ ]`, a space and `%`. Every output of the changed steps was compared byte for byte with the previous code on about 840 real files (resized and sRGB copies, decoded TIFFs, masters with and without Explorer thumbnails, recompressions) from camera exports and film scans.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.30.2-rc2/docs/version_history.md#v2302-rc2)
- [Bug tracker (round 87)](https://github.com/rsilvabr/jxl-photo/blob/v2.30.2-rc2/docs/bug_tracking_since_v1.0.md)
- Previous test release: [v2.30.2-rc1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2-rc1) · last stable: [v2.30.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.1)

---

## v2.30.2-rc1

**v2.30.2-rc1 — Explorer Thumbnails Without Temp Files (Pre-release)** — published 2026-11-14 06:11:58 UTC, tag `v2.30.2-rc1`, commit `85a357cfac958eaa5031d73294a26f65f45e428d`.

> **Superseded by [v2.30.2-rc2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.2-rc2)**, the next test release (everything in rc1 is in it).

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included.

A test release (pre-release) of v2.30.2; the last stable release is [v2.30.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.1).

### What you may notice

- **Explorer thumbnails no longer write the whole photo to the temp folder.** To make the 768 px thumbnail of a new JXL, the scripts decoded the full image into a temporary file (about 270 MB for a 45 MP photo) and read it back, once per output. That image now stays in memory: nothing is written, and the thumbnails come out byte for byte the same.
- **Much faster when the temp folder sits on a slow disk.** With the temp folder on a SATA SSD that had slowed down to about 45 MB/s, six thumbnails took 7 s instead of 262 s. On a fast NVMe drive the speed is the same, and each photo saves about 270 MB of writes to the SSD.

### Upgrading

Copy the six files together. Nothing else to do. While it makes a thumbnail, a script holds the decoded image in memory for a moment (about 270 MB for a 45 MP 16-bit photo), within the memory each worker already reserves.

### Testing

With libjxl 0.13.0: 4149 tests pass, and so does the real-photo battery (174 checks in 690 s), in folders named with Japanese characters, `[ ]`, a space and `%`. The new thumbnails are byte for byte the old ones on 114 files: film scans (8/16-bit gray, gray with alpha, RGB+IR, RGBA, no ICC profile, 84 MP), camera exports, phone PNGs, JPEG archives and 12-bit images.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.30.2-rc1/docs/version_history.md#v2302-rc1)
- [Bug tracker (round 86)](https://github.com/rsilvabr/jxl-photo/blob/v2.30.2-rc1/docs/bug_tracking_since_v1.0.md)
- Last stable: [v2.30.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.1)

---

## v2.30.1-rc2

**v2.30.1-rc2 — Ready for libjxl 0.13 (Pre-release)** — published 2026-11-13 17:03:48 UTC, tag `v2.30.1-rc2`, commit `899f7bc4bc07e75f47fb733b1ca6dec218fc04b4`.

> **Superseded by [v2.30.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.1)** (the same code as rc2, as a stable release).

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included. The package of this pre-release is `v2.30.1-rc2-exe1`, now with **libjxl 0.13.0**.

The second test release of v2.30.1: tested with **libjxl 0.13.0** (released 2026-11-12), and two fixes for it. Everything in [v2.30.1-rc1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.1-rc1) is in it. The last stable release is v2.30.0.

### What you may notice

- **With libjxl 0.13, your JPEG archives stay readable by every version.** cjxl 0.13 marks the lossless archive of a 4:4:4 or gray JPEG for a little smoothing when a viewer shows it, and djxl 0.12 and 0.11 then rebuild a slightly different JPEG (or none) instead of the original file. With cjxl 0.13 or newer the JPEG transcoder now turns that mark off: the archive has the same size and is rebuilt bit for bit by djxl 0.11, 0.12 and 0.13. Archives made before never had the mark and need nothing. Nothing was ever deleted on this account: before deleting a JPEG, the transcoder always proves that the installed djxl rebuilds it.
- **With libjxl 0.13, a lossy encode keeps an alpha channel even when it holds nothing** (every sample opaque). cjxl 0.13 drops such a channel by default; the real-photo battery caught a 16-bit RGBA scan whose lossy master came back with three channels instead of four. Every lossy encode of the toolkit now keeps it, as with 0.12.
- **libjxl 0.13.0 otherwise changes nothing you would see in the files:** masters, copies, sizes and quality come out byte for byte the same as with 0.12.0 on the photos measured, and each version reads the other's files pixel for pixel. Effort 9 is about a quarter faster.

### Upgrading

Copy the six files together. Updating libjxl to 0.13.0 is optional; 0.12.0 and 0.11.2 still work.

### Testing

With libjxl 0.13.0: 4118 tests pass (16 more than rc1), and so does the real-photo battery (174 checks in 1260 s, in folders named with Japanese characters, `[ ]`, a space and `%`; rc1 had 173, the alpha channel above). The lossless archives of 76 JPEG structures (phones, cameras, scans, 4:4:4, gray, progressive) made by this release with cjxl 0.13 are rebuilt bit for bit by djxl 0.11.2, 0.12.0 and 0.13.0, and archives made with 0.12 are rebuilt bit for bit by djxl 0.13. Masters, copies and sizes of nine photos and scans were compared between 0.12.0 and 0.13.0.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.30.1-rc2/docs/version_history.md#v2301-rc2)
- [Bug tracker (round 85)](https://github.com/rsilvabr/jxl-photo/blob/v2.30.1-rc2/docs/bug_tracking_since_v1.0.md)
- Previous: [v2.30.1-rc1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.1-rc1) (test release) · [v2.30.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.0) (stable)

---

## v2.30.1-rc1

**v2.30.1-rc1 — Manifest Checks Read Every Option (Pre-release)** — published 2026-11-13 14:57:18 UTC, tag `v2.30.1-rc1`, commit `e61356432e664a412b8bce145d87fab01a10c7ee`.

> **Superseded by [v2.30.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.1)** (the same code as rc2, as a stable release).

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included. The package of this pre-release is `v2.30.1-rc1-exe1`.

A test release of v2.30.1: the fixes from an independent review of v2.30.0. The files the scripts write are the same as v2.30.0's; what changes is the wizard's checks before a run. The last stable release is v2.30.0.

**Test releases are now named `-rc1`, `-rc2`…** (release candidate) instead of `_beta1`. The name is what every file converted with a release records in its history, so `v2.30.1` stays for the final release and each test release has its own number. A copy running `2.30.1-rc1` is told when `2.30.1` is out.

### What you may notice

- **The wizard's manifest check and delete preview read every option the run gets**: a resize or sharpening, the output folder name of modes 1/3 and the expert flags. Before, a JXL → JPEG manifest with a resize and a rename could have two rows write the same JPEG without a word (a JPEG archive is re-encoded under the new name when it is resized); such a manifest is now refused before anything runs. Nothing was deleted in that case: a resized copy never deletes its source.
- **A manifest row whose Source is a single file runs in mode 2 with an empty Destination.** It used to fail with "1 errors"; it now writes where that script's mode 2 puts a single file.
- **The file count and the manifest check of a JPEG ↔ JXL run are as fast as before v2.30.0** (v2.30.0 had made them several times slower on large folders).

### Fixes

- The delete preview counts 0 for a single file in modes 6/7 of the decoder and the transcoder, which refuse a file there.
- The decoder reads the libjxl version for each file's history once, before its workers start.

### Upgrading

Copy the six files together, as with v2.30.0. Nothing else to do.

### Testing

4102 tests pass (60 more than v2.30.0), and so does the real-photo battery (174 checks in 1463 s, in folders named with Japanese characters, `[ ]`, a space and `%`). Run side by side with v2.30.0 on 27 scenarios with real photos, this release writes the same files. The review's own check — the wizard's plan of each manifest row against the script's dry run of the same command, 1 058 rows — agrees on every row a script runs (930; v2.30.0 differed on 49), and every deliberate break of the code the review found going unnoticed is now caught by a test.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.30.1-rc1/docs/version_history.md#v2301-rc1)
- [Bug tracker (round 84)](https://github.com/rsilvabr/jxl-photo/blob/v2.30.1-rc1/docs/bug_tracking_since_v1.0.md)
- Previous: [v2.30.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.0)

---

## v2.30.0_beta1

**v2.30.0_beta1 — Faster Metadata and Flags in Full (Pre-release)** — published 2026-11-13 05:35:43 UTC, tag `v2.30.0_beta1`, commit `f7ee16bec6b6ebdbe007e38fe5ae8c570d5998ad`.

> **Superseded by [v2.30.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.30.0)**, the stable release that includes everything in this pre-release. Download that one.

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included. The package of this pre-release is `v2.30.0_beta1-exe1`.

A pre-release of v2.30.0: faster metadata writing, a stricter command line and the libjxl version in each output's history. Apart from that one field, the files the scripts write are the same. The last stable release is v2.29.1.

### What you may notice

- **Faster.** Each file's metadata is written by one ExifTool process instead of several, and the encoder no longer compresses its temporary PNG. On the photos measured (16-bit exports up to 45 MP), archiving a TIFF takes about half the time it did; decoding one back, 10–25% less.
- **Flags must be written in full.** A shortened flag such as `--work 4` for `--workers 4` is now refused before anything runs (exit code 2). The wizard writes every flag in full; if you run the scripts from your own `.bat` files or scheduled tasks, check them ([upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.30.0_beta1/docs/upgrading.md)).
- **Each output's history names the libjxl version that made it**: `jxl-photo 2.30.0 (…) with libjxl 0.12.0`. Useful in a long-term archive, because the same settings do not always give the same file in two libjxl versions. Nothing reads it back.
- **New:** `python jxl_photo.py encode|decode|recompress|transcode …` runs a script directly, with the names of the Windows program. The scripts still run on their own.

### Fixes

- A PNG saved with a `.jpg` name is no longer turned into a lossy JXL by the lossless JPEG → JXL transcode. It is now an error, and the file stays where it is. Nothing was deleted before either: the delete gate keeps such a file.
- The encoder's memory cap now budgets 16-bit scans with four channels (RGB + infrared, RGBA) correctly, so it does not start one worker too many near the memory limit.

### Under the hood

The wizard now asks each script how it reads its flags and which files a run takes, instead of keeping its own copy of those rules. The planning-time ExifTool reads run on one engine. An independent review of this work found nothing that had to be fixed before a release; its smaller findings are fixed here (bugs #652 and #653). #654, the PNG case above, is older and was found while closing the work.

### Upgrading

Copy the six files together, as with v2.29.1. Check your own command lines for shortened flags. [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.30.0_beta1/docs/upgrading.md).

### Testing

4007 tests pass, and so does the real-photo battery (174 checks in 1535 s, in folders named with Japanese characters, `[ ]`, a space and `%`). Run side by side with v2.29.1 on 27 scenarios with real photos, this release writes the same files (pixels, codestream, boxes, every metadata tag, exit codes), apart from the libjxl version in the history and one new line in the log file.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.30.0_beta1/docs/version_history.md#v2300_beta1)
- [Bug tracker (round 82)](https://github.com/rsilvabr/jxl-photo/blob/v2.30.0_beta1/docs/bug_tracking_since_v1.0.md)
- [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.30.0_beta1/docs/upgrading.md)
- Previous: [v2.29.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.29.1)

---

## v2.29.0_beta1

**v2.29.0_beta1 — The Shared Code in One File (Pre-release)** — published 2026-11-12 12:52:21 UTC, tag `v2.29.0_beta1`, commit `f31f5caa18ab1425c4e5f95a065b54f3b9482caa`.

> **Superseded by [v2.29.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.29.1)**, the stable release that includes everything in this pre-release. Download that one.

> **Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases) — the same scripts with cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included. The package of this pre-release is `v2.29.0_beta1-exe1`.

A pre-release with no change in what the scripts do with your photos. The code the five scripts share now lives in one file, and that file has to sit next to them. The last stable release is [v2.28.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.28.0).

### What you may notice

- **A release is now six files that go together:** `jxl_photo.py`, the four `jxl_*` scripts and `jxl_photo_common.py`, the code they share. If you update by copying files, copy all six into the same folder.
- **A script without that file says so and stops**, before doing anything: `jxl_photo_common.py is missing next to jxl_tiff_encoder.py: copy every file of the release into the same folder`. The same happens with files from two different releases mixed in one folder.
- Everything else is the same: command lines, settings, presets, manifests, the files written and their metadata. Archives written by earlier versions are read as before.

### Why

Until now each script carried its own copy of the helpers they have in common (the box and codestream readers, staging, the provenance proofs, the exiftool and subprocess runners and more), so a fix had to be made in up to four places. They now have one copy: 135 functions moved unchanged, and the five scripts are about 8 300 lines shorter.

### Upgrading

Copy the six files together. Nothing else to do: [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.29.0_beta1/docs/upgrading.md#v2290-the-scripts-need-jxl_photo_commonpy-next-to-them).

### Testing

Every moved function was compared with every copy it replaced in v2.28.0 (413 copies, no difference in logic), and apart from the move each script is statement for statement the v2.28.0 one. 3700 tests and the real-photo battery pass (174 checks in 1448 s, in folders named with Japanese characters, `[ ]`, a space and `%`); the battery also passed through the Windows program carrying the six files.

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.29.0_beta1/docs/version_history.md#v2290_beta1)
- [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.29.0_beta1/docs/upgrading.md)
- Previous release: [v2.28.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.28.0)

---

## v2.24.0_beta1

**v2.24.0_beta1 — 8-bit TIFFs Lossless at Low Distances, File Sizes in the Wizard (pre-release)** — published 2026-11-05 23:34:25 UTC, tag `v2.24.0_beta1`, commit `da21da8ff5d82caca8d29c0239ec0387a907cf74`.

A pre-release. One default changes for 8-bit TIFFs, and the wizard now tells you how big a file each distance makes. The last stable release is v2.23.0; the stable v2.24.0 will also move the encode record out of the photo's Title.

### What you may notice

- **8-bit TIFFs are archived lossless at `d=0.1` and below.** For an 8-bit TIFF, lossless is small (about 43 MB for 45 MP, a quarter of a 16-bit one) and `d=0.05` would come out larger than that, so the encoder now archives an 8-bit TIFF that is not a film scan lossless when the run's distance is 0.1 or less. 16-bit TIFFs and higher distances do not change. `--eight-bit-distance same` keeps the old behaviour; a number gives 8-bit TIFFs their own distance. The run says how many it encodes this way, and the wizard shows it in an "8-bit TIFFs" row on the review screen.
- **Typical file sizes next to every distance.** The wizard's distance lists show the typical JXL size at 45, 24 and 12 MP (medians of 40 photos from four cameras), and "your distance" shows every distance from 0 to 25 with the TIFF sizes for comparison.
- **The distance lists:** `d=0.7` (visually lossless, keeps the grain texture) in both lists, `d=5` for small copies, shorter names, and lighter notes that are easier to read.

### Upgrading

If you use `--delete-skipped` over 8-bit masters archived at `d=0.1` or less by an earlier version, add `--eight-bit-distance same` to that run (otherwise their TIFFs are kept, with a line saying why). Details: [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.24.0_beta1/docs/upgrading.md#v2240-8-bit-tiffs-are-archived-lossless-at-d01-and-below).

### Testing

New tests fail on the code before the change, including real-codec ones in folders with Japanese characters, `[ ]`, a space and `%`: an 8-bit TIFF comes back bit-exact next to a lossy 16-bit one, and a deleting run checks each at its own distance. 3455 tests and the real-photo battery pass (134 checks; a new one archives two real 8-bit camera TIFFs at `d=0.1` and checks they come back bit-exact).

**Full detail:**
- [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.24.0_beta1/docs/version_history.md#v2240_beta1)
- [File sizes, every distance](https://github.com/rsilvabr/jxl-photo/blob/v2.24.0_beta1/docs/file_sizes.md#every-distance-from-a-16-bit-and-an-8-bit-tiff)
- [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.24.0_beta1/docs/upgrading.md)
- Previous release: [v2.23.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.23.0)

---

## v2.22.0_beta1

**v2.22.0_beta1 — Every Option From The Wizard (Pre-release)** — published 2026-11-04 01:01:11 UTC, tag `v2.22.0_beta1`, commit `f7cbcbaf66ae2913b5fb4522cacc8a27fc899b69`.

A pre-release of the next wizard: every script option is now within reach without expert flags or editing the scripts. It also brings three behaviour changes worth reading before you run it on a library — see Upgrading. The last stable release is v2.21.0.

### What you may notice

- **All options for a job.** On the summary screen (Step 7), `O` lists every option of the run, grouped, with its value; `/text` filters it ("buf" finds `--buffering`). The options the wizard never asked about are there — `--buffering`, `--restamp-then-sync`, `--explorer-thumbnail`, `--clean-staging`, the sharpening parameters and more — and are saved with the last session and the presets. Options that cannot go together are refused before the run, not after the HHMM time.
- **New menus.** `R` fixes the path records after a folder was renamed or moved (dry run first, nothing re-encoded); `C` resets the encoder's ICC test cache; `S` changes the four scripts' own settings — folder names, timeouts, the memory cap, thumbnail sizes, the recompressor's policies — without editing the `.py` files. Every run logs which of those settings it took. Deletion, confirmations, guards and verification are never changed that way.
- **A JXL converted to a JPEG or an 8-bit PNG is never deleted.** Like the decoder's 8-bit TIFF and the recompressor's derivatives, such a copy is not a replacement for the master. The bit-exact recovery of a JPEG archive and a 16-bit PNG still delete.
- **Modes 6/7:** a library stored under a folder whose name also matches the marker (`Lightroom_Export`, `C1_Export`) now anchors on the real `_EXPORT` below it, and a derivative next to its master no longer stops the run on a duplicate output.
- **Expert flags are checked before the run:** a typo or a `--mode` there is refused before the HHMM time.

Your presets build the same command lines as in v2.21.0.

### Testing

New tests for each fix fail on the commit before it, including real-codec and real-exiftool ones in folders with Japanese characters, `[ ]`, a space and `%`. 3300 tests and the real-photo battery pass (132 checks, all optional fixture folders), with and without a script settings overlay.

---

**Previous release:** [v2.21.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.21.0)

**Full detail:** [Version history](https://github.com/rsilvabr/jxl-photo/blob/v2.22.0_beta1/docs/version_history.md#v2220_beta1) | [Bug tracking (#600–#607)](https://github.com/rsilvabr/jxl-photo/blob/v2.22.0_beta1/docs/bug_tracking_since_v1.0.md#round-71--delivery-b-of-the-wrapper-redesign-2026-11-04) | [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.22.0_beta1/docs/upgrading.md#v2220-a-jxl--jpeg-never-deletes-the-jxl-modes-67-anchor-below-the-input-and-leave-derivatives-out)

---

## v2.18.0_beta1

**v2.18.0_beta1 — Pixel Check Before Deleting TIFFs, Hardening** — published 2026-10-29 09:45:46 UTC, tag `v2.18.0_beta1`, commit `34882abea1e6b8078c283004fb66e9e25a49baa7`.

Pre-release. One safer default and a round of hardening from an audit of everything that changed since v2.15.2. Nothing to do when upgrading; the last stable release is v2.17.0.

### What changes

- **Runs that delete TIFFs now check every JXL's pixels first.** `VERIFY_ROUNDTRIP` in the encoder is on by default (it was opt-in): before a TIFF is deleted, its JXL is decoded and compared with it. Those runs take about twice as long; runs that delete nothing are unchanged. `VERIFY_ROUNDTRIP = False` at the top of `jxl_tiff_encoder.py` restores the old behaviour, and the menu no longer asks about it while it is on.
- **A JPEG → JXL run stopped midway no longer leaves JPEGs that later runs never delete.** Its archives could miss their checksum lines; the next deleting run now accepts such a JXL once `djxl --reconstruct_jpeg` gives the JPEG back byte for byte (#556).
- **A lossless JPEG archive is never overwritten by another photo of the same name**, also when its checksum line is gone or when the recompressor copied it (#557, #558).
- **Re-archiving a TIFF with fewer pages no longer leaves its old thumbnail page behind**, and colour-converted, resized or sharpened copies (as JXL, JPEG or PNG) skip the small preview page some TIFFs carry (#551, #552, #555).

### Edge cases closed

- Edits that work on a copy — the restamp after a folder rename, the Explorer-thumbnail refresh, `--repair-jbrd` — swap their copy in only if the file did not change meanwhile, and the decoder no longer writes over a TIFF that was edited and saved while it was decoding (#559, #560).
- exiftool works when the Windows temp folder has characters outside the system code page (a user name in another script) (#553).
- A film scan refused by a delete or adopt check now names `--scan-distance same` (#554).

2835 tests, and the real-photo battery passes (125 checks, run inside a folder with Japanese, brackets, a space and `%`).

**Full detail:** [version history](https://github.com/rsilvabr/jxl-photo/blob/v2.18.0_beta1/docs/version_history.md#v2180_beta1) · [upgrading](https://github.com/rsilvabr/jxl-photo/blob/v2.18.0_beta1/docs/upgrading.md#v2180-deleting-tiffs-checks-pixels-old-thumbnail-pages-are-removed-derivatives-skip-them) · [bug tracking](https://github.com/rsilvabr/jxl-photo/blob/v2.18.0_beta1/docs/bug_tracking_since_v1.0.md) · previous release: [v2.17.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.17.0)

---

## v2.17.0_beta1

**v2.17.0_beta1 (pre-release)** — published 2026-10-23 13:07:51 UTC, tag `v2.17.0_beta1`, commit `546b82d586fbda8684e08b4e6f95601cd0e23674`.

**Pre-release.** The stable v2.17.0 will add more for film scans; this beta is published so there is a version to go back to.

Film scans now come back the way their scanner software expects them.

### What changes

- **Film scans keep what their scanner software needs.** A SilverFast scan (image, thumbnail, IR page) archived and decoded back opens in SilverFast with its IR channel, dust removal (iSRD) and browser icon, like the original. Checked on a slide and a negative, also after recompressing the master. Two things were lost before: SilverFast's own XMP settings (the XMP is now copied whole, #548) and its private TIFF tags (now kept, #549).
- **Thumbnail pages are kept by default** (`--thumbnail-mode auto`). A TIFF's embedded preview is archived as `_page1_thumbnail.jxl` and comes back in the decoded TIFF, except Capture One's tiny export previews. `--thumbnail-mode scans` keeps them for scans only; `exclude` is the old behaviour. Both program lists are settings at the top of the encoder.

### Upgrading

Archives you already have keep their layout on a sync run. To add the thumbnail and the scanner settings to scans archived earlier, re-archive those folders once with `--overwrite`. A preset that chose `exclude` keeps it. Details: [Upgrading](https://github.com/rsilvabr/jxl-photo/blob/main/docs/upgrading.md#v2170-thumbnail-pages-are-kept-by-default).

Other scanner programs were not tested; test one file first.

Full notes: [version history](https://github.com/rsilvabr/jxl-photo/blob/main/docs/version_history.md#v2170_beta1). 2731 tests; the real-photo battery passes.

---

## v2.1.1_beta1

**v2.1.1_beta1** — published 2026-09-20 12:52:41 UTC, tag `v2.1.1_beta1`, commit `f4e0d0f813870906e6a2769bd920ad9a592c172d`.

Maintenance beta on top of v2.1.0 — ten fixes from the second audit of the recompressor release (round 37, bugs #347–#356), all in the safety/reporting layer. **No command line and no file format changes.**

### Highlights

- **Dry runs no longer lie.** The decoder and recompressor skipped the provenance refusal gate in dry runs — the simulation promised outputs the real run refuses, with `errors: 0` in the summary. Both now preview the refusals (`DRY | would REFUSE`, counted as predicted errors), and the recompressor dry run exits 0.
- **Outputs are never written under their final name.** A run killed externally (idle-timeout kill, Ctrl+C, power loss) used to leave a truncated file at the final path with a fresh mtime — which the next smart-sync run then treated as up to date forever. All four scripts now write a uuid temp beside the final and swap it in with an atomic same-folder `os.replace` only after the integrity check.
- **`checksums.md5` appends are serialized across processes.** Two manifest entries targeting one folder are two child processes; the thread lock only serialized one, and appends interleaved mid-line. Now a sibling `.lock` file, fail-closed: an untaken lock skips the line, never a torn write.
- **Wrapper: recompressor policies survive the wizard.** Step 6A rebuilt the advanced options from scratch and dropped `on_downgrade`/`on_regeneration`/`on_unknown`/`jbrd_policy`/`no_keep_smaller` — the child fell back to `ask`, a silent skip on the wrapper's pipe. The wizard now asks `on_unknown` and `jbrd_policy` on the recompressor path, carries the rest through every branch, and the manifest builder emits both flags.
- **Encoder: `--encode-tag xmp` merges the EXIF Software chain.** A TIFF recovered from a `--encode-tag software` JXL carries the lineage chain in EXIF Software; the xmp branch left it there beside the new dc:Description record, and the recompressor trusted the stale one. Both fields are merged into dc:Description and the machine block stripped from Software (unrelated text kept).
- **Recompressor: keep-smaller fallback passes the MD5 gate.** The verbatim-copy proof keyed on `action == "copy"`, but the keep-smaller fallback reports status `"copied"` with action still `"convert"` — a corrupt copy certified the deletion of its source.
- Smaller: log filenames carry the pid (two runs in the same second shared one log); the decoder counts a missing final output as a KEEP instead of leaving the gate silently; jbrd repair temps no longer wear a `.jxl` name (a crash left a fake input for the next scan) and honor `TEMP_DIR`; `--repair-jbrd` no longer requires cjxl (repair only needs djxl ≥ 0.12 + exiftool); the wrapper's mode-6 collision mirror matches the real finder's decoder-output skip.

Every fix has a regression test proven to fail against the pre-fix code (`tests/test_audit_round37.py`, 23 tests). **1357 tests** in the suite.

**Beta:** these are delete-path and audit fixes, every one with a failing-pre-fix regression test — but if you archive with `--delete-source`, stable v2.1.0 is the conservative choice until v2.1.1 final. Report issues at https://github.com/rsilvabr/jxl-photo/issues.

Full detail: [bug tracking, round 37](bug_tracking_since_v1.0.md).
