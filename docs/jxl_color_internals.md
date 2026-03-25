# JXL Internals — Color Management and Encoding

Reference document explaining how JPEG XL handles colorspaces and encoding internally.
Written based on hands-on experience converting ProPhoto RGB TIFFs from Capture One.

---

## 1. File structure — ISOBMFF boxes

A JXL file is a container, structured like a zip of named sections called **boxes**.
Each box has a 4-character type code and a payload.

A typical JXL produced by this script looks like:

```
JXL_   →  JXL signature (magic bytes)
ftyp   →  file type declaration
jxll   →  JXL level (2 = full)
Exif   →  EXIF metadata (injected by exiftool)
xml    →  XMP metadata
jxlc   →  image codestream (lossless)
  or
jxlp   →  image codestream part 1  ┐
jxlp   →  image codestream part 2  ├ (lossy VarDCT splits into multiple parts)
jxlp   →  image codestream part N  ┘
```

The box order matters for some readers. IrfanView reads boxes linearly and stops at
the first image box — so Exif must come before the codestream (see Bug 3 in
`bugs_fixes_explained.md`).

**Diagnostic command:**
```powershell
exiftool -v3 photo.jxl   # shows all boxes in order with byte offsets
```

---

## 2. Two encoding modes: VarDCT and Modular

JXL has two completely independent encoders.

### VarDCT (default for lossy)

DCT-based encoding, conceptually similar to JPEG but much more advanced:
- Variable block sizes (8×8 up to 256×256)
- Adaptive quantization per block
- Better prediction and entropy coding
- Works in XYB colorspace (see section 4)

This is what makes JXL lossy small and efficient for photos.
All lossy conversions from this script use VarDCT unless `CJXL_MODULAR = True`.

### Modular (always used for lossless)

Entropy-coded, conceptually similar to FLIF and PNG:
- No DCT, no frequency decomposition
- Pixel values predicted and residuals entropy-coded
- Mandatory for lossless (d=0)
- Can also be used for lossy, but is less efficient than VarDCT for photos

When you see "modular" in cjxl output or documentation, it refers to this encoder,
not to the container structure (which is separate).

**Choosing:**

| | VarDCT | Modular |
|--|--|--|
| d=0 (lossless) | Not available | Always used |
| d>0 (lossy photos) | Default — efficient | `--modular=1` — larger files |
| Colorspace | XYB (absolute) | non-XYB (preserves ICC blob) |

---

## 3. Two colorspace modes: XYB and non-XYB

Independent of the encoder, JXL can store pixel data in two ways.

### non-XYB

- Pixel values stay in the original colorspace (ProPhoto, AdobeRGB, sRGB, etc.)
- An ICC profile is embedded as a blob in the file
- Required for lossless
- Decoders need an external color management library (lcms2, skcms) to do
  colorspace conversions

This is what lossless JXL uses. GIMP reports "embedded ICC profile" because the
raw ICC bytes are literally stored inside the file.

### XYB

- Pixel values are converted internally to the XYB colorspace before encoding
- XYB is an absolute colorspace (covers the full visible spectrum)
- No ICC blob is stored — colorspace is signaled via compact numeric primaries
- Decoders can convert directly to sRGB, Display-P3, Rec.2100, etc. without
  external color management libraries
- Used by default for all lossy encoding

The colorspace primaries stored in the header for XYB files are a **hint** — they
tell the decoder "the original image was in this colorspace, use it as your target."
The actual pixel data is in XYB (absolute), so no gamut information is lost.

**This is why XnView shows "sRGB" for lossy files:**
XnView looks for an ICC blob. Finds none. Falls back to showing "sRGB" as a label.
The actual primaries (ProPhoto, AdobeRGB, etc.) are stored numerically, and XnView's
properties panel doesn't read them. The colors render correctly — only the label is wrong.

---

## 4. XYB colorspace explained

XYB is based on human vision, not on display primaries.

The human eye has three types of cone cells:
- **L** — Long wavelength (~red)
- **M** — Medium wavelength (~green)
- **S** — Short wavelength (~blue)

The brain doesn't process L, M, S directly. It creates opponent channels:

```
Y  =  L + M          (luminance — the eye is most sensitive here)
X  =  L - M          (red vs green)
B  =  S              (blue vs yellow — the eye is least sensitive here)
```

JXL applies lossy quantization in this opponent space instead of RGB.
Result: bits are spent where the eye is most sensitive, and saved where it isn't.
This is why VarDCT + XYB produces much smaller files than equivalent quality in RGB.

XYB is device-independent and absolute — it's not tied to any display's primaries.
It fully covers the ProPhoto RGB gamut and beyond. Storing ProPhoto data in XYB
does not clip or reduce the gamut; the XYB → ProPhoto conversion is reversible
(modulo lossy quantization, which is the intended quality loss).

---

## 5. ICC profiles

### What an ICC profile is

An ICC profile is a binary file (`.icc`) containing matrices and curves that describe
how a colorspace maps to an absolute reference (the PCS — Profile Connection Space).
It is the "rosetta stone" that lets two different colorspaces be compared and converted.

### ICC blob

"ICC blob" = the raw binary bytes of an ICC profile embedded inside another file.
A lossless JXL contains the full ICC profile of the source colorspace embedded as-is.
GIMP's "this file has an embedded color profile" message is referring to this blob.

### The ICC header structure

Every ICC profile starts with a 128-byte header containing fixed fields:
- Bytes 0–3: profile size
- Bytes 4–7: CMM type
- Bytes 16–19: colorspace (e.g., `RGB `)
- Bytes 20–23: PCS colorspace (always `XYZ `)
- Bytes 68–79: **illuminant** (fixed D50 reference — see section 6)
- Bytes 80+: tag table (primaries, TRC curves, etc.)

---

## 6. White point vs illuminant — why they are different

This is a common source of confusion. An ICC profile has two separate concepts:

### Native white point

The white point *of the colorspace itself* — the chromaticity of "pure white" in that
space. Defined in the `wtpt` tag inside the profile.

- **ProPhoto RGB**: D50 (x=0.3457, y=0.3585)
- **AdobeRGB**: D65 (x=0.3127, y=0.3290)
- **sRGB**: D65

### Illuminant field (bytes 68–79 of the header)

This is **not** the native white point. It is a fixed reference point for the PCS.

The PCS (Profile Connection Space) is the intermediate space ICC uses for all conversions:
```
AdobeRGB → PCS → ProPhoto RGB
```

For this intermediate space to work, every profile must share the same reference
point so the math is consistent. The ICC committee chose D50 for the PCS. Therefore,
**every conformant ICC profile must have D50 in bytes 68–79, regardless of its native
white point.**

An AdobeRGB profile has D65 as its native white point but D50 in the illuminant field.
The profile's color matrices already include the chromatic adaptation from D65 to D50
internally — so the ICC machinery handles everything correctly.

This is the field that Capture One writes incorrectly (0x2b instead of 0x2d — a 2-unit
rounding error). The fix in this script patches only that field, leaving all color
data untouched. See `bugs_fixes_explained.md` Bug 2 for details.

---

## 7. CICP — Coding-Independent Code Points

CICP is a standard (ITU-T H.273) that describes colorspaces as three small integers:

```
primaries | transfer_function | matrix_coefficients
```

For example:
```
1 | 1 | 0   →  BT.709 / sRGB primaries, sRGB transfer, identity matrix
9 | 16 | 0  →  BT.2020 primaries, PQ transfer, identity matrix (HDR)
```

Instead of embedding hundreds of bytes of ICC data, you write three numbers. Any
conformant decoder knows exactly what colorspace that means, no external library needed.

JXL uses CICP-style encoding for its "Enum" colorspace signaling — lossy XYB files
store the colorspace hint this way. The numeric primaries reported by `jxlinfo` come
from this encoding.

Common JXL primaries values and their meaning:

| Primaries | Colorspace |
|-----------|-----------|
| 1 | sRGB / BT.709 |
| 9 | BT.2020 (wide gamut) |
| 11 | Display-P3 |
| 12 | Custom (coordinates stored explicitly — ProPhoto, AdobeRGB) |

ProPhoto RGB is not in any standard CICP table, so JXL stores it as "Custom" with
explicit xy-chromaticity coordinates — which is what `jxlinfo` reports.

---

## 8. Diagnostic commands and what to look for

### jxlinfo — colorspace and encoding

```powershell
jxlinfo photo.jxl
```

Full output example (ProPhoto lossy):
```
JPEG XL file format container (ISO/IEC 18181-2)
Uncompressed Exif metadata: 892 bytes
Uncompressed xml  metadata: 4453 bytes
JPEG XL image, 1200x801, lossy, 16-bit RGB
Color space: RGB, Custom,
  white_point(x=0.345705, y=0.358540),
  Custom primaries:
    red(x=0.734698, y=0.265302),
    green(x=0.159600, y=0.840399),
    blue(x=0.036597, y=0.000106)
  gamma(0.555315) transfer function,
  rendering intent: Perceptual
```

**Key fields to check:**

| Field | What it means |
|-------|--------------|
| `lossy` / `lossless` | Encoding type |
| `16-bit` | Bit depth preserved |
| `white_point` | D50 = 0.3457/0.3585 → ProPhoto; D65 = 0.3127/0.3290 → sRGB/Adobe |
| `red/green/blue primaries` | Exact gamut coordinates — compare tables below |
| `gamma` | 0.5556 (=1/1.8) → ProPhoto; 0.4545 (=1/2.2) → sRGB/Adobe |
| `xyb_encoded` | Present and true → pixel data is in XYB (lossy default) |

**Primary coordinates reference:**

| Colorspace | White point | Red x | Green x | Blue x | Gamma |
|-----------|------------|-------|---------|--------|-------|
| ProPhoto RGB | 0.3457 (D50) | 0.7347 | 0.1596 | 0.0366 | 0.5556 |
| AdobeRGB | 0.3127 (D65) | 0.6400 | 0.2100 | 0.1500 | 0.4545 |
| sRGB | 0.3127 (D65) | 0.6400 | 0.3000 | 0.1500 | 0.4545* |

*sRGB uses a piecewise TRC, not a pure gamma — jxlinfo may report it as sRGB enum
rather than a gamma value.

---

### exiftool -v3 — box structure and metadata

```powershell
# Show all JXL boxes in order (critical for debugging EXIF visibility)
exiftool -v3 photo.jxl

# Read all EXIF fields
exiftool photo.jxl

# Read specific field
exiftool -ColorSpace photo.jxl
exiftool -ICC_Profile:all photo.jxl

# Extract embedded ICC profile to file (lossless JXL only)
exiftool -icc_profile -b photo.jxl > extracted.icc
```

**Good box order** (EXIF visible in IrfanView):
```
JXL_ → ftyp → jxll → Exif → xml → jxlc/jxlp...
```

**Bad box order** (EXIF injected after codestream — IrfanView won't see it):
```
ftyp → jxll → jxlc/jxlp... → Exif → xml
```

---

### Checking a round-trip (lossy JXL → TIFF back)

When decoding a lossy XYB JXL back to TIFF with `djxl`, the output will have
ProPhoto-equivalent primaries but **not** the original Kodak/ROMM ICC blob.
The decoder generates a generic profile using the stored primaries.

This is expected and correct — the color values are accurate. Only the ICC
"identity" (manufacturer name, copyright string) differs from the original Capture One
export. Visual color accuracy is unaffected.

```powershell
# Decode JXL back to PNG to inspect
djxl photo.jxl output.png

# Then check colorspace of the output
exiftool output.png
```

---

## 9. Summary — lossless vs lossy in this workflow

| Property | Lossless (d=0) | Lossy (d>0) |
|----------|---------------|------------|
| Encoder | Modular | VarDCT |
| Colorspace storage | non-XYB — ICC blob embedded | XYB — primaries as CICP/Custom |
| ICC blob in file | Yes (e.g. Kodak ROMM) | No |
| Color profile in GIMP | "Embedded ICC: ProPhoto" | No embedded profile |
| Color profile in XnView | "ProPhoto RGB / Kodak" | "sRGB" ← display label bug |
| Colors render correctly | Yes | Yes |
| Verify colorspace | exiftool shows ICC | jxlinfo shows primaries |
| Recover Kodak ICC blob | Yes (pixel-perfect) | No (generic profile on decode) |
| File size (45MP) | ~173 MB | ~8–47 MB depending on distance |
