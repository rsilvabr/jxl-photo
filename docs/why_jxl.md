# Why JPEG XL, and the numbers behind the settings

The long version of the README's first section: typical file sizes, the
grain tests behind the recommended distances, and the measurements they
come from. Per-photo numbers: [file_sizes.md](file_sizes.md).

Spectacular compression with no compromise on bit depth.

- Lossless 16-bit files much smaller than TIFF and TIFF with ZIP/Deflate
- Lossy 16-bit files — small files that retain full 16-bit tonal information, something no other common format achieves (JPEG is 8-bit, TIFF lossless is large)
- This is genuinely new: small lossy files, but with 16-bit color depth

Typical sizes in MB of ProPhoto exports from Capture One (median of 10–20 photos per resolution, ISO 64–4000). ZIP is the TIFF compression tiff-workflow writes. Detail and noise move the size more than the camera does — [every distance, with the spread across the photos](file_sizes.md#every-distance-from-a-16-bit-and-an-8-bit-tiff):

| Format | 45 MP (Z7, Z8) | 24 MP (Zf) | 12 MP (D700) |
|--------|:--------------:|:----------:|:------------:|
| TIFF 16-bit / with ZIP | 273 / 227 | 146 / 120 | 72 / 60 |
| TIFF 8-bit / with ZIP | 136 / 61 | 73 / 30 | 36 / 16 |
| JXL lossless, from 16-bit | 173 | 89 | 43 |
| JXL lossless, from 8-bit | 43 | 21 | 10 |
| JXL `d=0.05` (finest lossy) | 42 | 21 | 9.9 |
| JXL `d=0.1` (near-lossless) | 31 | 15 | 7.2 |
| JXL `d=0.5` (high quality) | 11 | 4.9 | 2.4 |
| JXL `d=1.0` (visually lossless) | 5.5 | 2.7 | 1.2 |
| JXL `d=3.0` (web) | 1.8 | 0.9 | 0.4 |

`d=0.1` is the default and the recommendation for masters. Two tests of the grain ([details](file_sizes.md#grain-after-heavy-editing-and-as-it-is)):

- **After heavy editing.** The worst case: the unlit black background of a flash photo — nothing there but grain (little noise reduction) — decoded back to TIFF and pulled up hard in Capture One (shadows +100, blacks +100, exposure unchanged). In that lifted black, at 100%, `d=0.1` matched lossless; at 300–800% its grain differed slightly, and only when flipping between the two files. `d=0.05` closes even that gap for about a third more space (42 against 31 MB at 45 MP, still a quarter of lossless); I use it for my own masters, out of caution. `d=0.5` softened that grain a little at 100% and `d=1.0` showed artifacts, while the lit subject looked clean even at `d=1.0`.
- **Without editing**, the photo as it is: `d=0.7` keeps the grain at 200%, where `d=1.0` starts to smooth it — a light file for copies you only look at.

Lossy sizes are about the same from an 8-bit and a 16-bit TIFF; only lossless differs. From an 8-bit TIFF, lossless is about the size of `d=0.05`, so jxl-photo archives 8-bit TIFFs lossless at `d=0.1` and below — see [Features](../README.md#features).

Film scans are archived lossless by default; that took 40–70% of the uncompressed TIFF on most scans tested (a 91 MP SilverFast negative with IR: 739 MB → 447 MB).

Effort 7 is the default: on files this size effort 9 gives the same files from `d=0.05` to `d=0.5`, slightly *larger* lossless ones, and only a small gain at `d=1.0`, for up to 16× the time. Per-photo numbers, scans and the quality measurements: [docs/file_sizes.md](file_sizes.md).

The numbers behind these settings are measured and posted on r/jpegxl, on 16-bit ProPhoto exports from Capture One:

- [Distance, error and SNR](https://www.reddit.com/r/jpegxl/comments/1s6k718/analysis_jxl_distance_error_and_snr_analysis/): per-pixel SNR from `d=0.01` to `d=10` on a 45 MP photo. `d=1.0` is the sweet spot for pixels above 30 dB, `d=0.3–0.5` for 40 dB, and `d=0.05` for 50 dB: over 90% of the pixels stay above 50 dB, and not one pixel falls below 20 dB. That is where the archival setting comes from; the stress test above is from the same post.
- [16-bit vs 8-bit, and JPEG](https://www.reddit.com/r/jpegxl/comments/1sp9qbj/analysis_jxl_distance_and_snr_16bit_vs_8bit_jpeg/): for lossy JXL an 8-bit file is **not** smaller than a 16-bit one, and it keeps less signal, so there is no reason to go 8-bit. JPEG needs 5–15× the file size to get close to JXL.
- [Generation loss](https://www.reddit.com/r/jpegxl/comments/1wlmfw0/my_jxl_archive_tool_refuses_bad_recompressions_i/): one recompression from an archived `d=0.1` down to `d=1.0` costs 0.02 dB, but reaching the same file size in many small steps costs up to 9 dB. That is why the recompressor goes to the target in one shot, counts generations, and refuses counterproductive re-encodes.

The per-pixel SNR analyzer used in the first two is [jxl-quality-analyzer](https://github.com/rsilvabr/jxl-quality-analyzer); the harness, raw results and figures of the third are in [jxl-generation-loss](https://github.com/rsilvabr/jxl-generation-loss).

## Compared with a ZIP-compressed TIFF

[tiff-workflow](https://github.com/rsilvabr/tiff-workflow) recompresses TIFFs
losslessly with ZIP/Deflate and needs no libjxl. A 45 MP photo (medians, MB):

| Format | 16-bit | 8-bit |
|--------|:------:|:-----:|
| Uncompressed TIFF | 273 | 136 |
| **ZIP/Deflate (tiff-workflow)** | 227 (~17% smaller) | 61 (~55% smaller) |
| JXL lossless (this toolkit) | 173 (~37% smaller) | 43 (~68% smaller) |
| **JXL `d=0.1` (this toolkit)** | 31 (~89% smaller) | 43 — kept lossless: lossy would save only ~10 MB |
