# Script commands, recommended settings and re-runs

The four scripts run on their own as well as through the wizard. This
page has the everyday commands, the settings I recommend, what a second
run redoes, and how the colour profile and the encode record are kept.
Every option of a script is in its own page: the encoder
([README_jxl_tiff_encoder.md](README_jxl_tiff_encoder.md)), the decoder
([README_jxl_tiff_decoder.md](README_jxl_tiff_decoder.md)), the JPEG
transcoder ([README_jxl_jpeg_transcoder.md](README_jxl_jpeg_transcoder.md))
and the recompressor ([README_jxl_recompressor.md](README_jxl_recompressor.md)).

## Typical workflow

```
Capture One
    ↓ Export 16-bit TIFF (sRGB, AdobeRGB, ProPhoto RGB)
jxl_tiff_encoder.py      TIFF → JXL      the master: 16-bit, lossless or near-lossless
    ↓                                    (~31 MB at d=0.1, ~173 MB lossless, 45 MP)
    ├─ jxl_recompressor.py    JXL → JXL       lighter 16-bit copies: sRGB, resized, a higher distance
    ├─ jxl_jpeg_transcoder.py JXL → JPEG/PNG  for sharing or print: colour conversion, resize, sharpening
    └─ jxl_tiff_decoder.py    JXL → TIFF      the TIFF back, in its original profile, to edit again
```

**The encoder only writes masters — by design.** It has no colour conversion, resize or sharpening: a master must be the TIFF, same pixels (up to the distance), same profile, same metadata, so that it can bring the TIFF back and prove it is safe to delete. A setting left on by mistake should never turn an archive into resized, sharpened files that look like masters. Resize, sharpening and colour conversion live where copies are made, from the master: the recompressor (lighter JXLs) and the transcoder (JPEG/PNG). Copies never delete anything and never pass for a master. [Why](README_jxl_tiff_encoder.md#why-the-encoder-never-converts-colour-or-resizes-design-decision).

## TIFF → JXL

```powershell
# Single file
py jxl_tiff_encoder.py "photo.tif"

# Folder (Capture One _EXPORT workflow)
py jxl_tiff_encoder.py "F:\Photos\2024" --mode 7

# With settings
py jxl_tiff_encoder.py "photo.tif" --mode 0 --workers 8

# Multi-page TIFF: also export embedded thumbnails
py jxl_tiff_encoder.py "F:\Photos\2024" --mode 2 --multipage-mode split --thumbnail-mode include
```

## JXL → TIFF

```powershell
# Single file — auto mode (Roundtrip if has ICC, Basic if not)
py jxl_tiff_decoder.py "photo.jxl"

# Force Matrix mode for color space conversion
py jxl_tiff_decoder.py "photo.jxl" --matrix --target-icc "C:\icc\sRGB.icc"

# Folder
py jxl_tiff_decoder.py "F:\Photos\2024" --mode 7

# Reconstruct multi-page TIFF and drop thumbnail pages
py jxl_tiff_decoder.py "F:\Photos\2024" --mode 2 --thumbnail-handling ignore
```

## JPEG ↔ JXL / JXL → PNG

```powershell
# JPEG → JXL (lossless transcoding)
py jxl_jpeg_transcoder.py "F:\Photos\2024"

# JXL → JPEG (auto: lossless recovery if jbrd present, else lossy)
py jxl_jpeg_transcoder.py "F:\Photos\2024" --mode 8

# JXL → PNG 16-bit (archival)
py jxl_jpeg_transcoder.py "F:\Photos\2024" --format png

# JXL → sRGB JPEG (ICC conversion via ImageMagick)
py jxl_jpeg_transcoder.py "F:\Photos\2024" --to-srgb --quality 95
```

## JXL → JXL (recompress an archive smaller)

```powershell
# Shrink a near-lossless archive to "visually lossless"
py jxl_recompressor.py "F:\Photos\Archive" --mode 1 --distance 1.0

# Simulate first — nothing written, copied or deleted
py jxl_recompressor.py "F:\Photos\Archive" --mode 1 --distance 1.0 --dry-run

# Replace the archive in place, deleting nothing until verified
py jxl_recompressor.py "F:\Photos\Archive" --mode 8 --distance 1.0 --verify-roundtrip

# Light 16-bit sRGB derivatives of the Capture One masters, profile swapped in the name
#   _EXPORT/16B_JXL/_DSC0013_ProPhoto-g22_v1.jxl -> _EXPORT/16B_JXL_sRGB/_DSC0013_sRGB_v1.jxl
py jxl_recompressor.py "F:\Photos\2025" --mode 7 --export-subfolder 16B_JXL --export-jxl-folder 16B_JXL_sRGB --output-icc sRGB --distance 1.0 --sync --rename-from ProPhoto-g22 --rename-to sRGB
```

A derivative is built from the master JXL, not the TIFF — measured on a real ProPhoto export, the extra generation costs 0.017 dB against a direct encode of the TIFF, at the same size. See [Colour-converted derivatives](README_jxl_recompressor.md#derivatives-colour-conversion-resize-and-sharpening).

Every option of every script: the per-script READMEs under [Documentation](../README.md#documentation).

## After conversion

1. Keep both TIFF and JXL — exclude the TIFF export folders from backups to save space (FreeFileSync folder filters make this easy).
2. Delete the TIFFs, keep only the JXLs — with this toolkit's `--delete-source`: each TIFF is deleted only after its JXL is written and verified (decoded and compared with the TIFF), and a TIFF re-exported during the run is kept. The older [delete-tiff-exports](https://github.com/rsilvabr/delete-tiff-exports) (archived) is a simple script that deletes the `_EXPORT\TIFF16` folders recursively and checks nothing: only for TIFFs whose JXLs you have already verified.

Then back up what you kept. [photo-backup](https://github.com/rsilvabr/photo-backup) copies, for every folder that has an export folder, the JXL master (instead of the TIFF), the RAW or camera JPEG it came from and your Capture One / NX Studio edits — and leaves out the outtakes and the copies this toolkit can make again from the master (sRGB, phone). It was written around one folder layout: the originals beside an export folder (`_EXPORT`, `Lightroom_Export`, … — any name containing "export"), the exports inside it. Run its dry-run first and read what it would copy; a different layout may need its settings, or the script, adapted.

## Recommended settings

### Masters (`jxl_tiff_encoder.py`)

```python
CJXL_DISTANCE = 0.1     # near-lossless, ~31 MB at 45 MP — the default
# CJXL_DISTANCE = 0.05  # finest lossy, ~42 MB — what I use, out of caution
CJXL_EFFORT = 7         # effort 9 gains nothing at these distances (docs/file_sizes.md)
```

Film scans and 8-bit TIFFs get lossless on their own.

### Copies, made from the masters

```powershell
# A light 16-bit sRGB copy (d=0.7 keeps the grain texture)
py jxl_recompressor.py "F:\Photos\2025" --mode 7 --export-subfolder 16B_JXL --export-jxl-folder sRGB --output-icc sRGB --distance 0.7 --sync --rename-from ProPhoto-g22 --rename-to sRGB

# JPEGs for the phone: sRGB, 2048 px, screen sharpening, quality 70
py jxl_jpeg_transcoder.py "F:\Photos\2025" --mode 7 --export-subfolder 16B_JXL --export-jpeg-folder JPG_mobile --force-convert --decode --format jpeg --icc-profile sRGB --resize-long 2048 --sharpen screen --quality 70 --sync --rename-from ProPhoto-g22 --rename-to sRGB
```

The same three steps as manifests and presets: [a whole library, end to end](README_manifest.md#7-a-whole-library-end-to-end-masters-a-16-bit-srgb-copy-phone-jpegs).

## What a re-run redoes

With sync (the wizard's and the presets' default), a run redoes only what changed:

| You change | Master | JXL copy | JPEG / PNG copy |
|---|---|---|---|
| The TIFF (re-exported) | redone | redone (its master is newer) | redone (its master is newer) |
| The distance, up or down | **kept** — run once with `--overwrite` | redone | — |
| The effort | **kept** | redone if higher, kept if lower | — |
| Colour, resize, sharpening (`none`/`screen`/`print`) | — | redone | redone (a resized or sharpened copy) |
| Sharpening sigma / gain / threshold | — | kept | kept |
| JPEG quality | — | — | redone (a resized or sharpened JPEG) |
| PNG bit depth | — | — | **kept** — run once with `--overwrite` |

A master is never redone because a setting changed: it is the archive, and a wrong value in a preset should not re-encode it. [Why each one](behavior_and_limitations.md#what-a-re-run-redoes).

## How the ICC profile is preserved

```
Plain cjxl/djxl:   TIFF (ProPhoto, Kodak TRC) → JXL (native primaries) → TIFF with a generic profile
With this toolkit: TIFF (ProPhoto, Kodak TRC) → JXL (+ the original ICC in XMP) → TIFF with the ORIGINAL profile
```

The encoder stores the original ICC profile, base64-encoded, in the JXL's XMP (a `jxlphoto-iccprofile` item in `dc:Relation`; `xmp:CreatorTool` up to v2.18.x), and the decoder's Roundtrip mode puts it back byte for byte. To check a round trip:

```powershell
exiftool -ProfileDescription -ProfileCopyright original.tif roundtrip.tif   # should match
```

XYB, ICC blobs and the table-curve profiles that need special handling: [docs/jxl_color_internals.md](jxl_color_internals.md).

## The encode record

Every JXL the toolkit writes also records how it was encoded, for example `gen=2 | cjxl d=0.05 e=7 | cjxl d=0.7 e=7`. Each `cjxl d= e=` entry is one encode, with its distance and effort, oldest first; `gen=` counts the lossy ones — how many times the photo has lost quality. A file re-encoded from another keeps the old entries and adds its own, so a copy made from a master still shows where it came from. The recompressor reads the record to decide what is worth doing: it asks before an encode that would only lose quality (the same or a finer distance than the file already has) and before a third lossy generation.

The record lives in the file's XMP processing history (`xmpMM:History`), out of the way: your caption stays alone in the photo's Title. To see it in Windows Explorer as well, `--encode-tag comment` copies it into Comments (or `title`, after the caption). Every field the toolkit writes: [docs/metadata_written.md](metadata_written.md#the-encode-record).
