# Technical notes — jxl_tiff_encoder.py

Converting 16-bit ProPhoto RGB TIFFs from Capture One to JPEG XL with full EXIF
visible in IrfanView turned out to be surprisingly difficult. Every standard approach
produced files where either the colors were wrong, the EXIF was missing, or the file
was silently corrupted.

This document explains each problem, why it happens, and what the fix is.
(This document was made before v1.0 release)

---

## Bug 1 — cjxl does not read TIFF

**Symptom:**
```
cjxl input.tiff output.jxl
→ Getting pixel data failed.
```

**What happened:**
cjxl simply does not support TIFF as input. The error message does not explain this clearly.

Using ImageMagick as an intermediary also failed, but in a more dangerous way: it produced
a valid-looking file but silently converted to 8-bit sRGB instead of 16-bit ProPhoto RGB.

The cause: Capture One TIFFs have malformed EXIF tags — `PixelXDimension` and `GainControl`
are stored as SHORT (2 bytes) instead of LONG (4 bytes) as required by the EXIF standard.
ImageMagick encounters these and, while trying to recover, loses the color profile and bit
depth. No warning is shown.

**Fix:**
Use Python's `tifffile` library to read the TIFF (it ignores malformed tags).
Write the pixel data to a 16-bit PNG manually using `zlib` and `struct` — pure Python,
no image library that could alter the data. Pipe the PNG to cjxl via stdin.
With `USE_RAM_FOR_PNG = True`, the PNG never touches disk.

---

## Bug 2 — Capture One's ICC profile has a 2-byte rounding error

**Symptom:**
```
Invalid ICC profile (bad connection space)
```
After conversion, JXL showed `Color Space: Uncalibrated`.

**What happened:**
Every ICC profile has a 128-byte header. One field is the "D50 illuminant" — a fixed
reference point that all ICC profiles must share so color management software can compare
and convert between them.

The ICC specification requires that bytes 68–79 of every conformant profile contain
the D50 reference value:
```
0000f6d6 00010000 0000d32d
```

Capture One exports with:
```
0000f6d6 00010000 0000d32b  ← last byte 0x2b instead of 0x2d
```

A difference of 2 in the last byte — a rounding error in Capture One's internal ICC
generation. The visual impact on color is imperceptible. However, cjxl and exiftool
validate ICC profiles strictly and reject this. Most other software (Photoshop, IrfanView,
browsers) is tolerant and ignores it.

**Important:** this illuminant field is a fixed PCS (Profile Connection Space) reference
required by the ICC spec for all profiles, regardless of the colorspace's native white
point. AdobeRGB (native D65), sRGB (native D65), and ProPhoto (native D50) all must
have D50 in this field. It is distinct from the colorspace's white point. See
`jxl_color_internals.md` section 6 for a full explanation.

**Fix:**
Patch bytes 68–79 before using the ICC:
```python
icc[68:80] = bytes.fromhex("0000f6d6000100000000d32d")
```
This only corrects the reference field. The color matrices, primaries, and all other
color data in the profile are untouched. Color accuracy is unaffected.

This fix is safe for any ICC profile: if the bytes are already correct, writing the
same value has no effect. It applies equally to ProPhoto, AdobeRGB, and sRGB exports.

---

## Bug 3 — IrfanView reads JXL boxes linearly and stops at the codestream

**Symptom:**
EXIF was correctly injected (confirmed with `exiftool -v3`), but IrfanView showed
no metadata. No error, no warning.

**What happened:**
A JXL file is a container — like a zip with named sections called "boxes".
After exiftool injects the EXIF, the box order is:

```
ftyp → jxll → jxlc (image data) → Exif → xml
```

IrfanView reads these boxes in order and **stops when it reaches the image data** (`jxlc`).
It assumes everything important comes before the image. The Exif box comes after, so
IrfanView never reads it.

Lightroom, which works correctly, produces this order:
```
JXL_ → ftyp → jxll → Exif → xml → jxlc
```

This was discovered by running `exiftool -v3 file.jxl`, which shows the internal box
structure in order.

**Fix:**
After conversion and EXIF injection, read the raw JXL bytes, parse all the boxes,
reorder them so Exif comes before the codestream, and rewrite the file.

---

## Bug 4 — Lossy JXL uses multiple `jxlp` boxes, breaking the reorder

**Symptom:**
With `d=0` (lossless): everything worked.
With `d>0` (lossy): exiftool failed, or produced a silently corrupted file.

**What happened — part A:**
With `d>0`, cjxl outputs a raw codestream without an ISOBMFF container by default.
exiftool needs the container to know where to inject the Exif box.

**What happened — part B:**
Lossy JXL (VarDCT) splits the image data across multiple boxes named `jxlp` instead
of a single `jxlc`. The original reorder function used a Python dict:
```python
box_map = {name: (header, payload) for name, header, payload in boxes}
```
A dict cannot have duplicate keys. All `jxlp` boxes collapsed into the last one.
The output JXL was missing most of the image data — silently corrupted.

**Fix:**
- Add `--container=1` to the cjxl command for lossy output, forcing ISOBMFF container.
- Rewrite the reorder function using lists instead of a dict, so all `jxlp` boxes
  are preserved in their original order.

Note: `--container=1` should only be used for lossy (`d>0`). For lossless (`d=0`),
cjxl already generates a container automatically, and adding `--container=1` changes
how the ICC profile is stored in a way that breaks color display in IrfanView.

---

## Bug 5 — Brackets in folder names break exiftool

**Symptom:**
```
Wildcards don't work in the directory specification
No matching files
```
Only on paths like `F:\Session [FINAL]\photo.tif`.

**What happened:**
exiftool supports wildcards in file paths, so `folder/*.tif` processes all TIFFs.
The bracket characters `[` and `]` are part of the wildcard syntax — `[abc]` means
"any one of a, b, or c". When a folder name contains `[FINAL]`, exiftool interprets
it as a wildcard pattern and finds no matching files.

On Windows, brackets in folder names are valid and common for indicating versions or
project status.

**Fix:**
exiftool has a `-@` flag that reads all arguments from a text file, one per line.
When arguments come from a file, wildcard processing is disabled — paths are treated
literally.

```python
arg_file.write_text(f"-b\n-Exif\n{tiff_path}\n")
subprocess.run(["exiftool", "-@", str(arg_file)])
```

All exiftool calls in the script use this approach.

---

## Bug 6 — Windows case-insensitive filesystem silently doubles the TIFF count

**Symptom:**
A folder with 90 TIFFs appeared as 180 files. Each file was converted twice, with
no error or warning.

**What happened:**
The script originally searched for TIFFs using both uppercase and lowercase extensions
to be portable across systems. On Linux this is necessary — `photo.TIF` and `photo.tif`
are different files. On Windows, the filesystem is case-insensitive, so both searches
return the same files. 90 TIFFs became 180.

What makes this hard to notice: the script completed successfully and produced correct
JXLs — just with each file converted twice, the second pass silently overwriting the
first. With `OVERWRITE = False`, the second pass would report "90 skipped", which could
mask real skip counts and make it look like the sync mode wasn't working.

**Fix:**
Deduplicate using `f.resolve()` before adding to the list. `resolve()` returns the
canonical normalized path — on Windows, the same file always resolves to the same
string regardless of how the extension was cased in the search.

```python
seen = set()
for ext in ("*.tif", "*.tiff"):
    for f in input_path.rglob(ext):
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            files.append(f)
```

---

## Bug 7 — Lossy JXL encoding darkens images with large scanner ICC profiles

**Symptom:**
Multi-page scanner TIFFs converted to JXL with `d > 0` decode to almost black images
when reconstructed to TIFF. The lossless path (`d = 0`) preserves the image correctly.

**Measured data (Epson V700 / SilverFast `SFprofT` profile):**

| Path | Pixel max | Pixel mean | Visual |
|---|---|---|---|
| Original TIFF | 24.974 | 8.355 | normal |
| Lossy JXL with ICC in PNG | 4.889 | 653 | almost black |
| Lossy JXL without ICC in PNG | 24.974 | 8.355 | normal |
| Lossless JXL with ICC in PNG | 24.974 | 8.355 | normal |

**What happened:**
The scanner's ICC profile is a `scnr` (input device) profile with a huge `A2B0` LUT
(`mft2` type, ~217 KB). When `cjxl` encodes in lossy mode, it converts the input to the
XYB colorspace. To do this it must first interpret the RGB pixels through the ICC
profile. For this particular scanner profile, `cjxl` applies the `A2B0` LUT in a way
that collapses the dynamic range, producing pixel values around 20% of the original.

Key observations from the investigation:
- The same problem occurs with **libjxl 0.11.2**, so it is not a regression in 0.12.0.
- Modifying the TRC gamma values in the profile (from 0.00859 to 2.2) does **not** fix
  the darkening, which confirms the `A2B0` LUT is the cause, not the TRC curves.
- LittleCMS (used by Pillow) handles the same profile without visible darkening, so
  the profile is not fundamentally broken — it is a `cjxl` / libjxl interaction.
- The `djxl` decoder does not darken the image on its own: if the ICC is injected into
  a JXL that was encoded without ICC, the decoded pixels remain normal. The darkening
  is introduced during **encoding**, not decoding.

**Why lossy is affected but lossless is not:**
Lossless (`d = 0`) uses the Modular encoder and does not convert to XYB. It stores the
raw pixels and the ICC as metadata. Lossy (`d > 0`) uses VarDCT and must convert to
XYB, triggering the ICC → XYZ conversion.

**Fix:**
For lossy encoding, do not embed the ICC in the intermediate PNG's `iCCP` chunk. The
`cjxl` encoder then treats the pixels as RGB values in an unknown/sRGB-like space and
codes them without applying the scanner's `A2B0` LUT. The original ICC is still injected
into the JXL container afterwards using ExifTool, and the decoder restores it to the
output TIFF. The numeric pixels are preserved and the color profile is preserved as
metadata.

```python
# In jxl_tiff_encoder.py, PNG iCCP is only used for lossless encoding
png_icc_bytes = icc_bytes if CJXL_DISTANCE == 0 else None
png_input = make_png_bytes(img, png_icc_bytes)
```

**Trade-off:**
This is a workaround around a `cjxl` behavior, not a theoretically perfect solution. The
ideal fix would be for `cjxl` to handle scanner input profiles correctly, or to expose a
flag that disables the `A2B0` LUT conversion. As of libjxl 0.12.0, no such flag is
available (`-x icc_pathname=...` produces the same darkening).

In practice, the workaround preserves both the numeric pixel values and the ICC
profile, so the round-trip is visually identical to the original.


```powershell
# Shows internal JXL box structure and order
exiftool -v3 file.jxl

# Shows what the JXL decoder actually sees (color space, bit depth, lossless/lossy)
jxlinfo file.jxl
```

These two tools together made it possible to distinguish between "the EXIF is there
but in the wrong place" and "the EXIF was never written".

---

## Known behavior — XnView MP showing "sRGB" for lossy JXL

XnView MP displays `Color Profile: sRGB` in the properties panel for lossy JXL files,
even when the actual colorspace is ProPhoto RGB, AdobeRGB, or any other space.

**This is not a conversion to sRGB.** It is a display error in XnView's metadata panel.

**Why it happens:**
Lossy JXL uses the XYB colorspace internally and signals the original colorspace via
compact numeric primaries (CICP-style) rather than an embedded ICC blob. XnView reads
the ICC blob field, finds nothing, and shows "sRGB" as a fallback label.

The actual colorspace primaries are stored correctly in the codestream. To verify:
```powershell
jxlinfo photo.jxl
```
Check `white_point` and the red/green/blue primary coordinates. ProPhoto has D50
white point (x≈0.3457) and a distinctive red primary (x≈0.7347). sRGB has D65
(x≈0.3127) and a red primary of x=0.6400. They are unmistakable.

Colors render correctly in all conformant viewers (GIMP, Darktable, browsers, etc.).
The XnView properties panel label is the only thing that is wrong.

See `jxl_color_internals.md` sections 3 and 8 for full details and a primary
coordinates reference table.

→ [How JXL color management works, how to verify your files, and a full primary coordinates reference table](jxl_color_internals.md)

---

## Known behavior — IrfanView and color-calibrated monitors (reported & fixed)

**Update (2026-04):** This issue was reported to the IrfanView developer and an updated plugin DLL with correct ICC profile handling was received. It is recommended to download the latest JXL plugin from the IrfanView website to test if the fix has been publicly released.

*Previous behavior (documented for reference):

JXL lossless files embed the ICC color profile as a blob. Most software handles this
correctly — GIMP, XnView MP, Darktable, Firefox, Waterfox, and `jxl_to_jpeg.py` all
display correct colors.

IrfanView's behavior with lossless JXL appeared to depend on the system display profile
installed on the machine. In testing, it worked correctly on an uncalibrated monitor.
After hardware calibration with an Eizo monitor, IrfanView stopped showing correct colors
for lossless JXL while continuing to show correct colors for lossy JXL. The cause appeared
to be double color management — IrfanView applying both the embedded ICC and the system
display profile simultaneously.

**The files themselves are correct.** Any conformant JXL decoder will display the colors
accurately. If using an old IrfanView plugin version, use XnView MP or browsers for 
verification, or use lossy at `d=0.1` (imperceptible difference, ~34MB for 45MP).
