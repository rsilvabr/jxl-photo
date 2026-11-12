# What's new, release by release

The short version: what you will notice in each release and whether you
need to do anything. Newest first. The technical notes of every release
are in [version_history.md](version_history.md), what to check when
upgrading in [upgrading.md](upgrading.md), and the same notes are published
as [GitHub Releases](https://github.com/rsilvabr/jxl-photo/releases).

## At a glance

| Version | Date | Highlights |
|---------|------|------------|
| **[v2.29.0_beta1](version_history.md#v2290_beta1)** | 2026-11-12 | Pre-release. A release is six files that go together: the five scripts and `jxl_photo_common.py`, the code they share; nothing changes in what they do |
| [v2.28.0](version_history.md#v2280) | 2026-11-11 | A small Photoshop resource block of a TIFF comes back in the decoded TIFF; JPEG/PNG copies drop the TIFF-tag markers; no copy of a camera TIFF's thumbnail page; resized copies work in a folder named like `100%off` |
| [v2.27.1](version_history.md#v2271) | 2026-11-11 | A lossy JPEG → JXL keeps a JPEG whose HDR gain map the JXL does not hold; the way out of unparseable maker notes named for more exiftool wordings |
| [v2.27.0](version_history.md#v2270) | 2026-11-11 | A TIFF's IPTC data reaches the master and comes back in the decoded TIFF; the encoder's sync skips a TIFF with a newer date and the same content; a lossy JPEG → JXL of a JPEG with a broken thumbnail IFD converts |
| [v2.26.1](version_history.md#v2261) | 2026-11-10 | A rotated JPEG's archive is shown the right way up even without `jxlinfo`; PNGs and turned-back JPEGs keep the camera's `InteropIndex` |
| [v2.26.0](version_history.md#v2260) | 2026-11-10 | The JPEG of a rotated JPEG's archive comes back as the camera wrote it; JXL → PNG of camera JPEGs and resizing sources with the standard sRGB profile work; `--exiftool-ignore-minor` |
| [v2.25.3](version_history.md#v2253) | 2026-11-10 | A rotated JPEG's archive decodes upright; `--delete-source` keeps layered TIFFs and float/animated JXLs; 10/12-bit TIFFs refused; a failed move out of staging is an error |
| [v2.25.2](version_history.md#v2252) | 2026-11-10 | Two runs at once never write over each other's output; a lossy decode with `--sync` leaves the camera's JPEG alone; flush to disk before every delete |
| [v2.25.1](version_history.md#v2251) | 2026-11-08 | The new-release notice shown on the next start; `D` checks for releases; a missing `TEMP_DIR` created by the JPEG transcoder too |
| [v2.25.0](version_history.md#v2250) | 2026-11-06 | The TIFF's byte order kept in masters, copies and decoded TIFFs; a new JPEG quality redoes the JPEG copies |
| [v2.24.0](version_history.md#v2240) | 2026-11-06 | The encode record leaves the Title for the XMP History; 8-bit TIFFs lossless at `d=0.1` and below; typical file sizes next to every distance in the wizard |
| [v2.22.5](version_history.md#v2225) | 2026-11-04 | Each distance named in the wizard, masters and smaller copies; the film-scan distance on the review screen; distances up to 25 |
| [v2.22.1](version_history.md#v2221) | 2026-11-04 | Every wizard question on one question layer; same questions and command lines |
| [v2.22.0](version_history.md#v2220) | 2026-11-04 | The wizard by task: a New job and one review screen with every option; new menus; a JXL → JPEG never deletes the JXL; modes 6/7 anchor below the input |
| [v2.21.0](version_history.md#v2210) | 2026-11-04 | JPEG/PNG of cautious-strategy masters carry their real profile; less memory per worker; a failed metadata copy keeps the source |
| [v2.20.0](version_history.md#v2200) | 2026-11-03 | Hardening: an unreadable provenance record is refused, not overwritten; modes 6/7 skip the other scripts' folders; planning reports progress |
| [v2.19.2](version_history.md#v2192) | 2026-11-03 | IrfanView shows the EXIF of JPEG → JXL files; menu option 8 repairs older ones in place, proving the same JPEG comes back |
| [v2.19.1](version_history.md#v2191) | 2026-11-02 | IrfanView shows the EXIF of new JXLs again; menu option 9 repairs files written by v2.17.0–v2.19.0 in place |
| [v2.18.1](version_history.md#v2181) | 2026-11-01 | A recompressor sync plans only what it will process: up-to-date files are skipped without being read |
| [v2.17.0](version_history.md#v2170) | 2026-10-24 | Film scans archived lossless and never recompressed lossy unless asked; they keep what their scanner software needs (whole XMP, private tags); thumbnail pages kept by default, except Capture One's |

Every release since v1.0: [version_history.md](version_history.md#release-history).

## v2.29.0_beta1

2026-11-12, pre-release. Nothing changes in what the scripts do with your photos; the last stable release is [v2.28.0](version_history.md#v2280).

- **A release is now six files that go together**: the five scripts and `jxl_photo_common.py`, which holds the code they share. Keep them in one folder ([upgrading](upgrading.md)); a script copied on its own stops with a line that says so. The Windows program already carries all of them.
- Same command lines, same settings, same files written.

[Version history](version_history.md#v2290_beta1).

## v2.28.0

2026-11-11. Small fixes to what a master and its copies carry; most people have nothing to do ([upgrading](upgrading.md)).

- **A TIFF's small Photoshop resource block comes back** in the decoded TIFF, like the IPTC block; a large one (a Lightroom preview) is still left out, and the run says so.
- **JPEG and PNG copies are lighter**: they no longer carry the markers only a TIFF decode uses, and no copy is made of a camera TIFF's own 160 px thumbnail page.
- **Resized copies work in a folder named like `100%off`**, where ImageMagick misread the name.

[Version history](version_history.md#v2280).

## v2.27.1

2026-11-11. Hardening that closes an edge case of the lossy JPEG → JXL conversion; the lossless transcode, the default for JPEGs, is unchanged.

- **A lossy JPEG → JXL keeps a JPEG that has an HDR gain map** (Ultra HDR photos from recent phones): the lossy JXL holds the standard picture only, so `--delete-source` no longer deletes such a JPEG, and the run names each one. The lossless transcode keeps the whole file ([upgrading](upgrading.md)).
- **A JPEG whose maker notes exiftool cannot rewrite** names the way out (`--force-transcode`) for more of exiftool's wordings.

[Version history](version_history.md#v2271).

## v2.27.0

2026-11-11. Two additions for TIFF archives and one fix; most people have nothing to do.

- **A TIFF's IPTC data reaches the master**: the fields are copied into the XMP where the XMP lacks them, and the decoder writes the IPTC block back byte for byte. A SilverFast scan whose capture date lived only in the IPTC keeps it. Exports from Capture One or Lightroom already carry the same data in their XMP.
- **The encoder's sync compares the content, not only the date**: a TIFF with a newer date but the same bytes (a backup restored, a folder copied) is skipped. `--overwrite` remakes a master on purpose ([upgrading](upgrading.md)).
- **A lossy JPEG → JXL of a JPEG with a broken thumbnail IFD converts** instead of ending in an error.

[Version history](version_history.md#v2270).

## v2.26.1

2026-11-10. Hardening that closes edge cases; nothing to do when upgrading.

- **A JXL made from a rotated JPEG is shown the right way up even without `jxlinfo`**: the scripts read the orientation from the file's header themselves.
- **PNGs and turned-back JPEGs keep the camera's `InteropIndex`** (the DCF colour-space tag), and a JXL → JPEG run calls exiftool once less per file.

[Version history](version_history.md#v2261).

## v2.26.0

2026-11-10. One new behaviour and the fixes left open after v2.25.3.

- **A JPEG made from the JXL of a rotated JPEG comes back as the camera wrote it**: the stored pixels and the original EXIF Orientation (it shows the same way up). `--jpeg-orientation upright` or `JPEG_ORIENTATION` keeps v2.25.3's upright JPEG; PNG and TIFF stay upright ([upgrading](upgrading.md)).
- **JXL → PNG of camera JPEGs, and resizing or colour-converting a source with the standard sRGB profile** (camera and phone JPEGs, sRGB TIFF masters), work instead of ending in an error. Nothing to do.
- The decoder's new `--exiftool-ignore-minor`, off by default, for the rare JXL whose maker notes exiftool cannot read; the end of a run lists such files. `jxlinfo` is now part of the dependency check.

[Version history](version_history.md#v2260).

## v2.25.3

2026-11-10. Fixes from a bug sweep; most close rare cases.

- **A JXL made from a rotated JPEG decodes upright** to JPEG, PNG or TIFF, and its recompressed copies too: the EXIF Orientation is set to 1, since the pixels are already turned. Uses `jxlinfo`, which comes with cjxl/djxl.
- **`--delete-source` keeps what a JXL cannot hold**: a TIFF with Photoshop layers, and a float or animated JXL decoded to TIFF/PNG. A 10/12-bit TIFF is refused instead of archived 16 times darker.
- **A move out of staging that fails is an error** (exit code 1), not a success with the old file in place.
- The wizard follows the recompressor's own `ON_DOWNGRADE`/`ON_REGENERATION`/`ON_SCAN`, and a preset without effort, quality or distance uses the script's setting. What changes on an existing command line: [upgrading](upgrading.md).

[Version history](version_history.md#v2253).

## v2.25.2

2026-11-10. Hardening that closes edge cases.

- **Two runs started at the same time never write over each other's output**; a lossy JXL → JPEG/PNG `--sync` leaves a same-named camera JPEG/PNG alone ([upgrading](upgrading.md)); every script flushes each output to the disk before deleting the source.

[Version history](version_history.md#v2252).

## v2.25.1

2026-11-08. Small fixes; nothing to do when upgrading.

- **The new-release notice reaches you even when you close the window after a job**: the check runs in the background and keeps its answer, so the next start shows it (with the day it was checked). `D` in the menu now checks for a new release too.
- **A `TEMP_DIR` that does not exist yet** is created before the JPEG transcoder starts, like the other scripts already did; a dry run says the folder is missing instead of failing its reads.

[Version history](version_history.md#v2251).

## v2.25.0

- **The TIFF's byte order is kept**: a big-endian TIFF (SilverFast scans,
  some Mac programs) gives a big-endian master and decodes back to a
  big-endian TIFF, and every copy keeps its master's order. Only how the EXIF
  numbers are stored changes, not their values; little-endian TIFFs (Capture
  One, Lightroom) behave as before.
- **A new JPEG quality redoes the JPEG copies** on the next sync, like a new
  size or sharpening. JPEGs made before are kept; run once with
  `--overwrite` to redo them ([Upgrading](upgrading.md#v2250-the-tiffs-byte-order-is-kept-a-new-jpeg-quality-redoes-the-jpeg-copies)).

[Version history](version_history.md#v2250).

## v2.24.0

- **The cjxl record no longer shows as the photo's Title** in Windows
  Explorer: it is kept in the file's XMP History, and your caption stays your
  own. `--encode-tag comment` shows it in Explorer's Comments,
  `--encode-tag title` keeps the old behaviour. Older files are read as they
  are ([Upgrading](upgrading.md#v2240-8-bit-tiffs-are-archived-lossless-at-d01-and-below-the-encode-record-leaves-the-title)).
- **8-bit TIFFs are archived lossless at `d=0.1` and below**: lossless is
  small for them (43 MB for 45 MP), and `d=0.05` would come out larger.
  `--eight-bit-distance same` keeps the old behaviour
  ([Upgrading](upgrading.md#v2240-8-bit-tiffs-are-archived-lossless-at-d01-and-below-the-encode-record-leaves-the-title)).
- **The wizard shows the typical file size next to every distance** (45, 24
  and 12 MP), and "your distance" shows them all, from 0 to 25.
- `d=0.7` (keeps the grain texture) in both distance lists, `d=5` for small
  copies; shorter names and lighter notes. The menu also says when
  [jxl-thumb](https://github.com/rsilvabr/jxl-thumb) has a newer release.

[Version history](version_history.md#v2240).

## v2.23.0

- **Derivatives (sRGB, resized) and JXL -> TIFF are about 35-40% faster**: a
  temp file in the pipeline is no longer compressed. Same pixels.
- **`--export-jpeg-folder`** names the folder a JXL -> JPEG/PNG run of modes
  6/7 writes (e.g. `JPG_mobile`); the wizard and the manifests use it too.
- **The wizard reads better**: choices, lists and the review in tables, one
  name per mode everywhere, actions grouped by what they do, a blank line
  before every question.
- The update check never makes the menu wait; jxl-thumb is recommended when
  Microsoft's extension makes the thumbnails; both reminders can be turned
  off in Settings.

[Version history](version_history.md#v2230).

## v2.22.1–v2.22.5

2026-11-04. Every wizard question goes through one question layer. Distances up to 25 (cjxl's own limit) are accepted, not only up to 15. The wizard names each distance it offers: for masters (lossless,
finest lossy, near-lossless, high-quality lossy, visually lossless for
viewing) and for smaller copies (cjxl's recommended 0.5 to 3). The review
screen shows the distance film scans get, right below the run's. Enter still
takes your default distance. [Version history](version_history.md#v2225).

## v2.22.0

The wizard is organised by task, and every option is within reach.

- **A New job asks what to do, where and how**, then shows one review screen with every option and where its value came from: `[r]` runs it, `[d]` runs it dry first, `[a]` shows all the options — including the ones the wizard never asked about (`--buffering`, `--restamp-then-sync`, …). The menu numbers changed ([Upgrading](upgrading.md#v2220-a-jxl--jpeg-never-deletes-the-jxl-modes-67-anchor-below-the-input-and-leave-derivatives-out)); presets and the last job carry over by themselves.
- **New menus** for renamed or moved folders, the ICC test cache and the scripts' own settings (changed without editing the scripts).
- **A JXL → JPEG (or 8-bit PNG) never deletes the JXL**; modes 6/7 anchor on the `_EXPORT` below the input and leave derivatives out.
- The menu says when a newer release is out (checked at most once a day).

Full notes: [version history](version_history.md#v2220).
