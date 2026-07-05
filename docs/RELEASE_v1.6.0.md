# v1.6.0 — Audit-Driven Fixes & Wrapper Hardening

**Release date:** 2026-07-05  
**Commit:** `f5006b3`

This release is the result of an independent audit on top of v1.5.3. It fixes critical concurrency and routing issues, hardens the wrapper/scripts integration, and cleans up edge cases in ICC handling and file matching.

---

## 🐛 Critical Fixes

- **Fixed encoder staging `status_map` crossing over under concurrency**  
  With `workers > 1`, the positional `zip(tasks, results)` mapped statuses to the wrong files. Failed files could be delivered; successful files could be withheld. Now uses the same key-based mapping as the decoder/transcoder.

- **Fixed staging move logic to never promote failed outputs**  
  Staging files are now moved to the final destination only when the worker status is a success. Corrupt/partial outputs stay in staging.

- **Added confirmation prompt to `cmd_auto --delete-source`**  
  Previously enabled source deletion would run without any confirmation.

- **Fixed Basic mode 16-bit PNG decode**  
  `imagecodecs.png_decode(str(path))` downgraded 16-bit PNGs to 8-bit. Switched to the bytes API for full 16-bit fidelity.

- **Fixed Matrix mode `--color_space` token for libjxl v0.11.x**  
  Changed to the token recognized by `cjxl`/`djxl` v0.11.x.

---

## 🔧 Wrapper / Auto Mode

- **Fixed `cmd_auto` routing JPEG folders to encode instead of decode**  
  JPEG-only directories were routed through `djxl`, producing `can't decode to .jxl`. They now go through `cjxl --lossless_jpeg=1`.

- **Fixed `--format jpeg` in auto mode**  
  Removed double-dot filenames (`name..jpg`) and fixed the default bit depth forcing a silent fallback to PNG.

- **Fixed mode 2 `output_dir` handling**  
  Standardized how the wrapper passes the parent output directory to all worker scripts.

- **Propagated `--export-marker` and made matching case-insensitive**  
  The wrapper setting is now passed to encoder/decoder/transcoder, and marker comparisons are case-insensitive throughout.

- **Preserved manifest modes 6/7 via new `Mode` column**  
  Generated manifests now include `Source,Destination,Mode`. This allows modes 6 and 7 to survive manifest execution.

  > **Note:** Manifests generated before v1.6.0 used only `Source,Destination` columns. Those older manifests may fall back to mode 0; regenerate them for full compatibility.

- **Removed non-functional "skip" option from wizard**  
  Existing-file handling is now `overwrite` or `sync` only, matching the CLI flags.

- **Fixed custom Target ICC prompt in advanced JXL→TIFF flow**  
  Selecting `custom` no longer passes the literal string `"custom"` to the decoder; the wizard now asks for the actual ICC file path.

---

## 🎨 Color / ICC

- **Removed non-functional Adobe RGB / ProPhoto RGB aliases**  
  Pillow's `ImageCms.createProfile()` only supports `sRGB`, `Lab`, and `XYZ`. The aliases were advertised but always failed. Only `sRGB` remains as a built-in alias; other profiles must be supplied as file paths.

---

## 📊 Logging / Stats

- **Fixed D50 patch statistics**  
  The summary no longer double-counts already-correct files as "needed patch".

- **Suppressed spurious "KEEP in staging (skipped)" warnings**  
  Skipped files never write to staging, so they no longer trigger staging warnings.

---

## 🛡️ Safety

- **Reject CMYK TIFFs early**  
  CMYK 4-channel TIFFs were silently encoded as RGBA. The encoder now aborts with a clear `CMYK TIFFs are not supported` error.

- **Fixed extended-size JXL box validation guard**  
  The `size == 1` extended-size branch was unreachable because the preceding guard rejected `size < 8`. Now allows extended-size boxes to be parsed correctly.

---

## 📈 Stats

- **Total documented fixes:** 103  
- **Full changelog:** [`docs/bug_tracking_since_v1.0.md`](bug_tracking_since_v1.0.md)

---

## Release History

| Version | Date | Highlights |
|---------|------|------------|
| **v1.6.0** | 2026-07-05 | Audit-driven fixes: staging concurrency, wrapper routing, manifest Mode column, CMYK rejection, ICC alias cleanup (103 fixes) |
| v1.5.3 | 2026-04-14 | Documentation & minor fixes: OVERWRITE log accuracy, PIL pixel limit, 8-bit PNG scaling, exiftool(-k).exe detection |
| v1.5.2 | 2026-04-13 | Critical fix: 8-bit TIFF → JXL black images |
| v1.5.1 | 2026-04-12 | Refinements and stability: cmd_auto handling, _EXPORT matching, MD5 staging, Matrix ICC parsing |
| v1.5 | 2026-04-11 | Stability & reliability: full AUTO mode, PNG bit depth, ICC with --no-ram, Mode 1 dirs, EXIF preservation |
| v1.4 | 2026-04-11 | JXL→JPEG workflow: lossy/lossless conversion modes, repeat workflow fixes |
| v1.3 | 2026-04-11 | Auto Mode (Beta), Manifest System, improved TIFF preview, Python 3.8+ support |
| v1.2 | 2026-04-05 | Basic mode preserves djxl ICC, None mode, ICC mode selector, full English codebase |
| v1.1 | 2026-04-04 | D50 patch modes, metadata strip, race condition / deadlock / PPM truncation fixes |
| v1.0 | 2026-04-02 | First stable release — TIFF ↔ JXL and JPEG ↔ JXL with ICC preservation |
