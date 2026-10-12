# What jxl-photo writes into your photos

Everything your source file carries — the EXIF, XMP and IPTC from Capture One,
Lightroom, the camera — is **copied** to the output. On top of that, the
toolkit adds the few fields below, almost all of them in XMP. This page lists
every one: what it is, which script writes it, and when it is removed again.

The copy is made by exiftool, so it holds what exiftool can write: an XMP field
in a namespace exiftool does not know (rare; some third-party apps write their
own) is not carried over. That has always been true of the encoder, the decoder
and the transcoder's re-encodes; since v2.13.0 it is true of the recompressor's
re-encodes too. Verbatim copies and the `jbrd` container keep every byte.

Nothing here contains a readable file name, folder, user name or computer
name. See [Privacy](#privacy) for what the hashes can and cannot reveal.

## The fields at a glance

| Field | What the toolkit puts there | Written by |
|---|---|---|
| `XMP-xmp:CreatorTool` | `ICC:<base64>` — the original ICC profile, byte for byte, appended after any text already there (`Capture One Windows \| ICC:…`) | encoder (`EMBED_ICC_IN_JXL`), recompressor |
| `XMP-dc:Description` | the encode record `gen=N \| cjxl d=… e=… \| …` (after your own caption, which stays first) | encoder, recompressor (`ENCODE_TAG_MODE = "xmp"`, the default) |
| `EXIF:Software` | the same encode record, instead of the Description | encoder, recompressor (`ENCODE_TAG_MODE = "software"`) |
| `XMP-dc:Relation` | `jxlphoto-*` tokens — see [the next table](#markers-in-xmp-dcrelation). Your own Relation values are kept | all four scripts |
| `XMP-xmpMM:History` | one entry per output: `Action` (`converted`, or `derived` for a derivative), `When`, `SoftwareAgent` = `jxl-photo <version> (<github link>)`, `Parameters` = `<script>: from <type> to <type>`. Earlier entries (Capture One, previous runs) are kept | all four scripts |
| `ExifImageWidth/Height`, `XMP-exif:PixelX/YDimension` | corrected to the new size | derivatives that were resized |

### The ICC copy in `CreatorTool`

A lossy JXL does not always keep the exact ICC profile: some come back from
`djxl` as a generic linear-sRGB decode. The base64 copy is what lets the
decoder give a TIFF back **in the original profile, with the original curves**
(see [jxl_color_internals.md](jxl_color_internals.md)). It is usually 1–3 KB;
a scanner profile with lookup tables can be larger.

- The decoder reads it and then **removes** it from the TIFF it writes
  (`CLEANUP_XMP_ICC_MARKER`).
- The JPEG transcoder **removes** it from the JPEG/PNG it decodes: a delivered
  file has its profile in the normal place.
- A recompressor derivative with `--output-icc` **replaces** it with the target
  profile, so it always names the colour space the pixels are in.

### The encode record

```
gen=2 | cjxl d=0.05 e=9 | cjxl d=3.0 e=9
```

One `cjxl d= e=` entry per encode, appended, never rewritten; `gen` is the
number of lossy generations. The recompressor reads it to refuse
counterproductive re-encodes and to guard repeated lossy generations
(`--on-regeneration`). The JPEG transcoder does not write one.
`ENCODE_TAG_MODE = "off"` writes nothing and strips any record the source
carries.

## Markers in `XMP-dc:Relation`

| Token | Meaning | Written by | Removed by |
|---|---|---|---|
| `jxlphoto-src:<16 hex>` | **which file made this output**: a hash of the source's full path ([Privacy](#privacy)) | encoder (on the JXL), decoder (on the TIFF), transcoder (on a JPEG/PNG → JXL re-encode and on a decoded JPEG/PNG); carried by a recompressor re-encode | derivatives drop it; rewritten by the encoder's `--restamp-moved` after a folder rename |
| `jxlphoto-srcsum:<16 hex>` | the same source's **content**: a hash of its bytes | same as `src` | derivatives drop it |
| `jxlphoto-origin:<v>` | what the **first** master was made from: `tiff8`, `tiff16`, `jpeg`, `png8`, `png16`, `png`, `jxl-lossy`, `jxl-lossless`, `jxl`. Absent = unknown (also every file written before v2.10.0) | encoder, transcoder, recompressor; carried by the decoder and by derivatives | never |
| `jxlphoto-depth:<8\|16>` | bit depth of the TIFF that was encoded | encoder | decoder |
| `jxlphoto-mpg:<id>` | the group a page of a split multi-page TIFF belongs to (a hash) | encoder | decoder |
| `jxlphoto-page:<n>` / `jxlphoto-pages:<n>` | page index / number of pages in the group | encoder | decoder |
| `jxlphoto-subfiletype:<n>` | the page's TIFF role (e.g. `4` = a scanner's IR/mask page) | encoder | decoder |
| `jxlphoto-extrasamples:<n>` | what the extra channel is (`0` = e.g. IR, `1`/`2` = alpha) | encoder | decoder |
| `jxlphoto-grayscale` | the page was single-channel grey | encoder | decoder |
| `jxlphoto-thumb` | the page was the TIFF's embedded thumbnail | encoder | decoder |
| `jxlphoto-icc:inherited` | the page had no ICC of its own and used page 0's | encoder | decoder, recompressor derivatives |
| `jxlphoto-pixsum:<pages>:<md5>` | a hash of the pixels the decoder wrote, so a TIFF edited later is never overwritten | decoder (on the TIFF) | the next encode |
| `jxlphoto-derived:<recipe>` | this file is a **derivative**, and how it was made: target profile (or `keep`), `@long<px>`/`@short<px>`/`@pct<n>` (`+up` when upscaling was allowed), `+<sharpen>`, and in the recompressor `/d<d>e<e>` (`s` when cjxl streamed). Example: `sRGB@long2048+screen/d3e9` | recompressor, transcoder (derivatives only) | decoder |

The decoder removes the tokens that describe the JXL (page structure, depth,
the derivative recipe), so a TIFF it writes reads like an ordinary TIFF, plus
its own `src`/`srcsum`/`pixsum` and the carried `origin`.

## Processing history (xmpMM:History)

The standard XMP field for "what software did what" (Lightroom, Photoshop and
Capture One write it too). Every output gains exactly ONE entry, appended after
the entries the file already had — one that a Capture One export wrote, then a
master encoded by the toolkit, then its recompression:

```
Action        : converted
When          : 2026:10:13 14:02:11-03:00
SoftwareAgent : jxl-photo 2.14.0 (https://github.com/rsilvabr/jxl-photo)
Parameters    : jxl_tiff_encoder: from image/tiff to image/jxl

Action        : converted
When          : 2026:10:13 14:07:48-03:00
SoftwareAgent : jxl-photo 2.14.0 (https://github.com/rsilvabr/jxl-photo)
Parameters    : jxl_recompressor: from image/jxl to image/jxl
```

`Action` is `derived` for a derivative (`--output-icc`, `--resize-*`,
`--sharpen`); the script that actually ran writes the entry, so files written by
different releases each name their real version. It is **write-only**: no gate,
policy or `gen=` calculation in the toolkit ever reads it back. Read it with:

```
exiftool -struct -XMP-xmpMM:History file.jxl
```

## Derivatives

A derivative is a recompressor or transcoder output made with `--output-icc`,
`--resize-*` or `--sharpen`. It keeps everything the master had, except:

- `jxlphoto-src` / `jxlphoto-srcsum` are **removed** — a 2048 px copy must
  never prove the master is archived;
- `jxlphoto-derived:<recipe>` is added;
- the pixel dimensions are corrected when it was resized;
- the `CreatorTool` ICC names the target profile (recompressor `--output-icc`).

## Where nothing is written

- **JPEG → JXL lossless (the `jbrd` reconstruction container)**: no XMP at all.
  Adding any breaks `djxl --reconstruct_jpeg`, the bit-exact way back to your
  JPEG. The proof of origin lives in `checksums.md5`, a file next to the
  outputs, instead.
- **JXL → JPEG bit-exact reconstruction**: the JPEG comes back byte for byte.
- **Verbatim copies** in the recompressor (a `jbrd` file kept as it is, or a
  re-encode that was not smaller): the bytes are the source's.
- **`--strip`** (encoder): no metadata, except the encode record unless
  `ENCODE_TAG_MODE = "off"`.

All of the cases above gain no `xmpMM:History` entry either, for the same
reason: the jbrd container and the bit-exact JPEG come back byte for byte, a
verbatim copy's bytes are the source's, and `--strip` writes no XMP. The
decoder's ICC strategy `none` (`--none`) writes no XMP to the TIFF, so no
History entry there too.

## Privacy

- **No path in clear text.** `jxlphoto-src` is the first 16 hex digits of a
  SHA-256 of the source's full, normalised path. It cannot be turned back into
  the path. What it *can* do is **confirm a guess**: whoever has the file and
  knows your folder layout can hash a candidate path and compare. On a real
  master from 2024, the right path was the 1st hit among 48 guesses built from
  the year, the event folder and the export subfolders — but only because the
  guesses already contained the exact event name. `jxlphoto-srcsum` likewise
  only confirms a file you already have.
- **The file name itself** usually says more than any field: an encoder output
  keeps the TIFF's name.
- **Derivatives carry neither hash.** They do carry the encode record (so the
  master's distance is visible), the recipe, the origin, the bit depth and the
  ICC profile.
- **What comes from your raw developer is copied as is**: camera and lens
  serial numbers, GPS, ratings, labels. To keep them out of shared files,
  remove them in the export recipe there; the toolkit does not filter them.
- **`xmpMM:History` names the tool, its version, the repository link and the
  time of each conversion.** It never names a file, a folder, a user name or a
  computer name.

A real master and its mobile derivative (camera fields left out):

```
16B_JXL/DSC_2290_ProPhoto-g22_v1.jxl
  Relation    : jxlphoto-depth:16, jxlphoto-src:b9cf20e1422140c1, jxlphoto-srcsum:e6317b596c1e840d
  Description : gen=1 | cjxl d=0.05 e=9
  CreatorTool : Capture One Windows | ICC:<base64, 1580 chars: LargeRGB-elle-V4-g22>

JXL_mobile/DSC_2290_sRGB_v1.jxl
  Relation    : jxlphoto-depth:16, jxlphoto-derived:sRGB/d3e9
  Description : gen=2 | cjxl d=0.05 e=9 | cjxl d=3.0 e=9
  CreatorTool : Capture One Windows | ICC:<base64, 812 chars: sRGB>
```

## Checking a file yourself

```
exiftool -XMP-dc:Relation -XMP-dc:Description -EXIF:Software file.jxl
exiftool -s3 -XMP-xmp:CreatorTool file.jxl
exiftool -struct -XMP-xmpMM:History file.jxl
```

`tests/test_metadata_doc.py` fails when a script gains a `jxlphoto-*` token
that this page does not list.
