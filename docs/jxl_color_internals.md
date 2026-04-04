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

### Method 2: ICC Blob (used by lossless or `--keep_icc`)

```
Color space: 940-byte ICC profile, CMM type: "KCMS"
```

**Pros:** Preserves exact original ICC with all metadata
**Cons:** Larger, ICC blob must be stored separately from image data

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

### Issue: IrfanView shows wrong colors (calibrated monitor)

IrfanView has known issues with lossless JXL on color-calibrated systems.

**Workarounds:**
- Use lossy JXL at `d=0.1` (imperceptible difference)
- Open in GIMP, Darktable, or browser instead
- The file itself is correct; the issue is viewer-specific

---

## Further Reading

- [libjxl documentation](https://github.com/libjxl/libjxl)
- [ICC Specification](https://www.color.org/icc_specs2.xalter)
- [CIE 1931 Color Space](https://en.wikipedia.org/wiki/CIE_1931_color_space)
- [XYB Colorspace technical paper](https://arxiv.org/abs/1908.03557)

---

*This document is part of the JXL-TIFF-JPEG Converter project.*
*For usage instructions, see [README_jxl_tiff_encoder.md](README_jxl_tiff_encoder.md) and [README_jxl_tiff_decoder.md](README_jxl_tiff_decoder.md).*
