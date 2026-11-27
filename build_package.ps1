$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstaller)) {
    throw "PyInstaller not found at $PyInstaller"
}

$BuildStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$TempWork = Join-Path $env:TEMP "vpd_pyi_work_$BuildStamp"
$TempDist = Join-Path $env:TEMP "vpd_pyi_dist_$BuildStamp"
New-Item -ItemType Directory -Force -Path $TempWork, $TempDist | Out-Null

try {
    Write-Host "==> Packaging portable Windows executable PersonVisionAI..." -ForegroundColor Cyan
    & $PyInstaller --noconfirm --clean --workpath $TempWork --distpath $TempDist build.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $BuiltApp = Join-Path $TempDist "PersonVisionAI"
    if (-not (Test-Path (Join-Path $BuiltApp "PersonVisionAI.exe"))) {
        throw "PersonVisionAI.exe not found after build!"
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $BuiltApp "data") | Out-Null

    $Readme = @"
=== PERSONVISION AI - VIDEO PERSON DETECTOR (PORTABLE EDITION) ===

HOW TO RUN ON ANY WINDOWS PC:
1. Copy the entire "PersonVisionAI" folder (or extract PersonVisionAI_Portable.zip) to any Windows 10/11 PC.
2. Open the folder and double-click: PersonVisionAI.exe
   (No Python, OpenCV, FFmpeg, or external AI model downloads required.)

PORTABLE DATA & HISTORY:
- Scan history (SQLite) and captured person snapshots are stored in the "data" folder next to PersonVisionAI.exe.
- When you move the folder to another PC, your scan history and snapshots stay intact.
- If original video files moved to a different drive or path on the new PC, click "Relocate Video File..." inside the video player to remap them automatically.

SYSTEM REQUIREMENTS:
- Windows 10 / Windows 11 (64-bit).
- Microsoft Edge WebView2 Runtime (preinstalled on most Windows 10/11 systems; falls back automatically to Edge/Chrome app mode if absent).
"@
    Set-Content -Path (Join-Path $BuiltApp "USER_GUIDE.txt") -Value $Readme -Encoding UTF8

    $FinalDistRoot = Join-Path $Root "dist"
    New-Item -ItemType Directory -Force -Path $FinalDistRoot | Out-Null
    $ZipPath = Join-Path $FinalDistRoot "PersonVisionAI_Portable.zip"
    if (Test-Path $ZipPath) {
        Remove-Item $ZipPath -Force
    }
    Write-Host "==> Compressing portable distribution archive..." -ForegroundColor Cyan
    Compress-Archive -Path $BuiltApp -DestinationPath $ZipPath -CompressionLevel Optimal

    $FinalDistApp = Join-Path $FinalDistRoot "PersonVisionAI"
    if (-not (Test-Path $FinalDistApp)) {
        Copy-Item -Path $BuiltApp -Destination $FinalDistApp -Recurse -Force
    } else {
        $running = Get-Process -Name "PersonVisionAI*" -ErrorAction SilentlyContinue | Where-Object {
            $_.Path -and $_.Path.StartsWith($FinalDistApp, [System.StringComparison]::OrdinalIgnoreCase)
        }
        if ($running) {
            Write-Host "WARNING: PersonVisionAI.exe is currently running, skipping overwrite of dist\PersonVisionAI." -ForegroundColor Yellow
            Write-Host "Updated portable ZIP is ready at: $ZipPath" -ForegroundColor Green
        } else {
            & robocopy $BuiltApp $FinalDistApp /MIR /XD data /R:2 /W:1 | Out-Null
            if ($LASTEXITCODE -ge 8) {
                throw "Robocopy dist update failed (exit code $LASTEXITCODE)"
            }
        }
    }

    Write-Host "==> BUILD COMPLETE!" -ForegroundColor Green
    Write-Host "Executable folder: $FinalDistApp"
    Write-Host "Portable ZIP archive: $ZipPath"
}
finally {
    Remove-Item -Recurse -Force $TempWork, $TempDist -ErrorAction SilentlyContinue
}
