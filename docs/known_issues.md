# Known issues by version

The bugs that changed **files the toolkit wrote**, not the ones that only
stopped a run, refused a file or printed something wrong. For each one: which
versions wrote such files, how to tell, and what to do. Everything else that
was fixed is in [bug_tracking_since_v1.0.md](bug_tracking_since_v1.0.md).

None of these lost pixels of a master made from a TIFF. They are metadata, a
few derived copies, and some lossless JPEG archives (those of v2.0.0–v2.0.3,
and those made with libjxl 0.13 before v2.30.1-rc2).

## Find the files a version wrote

Since v2.13.0 every file the toolkit writes records the version that wrote it
in its XMP history (`xmpMM:History`, the *SoftwareAgent* field: `jxl-photo
2.24.0 (...)`, since v2.30.0 also `with libjxl 0.12.0`). A file touched by
several versions lists each of them. To list the files a range of versions
wrote, with [ExifTool](https://exiftool.org/) (in `cmd`, from any folder):

```
exiftool -r -if "$HistorySoftwareAgent =~ /jxl-photo 2\.1[7-9]\./" -p "$Directory/$FileName" "D:\Photos"
```

The part between the slashes is a regular expression on the version:
`2\.1[7-9]\.` is 2.17.x to 2.19.x, `2\.(2[0-9]|30)\.` is 2.20.x to 2.30.x,
`2\.30\.1-rc` is the 2.30.1 test releases. Add `-ext jxl` (or `-ext jpg`) to
look at one kind of file.

Some files carry no history, and this command cannot find them: everything
written before v2.13.0, a **lossless JPEG archive** (its JXL must give back
the original JPEG byte for byte, so the toolkit never writes into it) and a
verbatim copy of one. The entries below say how to find those.

## The list, newest first

### A master that failed its check before a delete kept its name — before v2.31.0-rc5 (#701)

**What:** with `--delete-source`, a JXL that failed its round-trip check in
the delete gate kept its TIFF (right) but also its final name, and every
`--sync` after it took it for up to date. Rare: a check fails only on a
broken encode.
**Which files:** masters made from TIFFs in deleting runs.
**How to tell:** the run's log has `KEEP source (round-trip verification
FAILED`; the TIFF is still beside its JXL.
**What to do:** encode those TIFFs again with `--overwrite`.

### The Explorer thumbnail of an RGB+IR scan — before v2.31.0-rc5 (#700)

**What:** the thumbnail of a 4-sample film scan (RGB + infrared) was
flattened on white through the IR channel, taken as alpha: paler colours.
Only the thumbnail; the image is right.
**Which files:** JXLs of RGB+IR scans with an Explorer thumbnail.
**What to do:** `--refresh-explorer-thumbnails` on their folder (encoder or
recompressor) makes the thumbnails again.

### A TIFF re-exported during its encode — before v2.31.0-rc5 (#699)

**What:** a TIFF rewritten while its page was being encoded, in a run that
did not delete: the master held the old version with a newer date, so every
`--sync` after it said "up to date". Rare: it takes an export landing during
that very file's encode.
**Which files:** masters made from TIFFs.
**How to tell:** a master whose `jxlphoto-srcsum` is not its TIFF's (a sync
with `--provenance content`, or the decoder's restore compared with the TIFF).
**What to do:** encode the TIFF again with `--overwrite`.

### Private TIFF tags from 65000 up — TIFFs decoded before v2.31.0-rc5 (#698)

**What:** the decoder wrote a TIFF's private tags back from the master's
`jxlphoto-tifftag` tokens, but those from 65000 up (65000 is Adobe Camera
Raw's OwnerName for ExifTool) were moved or removed by the EXIF copy after
them. The master keeps them.
**Which files:** TIFFs restored from masters of TIFFs that had such tags.
**What to do:** decode them again from their masters if you need those tags.

### A caption with `|` — before v2.31.0-rc5 (#697)

**What:** the clean-up of the encode record rewrote a caption or comment
with a `|` in it although it held no record: `Tokyo|Kyoto` became
`Tokyo | Kyoto`, `A || B` became `A | B`.
**Which files:** masters and copies whose dc:Description or UserComment had
a `|`.
**What to do:** correct the caption by hand, or encode again from the TIFF.

### Page 0 of a multi-page TIFF took page 1's EXIF values — before v2.31.0-rc5 (#696)

**What:** for the EXIF tags several pages carry, page 0's master took the
last page's value: `PageNumber 1 2`, an `Artist` given only to page 1, a
SilverFast scan's `CompressedBitsPerPixel`; the TIFF decoded from it the
same. Camera TIFFs with a thumbnail page and two-page scans are concerned.
**Which files:** masters of page 0 of multi-page TIFFs, and TIFFs decoded
from them.
**What to do:** nothing for the image; encode again from the TIFF with
`--overwrite` if those tags matter to you.

### A resized copy of an RGB+IR scan — before v2.31.0-rc5 (#695)

**What:** the recompressor's derivative (resize, sharpening, colour
conversion) of a 4-sample film scan took the infrared as alpha: the resize
weighted the colour by it and the copy came out RGBA.
**Which files:** derivatives of RGB+IR scans.
**What to do:** make them again (the recompressor re-derives over its own
derivatives with `--overwrite`).

### A lossy master without its ICC in the codestream — before v2.31.0-rc5 (#693)

**What:** with `ICC_PNG_STRATEGY = cautious`, an ICC probe that could not
run (cjxl or djxl failed or timed out under load) was cached as "this
profile is unsafe": the lossy masters with that profile after it went out
without the ICC in the codestream (viewers show them in sRGB). The profile is
in their XMP and the decoder restores it: a TIFF restored from them is right.
**Which files:** lossy masters made from TIFFs with that strategy.
**What to do:** clear the ICC test cache (wizard menu 8, or
`--clear-icc-cache`) and, if the colours in viewers matter, encode those
masters again with `--overwrite`.

### A colour-converted 16-bit PNG carried its master's marks — before v2.31.0-rc5 (#691)

**What:** a 16-bit PNG the transcoder made from a JXL with `--to-srgb` or
`--icc-profile` carried the master's `jxlphoto-src`/`srcsum` marks, as a
faithful copy does. A later run decoding the same JXLs to 16-bit PNG with
`--delete-source --delete-skipped` found it ("SKIP (exists)") and deleted the
master. Since v2.31.0-rc5 such a PNG is a derivative (it never deletes, and
it carries `jxlphoto-derived` instead of those marks), and the delete gate
keeps a master whose PNG has another profile than the one the master carries
in its XMP — every master the TIFF encoder made. A JXL without it (one the
transcoder made from a JPEG or a PNG, or one from elsewhere) cannot be
judged that way. Rare: it takes a colour-converted 16-bit PNG and then a
deleting run over the same folder.
**Which files:** 16-bit PNGs made with `--to-srgb` or `--icc-profile`.
**How to tell:** the list of the 16-bit PNGs that carry a master's marks and
no derived mark, with their profile (one whose profile is not its master's is
a colour-converted one):

```
exiftool -r -ext png -if "$XMP-dc:Relation =~ /jxlphoto-src:/ and $XMP-dc:Relation !~ /jxlphoto-derived/ and $BitDepth == 16" -p "$Directory/$FileName | $ICC_Profile:ProfileDescription" "D:\Photos"
```

**What to do:** before a run with `--delete-source --delete-skipped` that
decodes JXLs to 16-bit PNG, delete those PNGs or make them again with
v2.31.0-rc5 or later (the same colour command with `--overwrite`).

### A temp folder with `%` in its path — before v2.31.0-rc3 (#687)

**What:** ExifTool reads a `%` in a file name as a format code. With a `%`
in the temp folder (`TEMP_DIR`, or Windows' TEMP when it is unset), the
encoder's copy of a TIFF's XMP block gave nothing: the master got only the
XMP fields ExifTool can write one by one, and the others (a SilverFast scan's
settings, for instance) were left out. Some other steps failed file by file.
Since v2.31.0-rc3 such a temp folder is refused at the start of every run.
**Which files:** masters made from TIFFs while the temp folder had a `%` in
its path.
**What to do:** nothing, unless your temp folder ever had a `%`; then encode
the masters made in that time again from their TIFFs, with `--overwrite`.

### A file held open by another program — before v2.31.0-rc3 (#686)

**What:** ExifTool answers a file it cannot open (another program holding it
without sharing for an instant: an antivirus, an indexer, a backup agent) with
an error, yet with exit status 0 when the same command wrote anything else.
When that hit the temporary file holding a TIFF's XMP block or its EXIF, the
master came out without them and the run said OK. Rare: it takes another
program holding that very file at that very moment.
**Which files:** masters made from TIFFs.
**How to tell:** the master shows no camera, lens or exposure data although
its TIFF has them, or lacks the TIFF's own XMP fields (rating, keywords, a
scanner's settings); its pixels and markers are there.
**What to do:** encode it again from its TIFF with `--overwrite`.

### EXIF values rounded — every version before v2.31.0 (#684)

**What:** the EXIF copied into an output went through ExifTool's printed
values: `MaxApertureValue` f/2.83 became f/2.8, a phone's `FocalLength`
6.62 mm became 6.6 mm, and the APEX shutter and aperture values were rounded
slightly. Pixels, dates, camera and lens names are not affected.
**Which files:** masters made from TIFFs, TIFFs decoded from them, and the
copies (JPEG, PNG, resized or converted JXL).
**What to do:** nothing is needed for viewing. For the exact values,
re-encode the masters from their TIFFs with v2.31.0 or newer and
`--overwrite`; a copy made from a master keeps what the master holds.

### The generated sRGB profile carried its creation time — before v2.31.0 (#685)

**What:** the sRGB profile embedded by the transcoder's `--to-srgb` and the
decoder's `--target-icc srgb` carried the moment it was made, so two otherwise
identical outputs differed in a few bytes. The colours are the same.
**What to do:** nothing.

### An alpha channel with nothing in it was dropped — v2.30.1-rc1 and older, with libjxl 0.13 (#670)

**What:** cjxl 0.13 drops an all-opaque alpha channel in a lossy encode; the
toolkit asks it to keep it since v2.30.1-rc2. A lossy master or copy of an
RGBA image (or of a scan whose 4th channel reached cjxl as alpha) came out
with 3 channels. No pixel of the picture changed.
**Which files:** lossy outputs of 4-channel images made with libjxl 0.13
(released 2026-11-12) by jxl-photo v2.30.1-rc1 or older.
**What to do:** re-encode those from the source with `--overwrite`.

### JPEG archives rebuilt only by djxl 0.13 — v2.30.1-rc1 and older, with libjxl 0.13 (#669)

**What:** with cjxl 0.13, the lossless archive of a 4:4:4 or grey JPEG was
marked for "LF smoothing": djxl 0.13 rebuilds the original JPEG bit for bit,
djxl 0.12 and 0.11 do not. Nothing was lost (the delete gate proved the
rebuild with the djxl installed).
**Which files:** lossless JPEG → JXL archives made with libjxl 0.13 by
v2.30.1-rc1 or older. They carry no history: they were made after
2026-11-12 (libjxl 0.13's release) by a jxl-photo older than v2.30.1-rc2
while libjxl 0.13 was installed. The proof for one archive: rebuild it with
djxl 0.12 (`djxl archive.jxl rebuilt.jpg`) and compare the result with the
original's MD5 in the folder's `checksums.md5`.
**What to do:** keep djxl 0.13 or newer, or archive those JPEGs again (the
original JPEG comes back with djxl 0.13).
**The same up to v2.31.0-rc4 (#694)** for an archive made while `cjxl
--version` did not answer in time (a busy PC): the run could not tell it had
cjxl 0.13 and left the flag out, without a word. Since rc5 the probe tries
again with more time and a run that still cannot tell says so. The same
proof (djxl 0.12 against `checksums.md5`) finds them.

### A grey copy kept its master's marks — v2.30.2-rc1 and older (#672)

**What:** a JPEG made from the grey page of a film scan (the infrared page)
kept the master's `jxlphoto-src`/`srcsum` and a broken XMP. It deletes
nothing; a later run refused to overwrite it.
**What to do:** delete such a JPEG and make it again.

### HDR gain-map XMP on files without a gain map; TIFF markers in JPEG/PNG — before v2.28.0 (#645, #646)

**What:** a lossy JXL, JPEG or PNG made from an Ultra HDR phone JPEG kept the
XMP that announces a gain map it does not hold; and every JPEG/PNG decoded
from a master carried the master's `jxlphoto-tifftag` markers (the IPTC and
Photoshop blocks in hex, up to 8 KB each).
**What to do:** nothing is needed; to clean them, make the copies again.

### IPTC-only fields not in the master — before v2.27.0 (#640)

**What:** a TIFF whose capture date, caption or keywords lived only in the
IPTC block (some SilverFast scans) gave a master without them, and a decoded
TIFF had no IPTC.
**What to do:** re-encode those masters from their TIFFs with `--overwrite`.

### InteropIndex lost — before v2.26.1 (#637)

**What:** PNGs, and JPEGs that went through ImageMagick, lost the EXIF
`InteropIndex` (which DCF colour space the camera used).
**What to do:** nothing is needed; make the copy again to have it.

### Copies of rotated JPEG archives turned twice — before v2.25.3 / v2.26.0 (#623, #634)

**What:** a JPEG, PNG or TIFF exported from the JXL of a portrait JPEG (a
phone or camera JPEG archived losslessly, or converted to a lossy JXL: cjxl
puts the orientation in the codestream both ways) came out rotated twice: the
pixels were already upright and the copied EXIF Orientation turned them again
(v2.26.0 also removes the XMP `tiff:Orientation` mirror). Masters made from
TIFFs are not affected.
**What to do:** export again with `--overwrite`, or reset the tag and its
mirror: `exiftool -Orientation#=1 -XMP-tiff:Orientation= -overwrite_original <file>`.

### IrfanView showed no EXIF — v2.17.0 to v2.19.0, and JPEG archives before v2.19.2 (#561, #562)

**What:** IrfanView reads the EXIF only from a plain Exif box placed before
the XMP box. Masters and their copies written by v2.17.0–v2.19.0 had the XMP
first; JPEG → JXL archives had their EXIF Brotli-compressed by cjxl. Other
viewers and ExifTool read both.
**What to do:** `jxl_recompressor.py --refresh-explorer-thumbnails` (wizard
menu 6) puts the Exif box first, in place; `jxl_jpeg_transcoder.py
--repair-jbrd` (menu 7) rewrites archives with plain boxes, the JPEG
unchanged. The archives carry no history: run the repair over the folders
they are in; it changes only the ones that need it.

### dc:Relation values doubled — v2.10.0 to v2.12.0 (#531)

**What:** a JXL from another program, recompressed, came back with each of
its own `dc:Relation` values twice. JXLs written by the toolkit were not
affected.
**What to do:** nothing is needed.

### Lossless JPEG archives that do not rebuild — v2.0.0 to v2.0.3 (#323)

**What:** the provenance marks written into a lossless JPEG archive made
`djxl --reconstruct_jpeg` fail for JPEGs that already had XMP (Lightroom and
Capture One exports). The pixels are there; the original JPEG does not come
back bit for bit until the archive is repaired.
**What to do:** `jxl_jpeg_transcoder.py --repair-jbrd` (wizard menu 7) finds
and repairs them. JPEG archives carry no history: run the repair over the
folders those versions wrote.
