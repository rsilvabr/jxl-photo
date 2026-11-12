# Installing jxl-photo (the Python version)

> Rather not install Python and the tools? A Windows program packages
> everything: see [jxl-photo-exe](related_projects.md#jxl-photo-as-a-windows-program).

### 1. Python 3.9+ and packages

```powershell
pip install tifffile numpy pillow rich imagecodecs
```

Install them in the same Python you will run the scripts with.

### 2. External tools (download the executables, not the source code)

| Tool | Download URL | What to download | Extract to |
|------|-------------|------------------|------------|
| **cjxl / djxl** | https://github.com/libjxl/libjxl/releases | `jxl-x64-windows-static.zip` (**not** `jxl-x64-windows.zip`, which has only DLLs) | `C:\tools\libjxl\` or your choice |
| **exiftool** | https://exiftool.org | `exiftool-XX.XX_64.zip` (**not** the `.tar.gz`, which is Perl source) | `C:\tools\exiftool\` or your choice |
| **ImageMagick** | https://imagemagick.org | Installer `.exe` (Q16-HDRI x64) | Default location |

The scripts find exiftool under either name, `exiftool.exe` or the download's `exiftool(-k).exe`.

`jxlinfo.exe` comes in the same libjxl zip, next to `cjxl.exe` and `djxl.exe`: keep the three together. The scripts ask it whether a JXL holds float or animated content (and came from a rotated JPEG); without it they warn once, skip the float/animation check and read the orientation from the JXL's header themselves.

Tested with libjxl 0.12.0 (0.11.2 still works), exiftool 13.59, ImageMagick 7.1.2-27, numpy 2.5.1, tifffile 2026.6.1, Pillow 12.3.0, imagecodecs 2026.6.26 and rich 15.0.0. The scripts detect the libjxl version and adapt. A high `--workers` is safe: the encoder and the recompressor run only as many cjxl at once as the free memory holds ([how much each takes](README_jxl_tiff_encoder.md#ram-per-worker)).

### 3. Add to PATH (PowerShell)

**Replace the example paths below with YOUR actual installation paths:**

```powershell
# EDIT THESE PATHS to match where YOU extracted the tools:
$myPaths = @(
    "C:\tools\libjxl\bin",                           # where cjxl.exe, djxl.exe and jxlinfo.exe are
    "C:\tools\exiftool",                              # where exiftool.exe is
    "C:\Program Files\ImageMagick-7.1.1-Q16-HDRI"     # where magick.exe is
)

# Add to user PATH
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", ($myPaths -join ";") + ";$p", "User")

# RESTART your PowerShell/terminal after this!
```

### 4. Verify

Restart PowerShell, then:

```powershell
cjxl --version          # cjxl v0.XX.X
jxlinfo -h              # prints its usage (it comes in the same zip)
exiftool -ver           # 12.XX or 13.XX
magick -version         # ImageMagick version
python -c "import tifffile, numpy, PIL, rich, imagecodecs; print('All Python packages OK')"
py jxl_photo.py         # from the repository folder
```

You should see: `[✓] cjxl/djxl | [✓] exiftool | [✓] magick | [✓] tifffile | [✓] pillow | [✓] imagecodecs | [✓] rich`

### Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| `cjxl` not recognized | Downloaded `jxl-x64-windows.zip` (runtime DLLs only) | Download `jxl-x64-windows-static.zip` |
| `exiftool` not recognized at the prompt | Its folder is not on PATH, or the file is still named `exiftool(-k).exe` — the scripts accept that name, your shell does not | Add the folder to PATH (step 3) and reopen the terminal; rename or copy to `exiftool.exe` if you want the bare command to work too |
| `exiftool` returns nothing | Downloaded `.tar.gz` (Perl source) | Download `.zip` with `_64` suffix |
| `jxl_photo_common.py is missing next to ...` or `jxl_photo_common.py is version ... and ... is ...` | Only some of the files were copied, or files from two releases were mixed | Keep every `.py` file of one release together in the same folder: the five scripts and `jxl_photo_common.py`, which holds the code they share |
| `ModuleNotFoundError` | Packages in different Python version | Run `python -m pip install tifffile numpy pillow rich imagecodecs` with the Python you run the scripts with |
| PATH not working | Terminal not restarted | Close and reopen PowerShell completely |

Setup feels heavy? [tiff-workflow](related_projects.md#a-simpler-tiff-only-alternative) needs no libjxl at all.
