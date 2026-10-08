# Behavior, defaults and known limitations

The README keeps a one-line summary of each of these; the detail is here.

How the tools behave by default, where other software does not follow along, and the limits worth knowing before a large batch.

## Multi-page TIFFs: every page is converted by default

`--multipage-mode` defaults to **`split`** — one JXL per page (`photo.jxl`, `photo_page1.jxl`, ...), rejoined into a multi-page TIFF on decode. A TIFF with a single real page produces exactly `photo.jxl`, so for ordinary photos this is indistinguishable from the old default.

**This default changed.** It used to be `ignore` (page 0 only, everything else discarded), which caught people out: plenty of TIFFs are multi-page without looking like it — Capture One and many scanners append an embedded preview, and film scanners add an IR/mask page. Worse, mode 8 then deleted the source after encoding page 0, destroying the other pages permanently.

Embedded **thumbnail/preview** pages are still dropped by default (`--thumbnail-mode exclude`); they are reduced-resolution copies of a page that is already in the output. Add `--thumbnail-mode include` if you want the decoded TIFF to reproduce the original page structure exactly.

```powershell
py jxl_tiff_encoder.py "F:\Photos"                             # split, thumbnails dropped
py jxl_tiff_encoder.py "F:\Photos" --thumbnail-mode include    # keep the previews too
py jxl_tiff_encoder.py "F:\Photos" --multipage-mode ignore     # old behavior: page 0 only
```

In the wizard the setting lives under **Advanced Options** (Step 6A — answer `y` when asked "Configure advanced options?"), and the Step 7 summary spells out the policy before you type YES. If you do choose a page-dropping policy, the encoder reports the totals in the run summary — and **mode 8 refuses to delete any source whose pages were dropped**, so you cannot lose them by accident.

## The decoder never overwrites an original TIFF

The encoder's default mode 0 leaves `photo.tif` and `photo.jxl` side by side, and the JXL is newer. A sync decode into that same folder used to overwrite the **original master** with the decode. The decoder now only overwrites TIFFs it wrote itself (they carry its `jxlphoto-src` marker); any other TIFF is **refused** — not decoded over, and its JXL never deleted — and listed at the end of the run. Decode into another folder (`--mode 1`/`3`) or pass `--overwrite` if replacing it is really intended. Details: [decoder README](README_jxl_tiff_decoder.md#original-tiff-masters-are-never-overwritten).

The same goes for a decode you **edited** after decoding it (Photoshop keeps the marker): since v2.8.1 each decode records its pixels, and one whose pixels changed is refused like a master. See [A decode you edited afterwards is not decoded over](README_jxl_tiff_decoder.md#a-decode-you-edited-afterwards-is-not-decoded-over-v281).

## Lossy is the default: `--distance 0.1`

The default is **near-lossless, not lossless**. At `d=0.1` a 45 MP file drops to roughly a tenth of the TIFF size, and a difference blend in Photoshop *will* show small deviations — that is the compression working as configured, not a bug. For a bit-exact archive use `--distance 0` (still ~40% smaller than an uncompressed TIFF). Note that `--mode` (0–8) only decides *where* output files go; quality is `--distance` alone.

### The distance floor depends on your cjxl: 0.05 on libjxl 0.12, 0.01 on 0.11

**cjxl 0.12 clamps every lossy distance at or below 0.05 to the same value.** On two real 16-bit photos, `--distance 0.005` through `0.05` produced a **byte-identical** file. The menu and the CLI accept anything from 0 to 15, so setting `0.02` looks like it buys you something, but on 0.12 it does not: either stay at `0.05`, or go to `--distance 0` for true lossless. The floor was added in libjxl 0.12 ([PR #4238](https://github.com/libjxl/libjxl/pull/4238)): below it the DC coefficients leave the `int16` range, so the file no longer conforms to Level 5 of the JPEG XL spec. libjxl itself still decodes such files, but a decoder built for Level 5 may not.

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

Counter-intuitive but reproducible across four real photos: at 8 bits, `--distance 0` produced a **smaller** file than `--distance 0.05` (38.6% vs 46.4% of source on one, 42.6% vs 47.8% on another). JXL's lossless mode is very efficient at 8 bits, while VarDCT at a very low distance carries overhead it cannot amortise. If your source is 8-bit and you want small files, measure before assuming lossy wins.

## Default re-run behavior differs per script

The TIFF encoder/decoder default to **smart sync** (reconvert when the source is newer than the existing output — there is no plain "skip existing" CLI mode), while the JPEG transcoder **skips existing outputs** by default. Use `--overwrite` (always) or `--sync` (source newer) to control it explicitly. See each script's README for details.

## Film scanners: IR channel / Digital ICE

If your scanner software (e.g. SilverFast, VueScan) uses the IR page as a hidden channel for Digital ICE / dust & scratch removal, converting the TIFF to JXL and back **may break that feature**. Those programs often rely on vendor-specific tags and exact page ordering beyond the standard TIFF `SubfileType`. This tool preserves the page as a standard grayscale `PAGE`, but the original scanner software may no longer recognize it as an IR mask. **Test with one file before batch-processing important film scans.**

## Scanner ICC profiles and lossy encoding

Scanner ICC profiles (e.g. SilverFast `SFprofT`) can cause `cjxl` to produce very dark images in lossy mode. The encoder works around this by not embedding the ICC in the intermediate PNG and restoring it into the reconstructed TIFF (see `--icc-png-strategy`). The JXL file may therefore display with shifted colors in some viewers, but the TIFF round-trip is accurate. For scanner workflows, treat **JXL as the backup container and the reconstructed TIFF as the final image**.

## Viewer quirks (not data loss)

| Viewer | Behavior | Why |
|--------|----------|-----|
| **Windows Explorer** | Thumbnails ignore the embedded EXIF thumbnail and are **not color-managed** — ProPhoto/Adobe RGB images look washed out | Limitation of Microsoft's JXL WIC codec |
| **IrfanView** | EXIF visible for TIFF → JXL, **hidden** for JPEG → JXL (lossless or lossy) | JPEG → JXL uses Brotli (`brob` box), which IrfanView cannot read |
| **IrfanView / XnView MP** | Wide-gamut JXL may look slightly muted on a calibrated monitor | Viewer rendering limitation — the file keeps the full gamut; decode back to TIFF to confirm |
| **XnView MP** | Shows `Color Profile: sRGB` for lossy JXL regardless of the real space | Lossy JXL stores compact numeric primaries, not an ICC blob; XnView falls back to an "sRGB" label |

For reliable EXIF and color, use **XnView MP** or **digiKam**. If an image looks *heavily* desaturated, that **is** a real bug — please report it.

## Matrix decode mode is 8-bit internally

The decoder's Matrix mode (`--matrix`, for color-space conversion via LittleCMS) quantizes pixels to 8-bit for the transform and scales the result back to 16-bit. Effective precision is 8 bits in that mode only — use **Roundtrip mode** (the default) for full 16-bit fidelity.

## eciRGB v2, scanner profiles and other table-curve ICC profiles

Most profiles have a native JPEG XL form (a pure gamma, sRGB, Rec.2020, DCI-P3…), and those round-trip correctly — ProPhoto, Elle's LargeRGB g2.2, Adobe RGB and Wide Gamut included, wide gamut intact. Profiles whose tone curve is a **table** (eciRGB v2's L* curve, scanner LUT profiles, ROMM RGB with its linear toe) have none: in lossy mode cjxl then stores the whole ICC, and djxl decodes such a file to **linear sRGB**, not to the original space.

The encoder's default `cautious` ICC strategy detects most of these and encodes them "skip" (verified correct round trips for eciRGB v2 and Epson scanner profiles; the JXL itself then shows wrong colours in viewers, because it is tagged sRGB). A table-curve profile that passes the cautious check is embedded as an ICC blob — and since bug #437 (2026-09-26) that case is handled everywhere: the decoder detects it from djxl's own `--icc_out`/`--orig_icc_out` output and decodes to float, CONVERTING to the original profile (51.9 dB measured, against 15.4 dB for the old paste-the-ICC behaviour), the derivative paths of the recompressor/transcoder do the same, and the cautious test now refuses "embed" for a lossy ICC blob whatever the brightness says. Decoding such a file needs ImageMagick on PATH; without it the decode fails closed instead of writing a wrong-colour TIFF. Measurements and the exact mechanism: [Lossy JXL with an ICC blob](jxl_color_internals.md#lossy-jxl-with-an-icc-blob-what-djxl-returns-measured-2026-09-25).
