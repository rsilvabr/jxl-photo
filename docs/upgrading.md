# Upgrading

Most releases need nothing from you: same pixels, same colour profiles, same
metadata, same command lines. This page lists the few that changed what an
existing command line does, or that found something worth checking in
archives an earlier version wrote. Newest first; skip everything older than
the version you are coming from.

Per-release notes: [version_history.md](version_history.md) and the
[GitHub releases](https://github.com/rsilvabr/jxl-photo/releases).

---

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
