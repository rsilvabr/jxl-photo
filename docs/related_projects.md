# Related projects

## jxl-photo as a Windows program

[jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe) packages this toolkit as `jxl-photo.exe`: no Python to install, and cjxl/djxl, ExifTool, ImageMagick and jxl-thumb come in the same folder. Unzip and double-click for the wizard, or use the same command lines as here (`jxl-photo.exe encode ...`). The five scripts inside are byte-for-byte those of a jxl-photo release, and it passes the same real-photo battery.

This Python version, the one I run on my own archive every day, remains by far the more tested. Use the program if you would rather not install Python; otherwise use the scripts here.

## Fast JXL thumbnails in Explorer

[jxl-thumb](https://github.com/rsilvabr/jxl-thumb) is a Windows thumbnail provider for the archives this toolkit writes (and any other JPEG XL). It reads only the small 1:8 image a lossy JXL stores at the start of the file and converts it to sRGB, so Explorer thumbnails come out about 40× faster than with the Microsoft extension on 25–45 MP masters (the preview pane about 14×), with the right colours for ProPhoto and Adobe RGB. Existing files work as they are; install is per user, no admin rights.

## A simpler, TIFF-only alternative

If this toolkit's setup is more than you want to deal with, [tiff-workflow](https://github.com/rsilvabr/tiff-workflow) is a PowerShell toolkit (with an optional Python wizard UI) that losslessly re-compresses TIFFs with ZIP/Deflate compression — pixel data stays identical, only the compression is re-optimized. It also covers related TIFF chores: copying EXIF from JPEG to TIFF (Fuji S3/S5 Pro workflow), diagnosing padded 16-bit files, and generating colour-managed sRGB thumbnails.

- **What you need:** PowerShell 7 (or Windows PowerShell 5.1), ImageMagick, ExifTool — Python 3.9+ with `rich` only if you want the wizard UI
- **What's NOT needed:** Python or Python packages for the direct PowerShell usage, libjxl (cjxl/djxl)

A 45 MP photo (medians, MB):

| Format | 16-bit | 8-bit |
|--------|:------:|:-----:|
| Uncompressed TIFF | 273 | 136 |
| **ZIP/Deflate (tiff-workflow)** | 227 (~17% smaller) | 61 (~55% smaller) |
| JXL lossless (this toolkit) | 173 (~37% smaller) | 43 (~68% smaller) |
| **JXL `d=0.1` (this toolkit)** | 31 (~89% smaller) | 43 — kept lossless: lossy would save only ~10 MB |

**Trade-off:** easier to install, but much less compression, and the output stays a TIFF. Still better than nothing. Once you are comfortable with ImageMagick and ExifTool, the setup here is the same two tools plus Python and libjxl.

## Selective backup (photo-backup)

[photo-backup](https://github.com/rsilvabr/photo-backup) backs up what this toolkit leaves you with. For every folder that has an export folder it copies the exports (the JXL master instead of the TIFF), the originals of the exported photos (RAW, camera JPEG, sidecars) and your Capture One / NX Studio edits, mirroring the folder tree on the destination. It leaves out the outtakes and the copies jxl-photo can make again from the master (sRGB, phone). The source is only read, nothing on the destination is ever deleted, and a later run can verify that the backup still matches.

It was written around one folder layout: the originals beside an export folder (`_EXPORT`, `Lightroom_Export`, … — any name containing "export"), the exports inside it. Run its dry-run first and read what it would copy; a different layout may need its settings, or the script, adapted.
