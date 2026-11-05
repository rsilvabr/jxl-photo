# Upgrading

Most releases need nothing from you: same pixels, same colour profiles, same
metadata, same command lines. This page lists the few that changed what an
existing command line does, or that found something worth checking in
archives an earlier version wrote. Newest first; skip everything older than
the version you are coming from.

Per-release notes: [version_history.md](version_history.md) and the
[GitHub releases](https://github.com/rsilvabr/jxl-photo/releases).

---

## v2.24.0: 8-bit TIFFs are archived lossless at d=0.1 and below

- **An 8-bit TIFF that is not a film scan is encoded lossless** when the run's
  `--distance` is 0.1 or less (`EIGHT_BIT_DISTANCE = "auto"`). Its JXL is
  bigger than before (about 43 MB instead of 33 MB for 45 MP at `d=0.1`) and
  exact. 16-bit TIFFs and runs at a higher distance do not change.
  `--eight-bit-distance same` (or `EIGHT_BIT_DISTANCE = "same"`) keeps the
  old behaviour.
- **`--delete-skipped` over 8-bit masters archived at `d=0.1` or less by an
  earlier version** now judges them as lossless and keeps their TIFFs (a
  `KEEP` line that names `--eight-bit-distance same`). Add that flag to such
  a run, or re-archive those TIFFs with `--overwrite`.

---

## v2.22.0: a JXL → JPEG never deletes the JXL; modes 6/7 anchor below the input and leave derivatives out

- **A JXL decoded to a JPEG or to an 8-bit PNG never deletes the JXL** (#600):
  a JPEG is lossy and an 8-bit PNG may have fewer bits than the master — the
  same rule as the decoder's 8-bit TIFF and the recompressor's derivatives.
  `--force-convert --decode … --delete-source` to JPEG/8-bit PNG now exits 2;
  the automatic JXL → JPEG keeps each JXL it converts lossy (a `KEEP` line);
  the wizard refuses the combination before the HHMM time and a manifest row
  runs without its delete. The bit-exact recovery of a JPEG archive and a
  16-bit PNG still delete.
- **Modes 6/7 anchor on the first `_EXPORT` at or below the input folder**
  (#601). A library kept under a folder whose name also matches the marker
  (`Lightroom_Export`, `C1_Export`) used to anchor there, writing outputs in
  `Lightroom_Export\16B_JXL\…`; they now go below the real `_EXPORT`. If you
  ran modes 6/7 on such a library, the next run writes the outputs in the
  right place: delete the old folder by hand once you have checked it. A run
  pointed AT the matching folder still anchors on it.
- **Modes 6/7 leave out derivatives** (#602): the decoder, the recompressor
  and the transcoder's JPEG/PNG export skip every JXL carrying a
  `jxlphoto-derived` marker (an sRGB or resized copy next to its master)
  unless mode 7 asks for its folder with `--export-subfolder`. Runs that
  stopped on "Duplicate output destination" now go ahead.
- **Expert flags are checked before the run** (#603, #604): a `--mode` there
  is refused (pick the mode in the wizard), and a flag the script does not
  know, or a value outside its choices, is refused before the HHMM time
  instead of failing after it.
- **The wizard is organised by task, and its menu numbers changed.**
  1 New job, 2 Repeat last job, 3 Presets, 4 Folder list (manifest),
  5 renamed folders, 6 Explorer thumbnails, 7 JPEG archives (jbrd), 8 ICC
  test cache, 9 Settings (wrapper defaults, script settings, reset, move),
  D dependencies. A New job asks the task, the folder, the mode and the
  quality, then shows one review screen: `[r]` runs it, `[d]` runs it dry
  first, `[a]` shows every option. A job started with `[r]` right away
  runs what the old wizard ran when every question was answered with
  Enter.
- **Presets and the last job carry over by themselves**: nothing to redo,
  and `--run-preset` runs the same command lines. Presets saved from now on
  also keep the old fields, so an older copy of the toolkit sharing the
  same settings file (an execution copy, the Windows program) still runs
  them.
- **The menu checks once a day whether a newer release is out** (an
  anonymous GitHub request; nothing is downloaded, `--run-preset` never
  checks). Off with `"check_for_updates": false` in the settings file or
  `JXLPHOTO_NO_UPDATE_CHECK=1`.

## v2.21.0: lossy JXL → JPEG/PNG keeps the master's profile; metadata failures keep the source

- **A JXL → JPEG/PNG conversion without `--icc-profile` now gives the output
  the master's own profile** when the master kept it only in XMP (the
  encoder's cautious ICC strategy does this for scanner and table-curve
  profiles). Those outputs used to be labelled sRGB: if you delivered JPEGs
  from such masters, convert them again. A lossy master storing its profile
  as an ICC blob now goes through ImageMagick on that path, so ImageMagick is
  needed there.
- **A failed metadata copy or marker write is an ERROR** in the transcoder's
  lossy conversions, as in the encoder and the decoder: the output is
  removed and, with `--delete-source`, the source is kept. Re-run once
  exiftool works again.
- With `DELETE_SOURCE_REQUIRE_MD5 = False` (not the default), a JXL → JPEG
  recovery without a checksum line deletes the JXL only when
  `djxl --reconstruct_jpeg` reproduces the JPEG on disk.

## v2.20.0: unreadable provenance records are refused; modes 6/7 skip other scripts' folders

Nothing to redo. Two things may look different:

- **"its provenance markers could not be read"**: an existing output whose
  record exiftool could not read (a very busy disk, a file locked by another
  program) is no longer overwritten. Re-run when the disk is quiet; with
  `--no-overwrite-guard` it is overwritten with a warning, as before.
- **Modes 6/7 of the decoder and the recompressor skip the other scripts'
  folders** below `_EXPORT` (`16B_JXL_small`, `recompressed_jxl`,
  `JXL_recompressed`, `JXL_small`, `*_JXL_small`, `JXL_jpeg`, and the names
  configured in those scripts), logged as `Ignored N JXL(s) in other tools'
  output folders`. To decode or recompress such a folder on purpose, use
  mode 7 with `--export-subfolder` set to it. Runs that used to stop with
  "Duplicate output destination" because of those folders now go through.

Optional: if you ran `--restamp-moved` or `--provenance adopt` on masters
with an Explorer thumbnail, menu option 9 puts the thumbnail back in its
documented place before the image data (Explorer showed it either way).

## v2.19.2: IrfanView shows the EXIF of JPEG → JXL files; repair older ones in place

Lossless JPEG → JXL files written before v2.19.2 show no EXIF in IrfanView:
cjxl stored it Brotli-compressed, which IrfanView cannot read (exiftool,
XnView MP and digiKam can). New files store it uncompressed, a median 2.3 KB
more per photo. To repair the ones you have, without touching the image:

- menu option 8, *Repair JPEG->JXL files*, on the folder (audit first, then
  the real run), or
- `py jxl_jpeg_transcoder.py "G:\Photos" --repair-jbrd --dry-run`, then
  without `--dry-run`.

Files it fixed are logged `EXIF MADE READABLE`; each still rebuilds the very
same JPEG (checked before the file is replaced) and keeps its modified date.
This step uses cjxl; without it on `PATH` the run says so and skips it.

Prefer the smallest file over IrfanView? Set `COMPRESS_METADATA_BOXES = True`
at the top of `jxl_jpeg_transcoder.py` (or pass `--compress-metadata`); the
repair then leaves the EXIF layout alone.

## v2.19.1: IrfanView shows the EXIF again; repair older files in place

JXLs written by v2.17.0, v2.18.x and v2.19.0 (masters and the copies made
from them) show no EXIF in IrfanView: the EXIF is in the file, but stored
after the XMP, where IrfanView does not look. New files are right. To
repair the ones you have, without re-encoding:

- menu option 9, *Explorer thumbnails for existing JXLs*, on the folder
  (with subfolders), or
- `py jxl_recompressor.py "G:\Photos" --mode 8 --refresh-explorer-thumbnails`

Files it repaired are logged `REORDERED` (or `WRITTEN` when they also got a
thumbnail, with "EXIF box moved before the XMP"). The image data is not
touched and each file keeps its modified date. Re-encoding with
`--overwrite` gives the same result.

The TIFF encoder's `WORKER_MEMORY_FRACTION` is now 1.0 (was 0.8): it may
start a few more workers on the same machine. Set 0.8 back at the top of
`jxl_tiff_encoder.py` if other heavy programs run during your conversions.

## v2.19.0: the ICC copy moves to a jxlphoto-iccprofile marker

Nothing to do for your files. The copy of the original ICC profile that the
encoder keeps in the XMP is now a `jxlphoto-iccprofile:<base64>` entry in
`XMP-dc:Relation` instead of a block appended to `CreatorTool`, so "creator
software" shows only the program's name again
([details](metadata_written.md#the-icc-copy-jxlphoto-iccprofile)).

- **Files written by older versions keep working**, and are not rewritten.
  They move to the new form only when a run writes a new file from them:
  re-encode the TIFFs with `--overwrite` (a sync skips up-to-date masters),
  or recompress.
- **Use v2.19.0 everywhere you decode.** An older version cannot read the
  new marker. A master whose profile is only in that copy (some scanner
  profiles, or `--icc-png-strategy skip`) decodes there to a TIFF with no
  profile, shown as sRGB; other masters keep their colours but get djxl's
  equivalent profile instead of the original one. Update every copy of the
  scripts, and the Windows program, together.

## v2.18.0: deleting TIFFs checks pixels; old thumbnail pages are removed; derivatives skip them

Nothing to do. Five things a run now does that it did not before:

- **A run of the encoder that deletes TIFFs verifies every JXL first.**
  `VERIFY_ROUNDTRIP` is now on by default: before a TIFF is deleted, its JXL
  is decoded and compared with it (identical pixels for `--distance 0`, a
  brightness and PSNR sanity check for lossy). Such runs take roughly twice
  as long and need imagecodecs (the exe includes it); runs that delete
  nothing are unchanged. For the old structure-only gate, set
  `VERIFY_ROUNDTRIP = False` at the top of `jxl_tiff_encoder.py`. The menu no
  longer asks about it for TIFF → JXL while it is on.

- **The encoder removes a thumbnail page an earlier encode of the same TIFF
  left behind** — after re-archiving with `--thumbnail-mode exclude
  --overwrite`, or when a photo re-exported from Capture One no longer keeps
  its thumbnail under `auto`. Only for a TIFF the run rewrote completely and
  that still exists, and only a file that carries that TIFF's provenance
  marker. A leftover real page (a scan saved again without its IR page) is
  never deleted: the run names it with a warning, for you to check.
- **Derivative runs** (the recompressor's `--output-icc`, `--resize-*` and
  `--sharpen`, and the transcoder's resized, sharpened or colour-converted
  JXL → JPEG/PNG) make no derivative of a thumbnail page. Derivatives of
  thumbnail pages written by v2.17.0 stay where they are; delete them if you
  have any (`*_thumbnail*` in the derivative folders).
- **A deleting JPEG → JXL run can delete JPEGs an earlier version kept.** A
  lossless JXL without its `checksums.md5` line — left by a run that was
  stopped midway, or written with `--no-md5` — is now accepted once
  `djxl --reconstruct_jpeg` gives its JPEG back byte for byte, so
  `--delete-source --delete-skipped` deletes that JPEG instead of keeping it
  for good. Every other delete check is unchanged.
- **A lossless JPEG archive is overwritten only by the same JPEG.** A sync or
  `--overwrite` that meets an existing lossless archive with no checksum line
  (transcoder), or an existing lossless archive in the output folder
  (recompressor), now asks `djxl --reconstruct_jpeg` first, and refuses — exit
  code 1, `REFUSING`/`REFUSED` in the log — when the archive holds another
  JPEG. If the photo really changed, delete the old `.jxl` first.

## v2.17.0: thumbnail pages are kept by default; film scans are lossless

- **Film scans are encoded lossless** (`SCAN_DISTANCE = 0`), whatever
  `--distance` the run uses: a scan's JXL is several times larger than at the
  photo distance (a 91 MP negative: ~440 MB lossless, 107 MB at `d=0.05`).
  A scan is a TIFF whose Software tag names a program in `SCANNER_SOFTWARE`,
  or that has an IR channel, or neither a Software tag nor camera EXIF (lab
  scans, Epson Scan; the run lists the folders of these guessed scans before
  it starts, and a camera's 160×120 thumbnail page rules one out). To keep the old behaviour, pass
  `--scan-distance same` or set `SCAN_DISTANCE = None`; `--scan-scope raw`
  keeps only raw scans lossless. Existing archives are not touched by a sync
  run.
- **The recompressor copies film scans verbatim** instead of re-encoding them
  lossy (`--on-scan copy`), including masters archived before v2.17.0 that it
  recognises by their Software tag or IR page. Pass `--on-scan convert` to
  recompress them anyway; derivatives are not affected.

The rest of this section came with the pre-release v2.17.0_beta1:


- The encoder's `--thumbnail-mode` default is now **`auto`**: the embedded
  thumbnail page of a multi-page TIFF is archived as
  `<name>_page1_thumbnail.jxl` (a few % of the master's size) and comes back
  in the decoded TIFF. Capture One's tiny export previews are still left out
  (`THUMBNAIL_DROP_SOFTWARE`). `--thumbnail-mode exclude` keeps the old
  behaviour; `scans` keeps them for scans only (`SCANNER_SOFTWARE`, or an IR
  page).
- **Archives you already have do not change on a sync run**: a TIFF whose
  JXLs are up to date keeps the pages it was archived with. To add the
  thumbnail (and, for film scans, the scanner's XMP settings and private tags
  that v2.17.0 now keeps), re-archive those folders from their TIFFs once with
  `--overwrite`.
- **A wizard answer or preset that chose `exclude`** keeps passing
  `--thumbnail-mode exclude`. Change it in the preset's advanced options if
  you want the new default there.
- Nothing else changes for files without a thumbnail page.

## v2.16.0: lossless outputs carry a small Explorer thumbnail

- The encoder and the recompressor now store a 768 px sRGB JPEG inside their
  outputs (`EXPLORER_THUMBNAIL = "auto"`), in a box of the JXL container that
  other programs skip, for [jxl-thumb](https://github.com/rsilvabr/jxl-thumb)
  0.2.0+: **every** output when jxl-thumb is installed for your Windows user,
  **lossless** outputs only when it is not. About 90 KB per file; ~2 s more
  per 45 MP lossless encode, ~1.2 s per lossy one. To keep the old
  behaviour, set `EXPLORER_THUMBNAIL = "off"` at the top of both scripts or
  pass `--explorer-thumbnail off`.
- Nothing to check in existing archives. To give existing files a
  thumbnail: menu option 9, or
  `jxl_recompressor.py <folder> --mode 8 --refresh-explorer-thumbnails`.

## v2.15.2: a deleting transcoder run leaves JXLs alone unless told to decode

One command line changes, and only when you call the transcoder yourself (the
`jxl_photo.py` workflows already name their direction):

- A **folder** run of `jxl_jpeg_transcoder.py` in auto mode (no `--decode`,
  `--force-transcode` or `--force-convert`) with `--delete-source` now converts
  only JPEGs and PNGs. The JXLs in the folder are left untouched, and the log
  says how many.
- To decode JXLs and delete them, say so: add `--from-jxl` (every JXL, lossy
  ones converted) or `--decode` (lossless recovery of `jbrd` JXLs only).

Before, such a run also decoded every JXL it found, and deleted it. For a
lossy master from the TIFF encoder that meant an 8-bit JPEG in place of the
16-bit master. Details: bug #542.

## v2.15.0: the D50 patch decides by the profile

Nothing to check for the usual setup. `--d50-patch auto` (and `D50_PATCH_MODE
= "auto"`, the default) now looks at the ICC profile instead of the EXIF
Software tag:

- It patches only a near-miss illuminant (Kodak's ProPhoto.icm, which Capture
  One and other programs embed). No exiftool call per TIFF any more.
- A TIFF from another program carrying that profile now gets the same
  normalisation as a Capture One one. The change is invisible.
- `D50_PATCH_SOFTWARE_LIST` is gone. An edited copy of the encoder that sets it
  just stops using it.

## v2.14.0: the overwrite guard can be turned off

Nothing to check: the guard stays ON unless you turn it off. If you rename or
move folders and never delete sources with the toolkit, consider turning it off
(`--no-overwrite-guard`, `OVERWRITE_GUARD = False`, or "Overwrite guard: off"
in the wizard) — see
[Renamed folders and the overwrite guard](behavior_and_limitations.md#renamed-folders-and-the-overwrite-guard).
A preset that deletes sources keeps the guard on: the two together are refused.

## v2.13.0: each output records the version that wrote it

Nothing to check. Two things you may notice:

- Every write now carries its own timestamp in `xmpMM:History`, so re-running
  with `--overwrite` gives a file with a different hash. A backup that compares
  hashes copies it again.
- The recompressor no longer carries XMP fields in a namespace exiftool does
  not know (rare; some third-party apps write their own). The other three
  scripts never did.

When you update a folder of the scripts by copying, copy all five: the
wrapper now warns when one of them is from another release.

## v2.12.0: `--embed-thumbnail` does nothing

Nothing to check. The encoder no longer writes the optional EXIF thumbnail —
no viewer tested reads it. `--embed-thumbnail` is still accepted and only logs
a warning, so scripts, manifests and presets keep working. The
`EMBED_JPEG_THUMBNAIL` setting at the top of `jxl_tiff_encoder.py` no longer
exists; nothing to carry over.

## v2.11.0: the regeneration guard can fire one generation earlier

Nothing to check for masters made from TIFFs. A recompressor run over a file
whose first master came from a JPEG or from a lossy JXL of another program
(`jxlphoto-origin:jpeg`, `jxl-lossy` or `jxl` — only files written since
v2.10.0 carry it) now meets `--on-regeneration` already at `gen=1`. With the
default `ask`, an unattended run (wrapper, manifest, scheduled task) **skips**
such a file where it used to re-encode it; the log says
`SKIP (policy) | … (+1 before this toolkit: …)`. Pass
`--on-regeneration convert` if you do want the re-encode.

## v2.9.0: the recompressor may run more workers than encodes

Nothing to check; the outputs are the same. On the big-memory settings
(effort 8–9 above distance 0.5, effort 7 from distance 3, effort 10) a
recompressor run may now use **more workers than before** while still running
at most as many cjxl at once as fit in memory — the log says
`workers W, cjxl at a time S`. `WORKER_MEMORY_FRACTION` at the top of
`jxl_recompressor.py` went from 0.8 to 1.0 (at 0.8 a typical run has no
memory left over for the extra workers). If you had edited it, re-apply your
value in the new script. The TIFF encoder's setting is unchanged.

## v2.8.1: an edited decode is left alone

Nothing to check. Two refusals you may notice:

- The decoder's sync no longer decodes over a TIFF you edited after decoding
  it, and such a TIFF no longer lets `--delete-skipped` delete its JXL. Pass
  `--overwrite` if you do want the fresh decode.
- The transcoder refuses an `--icc-profile` file that is an input/scanner
  profile (A2B tables, no B2A) — exit 2. Convert to a working space instead
  (sRGB, AdobeRGB, ProPhoto, ...).

## v2.8.0: an overwrite checks whose output it is

A run never overwrites an existing output whose provenance marker names a
**different** source — in every mode, with or without `--delete-source`,
`--overwrite` included. It stops that file with exit 1 and lists it in the
failures. This only meets you when two different photos land on the same
output name.

- Typical causes: two photos with the same name meeting in a folder-collapsing
  mode (2/4/5/6/7), `foto.tif` and `foto.tiff` in one folder, or a folder
  **moved** together with its outputs (the recorded location changed) — for
  the last one re-run with `--provenance content`, or delete the stale output
  if it is really obsolete.
- Transcoder: a JPEG edited in place after its lossless archive was made is
  refused on `--sync` (its checksum no longer matches); delete the old `.jxl`
  to archive it again. `--icc-profile`/`--to-srgb` with `--delete-source`
  exits 2 (a colour conversion is a derivative).
- Decoder: `--none` with `--delete-source` exits 2; `--depth 8` and `--basic`
  decodes keep the JXLs they degrade.
- Recompressor: a lossy file storing its ICC profile as a blob is copied
  verbatim, never re-encoded, and a scanner/table-curve profile is re-encoded
  tagged sRGB with the profile in XMP.
- **Film scans only:** a lossless scan master that an earlier version
  recompressed may have come out with shifted colours. To check one:
  `djxl f.jxl o.png --icc_out=a.icc --orig_icc_out=b.icc` — it is affected
  when `a.icc` and `b.icc` differ. The file cannot be repaired from itself;
  re-archive it from the TIFF if you still have it. The decoder decodes it as
  well as it can and never deletes it.

## v2.6.0: `--workers` can be lowered for you

The TIFF encoder and the recompressor now cap `--workers` so the parallel cjxl processes fit in memory. Nothing changes in the output; a run may just use fewer workers than you asked for, and the log says so (`--workers 30 reduced to 8`). It happens mostly at **effort 7 with distance ≥ 3**, **effort 8–9 with distance > 0.5** and **effort 10**, where cjxl encodes the whole image at once.

- The budget is the memory the system can still **commit** when the run starts, so programs left open (a raw editor can hold 15 GB) mean fewer workers that night.
- To get the workers back: `--buffering 1` keeps those settings on the low-memory streaming path — at effort 7 for files ~1.5 % larger and the same quality, but at effort 8–9 it simply gives effort 7's file (see [Streaming vs whole-image](README_jxl_recompressor.md#streaming-vs-whole-image-what---buffering-1-costs-measured), measured with cjxl 0.12.0); or close other programs before a scheduled run.
- `WORKER_MEMORY_FRACTION` at the top of `jxl_tiff_encoder.py` and `jxl_recompressor.py` sets the share used (default 0.8); `0` turns the cap off.
- If you schedule presets, switch the task to `cmd /k` so a failed run stays on screen: see [Keep the window open](README_jxl_tools.md#keep-the-window-open--or-a-failed-run-goes-unseen).

## v2.5.0: `--delete-skipped` needs a matching provenance marker in every mode

`--delete-skipped` deletes a source whose output **already existed** — a file this run never wrote. Until v2.4.0 the TIFF encoder certified that output by name, timestamp and an integrity check, and the decoder by the mere *presence* of a marker; in the folder-preserving modes (0/1/3/8) a valid, newer JXL of a **different photo** with the same name (a camera's file counter restarting across cards, folders merged) deleted the master TIFF. v2.5.0 requires the output's `jxlphoto-src`/`jxlphoto-srcsum` marker to **match** the source, in every mode.

- **Archives written before v2.0.0 carry no marker**, so `--delete-skipped` now **keeps** their sources (with a log line saying so) where it used to delete them. Nothing is lost; to finish such an archive, re-encode it (`--overwrite`), or stamp it once with `--provenance adopt` in a folder-collapsing mode (TIFF → JXL).
- A folder **moved** as a whole (sources and outputs together) no longer matches under the default `--provenance path`; pass `--provenance content`.
- The wrapper now **always** passes its export marker (menu option 4) to the scripts. If you edited `EXPORT_MARKER` at the top of a script and ran it through the wrapper, set the same marker in the wrapper.

## v2.3.0: TIFFs decoded from JXLs with a table-curve ICC profile

Earlier releases decoded a **lossy** JXL whose ICC profile has a table tone curve (ROMM RGB with its linear toe, eciRGB v2, scanner LUT profiles, Photoshop "Dot Gain" grey) with **wrong colours**, and logged it as OK. The same went for recompressor derivatives and transcoder JPEG/PNG conversions of those files. The **JXLs themselves are fine**: re-decode them with v2.3.0.

- Most archives are not affected: Capture One / Lightroom exports in ProPhoto, Elle's LargeRGB, Adobe RGB or sRGB have a native JPEG XL form, and the encoder's default `cautious` strategy already encoded eciRGB v2 and scanner profiles as "skip", which decoded correctly.
- Affected are files whose table-curve profile passed the cautious test, or files encoded with `--icc-png-strategy always`. To check one: `djxl file.jxl out.png --icc_out=a.icc --orig_icc_out=b.icc` — the file is affected when `a.icc` and `b.icc` differ.
- Decoding those files now needs ImageMagick on PATH (without it the decode fails with an error instead of writing a wrong TIFF).
- The encoder re-tests every ICC profile once on its first run (the cautious cache format changed), so that run is a little slower.

## v2.2.0: four things to check if you come from an earlier release

The audits behind v2.2.0 found paths where earlier releases produced a wrong archive or deleted a source they should not have. v2.2.0 refuses or keeps in every one of them; what it cannot do is repair what an earlier run already did:

- **TIFFs in CIELAB, YCbCr or MINISWHITE photometric** were archived by the encoder as if they were RGB/MINISBLACK — wrong colours, or inverted tones — and reported as a clean encode. v2.2.0 refuses them with a per-file reason. Capture One / Lightroom / NX Studio exports are RGB and unaffected; if you archived scans or files from other tools, spot-check them before discarding the TIFFs.
- **`jxl_tiff_decoder.py --matrix --delete-source`** dropped the alpha channel and deleted the JXL anyway. Under `--matrix` the sources are now always kept.
- **`jxl_jpeg_transcoder.py --force-convert --distance 0 --delete-source`** broke the JPEG reconstruction data and deleted the original JPEG without testing it. The JXLs are still valid images; the bit-exact JPEG is not recoverable from them. `--repair-jbrd --dry-run` tests the reconstruction of every JXL in a folder and reports the ones that fail.
- **`jxl_recompressor.py --delete-source --delete-skipped` in modes 1/3** could delete a source JXL when a same-named output written from a *different* photo already existed. It now requires the provenance proof in every mode.

## JPEG → JXL archives made with v2.0.0 – v2.0.3: check them before discarding the JPEGs

Those versions wrote their provenance marker into the XMP of every JXL — including the lossless JPEG transcodes (`jbrd`). For a JPEG that already carried XMP (typical of Lightroom / Capture One exports) that makes `djxl --reconstruct_jpeg` **fail**: the original JPEG is no longer recoverable bit-exactly, and `--delete-source` deleted those JPEGs anyway. JPEGs without XMP were not affected. The fix stops writing markers into `jbrd` containers and proves the reconstruction before any JPEG is deleted. For existing archives:

```powershell
py jxl_jpeg_transcoder.py "F:\Photos" --repair-jbrd --dry-run   # audit only
py jxl_jpeg_transcoder.py "F:\Photos" --repair-jbrd             # repair
```

A repaired file reconstructs a JPEG with **identical image data**; only its metadata bytes differ from the original. See [Repairing broken JPEG reconstruction](README_jxl_jpeg_transcoder.md#repairing-broken-jpeg-reconstruction---repair-jbrd).

## Coming from v1.9.1 or earlier: two things changed under existing command lines in v2.0.0

**1. `--delete-source` now works in every mode.** In v1.9.1 it was `if DELETE_SOURCE and mode == 8` — outside mode 8 the flag was silently ignored. A saved command or script with `--mode 3 --delete-source` deleted **nothing** then and deletes the originals **now**.

**2. An archive made before this release can be refused.** Runs that delete sources in a folder-collapsing mode (2/4/5/6/7, and mode 0 with an output folder) now check that the existing output really came from the source about to replace it. Outputs written before v2.0.0 carry no such record, so they are refused rather than overwritten. For TIFF → JXL, `--provenance adopt` verifies and stamps them in a single pass; the decoder and the transcoder have no equivalent yet — use a structure-preserving mode (0/1/3/8) for those folders.

Read [Upgrading from v1.9.1](version_history.md#upgrading-from-v191) before running anything destructive. Nothing about ordinary conversion changed: same pixels, same ICC, same metadata.
