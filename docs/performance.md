# Performance

How jxl-photo keeps a large photo library moving: the design it started with,
what each release changed since, and what each change measured. The settings
that matter for speed are [at the end](#settings-that-matter-for-speed).

Most of the time of a conversion should go to the codec — cjxl encoding, djxl
decoding — and the toolkit cannot make libjxl itself faster. What it controls
is everything around the codec:

- **Every core busy.** Several photos at once, one pool of workers for the
  whole run, as many workers as the memory holds, and the next photo prepared
  while a big encode runs.
- **The disks out of the way.** Images travel between the programs in memory
  instead of temporary files, outputs can be written to an SSD while the
  photos are read from a hard disk, and a new master is written once.
- **Few processes.** Up to 400 files per ExifTool call while planning, one
  ExifTool process for all of a photo's metadata, and a few bytes read
  directly where starting a tool would cost more.
- **No work a run does not need.** A re-run redoes only what changed, and
  photos that are up to date are not even read.

None of it at the cost of the files. Each change below writes the same files
as the code before it, unless its release notes say otherwise, and every check
before a delete still runs — some of them faster.

The numbers come from the release that made each change (the full detail is in
[version_history.md](version_history.md)), measured on Windows on real 16-bit
camera exports and film scans, mostly of 45 MP. Disks, core counts and libjxl
versions move them: read them as orders of magnitude.

## One photo, start to finish

What happens to a TIFF on its way to a master, and the release each step comes
from:

```text
TIFF on the library disk
 │  read once (tifffile)
 ▼
16-bit PNG in memory, stored uncompressed ──► cjxl, on its stdin    v1.7.0, v2.30.0
                                               │
                                               ▼
                                 JXL in the staging folder (SSD)    v1.0
 │
 ├─ metadata: every ExifTool command in one process,
 │  run on a small stub of the JXL, not on the JXL itself           v2.30.0, v2.30.2
 ├─ Explorer thumbnail: djxl → memory → ImageMagick (raw samples)   v2.30.2
 ▼
master written once: metadata boxes, thumbnail box, image data      v2.30.2
 │
 ▼
integrity check → out of staging with its folder
 │
 ▼
checks before a delete: the pixel check decodes in memory           v2.30.2
```

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
distance changed in between. It costs one read of each such TIFF per run.

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

## Settings that matter for speed

| Setting | What to do |
|---|---|
| `--workers` | Keep the default (one per CPU thread, at most 16); the memory cap lowers it when it must. With large photos, more than about 8 gains little. |
| `--staging` / `TEMP2_DIR` | Point it at an SSD when the photos are on a hard disk. |
| `TEMP_DIR` | A fast local drive. Since v2.30.2 full-size images rarely go there, but argfiles, stubs and the fallback PNGs still do. |
| Free space | Leave room on the staging and temp SSD: a nearly full SSD can write at a fraction of its usual speed. |
| `--effort` | 7. Effort 9 pays on small images (resized copies); on 45 MP photos it gives the same size and quality from `d=0.05` to `d=0.5`, and costs up to 16× the time at `d=1`. |
| `--buffering` | Leave it to cjxl. When the log says the memory cap lowered your workers on a whole-image setting, `--buffering 1` at effort 7 costs about 1.5 % in size and nothing measurable in quality ([measured](README_jxl_recompressor.md#streaming-vs-whole-image-what---buffering-1-costs-measured)); at effort 8–9 it gives effort 7's file, so use effort 7. |
| Re-runs | Let the sync decide; `--overwrite` redoes everything. |
| `USE_RAM_FOR_PNG` | Keep it on; `--no-ram` only on a machine short of memory. |
