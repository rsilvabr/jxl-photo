# What jxl-photo writes into your photos

Everything your source file carries — the EXIF and XMP from Capture One,
Lightroom, the camera — is **copied** to the output. (IPTC IIM, the older
keyword and caption block some programs still write, has no place in a JXL:
what exists only there is not in the master. The lossless JPEG archive keeps
everything.) On top of that, the
toolkit adds the few fields below, almost all of them in XMP. This page lists
every one: what it is, which script writes it, and when it is removed again.

The EXIF keeps the source TIFF's byte order (little- or big-endian) in the
master, in every copy made from it and in the TIFF the decoder writes back
(since v2.25.0; until v2.24.x a master was always little-endian and a copy
always big-endian).

Since v2.17.0 the encoder, the decoder and the recompressor copy the XMP as
**one block**, so it keeps namespaces exiftool can read but not write
(SilverFast keeps its IR and dust-removal settings in one; a scan decoded
without it is no longer recognised as having an IR channel). Before, the copy
was field by field and dropped them. The transcoder's JPEG/PNG outputs still
copy field by field: a block cannot be split across the 64 KB segments of a
JPEG. Verbatim copies and the `jbrd` container keep every byte.

No field the toolkit writes contains a readable file name, folder, user name
or computer name. Two of them are hashes of the source's path, so they cover
whatever that path holds (`C:\Users\<name>\...` includes your user name). See
[What these fields reveal](#what-these-fields-reveal) for what a hash can and
cannot give away.

## The fields at a glance

| Field | What the toolkit puts there | Written by |
|---|---|---|
| `XMP-xmp:CreatorTool` | nothing since v2.19.0 — it keeps naming the program that made the photo. Files written up to v2.18.x carry the ICC copy there, as `ICC:<base64>` after the program name (`Capture One Windows \| ICC:…`); [see below](#the-icc-copy-jxlphoto-iccprofile) | encoder and recompressor up to v2.18.x |
| `XMP-dc:Description` | your caption, untouched. Up to v2.23.x (and with `ENCODE_TAG_MODE = "title"`) also a copy of the encode record after it — Windows shows this field as the **Title** | encoder, recompressor (`"title"` only) |
| `XMP-exif:UserComment` | a copy of the encode record — Windows shows it as **Comments** | encoder, recompressor (`ENCODE_TAG_MODE = "comment"`) |
| `EXIF:Software` | a copy of the encode record after the program name | encoder, recompressor (`ENCODE_TAG_MODE = "software"`) |
| `XMP-dc:Relation` | `jxlphoto-*` tokens — see [the next table](#markers-in-xmp-dcrelation). Your own Relation values are kept | all four scripts |
| `XMP-xmpMM:History` | one entry per output: `Action` (`converted`, or `derived` for a derivative), `When`, `SoftwareAgent` = `jxl-photo <version> (<github link>) with libjxl <version>` (the libjxl part since v2.30.0), `Parameters` = `<script>: from <type> to <type>`, and since v2.24.0 the **encode record** at its end (encoder, recompressor). Earlier entries (Capture One, previous runs) are kept | all four scripts |
| `ExifImageWidth/Height`, `XMP-exif:PixelX/YDimension` | corrected to the new size | derivatives that were resized |
| A thumbnail box (ISO BMFF `uuid` box, not a metadata field) | `jxlphoto-thumbnail:v1:<image data bytes>:<hash>` and a 768 px sRGB JPEG of the image, for [jxl-thumb](https://github.com/rsilvabr/jxl-thumb); the hash names the image data it was made from, so it is shown only while current ([details](README_jxl_tiff_encoder.md#explorer-thumbnails-explorer_thumbnail---explorer-thumbnail)). Reveals nothing the image does not | encoder and recompressor (`EXPLORER_THUMBNAIL = "auto"`: every output when jxl-thumb is installed, lossless outputs otherwise); the recompressor's `--refresh-explorer-thumbnails` on existing files. Never in a `jbrd` container; never copied to another file |

### The ICC copy (`jxlphoto-iccprofile`)

A lossy JXL does not always keep the exact ICC profile: some come back from
`djxl` as a generic linear-sRGB decode. The base64 copy is what lets the
decoder give a TIFF back **in the original profile, with the original curves**
(see [jxl_color_internals.md](jxl_color_internals.md)). It is usually 1–3 KB;
a scanner profile with lookup tables can be larger.

Since v2.19.0 it is a `jxlphoto-iccprofile:<base64>` token in
`XMP-dc:Relation`, with the other markers. Up to v2.18.x it was appended to
`XMP-xmp:CreatorTool` as `ICC:<base64>`, where every program that shows "the
software that made this file" printed the whole block.

- Files written up to v2.18.x keep working: the decoder, the transcoder and
  the recompressor read the token first and the old `CreatorTool` copy second.
- Nothing rewrites your existing files. An output written **from** an old file
  moves the copy (a recompressor re-encode or derivative; a re-encode of the
  TIFF with the encoder): the profile goes to the token and `CreatorTool`
  goes back to the program name.
- **An older version cannot read the token.** Decoding a v2.19.0+ file with
  v2.18.x or earlier loses the exact profile: the decoder falls back to what
  `djxl` reports, and a file whose pixels were encoded tagged sRGB (the
  encoder's `skip`, for profiles with no native JXL form) comes back as a TIFF
  with the right pixel values but **no profile at all**, which programs show
  as sRGB. Decode with the version that wrote the file, or newer.
- The decoder reads it and then **removes** it from the TIFF it writes
  (`CLEANUP_XMP_ICC_MARKER`; `--no-icc-cleanup` keeps it).
- The JPEG transcoder **removes** it from the JPEG/PNG it decodes: a delivered
  file has its profile in the normal place. Without `--icc-profile`, that
  profile is this copy when the file carries one — also for a master whose
  pixels were encoded tagged sRGB, which used to come out labelled sRGB.
- A recompressor derivative with `--output-icc` **replaces** it with the target
  profile, so it always names the colour space the pixels are in. The sRGB
  profile the recompressor builds for `--output-icc sRGB` has a fixed creation
  date since v2.30.0 (13 Nov 2026), so the same derivative made on another day
  carries the same bytes; before, the header held the moment it was built.

### The encode record

```
gen=2 | cjxl d=0.05 e=9 | cjxl d=3.0 e=9
```

One `cjxl d= e=` entry per encode, appended, never rewritten; `gen` is the
number of lossy generations. The recompressor reads it to refuse
counterproductive re-encodes and to guard repeated lossy generations
(`--on-regeneration`). The JPEG transcoder does not write one.

**Where it lives (v2.24.0):** at the end of the `Parameters` of the
`xmpMM:History` entry the write appends — the whole chain up to that write:

```
Parameters : jxl_recompressor: from image/jxl to image/jxl | gen=2 | cjxl d=0.05 e=9 | cjxl d=3.0 e=9
```

The newest entry that carries one is the file's record. `ENCODE_TAG_MODE` /
`--encode-tag` only adds a **visible copy**: none (`history`, the default;
`xmp` is its old name), Explorer's Comments (`comment`, XMP
`exif:UserComment`), the Title (`title`, after your caption in
`dc:Description` — where every version up to v2.23.x wrote it) or EXIF
Software (`software`). Files written before v2.24.0 keep the record in
`dc:Description` or Software: every script still reads it there, and the
next output made from such a file carries it in its History (and, with the
default, no longer in the Title).

`ENCODE_TAG_MODE = "off"` writes no record — its History entry ends with
`| no encode record`, so an older entry's record is not trusted again — and
strips any record the source carries.

## Markers in `XMP-dc:Relation`

| Token | Meaning | Written by | Removed by |
|---|---|---|---|
| `jxlphoto-src:<16 hex>` | **which file made this output**: a hash of the source's full path ([what it reveals](#what-these-fields-reveal)) | encoder (on the JXL), decoder (on the TIFF), transcoder (on a JPEG/PNG → JXL re-encode and on a decoded JPEG/PNG); carried by a recompressor re-encode | derivatives drop it; rewritten by the encoder's `--restamp-moved` after a folder rename |
| `jxlphoto-srcsum:<16 hex>` | the same source's **content**: a hash of its bytes | same as `src` | derivatives drop it |
| `jxlphoto-origin:<v>` | what the **first** master was made from: `tiff8`, `tiff16`, `jpeg`, `png8`, `png16`, `png`, `jxl-lossy`, `jxl-lossless`, `jxl`. Absent = unknown (also every file written before v2.10.0) | encoder, transcoder, recompressor; carried by the decoder and by derivatives | never |
| `jxlphoto-orientation:<n>` | the EXIF Orientation (2–8) of the **JPEG** the photo came from, recorded when a re-encode turns the pixels upright (a JXL made straight from a JPEG keeps it in its codestream instead). The transcoder reads it to give a JPEG its original orientation back | recompressor (re-encodes and derivatives of a JXL made from a rotated JPEG); carried by the decoder and the encoder | transcoder (never left in a JPEG/PNG) |
| `jxlphoto-iccprofile:<base64>` | the **original ICC profile**, byte for byte ([details](#the-icc-copy-jxlphoto-iccprofile)) | encoder (`EMBED_ICC_IN_JXL`), recompressor (moved from an old `CreatorTool`; the target profile on an `--output-icc` derivative, or the profile of pixels it re-encoded tagged sRGB) | decoder, transcoder |
| `jxlphoto-depth:<8\|16>` | bit depth of the TIFF that was encoded | encoder | decoder |
| `jxlphoto-mpg:<id>` | the group a page of a split multi-page TIFF belongs to (a hash) | encoder | decoder |
| `jxlphoto-page:<n>` / `jxlphoto-pages:<n>` | page index / number of pages in the group | encoder | decoder |
| `jxlphoto-subfiletype:<n>` | the page's TIFF role (e.g. `4` = a scanner's IR/mask page) | encoder | decoder |
| `jxlphoto-extrasamples:<n>` | what the extra channel is (`0` = e.g. IR, `1`/`2` = alpha) | encoder | decoder |
| `jxlphoto-scan:raw` / `jxlphoto-scan:final` / `jxlphoto-scan:no` | the TIFF was a **film scan** (scanner software, an IR channel, or no Software tag and no camera EXIF), raw or final (see the encoder's `SCAN_SCOPE`); `no`: a TIFF with neither tag that the encoder took for a camera TIFF by its 160×120 thumbnail page. Read by the recompressor's `--on-scan` guard | encoder | decoder |
| `jxlphoto-tifftag:<code>:<type>:<hex>` | a **private TIFF tag** of the page that exiftool cannot copy (code 32768 and up, byte-sized types, up to 4 KB each; none of a page's when one is larger), e.g. SilverFast's `35243`/`35244`; also the **IPTC block** (`33723`, see below): accepted whatever its declared type (exiftool writes it as a LONG array), stored with type `7` (UNDEFINED), with its own 4 KB limit that never empties the page's other tags, and kept when another tag of the page is too large; and, since v2.28.0, the **Photoshop image resources** (`34377`) under the same rule as the IPTC block (most are a 28-byte caption digest; a Lightroom or Photoshop TIFF's, which holds a preview, is over the limit and left out). Not carried: the padding Windows Photos reserves (`59932`). Its bytes as stored, in hex. A JXL copy made by the recompressor keeps these tokens; a JPEG or PNG does not (v2.28.0) | encoder | decoder (written back as the tag itself) |
| `jxlphoto-grayscale` | the page was single-channel grey | encoder | decoder |
| `jxlphoto-thumb` | the page was the TIFF's embedded thumbnail | encoder | decoder |
| `jxlphoto-icc:inherited` | the page had no ICC of its own and used page 0's | encoder | decoder, recompressor derivatives |
| `jxlphoto-pixsum:<pages>:<md5>` | a hash of the pixels the decoder wrote, so a TIFF edited later is never overwritten | decoder (on the TIFF) | the next encode |
| `jxlphoto-derived:<recipe>` | this file is a **derivative**, and how it was made: target profile (or `keep`), `@long<px>`/`@short<px>`/`@pct<n>` (`+up` when upscaling was allowed), `+<sharpen>`, and in the recompressor `/d<d>e<e>` (`s` when cjxl streamed), in the transcoder's JPEG `/q<quality>` (since v2.25.0). Examples: `sRGB@long2048+screen/d3e9`, `sRGB@long2048+screen/q70` | recompressor, transcoder (derivatives only) | decoder |

The decoder removes the tokens that describe the JXL (page structure, depth,
the derivative recipe, the private tags it writes back), so a TIFF it writes
reads like an ordinary TIFF, plus its own `src`/`srcsum`/`pixsum` and the
carried `origin` and `orientation`.

## IPTC copied into the XMP

A JXL has no IPTC box. When the source TIFF has an IPTC block (tag 33723), the
encoder (v2.27.0) does two things: it copies these IPTC fields into the XMP, and
it keeps the whole block in a `jxlphoto-tifftag:33723` marker so the decoder can
write the identical bytes back. Since v2.28.0 an IPTC block that lives only
inside the Photoshop image resources (tag 34377; Sony's Edit writes it there)
is copied into the XMP the same way.

The copy only fills what the XMP lacks: a field (or a list, such as the
keywords) the XMP already holds is never replaced, and nothing goes into the
EXIF (the IPTC has a date, not a time).

| IPTC | XMP |
|---|---|
| Keywords | `dc:Subject` |
| Caption-Abstract | `dc:Description` |
| By-line | `dc:Creator` |
| ObjectName | `dc:Title` |
| CopyrightNotice | `dc:Rights` |
| Headline, City, Province-State, Country-PrimaryLocationName, Credit, Source, SpecialInstructions | `photoshop:` Headline, City, State, Country, Credit, Source, Instructions |
| Country-PrimaryLocationCode, Sub-location | `Iptc4xmpCore:` CountryCode, Location |
| DateCreated | `photoshop:DateCreated` |

Urgency, CodedCharacterSet, ApplicationRecordVersion, DigitalCreation* and
TimeCreated are not copied. An IPTC block larger than 4 KB is not kept as a
marker (the log says so); its fields still reach the XMP. The text is read with
the block's own CodedCharacterSet, as exiftool does.

Like the other private-tag markers, the IPTC marker travels with the XMP: a
recompressed copy and a JPEG or PNG the transcoder decodes carry it, and only
the decoder turns it back into the tag. It holds what the TIFF's IPTC block
held, nothing more; an export from Capture One has a block of about 100 bytes
(a marker of some 200 characters).

## Processing history (xmpMM:History)

The standard XMP field for "what software did what" (Lightroom, Photoshop and
Capture One write it too). Every output gains exactly ONE entry, appended after
the entries the file already had — one that a Capture One export wrote, then a
master encoded by the toolkit, then its recompression:

```
Action        : converted
When          : 2026:11:06 10:17:11+09:00
SoftwareAgent : jxl-photo 2.24.0 (https://github.com/rsilvabr/jxl-photo)
Parameters    : jxl_tiff_encoder: from image/tiff to image/jxl | gen=1 | cjxl d=0.05 e=7

Action        : converted
When          : 2026:11:14 13:02:40+09:00
SoftwareAgent : jxl-photo 2.30.0 (https://github.com/rsilvabr/jxl-photo) with libjxl 0.12.0
Parameters    : jxl_recompressor: from image/jxl to image/jxl | gen=2 | cjxl d=0.05 e=7 | cjxl d=0.5 e=7
```

`Action` is `derived` for a derivative (`--output-icc`, `--resize-*`,
`--sharpen`); the script that actually ran writes the entry, so files written by
different releases each name their real version. Since v2.30.0 the
`SoftwareAgent` also names the **libjxl version of the codec that made the
file** — cjxl for a JXL, djxl for a TIFF, JPEG or PNG decoded from one — as
`cjxl --version` / `djxl --version` report it (asked once per run, never per
file); it is left out when that version cannot be read. The same encode
record does not always mean the same file in two libjxl versions (0.12 encodes
every distance below 0.05 as 0.05; 0.11 did not), so this says which one it
was. Nothing reads it back. Since v2.24.0 the entries the
encoder and the recompressor write end with the encode record
([above](#the-encode-record)), which the recompressor's policies and the `gen=`
calculation read; nothing else in the History is read back. Entries written
before v2.24.0 end after the MIME types, as `from image/tiff to image/jxl`.
`exiftool -struct` shows every `|` of the record doubled (`||`): that is how a
structure escapes it, not what is stored. Read it with:

```
exiftool -struct -XMP-xmpMM:History file.jxl
```

## Derivatives

A derivative is a recompressor or transcoder output made with `--output-icc`,
`--resize-*` or `--sharpen`. It keeps everything the master had — its EXIF
in the master's byte order (since v2.25.0; before, a copy's EXIF was always
big-endian) — except:

- `jxlphoto-src` / `jxlphoto-srcsum` are **removed** — a 2048 px copy must
  never prove the master is archived;
- `jxlphoto-derived:<recipe>` is added;
- the pixel dimensions are corrected when it was resized;
- the `jxlphoto-iccprofile` token names the target profile (recompressor
  `--output-icc`); a transcoder JPEG/PNG carries none.

## Where nothing is written

- **JPEG → JXL lossless (the `jbrd` reconstruction container)**: no XMP at all.
  Adding any breaks `djxl --reconstruct_jpeg`, the bit-exact way back to your
  JPEG. The proof of origin lives in `checksums.md5`, a file next to the
  outputs, instead.
- **JXL → JPEG bit-exact reconstruction**: the JPEG comes back byte for byte.
- **Verbatim copies** in the recompressor (a `jbrd` file kept as it is, or a
  re-encode that was not smaller): the bytes are the source's.
- **`--strip`** (encoder): no metadata, except the encode record (its
  History entry, plus the visible copy of `ENCODE_TAG_MODE`); with `"off"`,
  only a History entry that says there is none.

All of the cases above gain no `xmpMM:History` entry either, for the same
reason: the jbrd container and the bit-exact JPEG come back byte for byte, a
verbatim copy's bytes are the source's, and `--strip` writes no XMP. The
decoder's ICC strategy `none` (`--none`) writes no XMP to the TIFF, so no
History entry there too.

## What these fields reveal

- **No path in clear text.** `jxlphoto-src` is the first 16 hex digits of a
  SHA-256 of the source's full, normalised path. It cannot be turned back into
  the path. What it *can* do is **confirm a guess**: whoever has the file and
  knows your folder layout can hash a candidate path and compare. On a real
  master from 2024, the right path was the 1st hit among 48 guesses built from
  the year, the event folder and the export subfolders — but only because the
  guesses already contained the exact event name. `jxlphoto-srcsum` likewise
  only confirms a file you already have.
- **Guessing works on the whole path, never letter by letter.** A path with
  one wrong character hashes to something completely different, so there is
  no partial hit to build on, and trying every possible name is out of reach
  for anything longer than a few characters. A dictionary attack is not: the
  photo's own date, its file name and a typical folder layout leave only the
  event text unknown, and a graphics card tests billions of candidates a
  second. A one-word event name falls in under a second, two words from a
  long list of place names in minutes; three free words and a status tag
  take decades. The hash hides a path from a casual reader, not from someone
  determined who guesses well.
- **The path is hashed as it is.** Photos under your user profile
  (`C:\Users\<name>\Pictures\...`) put the user name into what is hashed:
  not readable, but part of what a guess has to get right. `jxlphoto-mpg`
  (split multi-page TIFFs) is built from the path too.
- **The file name itself** usually says more than any field: an encoder output
  keeps the TIFF's name.
- **Derivatives carry neither hash.** They do carry the encode record (so the
  master's distance is visible), the recipe, the origin, the bit depth and the
  ICC profile.
- **What comes from your camera and raw developer is copied as is**: camera
  and lens serial numbers, the owner / artist name set in the camera, GPS,
  ratings, labels. GPS and the capture time say far more about where and
  when than any hash. To keep them out of shared files,
  remove them in the export recipe there; the toolkit does not filter them.
- **`xmpMM:History` names the tool, its version, the repository link, the
  libjxl version of the codec (since v2.30.0) and the time of each
  conversion.** It never names a file, a folder, a user name or a computer
  name.

A master and its mobile derivative as v2.24.0 writes them with the default
`--encode-tag` (camera fields left out; `comment` would add the same record as
`UserComment`, and files from v2.23.x and older have it as `Description`):

```
16B_JXL/DSC_2290_ProPhoto-g22_v1.jxl
  Relation          : jxlphoto-depth:16, jxlphoto-origin:tiff16, jxlphoto-src:b9cf20e1422140c1, jxlphoto-srcsum:e6317b596c1e840d, jxlphoto-iccprofile:<base64, 1580 chars: LargeRGB-elle-V4-g22>
  HistoryParameters : jxl_tiff_encoder: from image/tiff to image/jxl | gen=1 | cjxl d=0.05 e=9
  CreatorTool       : Capture One Windows

JXL_mobile/DSC_2290_sRGB_v1.jxl
  Relation          : jxlphoto-depth:16, jxlphoto-origin:tiff16, jxlphoto-derived:sRGB/d3e9, jxlphoto-iccprofile:<base64, 812 chars: sRGB>
  HistoryParameters : jxl_tiff_encoder: from image/tiff to image/jxl | gen=1 | cjxl d=0.05 e=9, jxl_recompressor: from image/jxl to image/jxl | gen=2 | cjxl d=0.05 e=9 | cjxl d=3.0 e=9
  CreatorTool       : Capture One Windows
```

## Checking a file yourself

```
exiftool -XMP-dc:Relation -XMP-dc:Description -XMP-exif:UserComment -EXIF:Software file.jxl
exiftool -XMP-xmpMM:HistoryParameters file.jxl
exiftool -s3 -XMP-xmp:CreatorTool file.jxl
exiftool -struct -XMP-xmpMM:History file.jxl
```

The Relation line includes the base64 profile, so it is long. A file written
up to v2.18.x shows that block in `CreatorTool` instead.

`tests/test_metadata_doc.py` fails when a script gains a `jxlphoto-*` token
that this page does not list.
