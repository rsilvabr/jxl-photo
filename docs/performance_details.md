# Performance in detail

The long version of [performance.md](performance.md): every change made for
speed or memory since v1.0, by theme, with the release that made it and what
it measured. The summary, the life of one photo and the settings that matter
for speed are on that page.

The numbers come from the release that made each change (the full detail is in
[version_history.md](version_history.md)), measured on Windows on real 16-bit
camera exports and film scans, mostly of 45 MP. Disks, core counts and libjxl
versions move them: read them as orders of magnitude.

- [At a glance](#at-a-glance) — every change by version, oldest first
- [The starting design](#the-starting-design) — threads, memory, staging,
  sync, effort 7
- [Keeping every core busy](#keeping-every-core-busy) — one pool, the memory
  cap, the next photo prepared
- [Fewer processes](#fewer-processes)
- [Less written to disk](#less-written-to-disk) — uncompressed temporary PNGs,
  decodes in memory, a master written once
- [Work a run does not do](#work-a-run-does-not-do)
- [A long run keeps going](#a-long-run-keeps-going)
- [How a speed change is checked](#how-a-speed-change-is-checked)
- [Not done, on purpose](#not-done-on-purpose)

## At a glance

Oldest first. Each version links to its notes; the sections below tell the
story by theme.

| Version | What changed | Measured |
|---|---|---|
| [v1.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.0) | Several photos at once (worker threads around the codec processes); an SSD staging folder; smart sync; effort 7 | — |
| [v1.7.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.0) | The encoder's PNG for cjxl stays in memory by default (`USE_RAM_FOR_PNG`) | no temporary image on disk |
| [v1.7.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.1) | The ICC round-trip test keeps its verdict per profile, across runs | each profile tested once, not once per photo |
| [v1.8.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.0) | cjxl 0.12's streaming encode kept: `--buffering` measured and left to cjxl | `--buffering 0`: ~1.2 % smaller for ~7× the time |
| [v1.8.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.2) | One pool of workers for the whole run, not one per output folder (encoder); TIFF pages listed in parallel | 8 photos over 8 folders: 33 → 10 s |
| [v1.9.1](version_history.md#v191) | The manifest's collision scan runs only where two rows can collide | mode 6/7 manifests start at once |
| [v2.0.0](version_history.md#v200) | One pool per run in the decoder and the transcoder too; markers read 400 files per ExifTool call; the decoder's markers ride on a call it already makes | one ExifTool start (~0.1 s) less per decoded photo |
| [v2.1.0](version_history.md#v210) | Lossy modular measured and kept off | VarDCT 20–100× faster, and smaller |
| [v2.6.0](version_history.md#v260) | Workers capped by memory (encoder, recompressor); no reader threads around subprocesses | before it, one run lost 314 of 681 files to memory |
| [v2.8.0](version_history.md#v280) | The cap reads the smaller of commit and physical RAM; streaming against whole-image encodes measured | effort 9 streamed gives effort 7's file |
| [v2.9.0](version_history.md#v290) | Recompressor: big encodes capped apart from the workers | 3 workers → 3 encodes + 3 workers preparing |
| [v2.14.0](version_history.md#v2140) | A resized copy budgeted by the size it encodes, not by its master | 6 → 27 workers (42 GB free) |
| [v2.15.0](version_history.md#v2150) | The D50 check reads the profile's bytes; the space estimate counts only what a run writes; planning batches stop only on a stall | one ExifTool call less per TIFF |
| [v2.16.0](version_history.md#v2160) | A 768 px thumbnail stored in the JXL, for jxl-thumb | 100 lossless 45 MP files in Explorer: 176 s → under 2 s |
| [v2.18.1](version_history.md#v2181) | Recompressor sync: up-to-date files set aside before planning reads them | 0.15–0.25 s per such file on a hard disk, no longer spent |
| [v2.21.0](version_history.md#v2210) | The PNG for cjxl built in blocks, without copying the page; a lighter decoder preview | encoder 0.96 → 0.44 GB per worker (d=0.1); decoder, 4 workers: 9.73 → 4.28 GB |
| [v2.23.0](version_history.md#v2230) | ImageMagick's temporary PNG written uncompressed | sRGB copies of 7 masters: 124 → 74.6 s |
| [v2.26.1](version_history.md#v2261) | The transcoder finds its orientation token in the XMP box itself | 0.18 s per JXL → JPEG |
| [v2.27.0](version_history.md#v2270) | The encoder's sync compares a newer TIFF's bytes with its master's record | a TIFF restored from a backup is not re-encoded |
| [v2.30.0](version_history.md#v2300_beta1) | A photo's ExifTool commands share one process; the PNG for cjxl stored uncompressed | 8 photos archived: 12.6 → 6.8 s |
| [v2.30.1](version_history.md#v2301-rc1) | The wizard's JPEG ↔ JXL count: one `lstat` per file, each folder resolved once | as fast as before v2.30.0: 80 000 files, 9.0 → 2.4 s |
| [v2.30.2](version_history.md#v2302-rc2) | Full-size decodes stay in memory: the thumbnail, copies, decoded TIFFs, the checks before a delete | resized JPEG copy of a 45 MP master: 14.1 → 4.5 s |
| [v2.30.2](version_history.md#v2302) | A new master written once instead of seven times; planning reads a stub of each JXL; a decoded TIFF rewritten fewer times | archiving writes 79 MB instead of 520; decoding 15.8 → 5.6 s |
| [v2.31.0-rc2](version_history.md#v2310-rc2) | sRGB derivative: colour converted in Python, image to cjxl in memory | 60 masters, sRGB copies at d=0.7 e7: 133 → 123 s (temp folder on an NVMe drive), 176–196 → 121 s (on a SATA SSD); 16 % less CPU |
| v2.31.0 | ExifTool kept open; a decoded TIFF written once; one decode for the thumbnail and the page check, then none (the thumbnail from the image in memory); cjxl's threads shared among the encodes; the decoder's preview by a table; the delete gate's checks in parallel; ImageMagick's PNG in memory; `--serve`; `jxl_photo.py encode` (and the other script commands) dispatched before the wizard loads | 4 TIFFs archived one call each with every page checked: 16.9 → 13.2 s; 6 masters decoded: 5.1 → 4.2 s; a deleting run of 6 TIFFs: 11.3 → 9.0 s; 6 masters resized to 2048 px: 4.1 → 3.6 s; 30 JPEGs archived through one `--serve` process instead of a call each: 8.9 → 4.8 s; `jxl_photo.py encode`/`transcode --version`: 413 → 379 ms, 312 → 272 ms; since rc5 (cjxl's threads shared among the encodes, the Explorer thumbnail from the image in memory, the decoder's preview by a table), 37 varied exports with 8 workers: archived 22.3 → 17.6 s, sRGB copies 32.5 → 26.0 s, decoded 13.5 → 11.8 s |

## The starting design

### Workers are threads; the codecs do the heavy work

Every script converts `--workers` photos at once — by default one per CPU
thread, at most 16. The workers are threads of one Python process, not
separate processes: the heavy work runs in cjxl, djxl, ImageMagick and
ExifTool, each a process of its own, so a worker spends most of its time
waiting for one of them. Threads cost almost nothing to start, and they share
the run's plan, counters and caches without copying them.

### The image reaches cjxl through memory

cjxl cannot read a TIFF. The encoder reads it with tifffile, builds a 16-bit
PNG in Python and hands it to cjxl on its standard input (`USE_RAM_FOR_PNG`:
an option since v1.0, the default since v1.7.0). The intermediate image never
touches the disk: no 270 MB temporary file per 45 MP photo. `--no-ram` writes
the PNG to `TEMP_DIR` instead, for a machine short of memory. Two later changes made that PNG cheaper: since v2.21.0 it is
built in blocks without copying the page (less memory per worker), and since
v2.30.0 it is stored without compression
([below](#uncompressed-temporary-pngs)).

The first JXL → JPEG script did the same the other way round, piping djxl's
output straight into ImageMagick. A plain decode has djxl write the JPEG or
PNG itself; colour conversions and resized copies need ImageMagick, and
ImageMagick needs the colour profile djxl writes after the image, so those
went through a temporary 16-bit PNG until v2.30.2 brought them back into
memory ([below](#decodes-that-stay-in-memory)).

### Staging: read from one disk, write to another

With `--staging` (`TEMP2_DIR`) pointing at an SSD, outputs are written there
and moved to their destination afterwards. In the encoder, the decoder and the
transcoder a folder's outputs leave staging as soon as its last photo is done
(the recompressor moves each file as it finishes), so staging never holds the
whole batch. The library's hard disk then mostly reads, and the new files reach
it in bulk moves instead of small writes scattered between the reads, which
would keep its heads seeking back and forth. The move is checked: a failed one is an error, and a source is deleted
only once its output has passed its checks at the final destination.

### Smart sync: redo only what changed

A re-run does not start over. The TIFF encoder, the decoder and the
recompressor redo only a source newer than its output (`--sync`, the
default); the JPEG transcoder skips outputs that already exist. A library is
converted once, and each scheduled run after that touches only what was
re-exported. Later releases refined it — see
[Work a run does not do](#work-a-run-does-not-do).

### Effort 7, and cjxl's own fast path

Effort 7 has been the default since the first version, and every measurement
since has kept it there: on 45 MP photos effort 9 gives the same size and
quality from `d=0.05` to `d=0.5`, a slightly larger lossless file, and a small
gain at `d=1.0`, for up to 16× the time
([file_sizes.md](file_sizes.md#effort-7-or-9)).

libjxl 0.12 brought a streaming encode that works through a large image in
chunks. v1.8.0 measured it and left the choice to cjxl
(`CJXL_BUFFERING = None`): `--buffering 0`, the whole image at once, made a
45 MP lossless file about 1.2 % smaller for about 7× the time (85.7 s against
12.6 s). Memory and time per worker, per image size and effort:
[RAM per worker](README_jxl_tiff_encoder.md#ram-per-worker). v2.1.0 measured
lossy modular encoding the same way — no better for photos, larger files, and
VarDCT encodes 20–100× faster — so it stays off.

## Keeping every core busy

### One pool for the whole run

Up to v1.8.1 the scripts started a pool of workers for each output folder and
waited for it to empty before starting the next. A library has one output
folder per shoot, so a run spent much of its time on the last photos of each
folder with most workers idle — about 4 % CPU with `--workers 12`. Since v1.8.2
the encoder feeds one pool for the whole run (eight 45 MP photos spread over
eight folders, 8 workers: 33 s → 10 s), and since v2.0.0 the decoder and the
transcoder do too. The planning step that opens each TIFF to list its pages
runs on its own pool since v1.8.2: on an external drive, a large library used
to sit there silent for minutes.

### As many workers as the memory holds

cjxl normally streams: about 1.5 GB per worker on a 45 MP photo. At effort 7
with distance ≥ 3, effort 8–9 with distance > 0.5, effort 10, or with
`--buffering 0`, it encodes the whole image at once: 3.6 GB at effort 7,
12.4 GB at effort 8 and above. A scheduled run at d=3 with 30 workers once
asked for about 107 GB on a 64 GB machine, and 314 of its 681 files failed.
Since v2.6.0 the encoder and the recompressor estimate one worker's peak from
the largest image in the batch and the settings, start only as many workers
as fit, and say in the log how many and why. The estimate got closer to the
truth release by release:

- **v2.8.0** — the budget is the smaller of the memory the system can still
  commit and the physical RAM still free (`WORKER_MEMORY_LIMIT`). With a large
  pagefile, the commit limit alone allowed more workers than the RAM held, and
  the run paged to disk.
- **v2.9.0** — the recompressor caps the big encodes rather than the workers
  ([next section](#the-next-photo-prepared-while-the-big-encodes-run)), with
  `WORKER_MEMORY_FRACTION` at 1.0: the estimate of one encode already sits
  about 10 % above its measured peak. The encoder went to 1.0 in v2.19.1.
- **v2.14.0** — a resized copy is budgeted by the size cjxl actually encodes.
  A 2048 px copy of a 45 MP master at d=1 e=9 had been budgeted as a 45 MP
  whole-image encode, about 11 GB: 6 workers on a machine with 42 GB free,
  27 since.
- **v2.21.0** — the encoder builds its PNG in blocks and no longer copies a
  16-bit page, and the cap counts what Python itself holds per worker: 0.96 →
  0.44 GB per worker at d=0.1 on 44 MP photos. The decoder's small preview no
  longer converts the whole image to floating point: 4 workers on 45 MP
  masters went from 9.73 to 4.28 GB.
- **v2.30.0** — the Python share is budgeted per channel, so four-channel scans
  (RGB plus infrared, RGBA) get a true estimate.

The rules and the measurements behind them:
[Memory and --workers](README_jxl_recompressor.md#memory-and---workers).

### The next photo prepared while the big encodes run

On the whole-image settings, the cap of v2.6.0 lowered the workers so that each
could hold one big encode. But a worker spends about a third of each photo
outside cjxl — the djxl decode, the colour conversion or resize, ExifTool, the
checks — and its share of the memory sat idle meanwhile. Measured on a real
run (sRGB copies of 45 MP masters at d=3 e=9, 3 workers): only about 2.2 of the
3 encodes ran at a time on average, with 30–40 GB of RAM free.

Since v2.9.0 the recompressor caps the encodes, not the workers: at most as
many full-size cjxl run at once as fit, and the memory left over pays for extra
workers that prepare the next photos, so an encode slot is fed the moment it
frees up. The same run became 3 encodes plus 3 workers preparing. The worst
case — every slot at its cjxl peak and every extra worker at its own peak —
still fits the budget.

### The checks before a delete, in parallel

With `--delete-source` the encoder decodes every page it wrote and compares
it with the TIFF before deleting the TIFF (`VERIFY_ROUNDTRIP`). Those checks
ran one after the other once the encodes were done: 74 % of a deleting run's
time (131 of 177 s, measured in the 261113 plan). Since v2.31.0 they start at
once in a pool of the run's memory-capped worker count (a check needs less
memory than the encode before it), only for the TIFFs the cheaper checks
before them do not already keep, and the gate reads each result where it used
to run the check: every source is still checked before its own delete, with
the same checks in the same order. A deleting run of six real TIFFs with 6
workers, with ExifTool kept open too: 11.3 → 9.0 s.

### More workers are not always faster

With large photos the curve flattens early. Recompressing 24 files at d=3 e=7:

| Workers | 1 | 4 | 8 | 8, `--buffering 1` | 16, `--buffering 1` |
|---|---|---|---|---|---|
| files/s | 0.23 | 0.50 | 0.61 | 0.68 | 0.73 |

The default ceiling of 16 is generous, and the memory cap lowers it when it
must.

## Fewer processes

Starting a program takes time on Windows — about 0.1 s for each ExifTool — and
a photo passes through several of them. The toolkit starts as few as it can:

- **Versions asked once.** Each tool's version is asked once per run, never
  per photo (v1.8.0); the libjxl version that each file's history records
  comes from that same answer (v2.30.0).
- **Metadata read in batches.** Planning reads the markers of up to 400 files
  per ExifTool call (v2.0.0; the recompressor's encode records the same way
  since v2.1.0).
- **One ExifTool process per photo.** The encoder's metadata writes after cjxl
  (and its reads before them) and the decoder's metadata copy run all their
  commands in one ExifTool process, separated by `-execute`, each command
  keeping its own exit status (v2.30.0). An ExifTool older than 12.15, which
  cannot report a status per command, keeps one process per command. Together
  with the uncompressed PNG: eight varied photos (54 MP in all, 4 workers)
  archived in 6.8 s instead of 12.6 and decoded in 10.5 s instead of 14.2;
  four larger ones (146 MP in all) archived in 9.7 s instead of 18.0.
- **ExifTool kept open.** Since v2.31.0 each script starts ExifTool once
  (`-stay_open`, on Windows) and hands it every per-file command, one at a
  time, instead of starting it for each: about 15 ms a command against 197 ms
  for a fresh process (measured 2026-11-21). A 45 MP two-page TIFF had started
  nine ExifTools, 3.7 of its 8.6 s. The processes are in a Windows Job Object,
  so they end with the script however it ends (ExifTool does not end when its
  input closes); a command it ended without answering is a failure, never sent
  twice. A command that copies XMP fields from a file still gets a process of
  its own: after one, ExifTool writes some XMP namespace prefixes its own way
  in every later command, and a file must come out the same whatever ran
  before it. `EXIFTOOL_STAY_OPEN = False` goes back to a process per command.
  Measured on copies of real photos, one after the other with the code before
  it: four TIFFs archived one call each with every page checked (photo-backup's
  command) 16.9 → 13.2 s; six TIFFs with 6 workers 6.7 → 6.3 s (folders
  without a `%`). A JPEG archived one call each gains nothing: each call is a
  process of its own, and the archive needs almost no ExifTool.
- **One process for many files (`--serve`).** A program that converts its
  files one call at a time (photo-backup) pays, per file, Python's start, the
  imports, the tool checks and a log of its own — more than the work on a
  JPEG. Since v2.31.0 the encoder, the decoder and the transcoder take
  `--serve`: started once, they read one job per line and run each exactly as
  a call of its own, with the kept-open ExifTool shared by all of them.
  Measured on copies of real photos, the same options both ways: 30 JPEGs
  archived losslessly 8.9 → 4.8 s, 4 TIFFs archived with every page checked
  13.5 → 10.8 s; the outputs are the same (the JPEG archives byte for byte,
  the masters' codestreams). photo-backup uses it since its v0.14.0-rc7; its
  own measurement (16 workers, a quiet PC): 160 JPEGs of 24 MP backed up
  20.0 → 18.1 s, 32 TIFFs restored 6.85 → 4.55 s.
- **No new call where one already runs.** The decoder writes its markers in a
  call it already makes: one ExifTool start less per decoded photo (v2.0.0).
- **A few bytes read directly.** The D50 check decides from the ICC profile
  already in memory instead of asking ExifTool which program wrote the TIFF
  (v2.15.0: one call less per TIFF); the transcoder looks for its orientation
  token in the plain XMP box before asking ExifTool (v2.26.1: 0.18 s per
  JXL → JPEG); the HDR gain-map check reads the JPEG's own segments (v2.27.1).
- **Answers kept.** The ICC round-trip test stores its verdict per profile on
  disk, so a profile is tested once, not once per photo (v1.7.1); the master's
  tags a copy needs come from one ExifTool process instead of up to four
  (v2.30.2).

## Less written to disk

A 45 MP 16-bit image is about 270 MB uncompressed. Every temporary copy of it,
and every rewrite of a finished file, costs the write, the read back, and a
disk that is busy for everything else meanwhile. Two releases made the
temporary PNGs cheaper to write; v2.30.2 removed most of the copies and
rewrites.

### Uncompressed temporary PNGs

A PNG that only carries an image from one program to the next does not need
compressing: 16-bit photo data barely compresses, and zlib spent about 6.5 s of
a CPU thread per 45 MP photo on it. v2.23.0 writes ImageMagick's temporary PNG
without compression (sRGB copies of seven 45 MP masters: 124 → 74.6 s with one
worker, 26.7 → 17.9 s with eight; the same pixels), and v2.30.0 does the same
for the PNG the encoder pipes to cjxl (6.55 → 0.40 s of Python per 43 MP
photo).

### Decodes that stay in memory

Several steps decode a whole JXL only to pass it on or to look at it: the
Explorer thumbnail, a resized or colour-converted copy, the decoder's TIFF, the
pixel check before a source is deleted, the JPEG rebuilt from its archive before
the original JPEG is deleted. Each had djxl write its result to the temp folder
for the next step to read back: for a 45 MP photo a 230–270 MB image (the
rebuilt JPEG aside), plus up to 8.6 s of PNG compression inside djxl.

Since v2.30.2 djxl writes the image to its standard output and the script reads
it into memory. It uses the samples there (the pixel check, the decoder's TIFF,
the rebuilt JPEG's checksum), or hands them to ImageMagick raw on its standard
input (`-size WxH -depth N rgb:-`). Raw, because given a PAM, a PNG or a MIFF on
its input, ImageMagick first copies it to a temporary file. ImageMagick starts
once djxl has exited: djxl writes the colour profile after the image. Whatever
the memory route cannot carry exactly keeps the PNG route — a 12-bit or
floating-point image, a conversion to another bit depth, a few colour cases —
and any failure falls back to it.

The copies and the decoder also skip djxl's PNG compression, so they gain on
any disk ([below](#what-it-adds-up-to)). The thumbnail's temporary file was an
uncompressed PAM: its gain is the disk left alone, and it shows on a slow one.
With the temp folder on an SSD that had slowed to about 45 MB/s, the thumbnail's
decode alone held a rebuild of masters to 7 photos a minute, against 34 with the
temp folder on an NVMe drive; six thumbnails took 262.5 s, now 7.4 s. On the
NVMe drive their time did not change.

### The sRGB copy's colour conversion in Python

In an sRGB derivative run (`--output-icc`) ImageMagick took 35 % of the CPU,
and its temporary 16-bit PNG (up to 270 MB per 45 MP master) set the pace of
the whole run when the temp folder was on a slow SSD. Since v2.31.0-rc2, when
the master's profile and the target are both plain matrix/shaper profiles
(gamma or sRGB-form curves: ProPhoto, AdobeRGB, sRGB), the conversion runs in
numpy inside the worker with littleCMS's own math and the PNG goes to cjxl on
its standard input. Three choices were each measured: the target curve is read
from a table indexed by the square root of the linear value (`pow` per sample
made the run slower than ImageMagick); the tables are built once per run and
shared by every worker (per file, 24 workers' copies did not fit in the CPU
cache); the image is converted in chunks of 65 536 pixels (smaller chunks made
the threads wait on the GIL). The result is never more than 1 apart in 65 535
from ImageMagick's on real photos. Other profiles, a resize or a sharpening keep
ImageMagick — and since v2.31.0 ImageMagick's own PNG for cjxl stays in memory
too (`png:-`, then cjxl's input): no `conv.png` in the temp folder. Its pipe is
1 MB: ImageMagick writes in small pieces, and through Windows' default pipe each
piece waited for the reading thread (a 13 MB PNG: 2.0 s against 1.0 s for a
file). Six masters resized to 2048 px, 6 workers: 4.17 s before v2.31.0, 3.74 s
now (−10 %, ExifTool kept open included).
Measured on 60 real masters, sRGB copies at d=0.7 e7 (30 workers
asked, 23–28 after the memory cap), v2.31.0-rc1 and rc2 one after the other:
133 → 123 s with the temp folder on an NVMe drive (−8 %), 176–196 → 121 s on a
SATA SSD (−31 to −38 %), 16 % less CPU; the same quality (40.894 dB against the
reference conversion, both) and the same sizes. ImageMagick's share fell from
1 370 to 210 CPU-seconds (the Explorer thumbnails); cjxl took 9 % more, sharing
the memory and the caches with the conversion.

### A master written once

After cjxl, the encoder adds the metadata with ExifTool, puts the Exif box
before the XMP box (IrfanView reads the EXIF only from a box placed first) and
adds the thumbnail box. ExifTool rewrites the whole file for every command, and
the box reorder and the thumbnail rewrote it again: seven writes, 337 MB, to
append 8 KB of metadata to a 48 MB master. Since
v2.30.2 the same commands run on a stub — cjxl's boxes and the first 4 KB of
the image data — and the master is put together in one pass, boxes in the right
order, thumbnail included. Anything the stub route cannot do falls back to the
old steps on the untouched file. The same stub serves planning
([below](#a-few-kb-per-jxl-while-planning)).

### A decoded TIFF written fewer times

Since v2.30.2 the decoder takes its pixels from memory instead of a 16-bit PNG,
writes the small preview page together with the image instead of rebuilding
the finished TIFF to add it (2 × 251 MB read and written per 45 MP page), and
leaves out two ExifTool clean-ups that found nothing to change. ExifTool writes
a whole temporary copy of a TIFF even for a command that changes nothing —
241 MB and 0.7 s per 45 MP page, each time. The four ExifTool writes left each
change something.

Since v2.31.0 those four are one. They were apart because a command that
writes the XMP as a block (the way it keeps a namespace ExifTool can only read,
such as SilverFast's) ignores every XMP field it also edits. Now the block goes
into a small file of its own first, the edits (the markers, the history, the
orientation) are made there, and one command writes the EXIF, that block and
the IPTC into the TIFF. On 19 real TIFFs decoded by the code before and after,
every page's pixels and every tag are the same (only the preview's offset in
the file moved); with ExifTool kept open, the 19 took 23.3 s instead of 34.3
(4 workers), and six masters with 6 workers 5.1 → 4.2 s.

### What it adds up to

Measured for v2.30.2 against v2.30.1 on one 45 MP photo (a 241 MB TIFF, its
39 MB master at d=0.05), one photo at a time, on an NVMe drive, libjxl 0.13.0,
Explorer thumbnails on. "Written" is what each process wrote, from its own
counters:

| Task | Written to disk | Time |
|---|---|---|
| Archive a TIFF | 520 → 79 MB | — |
| … with `--delete-source` (adds the pixel check) | + 206 → + 0 MB | 17.5 → 8.0 s |
| Resized JPEG copy | 209 → 2.5 MB | 14.1 → 4.5 s |
| Resized JXL copy | 241 → 19 MB | 13.9 → 4.4 s |
| sRGB JXL copy | 734 → 282 MB | 20.2 → 10.7 s |
| Decode to TIFF | 2,376 → 1,205 MB | 15.8 → 5.6 s |

Against the code just before the in-memory decodes, on batches of 99 real
masters with 8 workers (NVMe): resized JPEG copies 137 → 91 s, resized JXL
copies 127 → 80 s, sRGB JXL copies 238 → 165 s; the decoder 72 → 37 s on
19 masters. A rebuild of a real library — 8,438 masters
from their TIFFs — ran about 25 % more photos a minute than the previous full
rebuild, with 15–16 workers instead of 18–19.

## Work a run does not do

### Photos that are up to date are not read

The recompressor's sync used to read every source while planning — its encode
record through ExifTool, its boxes for a JPEG archive — about 0.15–0.25 s per
file on a hard disk, and then skip most of them because their copy was up to
date. Since v2.18.1 those are set aside from the two files' dates alone and
never read:

```text
1290 file(s) already up to date at the destination: skipped without reading them
Planning 65 file(s): reading each one's encode record and checking for a jbrd box — ...
```

A run that makes copies still reads each existing copy's recipe, to redo it
when the distance or the recipe changed; runs that delete, overwrite or work in
place still plan every file, so their checks see everything.

### A few KB per JXL while planning

ExifTool reads a whole JXL to answer any question about it — 50 MB for a 48 MB
master, with `-fast` or without. A sync with nothing to do still read every
master in full while planning: 86 GB for one year of masters. Since v2.30.2 the
planning reads hand ExifTool a stub of each JXL (its metadata boxes, the image
data cut to its first bytes) and map the answers back; anything less than a
clean answer for every stub reads those files themselves, as before. Ten
readers on 99 masters: 36.8 → 12.5 s from the disk cache, and a few MB read
from a hard disk instead of 3 GB.

### A TIFF restored from a backup is not re-encoded

A restored TIFF has a new date and its old bytes, and the date alone made the
encoder's sync encode it again. Since v2.27.0 the sync compares such a TIFF
with the hash its master records (`jxlphoto-srcsum`) and keeps the master when
they match: no re-encode, and no master replaced by a worse one when the
distance changed in between. It costs one read of each such TIFF per run;
since v2.31.0 four TIFFs are read at once (on a hard disk the total speed is
the same, 141–159 MB/s with 1 to 32 readers measured; an SSD, and the antivirus
scan of a file's first open, no longer set the pace one file at a time).

### One decode for the Explorer thumbnail and the page check

With `--verify-roundtrip` and no delete (photo-backup's command), each page is
decoded and compared with its TIFF page before it takes its final name; with
an Explorer thumbnail, the same page was decoded a second time for it. Since
v2.31.0 the thumbnail's decode is kept for the check when the file being
checked holds the very same image data (its codestream's sha256): one decode
per page. Normal runs keep nothing. Since v2.31.0-rc5 the thumbnail is not a
decode at all (next section), and the check decodes once on its own.

### The Explorer thumbnail from the image in memory

Until v2.31.0-rc4 the encoder decoded each new master again, whole, to make its
768 px thumbnail: djxl wrote the full image and ImageMagick read it back to
shrink it, 14–21 % of an archiving run with thumbnails on. Since v2.31.0-rc5 it
is made from the TIFF page the encoder already holds, before cjxl, in the
page's own profile, by the same ImageMagick command; the recompressor's sRGB
copies make theirs from the pixels they have just converted in Python.
Compared with the decode on 73 real outputs: identical wherever the TIFF has a
colour profile. A failure falls back to the decode.

### cjxl's threads shared among the encodes running at once

Each cjxl starts one thread per logical core. With 8 to 16 workers that was
hundreds of threads on 32 cores, waiting on each other. Since v2.31.0-rc5, when
several encodes run at once each gets `--num_threads = max(4, 2 × cores ÷
encodes at once)`; one encode alone keeps every core. The files are byte for
byte the same with any thread count (measured: lossy and lossless,
`--buffering 0`, 16-bit RGB and 8-bit gray).

### The decoder's preview by a table

The 8-bit JPEG preview of a decoded TIFF divided every 16-bit sample by 257 in
floating point. Since v2.31.0-rc5 a table of the 65 536 values gives the same
byte for each: 0.68 → 0.38 s per 45 MP page.

### A script command that does not load the wizard

`python jxl_photo.py encode ARGS` (decode, recompress, transcode) runs that
script as if it was started directly. Until v2.31.0-rc3 the dispatch sat on
the last line of the 11 000-line wizard file, so every such call ran the
wizard's imports and definitions first (rich, the option registry, the
screens) for nothing. Since v2.31.0-rc4 it sits at the top of the file:
35–40 ms less a call (20 interleaved rounds of `--version` with the PC
quiet: encode 413 → 379 ms, transcode 312 → 272 ms). What is left is Python
compiling the whole wizard file before it runs a line of it (about 60 ms: a
script run directly is never read from the bytecode cache, an imported module
is) — started directly, the encoder takes 246 ms and the transcoder 110 ms.
photo-backup starts the scripts with `runpy.run_module` and never paid it.

### Estimates and scans only where they mean something

- The space estimate before a run (it encodes three crops of the photos)
  counts only what the run will write since v2.15.0: a quiet sync skips it.
- The manifest's cross-row collision scan runs only in the modes where two rows
  can write into the same folder (v1.9.1): a manifest of modes 6/7 over a large
  library starts at once.
- The wizard's file count and collision check never start ExifTool or read a
  whole TIFF: they ask each script which files a run takes and where each
  output goes, from the folder listing (v2.30.0). The JPEG transcoder's listing
  takes one `lstat` per file and resolves each folder's real path once
  (v2.30.1): 80 000 files on an SSD counted in 2.4 s for a JXL → JPEG run in
  mode 2, 5.5 s in mode 6.

### A thumbnail a viewer can show without decoding

Not the toolkit's own speed, but a reason it writes something: a lossless JXL
has no small preview inside, so Windows Explorer decodes the whole file for its
thumbnail — about 1.8 s per 45 MP photo on a hard disk. Since v2.16.0 the
encoder stores a 768 px JPEG in a box of its own (in every output when
[jxl-thumb](https://github.com/rsilvabr/jxl-thumb) is installed, in the
lossless ones otherwise). With jxl-thumb, a folder of 100 lossless 45 MP photos
shows in under 2 s instead of 176 s. It costs about 90 KB per file and 1–2 s per
45 MP encode, for a decode that v2.30.2 keeps in memory.

## A long run keeps going

A run that hangs at two in the morning converts nothing until someone notices.
Several changes keep long unattended runs moving:

- **No reader threads** (v2.6.0, v2.6.1). On Windows, `capture_output=True`
  starts two reader threads per call; when memory ran out, a thread could die
  while starting and the call waited forever, before its timeout was armed.
  Every call after the version probes at start-up now captures its output in
  a temporary file, or reads it in the calling thread (the in-memory decodes
  of v2.30.2).
- **Timeouts that fit the work** (v2.7.0, v2.15.0). Per-file ExifTool calls get
  the codec's patience (`EXIFTOOL_TIMEOUT`): a fixed 60–120 s had turned twelve
  good files into errors on a busy hard disk. A planning batch stops only when
  ExifTool goes silent (`EXIFTOOL_BATCH_STALL`), not after a fixed time that a
  slow disk can exceed.
- **Progress while planning** (v2.6.2, v2.7.0, v2.20.0). Planning counts its
  files and estimates the time left, so neither you nor the wizard's watchdog
  takes a slow disk for a hang.
- **A full disk stops the run** (v1.9.0) instead of failing every remaining
  file one by one.

## How a speed change is checked

Faster must never mean different. A change made for speed is checked against
the code before it — the changes of v2.30.0 and v2.30.2 went through all of
this:

- **The same files, byte for byte.** The old and the new version run the same
  scenarios on real photos, in the same folder, with the clock frozen, and
  everything they write is compared: the image data, the boxes and their order,
  every metadata tag, the decoded pixels, the checksum files
  (`tools/side_by_side.py`). The disk changes of v2.30.2 were also run old
  against new on real masters and on lossless round trips.
- **A way back.** The in-memory decodes and the metadata stubs fall back to the
  old route when something is unusual, and the single ExifTool process falls
  back to one process per command when ExifTool cannot report each command's
  result.
- **No check skipped.** The pixel check before a delete still decodes the whole
  image, now in memory; the integrity, provenance and delete checks run as
  before.
- **Hostile paths, real photos.** The tests run in folders named with Japanese
  characters, `[ ]`, spaces and `%`, and the real-photo battery runs before
  every release.

## Not done, on purpose

- **cjxl's output into memory, and the recompressor's second temporary file.**
  Both need a process fed and read at the same time, which takes a second
  thread or risks a deadlock, for a gain of under a second per file on a
  healthy disk. They wait.
- **Effort 9, `--buffering 0` or lossy modular by default.** Measured above:
  more time or more memory, for the same file or a larger one.
