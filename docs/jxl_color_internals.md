# JXL Color Internals

Deep dive into how JPEG XL handles color management internally — XYB vs non-XYB,
ICC blobs vs native primaries, CICP encoding, and how to verify your files.

---

## Table of Contents

1. [How JXL Stores Colorspace Information](#how-jxl-stores-colorspace-information)
2. [XYB Colorspace](#xyb-colorspace)
3. [ICC Blob vs Native Primaries](#icc-blob-vs-native-primaries)
4. [ICC Profile Preservation in This Workflow](#icc-profile-preservation)
5. [Primary Coordinates Reference Table](#primary-coordinates-reference-table)
6. [How to Verify Your Files](#how-to-verify-your-files)
7. [Troubleshooting Color Issues](#troubleshooting-color-issues)

---

## How JXL Stores Colorspace Information

JPEG XL uses two different methods depending on the encoding:

### Method 1: Native Primaries (most common for lossy)

```
Color space: RGB, Custom,
  white_point(x=0.345705, y=0.358540),
  Custom primaries:
    red(x=0.734698, y=0.265302),
    green(x=0.159600, y=0.840399),
    blue(x=0.036597, y=0.000106)
  gamma(0.555315) transfer function
```

**Pros:** Compact, efficient, mathematically precise
**Cons:** Loses ICC-specific metadata (TRC curves, copyright, device calibration)

### Method 2: ICC Blob (lossless — and LOSSY when the profile has no native form)

```
Color space: 940-byte ICC profile, CMM type: "KCMS"
```

**Pros:** Preserves exact original ICC with all metadata
**Cons:** Larger, ICC blob must be stored separately from image data

cjxl picks Method 1 only when the profile can be described natively (primaries +
white point + a standard transfer function: a pure gamma, sRGB, linear, PQ, HLG,
709, DCI). A profile whose curve is a table — eciRGB v2 (L* curve), ROMM RGB
with its linear toe, scanner LUT profiles — has no native form, so cjxl stores
the whole ICC **even in lossy mode**. What djxl then returns is very different;
see [Lossy JXL with an ICC blob](#lossy-jxl-with-an-icc-blob-what-djxl-returns-measured-2026-09-25).

---

## XYB Colorspace

XYB is JXL's internal colorspace for lossy encoding. It's based on:
- **X**: Luminance opponent (approximates human luminance perception)
- **Y**: Luminance channel (like Y in YCbCr)
- **B**: Blue-yellow opponent (chrominance)

Key characteristics:
- **LMS cone response modeled** — matches human visual system
- **Perceptually uniform** — equal steps appear equally different
- **Separates luminance from chrominance** — enables better compression

When you encode with `cjxl` (lossy), your RGB data is:
1. Converted to linear light (remove gamma)
2. Converted to XYB colorspace
3. Compressed using VarDCT
4. On decode: XYB → linear RGB → gamma-corrected RGB

The conversion is **mathematically reversible** with sufficient bit depth, but
lossy quantization in XYB space introduces the compression artifacts.

---

## ICC Blob vs Native Primaries

### The Problem: Lossy JXL Discards ICC Detail

When encoding **lossy** JXL (`d>0`), `cjxl` converts your ICC profile to native primaries:

```
Input:  ProPhoto RGB ICC (940 bytes, with Kodak TRC curves)
           ↓
cjxl:    Extracts primaries (R,G,B,W coordinates)
         Discards TRC curves and ICC metadata
           ↓
JXL:     "Custom primaries: red(x=0.7347,y=0.2653)..."
```

On decode with `djxl`:
```
JXL:     Native primaries
           ↓
djxl:    Creates generic ICC from primaries
           ↓
Output:  Generic RGB profile (628 bytes, auto-generated)
         (different TRC, no Kodak copyright, etc.)
```

### The Solution: XMP-Embedded ICC

This workflow solves the problem by embedding the original ICC as XMP metadata:

```
TIFF:   Original ICC (ProPhoto RGB)
           ↓
Encode: cjxl creates native primaries for image data
        + Original ICC base64-encoded in XMP
           ↓
JXL:    "Custom primaries" + XMP(dc:Description="ICC:AAADrE...")
           ↓
Decode: djxl generates generic ICC from primaries
        + Script extracts original ICC from XMP and replaces
           ↓
TIFF:   Original ICC restored (ProPhoto RGB, 940 bytes)
```

### Why TRC Curves Matter

**TRC (Tone Reproduction Curves)** define how digital values map to luminance:

```
Without precise TRC:
  Digital value 16384 → "approximately 25% luminance"

With original ICC TRC:
  Digital value 16384 → "exactly 24.7% luminance per Kodak spec"
```

For casual viewing: **no visible difference**.

For professional editing:
- Shadow recovery may have slightly different response
- Color grading curves interact differently
- Print profiles may have slight color shifts
- Multi-conversion workflows accumulate drift

---

## Lossy JXL with an ICC blob: what djxl returns (measured 2026-09-25)

**Short version:** for the common profiles (any pure gamma — ProPhoto, Elle's
LargeRGB g2.2, Adobe RGB, Wide Gamut, Melissa — plus sRGB, Rec.2020, DCI-P3)
nothing below applies: djxl returns the pixels in the original space, wide
gamut intact, and pasting the original ICC back onto the TIFF is correct. It is
only for profiles WITHOUT a native form (a table curve) that the lossy decode
changes space.

### What djxl does, per case

| JXL | djxl integer output (PNG/PPM) is in | Pasting the original ICC on it is |
|---|---|---|
| Lossy, native colour (Method 1) | the original space (djxl writes a synthetic ICC describing it) | **correct** |
| Lossless, ICC blob | the original space, pixels identical (0 differing pixels) | **correct** |
| Lossy, ICC blob | **linear sRGB** (`RGB_D65_SRG_Rel_Lin`) | **wrong** — colours completely off |

Same behaviour in libjxl 0.11.2 and 0.12.0. The case is detectable from djxl
itself: `djxl in.jxl out.png --icc_out=out.icc --orig_icc_out=orig.icc` — the two
files are identical in the first two rows and different in the third.

### Wide gamut is kept in the JXL, but an integer decode clips it

Linear sRGB cannot hold colours outside sRGB without negative values, and an
integer PNG/TIFF has none. Measured on a 16 MP Capture One ProPhoto export
(9.6% of its pixels outside sRGB), encoded with a ROMM-with-toe profile at
d=0.05 and converted back correctly from djxl's integer output: **52.9 dB** on the
in-gamut pixels, **40.7 dB** on the out-of-gamut ones. The JXL itself lost
nothing: `djxl … out.pfm` (32-bit float) returns the negative values (9.4% of the
pixels). A correct decode of this case is therefore *float output + a colour
conversion from `--icc_out` to the original profile*, never an assignment.

### Real profiles, measured (1024 px crop of the same photo, d=0.05)

Error back in the photo's own Elle g2.2 space. "Assign" = paste the original ICC
on djxl's integer output (the toolkit's roundtrip decode); "convert" = float
output converted from `--icc_out` to the original.

| Profile | JXL stores | Assign | Convert |
|---|---|---|---|
| LargeRGB-elle-V4-g22, ProPhoto (Windows/Adobe, pure γ1.8) | native | 47.8 dB | 47.8 dB |
| Adobe RGB (1998), Wide Gamut RGB, Melissa RGB | native | 50.9 / 51.1 / 47.8 dB | same |
| sRGB (two profiles), Rec.2020, DCI-P3 | native | 49.0 / 50.4 / 51.0 dB | same |
| eciRGB v2 (v2 and v4 profiles) | **ICC blob** | **23.6 dB** | 51.2 / 48.0 dB |
| Epson Perfection V800 scanner (SFprofR/SFprofT) | **ICC blob** | **20.8 / 20.4 dB** | 30.3 / 28.5 dB¹ |
| ROMM RGB with the linear toe (test profile) | **ICC blob** | **15.4 dB** | 48.9 dB |

¹ Converting a camera photo *into* a scanner input profile is lossy on its own;
the absolute number says little, the gap between the columns is the point.

Pure gamma or table curve also does **not** matter for compression: the same
photo as Elle g2.2 and as ProPhoto γ1.8 gave byte-for-byte the same size and the
same PSNR at d=0.05, 0.1 and 1.0 (XYB linearises the input first). There is no
quality reason to convert a pure-gamma ProPhoto TIFF to a g2.2 profile before
encoding.

### How the toolkit copes today

`jxl_tiff_encoder.py`'s default `ICC_PNG_STRATEGY = "cautious"` test-encodes
each new profile. For eciRGB v2 and the Epson scanner profiles it decides
**skip**: the pixels are encoded as if they were sRGB, so the JXL is native
(sRGB-tagged), the decode returns the original numbers, and the roundtrip is
correct (measured 57.3 / 56.4 dB, same-space numbers). The price is that the
JXL itself displays with wrong colours in viewers, because it says sRGB.

**Fixed (2026-09-26, bug #437):** the decode now detects the case from djxl
itself — `--icc_out` and `--orig_icc_out` on the same decode differ ONLY here
— and takes the float path: `djxl … out.pfm --icc_out=…`, then ImageMagick
converts from djxl's linear sRGB to the original profile (the "Convert"
column above; alpha, which is not colour-managed, comes from the integer
decode). The same rule sits in the recompressor's and the transcoder's
derivative paths: the intermediate is the float PFM with djxl's own profile
assigned, and a "keep the source space" derivative is CONVERTED back to the
original ICC rather than re-assigned linear sRGB. The encoder's cautious test
checks the same condition and refuses "embed" for a lossy ICC blob whatever
the brightness ratio (the icc_cache key gained `:t=2`, so every profile is
retested once). Without ImageMagick on PATH the decode of such a file fails
closed with an error — a wrong-colour TIFF is never written. A grey master
with a table-curve grey profile behaves the same (djxl returns linear grey):
every path converts it back to the file's own grey profile, never to an RGB
`--output-icc` (bug #438).

### Why the fix goes through a float decode (the plain version)

A lossy JPEG XL never stores the pixels in the original colour space. cjxl
converts them to the format's internal space (XYB) and records what the
original was; djxl has to convert back on the way out.

- **Profile with a native form** (pure gamma, sRGB, Adobe RGB, Rec.2020…): the
  space fits the format's compact description, djxl converts back to it on its
  own, and pasting the original ICC on the output is correct.
- **Table-curve profile:** there is no compact description, so cjxl stores the
  whole ICC as an attachment. djxl does not apply an arbitrary ICC itself (that
  takes a colour-management engine); it hands the pixels back in a space it can
  produce — **linear sRGB** — and says so in `--icc_out`. The old bug was
  ignoring that and pasting the original ICC on numbers that are in another
  space.

Why not just convert djxl's 16-bit PNG? **Gamut.** The master is in a large
space (ProPhoto), sRGB is small, and a saturated ProPhoto green written in sRGB
coordinates is something like R = −0.3, G = 1.2: valid numbers, just outside
0–1. An integer PNG/TIFF can only hold 0–1, so those values are clipped *when
the file is written*, before any conversion can bring them back. A **PFM** is a
plain 32-bit-float image format and keeps −0.3 and 1.2 as they are:

```
JXL ──djxl──► PFM float, linear sRGB (values outside 0–1 preserved)
                    │
        magick: assign djxl's linear-sRGB profile,
                convert to the original profile (ROMM, eciRGB…)
                    │
                    ▼
        pixels back in the master's space, all inside 0–1 again
```

On the way back to ProPhoto, the "impossible" sRGB green is a valid colour
again. Measured (2026-09-25): the whole photo comes back at **51.9 dB** — the
same as a native-profile file — and the 9.4% of out-of-sRGB pixels at
**47.3 dB**, against 40.7 dB for the same conversion from the clipped PNG and
15.4 dB for the old paste-the-ICC behaviour.

In practice:

- **It only costs where it is needed.** The detection is free: the djxl call
  that already existed gained `--icc_out`/`--orig_icc_out`, and the two profiles
  differ only in this case. Only then does a second decode to PFM run. Files
  with a native profile go through exactly the old path (the tests pin their
  derivatives byte-identical to the pre-fix code).
- **Alpha:** a PFM has no alpha channel. The decoder takes the alpha from the
  first, integer decode (alpha is not colour-managed); the derivative paths
  refuse the file instead of dropping the channel.
- **Grey:** djxl writes a one-channel PFM, converted back to the file's own grey
  profile.
- **No ImageMagick:** the decoder stops with an error instead of writing a
  wrong TIFF, and the delete gate never removes the JXL.
- **New files rarely land here:** the encoder's cautious test now refuses to
  embed such a profile in a lossy JXL and picks "skip", which already decoded
  correctly. The float path exists mainly for JXLs already in an archive.

---

## ICC Profile Preservation

### How It Works

In `jxl_tiff_encoder.py`:

```python
# 1. Extract ICC from source TIFF
icc_data = extract_icc_from_tiff(tiff_path)  # ~500-3000 bytes

# 2. Base64 encode for text embedding
icc_base64 = base64.b64encode(icc_data)  # ~700-4000 chars

# 3. Create XMP with embedded ICC
xmp = f"""<?xpacket...?>
  <dc:description>
    <rdf:Alt>
      <rdf:li>ICC:{icc_base64}</rdf:li>
    </rdf:Alt>
  </dc:description>
"""

# 4. Embed encoding params separately (visible in Windows)
xmp += f"""<xmp:CreatorTool>cjxl d={distance} e={effort}</xmp:CreatorTool>"""

# 5. Inject XMP into JXL
exiftool -XMP<=xmp_file.jxl
```

In `jxl_tiff_decoder.py`:

```python
# 1. Extract XMP from JXL
xmp_data = exiftool("-XMP", jxl_path)

# 2. Find and decode ICC
match = re.search(r'ICC:([A-Za-z0-9+/=]+)', xmp_data)
icc_data = base64.b64decode(match.group(1))

# 3. Apply to output TIFF
exiftool(f"-ICC_Profile<={icc_data}", tiff_path)

# 4. Clean up XMP (remove base64, keep encoding params)
if "ICC:" in creator_tool:
    # Remove ICC: prefix, keep suffix
    new_ct = creator_tool.replace("ICC:AAADrE...", "").strip(" |")
    exiftool(f"-XMP-xmp:CreatorTool={new_ct}", tiff_path)
```

### Storage Locations

| Location | Content | Visibility |
|----------|---------|------------|
| JXL codestream | Native primaries | Internal |
| XMP dc:Description | Base64 ICC | Windows shows first 255 chars |
| XMP CreatorTool | Encoding params | Windows Properties panel |

The base64 data appears in Windows Explorer as:
```
Title: ICC:AAADrEtDTVMCEAAAbW50clJHQiBYWVogB84A...
```
This is intentional — the encoding params are more useful to see at a glance.

---

## Primary Coordinates Reference Table

Common colorspaces and their CIE 1931 xy chromaticity coordinates:

| Colorspace | White Point | Red Primary | Green Primary | Blue Primary | Gamma |
|------------|-------------|-------------|---------------|--------------|-------|
| **sRGB** | D65 (0.3127, 0.3290) | (0.6400, 0.3300) | (0.3000, 0.6000) | (0.1500, 0.0600) | ~2.2 |
| **Adobe RGB 1998** | D65 (0.3127, 0.3290) | (0.6400, 0.3300) | (0.2100, 0.7100) | (0.1500, 0.0600) | 2.2 |
| **ProPhoto RGB** | D50 (0.3457, 0.3585) | (0.7347, 0.2653) | (0.1596, 0.8404) | (0.0366, 0.0001) | 1.8 |
| **DCI-P3** | DCI (0.3140, 0.3510) | (0.6800, 0.3200) | (0.2650, 0.6900) | (0.1500, 0.0600) | 2.6 |
| **Rec. 2020** | D65 (0.3127, 0.3290) | (0.7080, 0.2920) | (0.1700, 0.7970) | (0.1310, 0.0460) | Various |
| **Display P3** | D65 (0.3127, 0.3290) | (0.6800, 0.3200) | (0.2650, 0.6900) | (0.1500, 0.0600) | ~2.2 |

### Detecting Colorspace from Primaries

To identify a colorspace from JXL primaries:

```python
def detect_colorspace(red_x, green_x, blue_x):
    if abs(red_x - 0.7347) < 0.01:
        return "ProPhoto RGB"
    elif abs(red_x - 0.6400) < 0.01:
        if abs(green_x - 0.2100) < 0.01:
            return "Adobe RGB"
        return "sRGB"
    elif abs(red_x - 0.6800) < 0.01:
        return "DCI-P3 / Display P3"
    elif abs(red_x - 0.7080) < 0.01:
        return "Rec. 2020"
    return "Unknown / Custom"
```

---

## How to Verify Your Files

### Check JXL colorspace:

```powershell
jxlinfo -v photo.jxl
```

Look for:
- `"ICC profile"` → Original ICC preserved
- `"Custom primaries"` → Converted to native primaries

### Check ICC in JXL:

```powershell
# Direct ICC (lossless)
exiftool -ICC_Profile photo.jxl

# XMP-embedded ICC (this workflow)
exiftool -XMP-dc:Description photo.jxl | findstr "ICC:"

# Encoding params
exiftool -XMP-xmp:CreatorTool photo.jxl
```

### Check ICC in TIFF:

```powershell
# Full ICC info
exiftool -ICC_Profile:All photo.tif

# Just the description
exiftool -ProfileDescription photo.tif

# Check for preview (multiple pages)
tiffinfo photo.tif
```

### Compare original vs round-trip:

```powershell
# Original TIFF
exiftool -ProfileDescription -ProfileCopyright original.tif

# Round-trip TIFF
exiftool -ProfileDescription -ProfileCopyright roundtrip.tif

# Should match exactly if ICC preservation worked
```

---

## Troubleshooting Color Issues

> **Golden rule:** When in doubt, test with a single photo or a small batch. Encode them, decode them back, and compare with the originals. If the round-trip files match, your workflow is safe — any difference you see in a viewer is simply how that viewer renders the file.

### Issue: Colors look different after round-trip

**Check 1:** Was ICC embedded?
```powershell
exiftool -XMP-dc:Description photo.jxl | findstr "ICC:"
# Should show "ICC:AAAD..."
```

**Check 2:** Is ICC the same size?
```powershell
# Original
exiftool -ICC_Profile -b original.tif | wc -c

# Round-trip
exiftool -ICC_Profile -b roundtrip.tif | wc -c

# Should be identical (e.g., both 940 bytes)
```

**Check 3:** Profile description match?
```powershell
exiftool -ProfileDescription original.tif roundtrip.tif
```

### Issue: XnView MP shows wrong colorspace

**Normal behavior.** XnView MP's properties panel shows "sRGB" for all lossy JXL
files, regardless of actual colorspace. This is a display bug, not a conversion issue.

Verify with `jxlinfo` or open in GIMP/Darktable.

### Issue: Wide-gamut JXL looks slightly muted in IrfanView / XnView MP on Adobe RGB monitors

**What you see:** When opening a wide-gamut JXL (e.g. ProPhoto RGB) on an Adobe RGB calibrated monitor using IrfanView or XnView MP, the image may look slightly muted — comparable to the difference between viewing an original Adobe RGB file and the same file properly converted to sRGB on that same monitor. The most vibrant colors that extend beyond sRGB appear dulled, as if the viewer were limiting the gamut to sRGB for on-screen preview.

> **Important distinction:** If the image looks *heavily* desaturated — as if ProPhoto RGB were being treated as sRGB — that is not this issue. Strong desaturation indicates a real conversion or color-management bug.

**What is actually happening:** The JXL file still contains the full wide-gamut color data. IrfanView and XnView MP appear to render JXL on screen using a narrower gamut path, even when your monitor is capable of displaying a larger gamut. They are not showing you the full gamut that the file actually holds.

**How to verify the file is really intact:**
1. Decode the JXL back to TIFF with `jxl_tiff_decoder.py`
2. Open the round-trip TIFF in an ICC-aware editor (Capture One, Photoshop, NX Studio)
3. The colors will be fully vibrant again, identical to the original TIFF

Alternatively, open the original JXL in a color-managed browser such as Waterfox (or Firefox). Browsers with proper color management may render the full gamut correctly on wide-gamut displays.

> **The file is safe.** The subtle gamut reduction is only in how these viewers render the image on screen — the underlying pixel data and ICC profile remain fully preserved.

---

### Issue: IrfanView shows wrong colors (calibrated monitor) — reported & fixed

**Update:** This issue was reported to the IrfanView developer and an updated plugin DLL with correct ICC profile handling was received. It is recommended to download the latest JXL plugin from the IrfanView website to test if the fix has been publicly released.

*Previous behavior (old plugin):
IrfanView had issues with lossless JXL on color-calibrated systems.

**Workarounds (if using old plugin):**
- Update to latest IrfanView JXL plugin
- Use lossy JXL at `d=0.1` (imperceptible difference)
- Open in GIMP, Darktable, or browser instead
- The file itself is correct; the issue was viewer-specific

---

## Further Reading

- [libjxl documentation](https://github.com/libjxl/libjxl)
- [ICC Specification](https://www.color.org/icc_specs2.xalter)
- [CIE 1931 Color Space](https://en.wikipedia.org/wiki/CIE_1931_color_space)
- [XYB Colorspace technical paper](https://arxiv.org/abs/1908.03557)

---

*This document is part of the JXL-TIFF-JPEG Converter project.*
*For usage instructions, see [README_jxl_tiff_encoder.md](README_jxl_tiff_encoder.md) and [README_jxl_tiff_decoder.md](README_jxl_tiff_decoder.md).*
