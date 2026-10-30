# File sizes and effort

How large the files get, measured on real photos and film scans, and what a
higher `--effort` buys. The short version is in the
[README](../README.md#why-jpeg-xl).

## How it was measured

- **Camera photos:** 10 per camera from 5 shoots in 2024–2026 (flowers,
  landscapes, portraits, travel, an air show, animals), ISO 64–4000. All are 16-bit
  ProPhoto (gamma 2.2) TIFFs exported from Capture One, zip-compressed, some of
  them cropped.
- **Film scans:** 13 scans from SilverFast, VueScan, Epson Scan and a Noritsu lab
  scanner: colour and black and white, 8 and 16 bits, with and without an IR
  channel.
- **Encoder:** `jxl_tiff_encoder.py` v2.18.0 with cjxl 0.12.0, effort 7 (the
  default), without the Explorer thumbnail box, which adds a small 768 px JPEG.
  Scans used `--scan-distance same` so that the lossy columns apply to them too;
  by default they are archived lossless.
- **Uncompressed TIFF** = width × height × 6 bytes (16-bit RGB).
- **Summary ranges** are the middle 8 of the 10 photos (the smallest and the
  largest left out). Cropped photos are scaled to the full sensor by their size
  per pixel.
- **Quality** is measured against the original pixels with `ssimulacra2` and
  `butteraugli_main` (`--pnorm 3`) from libjxl 0.12.0: higher ssimulacra2 and
  lower butteraugli are better.

## Camera photos

| Camera | Uncompressed TIFF | Lossless | `d=0.05` | `d=0.1` | `d=0.5` | `d=1.0` |
|---|---|---|---|---|---|---|
| Nikon Z7 | 273 MB | 174 MB (138–192) | 44 MB (38–56) | 32 MB (27–42) | 12 MB (7–18) | 6.3 MB (3.0–11.7) |
| Nikon Z8 | 273 MB | 170 MB (161–176) | 42 MB (36–46) | 30 MB (24–34) | 11 MB (7–13) | 5.5 MB (3.7–8.2) |
| Nikon Zf | 146 MB | 89 MB (82–101) | 21 MB (19–26) | 15 MB (13–20) | 4.9 MB (3.5–9.3) | 2.7 MB (1.4–6.1) |

Capture One's zip compression saves little on 16-bit files: about 255 MB for a
full Z7/Z8 frame, 139 MB for the Zf. The Z7 and the Z8 give nearly the same
sizes, so on a given sensor the photo matters more than the camera: fine
detail and noise (high ISO) make a file larger, smooth skies and soft
backgrounds make it smaller. The spread grows with the distance: at `d=1.0`
the largest photo of a camera is up to six times the smallest.

Per photo (MB; "Size" is what is left after cropping):

#### Nikon Z7

| Photo | ISO | Size | TIFF (zip) | Lossless | `d=0.05` | `d=0.1` | `d=0.5` | `d=1.0` |
|---|---|---|---|---|---|---|---|---|
| Z7 #1 | 64 | 45 MP | 266 | 192 | 55.7 | 41.7 | 18.5 | 12.0 |
| Z7 #2 | 64 | 40 MP | 230 | 178 | 50.7 | 37.4 | 16.1 | 10.2 |
| Z7 #3 | 200 | 44 MP | 254 | 182 | 49.8 | 36.9 | 15.4 | 10.1 |
| Z7 #4 | 200 | 43 MP | 243 | 169 | 44.7 | 33.0 | 13.3 | 8.4 |
| Z7 #5 | 80 | 45 MP | 248 | 163 | 38.6 | 28.4 | 10.4 | 4.7 |
| Z7 #6 | 220 | 45 MP | 253 | 173 | 42.5 | 32.3 | 14.0 | 8.0 |
| Z7 #7 | 64 | 45 MP | 146 | 129 | 38.3 | 27.4 | 5.5 | 2.0 |
| Z7 #8 | 64 | 45 MP | 194 | 175 | 41.9 | 30.9 | 7.0 | 3.0 |
| Z7 #9 | 280 | 43 MP | 241 | 131 | 27.6 | 20.0 | 7.9 | 4.2 |
| Z7 #10 | 320 | 45 MP | 262 | 172 | 46.3 | 32.4 | 9.5 | 4.1 |

#### Nikon Z8

| Photo | ISO | Size | TIFF (zip) | Lossless | `d=0.05` | `d=0.1` | `d=0.5` | `d=1.0` |
|---|---|---|---|---|---|---|---|---|
| Z8 #1 | 72 | 44 MP | 251 | 169 | 44.6 | 32.6 | 12.8 | 7.9 |
| Z8 #2 | 90 | 45 MP | 258 | 173 | 42.7 | 30.9 | 11.2 | 5.9 |
| Z8 #3 | 400 | 38 MP | 225 | 152 | 40.2 | 29.9 | 12.7 | 7.6 |
| Z8 #4 | 64 | 45 MP | 253 | 164 | 38.5 | 27.2 | 9.5 | 5.5 |
| Z8 #5 | 64 | 45 MP | 245 | 151 | 30.4 | 19.9 | 5.8 | 2.7 |
| Z8 #6 | 64 | 45 MP | 252 | 160 | 35.7 | 23.8 | 7.3 | 3.6 |
| Z8 #7 | 64 | 45 MP | 264 | 168 | 40.9 | 28.7 | 9.6 | 4.7 |
| Z8 #8 | 100 | 45 MP | 256 | 173 | 45.5 | 32.7 | 12.5 | 7.0 |
| Z8 #9 | 72 | 45 MP | 255 | 164 | 41.5 | 29.7 | 10.5 | 5.3 |
| Z8 #10 | 720 | 30 MP | 178 | 116 | 28.1 | 20.8 | 7.3 | 3.3 |

#### Nikon Zf

| Photo | ISO | Size | TIFF (zip) | Lossless | `d=0.05` | `d=0.1` | `d=0.5` | `d=1.0` |
|---|---|---|---|---|---|---|---|---|
| Zf #1 | 100 | 24 MP | 141 | 86.7 | 18.8 | 12.9 | 3.5 | 1.6 |
| Zf #2 | 100 | 8 MP | 49.5 | 30.0 | 6.9 | 4.8 | 1.5 | 0.8 |
| Zf #3 | 1100 | 24 MP | 137 | 91.3 | 21.4 | 15.8 | 5.6 | 3.2 |
| Zf #4 | 250 | 23 MP | 133 | 89.8 | 22.2 | 16.3 | 6.3 | 3.8 |
| Zf #5 | 140 | 24 MP | 136 | 82.2 | 18.3 | 12.3 | 2.7 | 1.1 |
| Zf #6 | 220 | 24 MP | 131 | 83.2 | 18.9 | 13.5 | 4.0 | 2.0 |
| Zf #7 | 100 | 23 MP | 132 | 87.0 | 21.9 | 15.7 | 6.0 | 3.6 |
| Zf #8 | 400 | 24 MP | 131 | 79.3 | 19.5 | 13.2 | 3.7 | 1.4 |
| Zf #9 | 200 | 24 MP | 143 | 102 | 30.0 | 22.5 | 10.0 | 6.4 |
| Zf #10 | 4000 | 24 MP | 142 | 101 | 26.2 | 20.1 | 9.3 | 6.1 |

## Film scans

Scans are archived lossless by default (`SCAN_DISTANCE = 0`): a scan can only
be redone by scanning the film again. Lossless took 40–70% of the
uncompressed TIFF on most of these scans (29% on the smallest VueScan file).
Sizes in MB:

| Scan | Software | Size | TIFF | Lossless | `d=0.05` | `d=0.1` | `d=0.5` | `d=1.0` |
|---|---|---|---|---|---|---|---|---|
| Colour negative + IR, 16-bit | SilverFast | 91 MP | 739 | 447 | 106 | 71.8 | 16.4 | 6.9 |
| Slide + IR, 16-bit | SilverFast | 31 MP | 254 | 181 | 52.7 | 39.0 | 15.7 | 8.3 |
| Colour negative + IR, 16-bit, linear | VueScan | 32 MP | 257 | 167 | 67.6 | 56.7 | 40.9 | 38.2 |
| Colour + IR, 16-bit | VueScan | 9 MP | 68.6 | 19.9 | 15.9 | 14.8 | 11.7 | 10.7 |
| Colour negative, 16-bit | Epson Scan | 16 MP | 70.4 | 53.7 | 11.7 | 8.1 | 2.7 | 1.2 |
| Colour, 16-bit | Epson Scan | 11 MP | 66.0 | 47.4 | 13.4 | 9.5 | 3.3 | 1.5 |
| B&W, 16-bit gray | Epson Scan | 9 MP | 18.6 | 12.1 | 6.1 | 5.0 | 2.5 | 1.6 |
| Colour negative, 8-bit | SilverFast | 84 MP | 257 | 137 | 114 | 86.0 | 36.9 | 22.0 |
| Colour negative, 8-bit | SilverFast | 8 MP | 23.1 | 15.2 | 13.5 | 10.2 | 4.2 | 2.5 |
| Colour, 8-bit (lab scan) | Noritsu | 14 MP | 24.9 | 17.6 | 15.5 | 11.4 | 4.4 | 2.5 |
| Colour, 8-bit | Epson Scan | 6 MP | 18.9 | 11.6 | 9.8 | 7.7 | 3.7 | 2.2 |
| B&W, 8-bit gray | SilverFast | 34 MP | 34.3 | 18.5 | 23.9 | 19.5 | 10.1 | 6.7 |
| B&W, 8-bit gray | SilverFast | 39 MP | 20.8 | 14.7 | 22.7 | 18.1 | 8.4 | 4.9 |

- **8-bit scans: keep them lossless.** `d=0.05` and `d=0.1` save little over
  lossless on 8-bit colour, and on 8-bit gray they are *larger* than lossless.
  This matches the [16-bit vs 8-bit analysis](https://www.reddit.com/r/jpegxl/comments/1sp9qbj/analysis_jxl_distance_and_snr_16bit_vs_8bit_jpeg/):
  for lossy JXL an 8-bit source does not give a smaller file.
- The SilverFast files store the IR channel as a page of its own (plus a small
  preview page), and every page shrinks with the distance. The VueScan files
  keep IR as a fourth channel of the same image; those barely shrink at
  `d=0.5`–`1.0`.
- The TIFF column is the file as the scanner saved it; some were already
  zip-compressed. The SilverFast negative's 739 MB includes its IR page.

## Effort 7 or 9?

Effort 7 is the default, and on files this size it is the better choice:

| Distance | Effort 9 vs 7, size | Quality | Time (10 photos, 45 MP) |
|---|---|---|---|
| Lossless | **+0.6% larger** (28 of 30 photos) | identical (lossless) | 134 s → 442 s |
| `d=0.05`, `0.1`, `0.5` | same (−0.1%) | identical: same ssimulacra2 and butteraugli on all 30 photos | ~30 s → ~30 s |
| `d=1.0` | −2% in total (−8% to +12% per photo) | slightly better per byte (see below) | 30 s → 502 s |

Why it comes out this way:

- **Large images are encoded in chunks.** cjxl 0.12 splits a large image into
  chunks to save memory and time, and effort 9's extra work does not pay
  off across those chunks. On two Zf photos, plain cjxl gave:

  | | Photo A | Photo B |
  |---|---|---|
  | `-e 7` | 82.18 MB, 10 s | 91.27 MB, 11 s |
  | `-e 8` | 82.46 MB, 32 s | 90.64 MB, 36 s |
  | `-e 9` | 82.96 MB, 69 s | 91.06 MB, 74 s |
  | `-e 7 --buffering=0` | 81.03 MB, 73 s | 90.19 MB, 82 s |
  | `-e 9 --buffering=0` | 80.90 MB, 316 s | 89.10 MB, 327 s |

  `--buffering 0` (the encoder accepts it) encodes the whole image at once.
  It is what makes a lossless file smaller (about 1.2–1.4% here, for 7× the
  time and much more memory); effort 9 adds little on top of it.
- **From `d=0.05` to `d=0.5`, effort 9 changes nothing measurable** on images
  this size: same size, same scores.
- **At `d=1.0`, effort 9 aims closer to the requested distance.** It shrinks
  the photos effort 7 made better than needed (up to −8%) and grows the ones it
  made worse (up to +12%). The largest error per photo (butteraugli max,
  median) drops from 1.66 to 1.35. Compared with effort 7 at the same file
  size (interpolated between its `d=0.5` and `d=1.0` results), effort 9 scores
  slightly better on 28 of the 30 photos: +0.15 ssimulacra2, about what 1.5%
  more file would buy. That is real, but it costs 16× the time on a 45 MP photo.
  On small images, such as resized copies, effort 9 is cheap and worth it.

| | ssimulacra2 (median) | butteraugli 3-norm | butteraugli max |
|---|---|---|---|
| `d=0.05`, e7 and e9 | 90.06 | 0.047 | 0.17 |
| `d=0.1`, e7 and e9 | 89.71 | 0.091 | 0.32 |
| `d=0.5`, e7 and e9 | 87.08 | 0.380 | 1.01 |
| `d=1.0`, e7 | 82.58 | 0.614 | 1.66 |
| `d=1.0`, e9 | 82.52 | 0.625 | 1.35 |

These numbers hold for libjxl 0.12.0 with its default buffering; other
versions may behave differently.
