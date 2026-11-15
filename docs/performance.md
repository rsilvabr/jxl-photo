# Performance

Converting a photo library means hours of cjxl and djxl, and the toolkit cannot
make libjxl itself faster. What it controls is everything around the codec, and
the aim is simple: almost all of a run's time should go to the codec.

None of it costs anything in the files. Each speed change writes the same files
as the code before it, and every check before a delete still runs.

This page is the summary: where the time went, why, and what changed. Every
change, with the release that made it and what it measured, is in
[Performance in detail](performance_details.md).

## What it adds up to

A few measurements from the releases that made the changes (Windows, real
16-bit photos, mostly 45 MP):

| Task | Before | Now | Since |
|---|---|---|---|
| Eight photos spread over eight folders, 8 workers | 33 s | 10 s | v1.8.2 |
| Archive eight varied photos, 4 workers | 12.6 s | 6.8 s | v2.30.0 |
| Archive one TIFF: written to disk | 520 MB | 79 MB | v2.30.2 |
| Decode one master to TIFF | 15.8 s | 5.6 s | v2.30.2 |
| Resized JPEG copy of one master | 14.1 s | 4.5 s | v2.30.2 |
| Plan a sync of 99 masters: read from a hard disk | 3 GB | a few MB | v2.30.2 |
| Explorer thumbnails of 100 lossless photos (with [jxl-thumb](https://github.com/rsilvabr/jxl-thumb)) | 176 s | under 2 s | v2.16.0 |
| A d=3 run with 30 workers on a 64 GB machine | 314 of 681 files failed | workers capped to what fits | v2.6.0 |

The single-photo rows ran one photo at a time on an NVMe drive. Every number,
with its conditions: [At a glance](performance_details.md#at-a-glance).

## Where the time went, and why

The toolkit glues separate programs together: cjxl and djxl (the codec),
ImageMagick (colour and size), ExifTool (metadata). Gluing programs the simple
way — each one reads a file and writes a file, one call per task — is safe and
easy to get right, and it is where most of the time outside the codec went.

### Every file was written several times

**Why.** ExifTool never edits a file in place: for every command it writes a
complete new copy and then replaces the old one, so a crash can never leave a
half-written file. The encoder needs several commands after cjxl (some must run
in order: a block of metadata copied first, edits on top of it), then it moved
the Exif box in front of the XMP box (IrfanView shows the EXIF only when it
comes first) and added the thumbnail. Seven full rewrites of a 48 MB master,
337 MB written, to add 8 KB of metadata. The decoder had the same habit: it
rewrote a finished 251 MB TIFF to add the small preview page, and ExifTool
copies a whole TIFF even for a command that ends up changing nothing.

**Now.** The metadata lives in small boxes next to the image data, so ExifTool
works on a stub — the boxes plus the first 4 KB of the image — and the master
is assembled once at the end, boxes in the right order, thumbnail included. The
decoder writes the preview together with the image and runs only the ExifTool
commands that change something.

**What stays.** ExifTool itself: it is what writes EXIF and XMP correctly, and
replacing it with home-made code would put the metadata at risk. If the stub
route meets anything unusual, the old steps run on the untouched file.
→ [Less written to disk](performance_details.md#less-written-to-disk)

### Images went through temporary files

**Why.** Programs that run separately usually pass an image as a file: one
writes it, the next reads it back. For a 45 MP 16-bit photo that is 230–270 MB
written and read for each hand-over. Worse, those PNGs were compressed: zlib
spent about 6.5 s of CPU per photo squeezing data that barely compresses (the
fine noise of a 16-bit photo), only for the next program to unpack it seconds
later. And there is a trap: ImageMagick needs the colour profile, which djxl
writes only after the image, and given a PNG on its input ImageMagick copies it
to a temporary file anyway.

**Now.** cjxl gets the image on its input, straight from memory. djxl's
full-size decodes (the Explorer thumbnail, resized and colour-converted copies,
the decoded TIFF, the pixel check before a delete) come back into memory; once
djxl has finished, ImageMagick gets the raw pixel values, which it does not
copy. A temporary PNG that is still needed is stored uncompressed.

**What stays.** A 12-bit or floating-point image, which raw values cannot carry
exactly, keeps the temporary-file route, and so does anything that fails on the
memory route. cjxl still writes its output as a file: reading it into memory
while feeding cjxl its input would need a second thread or risk both programs
waiting on each other forever, for under a second per photo.
→ [Decodes that stay in memory](performance_details.md#decodes-that-stay-in-memory)

### Too many programs started

**Why.** Starting a program on Windows takes time — about 0.1 s for ExifTool,
which loads its whole Perl interpreter each time — and a photo needed several:
one ExifTool per metadata command, one per question while planning.

**Now.** Planning asks about up to 400 files per ExifTool call; all of a photo's
metadata commands share one ExifTool process; each tool's version is asked once
per run; and small things (the colour profile, the orientation, an HDR gain map)
are read straight from the file's bytes.

**What stays.** An ExifTool older than 12.15 keeps one process per command: it
cannot report each command's result separately, and a failed write must never
pass for a good one.
→ [Fewer processes](performance_details.md#fewer-processes)

### Whole files read to answer small questions

**Why.** ExifTool reads the whole file to answer any question about a JXL, even
in its "fast" mode. A sync with nothing to do still read every master in full
while planning: 86 GB for one year of masters, just to learn that nothing had
changed.

**Now.** Photos whose output is newer are set aside by their dates alone,
before anything reads them. The rest are planned from the same few-KB stubs. A
TIFF restored from a backup (new date, same bytes) is recognised by its
checksum and not re-encoded.

**What stays.** A run that deletes, overwrites or works in place still reads
every file it plans: its safety checks must see everything.
→ [Work a run does not do](performance_details.md#work-a-run-does-not-do)

### Cores sat idle, or memory ran out

**Why.** Up to v1.8.1 each output folder got its own group of workers, and the
next folder waited until the last photo of the previous one was done: with one
folder per shoot, most workers sat idle much of the time (about 4 % CPU with 12
workers). The opposite problem: at some settings cjxl encodes the whole image
at once, up to 12.4 GB for one 45 MP photo, and too many workers ran the
machine out of memory — files failed.

**Now.** One pool of workers serves the whole run. Before starting, the script
estimates one worker's peak from the largest photo and the settings and starts
only as many as fit. The recompressor limits the big encodes instead and spends
the memory left over on workers that prepare the next photos, so the codec
never waits for them.

**What stays.** The peak cannot be known exactly in advance, so the estimate
keeps a margin of about 10 %. And more workers stop paying with large photos:
the disks and the memory become the limit (recompressing large photos: 0.68
photos/s with 8 workers, 0.73 with 16).
→ [Keeping every core busy](performance_details.md#keeping-every-core-busy)

### Long runs could stop for nothing

**Why.** On Windows, the usual way to collect a program's output starts two
helper threads per call. When memory ran short, one could fail to start and the
call waited forever, before its timeout even began. Fixed timeouts on ExifTool
also turned good files into errors on a busy hard disk, and a long silent
planning step looked like a hang.

**Now.** Output goes to a temporary file or is read in the same thread;
timeouts follow the work (a planning batch stops only when ExifTool goes
silent); planning shows its progress; a full disk stops the run instead of
failing every file left.
→ [A long run keeps going](performance_details.md#a-long-run-keeps-going)

## What cannot get faster

- **The codec.** Encoding and decoding take what libjxl takes. The toolkit
  chooses the cheaper settings that give the same file: effort 7, cjxl's
  streaming encode. Effort 9 on a 45 MP photo gives the same size and quality
  up to `d=0.5`, for up to 16× the time.
- **Reading each TIFF once.** Every byte has to come from the disk.
- **The checks before a delete.** The pixel check decodes the whole image every
  time: a check skipped to save time is not a check. It now decodes in memory.

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

The staging folder is there for the disks: with it on an SSD (`--staging`), the
library's hard disk mostly reads, and the new files reach it in bulk moves
instead of small writes scattered between the reads
([Staging](performance_details.md#staging-read-from-one-disk-write-to-another)).

## Faster never means different

A change made for speed runs old against new on real photos, in folders with
Japanese characters, brackets and `%` in their names, and everything both
versions write is compared byte for byte: image data, boxes, every metadata
tag, decoded pixels. Each new route falls back to the old one when something is
unusual. → [How a speed change is checked](performance_details.md#how-a-speed-change-is-checked)

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

What was measured and left out on purpose:
[Not done, on purpose](performance_details.md#not-done-on-purpose).
