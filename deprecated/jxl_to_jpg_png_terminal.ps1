# ── SETTINGS ──────────────────────────────────────────────────────
$OutputFolderName  = "jpeg-srgb"   # output subfolder name
$Quality           = 95             # JPEG quality (1-100)
$Workers           = 8              # parallel workers
$Overwrite         = $false         # $true = overwrite existing files
$RenameFrom        = "ProPhotoRGB"  # string to replace in filename ("" = disabled)
$RenameTo          = "sRGB"         # replacement string
# ──────────────────────────────────────────────────────────────────

$root = $PWD.Path
$jxls = Get-ChildItem -LiteralPath $root -Filter "*.jxl" -File

if ($jxls.Count -eq 0) {
    Write-Host "No JXL files found in: $root"
    return
}

$outDir = Join-Path $root $OutputFolderName
[System.IO.Directory]::CreateDirectory($outDir) | Out-Null

Write-Host "JXLs found: $($jxls.Count) | Output: $outDir | Quality: $Quality"

$counter = 0
$total   = $jxls.Count

$jxls | ForEach-Object -Parallel {
    $jxl        = $_.FullName
    $name       = $_.Name
    $stem       = $_.BaseName
    $outDir     = $using:outDir
    $quality    = $using:Quality
    $overwrite  = $using:Overwrite
    $renameFrom = $using:RenameFrom
    $renameTo   = $using:RenameTo

    # Apply filename rename if configured
    $outStem = if ($renameFrom -and $stem.Contains($renameFrom)) {
        $stem.Replace($renameFrom, $renameTo)
    } else { $stem }

    $outFile = Join-Path $outDir "$outStem.jpg"

    if ((Test-Path -LiteralPath $outFile) -and -not $overwrite) {
        return "SKIP | $name"
    }

    # djxl → temp PNG → magick → sRGB JPEG
    $tmpPng = [System.IO.Path]::GetTempFileName() + ".png"
    $r1 = Start-Process -FilePath "djxl" -ArgumentList "`"$jxl`"", "`"$tmpPng`"" -NoNewWindow -PassThru -Wait
    if ($r1.ExitCode -ne 0) {
        Remove-Item $tmpPng -Force -ErrorAction SilentlyContinue
        return "ERROR (djxl) | $name"
    }

    $r2 = Start-Process -FilePath "magick" -ArgumentList "`"$tmpPng`"", "-colorspace", "sRGB", "-depth", "8", "-quality", "$quality", "`"$outFile`"" -NoNewWindow -PassThru -Wait
    Remove-Item $tmpPng -Force -ErrorAction SilentlyContinue

    if ($r2.ExitCode -ne 0) { return "ERROR (magick) | $name" }
    return "OK | $name → $outStem.jpg"

} -ThrottleLimit $Workers | ForEach-Object {
    $counter++
    Write-Host "[$counter/$total] $_"
}

Write-Host "`nDone. JPEGs in: $outDir"
