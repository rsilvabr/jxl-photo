# Behavior, defaults and known limitations

The README keeps a one-line summary of each of these; the detail is here.

How the tools behave by default, where other software does not follow along, and the limits worth knowing before a large batch.

## Multi-page TIFFs: every page is converted by default

`--multipage-mode` defaults to **`split`** — one JXL per page (`photo.jxl`, `photo_page1.jxl`, ...), rejoined into a multi-page TIFF on decode. A TIFF with a single real page produces exactly `photo.jxl`, so for ordinary photos this is indistinguishable from the old default.

Why every page: plenty of TIFFs are multi-page without looking like it — Capture One and many scanners append an embedded preview, and film scanners add an IR/mask page. Keeping only page 0 (`ignore`, the default of early versions) silently threw those pages away, and a run that deleted its sources made the loss permanent.

Embedded **thumbnail/preview** pages are kept by default since v2.17.0 (`--thumbnail-mode auto`), as `photo_page1_thumbnail.jxl`, except the tiny preview Capture One adds to every export (the programs left out are listed in `THUMBNAIL_DROP_SOFTWARE`). Scanner software can need that page: SilverFast shows "Loading..." instead of the picture for a scan without it. `--thumbnail-mode scans` keeps it only for film scans (a scanner program in `SCANNER_SOFTWARE`, an IR page, or no Software tag and no camera EXIF); `exclude` drops every thumbnail, as before v2.17.0. An archive written without the thumbnails keeps that layout on a sync run; run once with `--overwrite` to add them ([details](README_jxl_tiff_encoder.md#thumbnail-pages---thumbnail-mode)).

```powershell
py jxl_tiff_encoder.py "F:\Photos"                             # split, thumbnails kept (not Capture One's)
py jxl_tiff_encoder.py "F:\Photos" --thumbnail-mode exclude    # drop every preview page
py jxl_tiff_encoder.py "F:\Photos" --multipage-mode ignore     # old behavior: page 0 only
```

In the wizard the review screen shows the multi-page policy on its own line, and `[a]` (all options) changes it. If you do choose a page-dropping policy, the encoder reports the totals in the run summary — and **a run that deletes its sources refuses to delete any TIFF whose pages were dropped**, so you cannot lose them by accident.

## The decoder never overwrites an original TIFF

The encoder's default mode 0 leaves `photo.tif` and `photo.jxl` side by side, and the JXL is newer. A sync decode into that same folder used to overwrite the **original master** with the decode. The decoder now only overwrites TIFFs it wrote itself (they carry its `jxlphoto-src` marker); any other TIFF is **refused** — not decoded over, and its JXL never deleted — and listed at the end of the run. Decode into another folder (`--mode 1`/`3`) or pass `--overwrite` if replacing it is really intended. Details: [decoder README](README_jxl_tiff_decoder.md#original-tiff-masters-are-never-overwritten).

The same goes for a decode you **edited** after decoding it (Photoshop keeps the marker): since v2.8.1 each decode records its pixels, and one whose pixels changed is refused like a master. See [A decode you edited afterwards is not decoded over](README_jxl_tiff_decoder.md#a-decode-you-edited-afterwards-is-not-decoded-over).

## Lossy is the default: `--distance 0.1`

The default is **near-lossless, not lossless**. At `d=0.1` a 45 MP file drops to roughly a tenth of the TIFF size, and a difference blend in Photoshop *will* show small deviations — that is the compression working as configured, not a bug. For a bit-exact archive use `--distance 0` (still ~40% smaller than an uncompressed TIFF). Note that `--mode` (0–8) only decides *where* output files go; quality is `--distance` alone.

### The distance floor depends on your cjxl: 0.05 on libjxl 0.12, 0.01 on 0.11

**cjxl 0.12 clamps every lossy distance at or below 0.05 to the same value.** On two real 16-bit photos, `--distance 0.005` through `0.05` produced a **byte-identical** file. The menu and the CLI accept anything from 0 to 25, so setting `0.02` looks like it buys you something, but on 0.12 it does not: either stay at `0.05`, or go to `--distance 0` for true lossless. The floor was added in libjxl 0.12 ([PR #4238](https://github.com/libjxl/libjxl/pull/4238)): below it the DC coefficients leave the `int16` range, so the file no longer conforms to Level 5 of the JPEG XL spec. libjxl itself still decodes such files, but a decoder built for Level 5 may not.

**cjxl 0.11.2 does not clamp at 0.05.** Its floor is 0.01, and the distances between 0.01 and 0.05 are real steps. Measured on the same photos (effort 7):

| distance | cjxl 0.11.2 | cjxl 0.12.0 |
|---|---|---|
| 0.005 | same file as 0.01 | same file as 0.05 |
| 0.01 | 30.5 MB, 60.4 dB PSNR | same file as 0.05 |
| 0.02 | 24.4 MB, 56.2 dB | same file as 0.05 |
| 0.05 | 17.4 MB, 51.7 dB | 17.7 MB, 51.9 dB |
| 0.1 | 13.2 MB, 48.9 dB | 13.4 MB, 49.0 dB |

At 0.05 and above both versions write nearly the same file (0.12 is ~1–2% bigger and ~0.2 dB better). Since v2.2.0 the scripts read the installed cjxl version: the dead-zone warning and the recompressor's "same distance" check use 0.05 on libjxl 0.12 and later, and 0.01 before it.

Measured ratios on real 16-bit ProPhoto photos already stored as Deflate TIFF (output ÷ source), so you can see where the curve actually bends:

| distance | 0 | 0.05 | 0.1 | 0.2 | 0.5 | 1.0 |
|---|---|---|---|---|---|---|
| camera files | 61–70% | 14–18% | 10–14% | 7–10% | 4–7% | 2–4% |

The spread across photos at one distance is 1.1×–2.1×, so treat any single number as an order of magnitude, not a promise.

### 8-bit sources: lossless can be *smaller* than lossy

Counter-intuitive but reproducible: at 8 bits, `--distance 0` produces a **smaller** file than `--distance 0.05` (43 against 47 MB, the median of 20 photos at 45 MP), and `d=0.1` is only about a quarter smaller than lossless. JXL's lossless mode is very efficient at 8 bits, while VarDCT at a very low distance carries overhead it cannot amortise. The encoder therefore archives an 8-bit TIFF lossless whenever the run's distance is 0.1 or less ([details](README_jxl_tiff_encoder.md#8-bit-tiffs-are-archived-lossless-at-low-distances-eight_bit_distance)).

## Renamed folders and the overwrite guard

Every output records the full path of the file that made it (`jxlphoto-src`) and a hash of its bytes (`jxlphoto-srcsum`). Before overwriting an existing output, every run checks that record: one made from another path is refused. That protects the only copy of a photo when an earlier run deleted its source and a different photo with the same name now lands on the same output.

A renamed or moved folder changes the path. Renaming alone breaks nothing: the next sync sees nothing newer and skips. But re-exporting a photo after renaming its folder (or a full `--overwrite` rebuild) is refused, with a message that points here. Renaming inside Lightroom's Folders panel, in Capture One or in Explorer is the same thing on disk.

**Which setting to use:**

- **You sync, and you rename or move folders anywhere on the path to the photo** (an event folder, a year folder, a drive letter) — and you never delete sources with the toolkit: turn the guard off. CLI `--no-overwrite-guard`, `OVERWRITE_GUARD = False` at the top of the script, or "Overwrite guard: off" in the wizard (saved in the preset). Each overwrite it would have refused is logged.
- **You keep your folder names stable** — typical with Lightroom, whose catalog tracks folders by path: keep the default.
- **You keep the guard on but renamed a folder:** before re-editing any photo in it, run the encoder once with `--restamp-moved` on that folder (menu 5 of `jxl_photo.py`: dry run first). It rewrites the path record of every master whose TIFF is unchanged, in seconds, without re-encoding (file dates kept, so derivative runs do not redo them). Masters whose TIFF was already edited are listed: delete those old JXLs by hand (the TIFF is kept) and sync again.
- **A scheduled sync can do this on every run:** `--restamp-then-sync` restamps the unchanged masters first and then syncs in the same pass (opt-in; it reads the record of every existing master, and it is refused with any deletion).

**Never with deletion:** `--no-overwrite-guard` together with `--delete-source` is refused (exit 2).

**When the record cannot be read** (exiftool's planning read failed or stalled — a very busy disk, a file locked by another program): the existing output is refused too, with "its provenance markers could not be read", in every script. Re-run once the disk is quiet. With the guard off it is overwritten with a warning, like a record naming another path. (Until this fix such an output was taken for an old archive without a record, and overwritten.)

**Still protected with the guard off:** a lossless JPEG archive (`jbrd`) of a different JPEG, an original TIFF master (the decoder never overwrites a TIFF it did not write) or an edited decode, and a file a derivative run did not write.

**A lossy JXL → JPEG/PNG run with `--sync`** refuses to replace a JPEG or PNG that carries no `jxlphoto-src` marker: every JPEG/PNG this decode writes carries one, so a marker-less file is not a file the tool converted (typically the camera's own JPEG beside the master). `--overwrite` replaces it; `--no-overwrite-guard` does not lift the refusal.

**Two runs writing one folder at once:** each output is replaced only if it is still what the run found when it planned; a file another run wrote there meanwhile is left alone, this run's source is kept and the run ends with exit code 1 (encoder, JPEG transcoder, recompressor).

**Before deleting a source, every script flushes the output to the disk.** An output it cannot flush keeps its source with a `KEEP` line — including a read-only file, e.g. an archive you protected, under `--delete-skipped`.

## A JPEG or 8-bit PNG made from a JXL never deletes it

A JXL decoded to a JPEG, or to an 8-bit PNG, is not a copy of the master: it has fewer bits and, for a JPEG, a lossy encode. Like the decoder's 8-bit TIFF and the recompressor's derivatives, it never deletes the JXL: the transcoder refuses `--force-convert --decode` to JPEG/8-bit PNG with `--delete-source` (exit 2), auto mode keeps each such JXL with a `KEEP` line, and the wizard refuses the combination before asking for the HHMM time (for a lossy JPEG, already when you pick `[D]`). The bit-exact recovery of a lossless JPEG archive and a 16-bit PNG still delete, behind their checks.

## What a JXL cannot hold: layers, odd bit depths, IPTC

- **Layered TIFFs.** A TIFF with Photoshop layers (tag 37724) or a private tag too large to carry (Affinity Photo's document) is archived flattened. `--delete-source` and `--delete-skipped` **keep** such a TIFF (`KEEP source`); there is no flag to release it. A tag 34377 on its own (paths, resources) does not block the delete.
- **10/12-bit (and 1/2/4-bit) TIFFs are refused.** The values would be archived unscaled, 16 times darker than the original. The file gets an ERROR and stays. A master an earlier version wrote from such a TIFF holds those unscaled values: `--delete-skipped` never takes it as the TIFF's archive (`KEEP source`). 8- and 16-bit TIFFs are unaffected.
- **IPTC (IIM) is not carried into a JXL.** Keywords, captions and authors that exist only there (older Photoshop, Photo Mechanic, agencies) are not in the master; EXIF and XMP are. The lossless JPEG archive (`jbrd`) keeps everything.

## A JXL made from a rotated JPEG

The JXL's codestream carries the JPEG's orientation, so `djxl` already turns the pixels. A decode to PNG or TIFF, and a recompression, therefore set the copied EXIF Orientation to 1 (and remove XMP `tiff:Orientation`); the picture is shown once, the right way up. **A decode to JPEG gives the original back** (v2.26.0): the pixels in the order the camera stored them and the original EXIF Orientation — an exact reordering before the one JPEG encode, so nothing is lost by it. `--jpeg-orientation upright` (or `JPEG_ORIENTATION`) keeps the turned pixels with Orientation 1, which is also what a PC without ImageMagick writes. A recompressed copy records the JPEG's orientation as `jxlphoto-orientation:<n>`, since its own codestream no longer has it. The orientation is read with `jxlinfo` (it ships with libjxl, next to `djxl`), or, without it, from the JXL's header directly (v2.26.1). The bit-exact JPEG recovery returns the original JPEG, Orientation included, and a TIFF → JXL → TIFF chain keeps the TIFF's own Orientation.

## A JPEG with a malformed EXIF, converted lossy to JXL

`--force-convert --from-jpeg` (distance above 0) lets `cjxl` copy the JPEG's EXIF into the JXL, and the transcoder then adds its markers with exiftool. A JPEG whose EXIF is malformed — a broken thumbnail IFD, maker notes exiftool cannot parse — makes exiftool refuse every write to that JXL. The file gets **one retry**: `cjxl` again without the EXIF it would copy, and the EXIF written fresh from the JPEG by exiftool (log line `metadata copied again without the EXIF the codec wrote`). That is enough for a broken IFD1 (an HDR program's output, for one); the EXIF and the markers arrive, and an output whose EXIF did not arrive at all is an error (`METADATA PARTIAL`). **A rotated JPEG (EXIF Orientation 2–8) is not retried**: `cjxl` keeps the orientation in the codestream, and the second encode, without the EXIF, would lose it and show the picture sideways in a JXL viewer. It is an error with the same way out (`--force-transcode`); so is a first encode whose orientation cannot be read. Never `-m`: the toolkit does not tell exiftool to ignore minor errors on its own.

**Unparseable maker notes are still an error** (a Nokia N73 JPEG, for one: exiftool says `[minor] Maker notes could not be parsed`; an HTC Desire 626 one: `[minor] Bad MakerNotes offset`): copying them recreates the block, and the next write fails again. The message names the cause and the way out. The lossless transcode (`--force-transcode`, no `--distance`) writes no metadata and keeps the JPEG's EXIF as it is, bit-exact. Nothing is deleted in either case.

## A JPEG with an HDR gain map, converted lossy to JXL

Phones write HDR photos as a standard JPEG followed by a second, smaller image, the gain map, which an HDR screen uses to show the highlights brighter (Android's Ultra HDR, ISO 21496-1, Apple's HDR gain map). `cjxl` encodes the first image of the file, so a pixel re-encode (`--force-convert --from-jpeg` at a distance above 0) gives a JXL with the standard picture only. The run says so per file (`HDR gain map not carried`), and **`--delete-source` and `--delete-skipped` keep such a JPEG** (`KEEP (the JPEG carries an HDR gain map …)`): it is the only copy of the gain map. There is no flag to release it.

The lossless transcode (the default for JPEGs, `--force-transcode`) keeps the whole file — the JPEG comes back bit for bit, gain map included — and deletes as usual. A camera's MPO (Nikon, Panasonic, Sony: preview images after the picture) has no gain map and is not held back. A JPEG or PNG decoded from such an archive is the standard picture.

The JXL format has a place for a gain map (the `jhgm` box), but `cjxl` and `djxl` have no option to write or apply one, so the toolkit does not carry it into a lossy JXL.

## A float, deeper-than-16-bit or animated JXL is not deleted by a decode

A decode to TIFF (16-bit integers, first frame) or to a 16-bit PNG is not a full copy of a JXL that holds floating-point or deeper-than-16-bit samples, and the TIFF decode is not one of an animation either (the PNG keeps the frames). `--delete-source` keeps such a JXL (`KEPT`). The toolkit's own masters are never any of these; this concerns JXLs from other programs. It relies on `jxlinfo`.

## A move out of staging that fails is an error

If the finished output cannot be moved from the staging folder to its destination (the old file is open in a viewer, say), the file counts as an error: the summary says so and the exit code is 1. The source is kept, and the next `--sync` run does the file again.

## Modes 6/7: which `_EXPORT` counts, and what is left out

The anchor is the first folder matching the marker at or below the input folder; only when there is none below it, the closest one above it. A library kept under a folder whose name also matches (`Lightroom_Export`, `C1_Export`) is not anchored there — unless the run is pointed at that very folder, which then counts as the marker: point it at the year or event folder inside. Below the marker, the decoder and the recompressor leave out the other scripts' output folders (by the names in their settings), and these two and the transcoder's decode leave out every derivative (by its `jxlphoto-derived` marker, wherever it sits), unless mode 7 asks for that folder by name.

## The scripts' settings: in the file, or in the overlay

Each script's settings are the variables at the top of the file. `jxl_photo.py`'s Settings → script settings (menu 9 → 2) changes them in a JSON overlay instead (`JXLPHOTO_SETTINGS`, else `.jxl_script_settings.json` next to the scripts, else in the user profile), which each script reads and checks when it starts; every run's log names the file and what it changed (and says so when `JXLPHOTO_SETTINGS` names a file that does not exist: nothing changes then). The safety settings — deletion, confirmations, the overwrite guard and the provenance check, the verification thresholds, `OVERWRITE`, `STRIP_METADATA`, the encoder's copy of the original profile (`EMBED_ICC_IN_JXL`) — are never taken from it. [Details](README_jxl_tools.md#script-settings-menu-9-option-2)

## Default re-run behavior differs per script

The TIFF encoder/decoder default to **smart sync** (reconvert when the source is newer than the existing output — there is no plain "skip existing" CLI mode), while the JPEG transcoder **skips existing outputs** by default. Use `--overwrite` (always) or `--sync` (source newer) to control it explicitly. See each script's README for details.

## What a re-run redoes

| You change | Master (encoder) | JXL copy (recompressor) | JPEG / PNG copy (transcoder) |
|---|---|---|---|
| The TIFF (re-exported) | redone | redone (its master is newer) | redone (its master is newer) |
| The distance, up or down | **kept** — run once with `--overwrite` | redone | — |
| The effort | **kept** | redone if higher, kept if lower | — |
| Colour, resize, sharpening (`none`/`screen`/`print`) | — | redone | redone (a resized or sharpened copy) |
| Sharpening sigma / gain / threshold | — | kept | kept |
| JPEG quality | — | — | redone (a resized or sharpened JPEG) |
| PNG bit depth | — | — | **kept** — run once with `--overwrite` |

This is with sync, the wizard's and the presets' default (`--sync`; the
encoder's and the decoder's own default). Why each column behaves as it does:

- **A master follows its TIFF, not the settings.** The encoder's sync compares
  dates only: a master is encoded again when its TIFF is newer. A new distance
  or effort applies to new TIFFs, and to the existing ones only with
  `--overwrite` (a rebuild). That is deliberate: the master is the archive, a
  rebuild of a large library takes hours, and a wrong value in a preset
  (`d=3` tried once and saved) must not quietly re-encode every master — once
  the TIFFs are gone, there is no way back. Reading every master's record on
  each sync would also slow every run down.
- **A copy follows its master and its recipe.** Each copy records the recipe
  it was made with, e.g. `jxlphoto-derived:sRGB@long2048+screen/d0.7e7`. A
  newer master, or a recipe that differs from the one asked for, makes it
  again. In a JXL copy the recipe includes the distance and the effort
  (`REDERIVE_ON_ENCODE_CHANGE`); at the same distance only a higher effort
  counts (`REDERIVE_ON_LOWER_EFFORT` to change that), because a lower one
  would spend a run on a larger file. Copies made before v2.7.0 carry no
  distance in their recipe and are kept.
- **A JPEG or PNG copy has a recipe only when it is resized or sharpened**
  (`jxlphoto-derived:sRGB@long2048+screen/q70`); a JPEG also records its
  quality since v2.25.0. A copy that is only colour-converted carries no
  recipe, so for it only a newer master counts. JPEGs written before v2.25.0
  carry no quality and are kept when the quality changes.
- **What the recipe does not record is not compared:** the sharpening's
  sigma, gain and threshold (only `screen`/`print` is), and a PNG copy's bit
  depth. After changing one of those, run once with `--overwrite`.
- After a rebuild of the masters, every master is newer than its copies, so
  the next sync of the copies redoes them all.

## Film scanners: IR channel / Digital ICE

Since v2.17.0 a **SilverFast** scan (image, thumbnail, IR page) archived with the defaults and decoded back opens in SilverFast with its IR channel and dust removal (iSRD) recognised and its browser icon — checked on a slide and a negative, also after recompressing the master. It takes the IR page's role (`SubfileType` 4), the thumbnail page (`--thumbnail-mode auto`), SilverFast's own XMP settings (now copied as one block) and its private TIFF tags (now kept); before v2.17.0 the XMP settings and the thumbnail were lost and SilverFast saw no IR channel. Masters archived before v2.17.0 do not carry them: re-archive those scans from their TIFFs with `--overwrite`. Other scanner programs (VueScan, Epson Scan, ...) were not tested; **test one file before converting a whole archive.** [Details](README_jxl_tiff_encoder.md#film-scans-and-their-scanner-software)

Film scans are also archived **lossless** by default since v2.17.0, whatever `--distance` the rest of the run uses (`SCAN_DISTANCE = 0`, `--scan-distance`), and the recompressor never re-encodes them lossy unless asked (`--on-scan`). A raw negative's later processing magnifies even a small loss, and a scan can only be redone by scanning again. `--scan-distance same` treats them like any TIFF; `--scan-scope raw` keeps only raw scans lossless. [Details](README_jxl_tiff_encoder.md#film-scans-are-archived-lossless-scan_distance)

8-bit TIFFs that are not scans are archived **lossless** when the run's distance is 0.1 or less (`EIGHT_BIT_DISTANCE = "auto"`, `EIGHT_BIT_LOSSLESS_UP_TO`): lossless is small for them, and `d=0.05` would come out larger. `--eight-bit-distance same` treats them like any TIFF, a number gives them their own distance. [Details](README_jxl_tiff_encoder.md#8-bit-tiffs-are-archived-lossless-at-low-distances-eight_bit_distance)

## Scanner ICC profiles and lossy encoding

Scanner ICC profiles (e.g. SilverFast `SFprofT`) can cause `cjxl` to produce very dark images in lossy mode. The encoder works around this by not embedding the ICC in the intermediate PNG and restoring it into the reconstructed TIFF (see `--icc-png-strategy`). The JXL file may therefore display with shifted colors in some viewers, but the TIFF round-trip is accurate. For scanner workflows, treat **JXL as the backup container and the reconstructed TIFF as the final image**.

## Known limits, by design

- **The recompressor does not prove the origin of an output made from a JXL without a marker** (a third party's, or one older than the markers). With `--delete-source` and then `--sync` or `--overwrite` into the same destination, in a folder that gathers several origins (modes 2, 0 with a destination, 4–7), a file of the same name can replace the only copy. Masters of the encoder (with `jxlphoto-src`/`srcsum`) are refused. Workaround: do not combine `--delete-source` with `--on-unknown copy|convert` into a shared destination, and check with `--dry-run` first.
- **The decoder's `--overwrite` replaces any TIFF in the destination**, an original master included; with `--sync` both are refused. With `OVERWRITE_GUARD` off (the SYNC PHOTOS preset) the master of a deleted photo can be replaced by another photo with the same name.
- **After a kill, `<uuid>_*.jxl` leftovers stay in the staging folder**; only `--clean-staging` removes them.

## Viewer quirks (not data loss)

| Viewer | Behavior | Why |
|--------|----------|-----|
| **Windows Explorer** | Thumbnails decode the whole image (about 1.5 s for a 24 MP file, whatever the distance) and are **not color-managed** — ProPhoto/Adobe RGB images look washed out. Once made they come from Windows' thumbnail cache. **Fix:** [jxl-thumb](https://github.com/rsilvabr/jxl-thumb) replaces the thumbnail handler (and with it the preview pane): ~40× faster thumbnails on 25–45 MP masters (preview pane ~14×), converted to sRGB. **Lossless** JXLs have no small preview inside, so even jxl-thumb decodes the whole file (~1.8 s per 45 MP photo on a hard disk): the encoder therefore stores a 768 px thumbnail inside lossless outputs (inside every output when jxl-thumb is installed), which jxl-thumb 0.2.0+ shows in ~2 ms ([Explorer thumbnails](README_jxl_tiff_encoder.md#explorer-thumbnails-explorer_thumbnail---explorer-thumbnail); menu 6 adds it to existing files) | Limitation of Microsoft's JXL WIC codec |
| **IrfanView** | Shows a JXL's EXIF only from a **plain** `Exif` box placed **before the XMP and the image data**; otherwise it shows just the file name, or nothing. jxl-photo writes that layout by default in every script. Files from older versions: JPEG → JXL before v2.19.2 (compressed EXIF) → menu 7 / `--repair-jbrd`; TIFF → JXL masters of v2.17.0–v2.19.0 (EXIF after the XMP) → menu 6 | cjxl's own default Brotli-compresses the EXIF/XMP it copies from a JPEG (`brob` boxes), which IrfanView cannot read. Compressing saves little: a median 2.3 KB per photo, 0.19 % over 23 varied JPEGs (0.03 %–0.6 % on camera JPEGs of several MB). Want it anyway? `COMPRESS_METADATA_BOXES = True` / `--compress-metadata` in the transcoder ([details](README_jxl_jpeg_transcoder.md#irfanview-and-the-exif)) |
| **IrfanView / XnView MP** | Wide-gamut JXL may look slightly muted on a calibrated monitor | Viewer rendering limitation — the file keeps the full gamut; decode back to TIFF to confirm |
| **XnView MP** | Shows `Color Profile: sRGB` for lossy JXL regardless of the real space | Lossy JXL stores compact numeric primaries, not an ICC blob; XnView falls back to an "sRGB" label |

For reliable EXIF and color, use **XnView MP** or **digiKam**. If an image looks *heavily* desaturated, that **is** a real bug — please report it.

## Matrix decode mode is 8-bit internally

The decoder's Matrix mode (`--matrix`, for color-space conversion via LittleCMS) quantizes pixels to 8-bit for the transform and scales the result back to 16-bit. Effective precision is 8 bits in that mode only — use **Roundtrip mode** (the default) for full 16-bit fidelity.

## eciRGB v2, scanner profiles and other table-curve ICC profiles

Most profiles have a native JPEG XL form (a pure gamma, sRGB, Rec.2020, DCI-P3…), and those round-trip correctly — ProPhoto, Elle's LargeRGB g2.2, Adobe RGB and Wide Gamut included, wide gamut intact. Profiles whose tone curve is a **table** (eciRGB v2's L* curve, scanner LUT profiles, ROMM RGB with its linear toe) have none: in lossy mode cjxl then stores the whole ICC, and djxl decodes such a file to **linear sRGB**, not to the original space.

The encoder's default `cautious` ICC strategy detects most of these and encodes them "skip" (verified correct round trips for eciRGB v2 and Epson scanner profiles; the JXL itself then shows wrong colours in viewers, because it is tagged sRGB). A table-curve profile that passes the cautious check is embedded as an ICC blob — and since bug #437 (2026-09-26) that case is handled everywhere: the decoder detects it from djxl's own `--icc_out`/`--orig_icc_out` output and decodes to float, CONVERTING to the original profile (51.9 dB measured, against 15.4 dB for the old paste-the-ICC behaviour), the derivative paths of the recompressor/transcoder do the same, and the cautious test now refuses "embed" for a lossy ICC blob whatever the brightness says. Decoding such a file needs ImageMagick on PATH; without it the decode fails closed instead of writing a wrong-colour TIFF. Measurements and the exact mechanism: [Lossy JXL with an ICC blob](jxl_color_internals.md#lossy-jxl-with-an-icc-blob-what-djxl-returns-measured-2026-09-25).
