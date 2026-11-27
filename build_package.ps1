$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstaller)) {
    throw "Khong tim thay PyInstaller tai $PyInstaller"
}

$BuildStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$TempWork = Join-Path $env:TEMP "vpd_pyi_work_$BuildStamp"
$TempDist = Join-Path $env:TEMP "vpd_pyi_dist_$BuildStamp"
New-Item -ItemType Directory -Force -Path $TempWork, $TempDist | Out-Null

try {
    Write-Host "==> Dang dong goi ung dung portable PersonVisionAI..." -ForegroundColor Cyan
    & $PyInstaller --noconfirm --clean --workpath $TempWork --distpath $TempDist build.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller that bai voi ma loi $LASTEXITCODE"
    }

    $BuiltApp = Join-Path $TempDist "PersonVisionAI"
    if (-not (Test-Path (Join-Path $BuiltApp "PersonVisionAI.exe"))) {
        throw "Khong tim thay PersonVisionAI.exe sau khi build!"
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $BuiltApp "data") | Out-Null

    $Readme = @"
=== PERSONVISION AI - PHAT HIEN NGUOI TRONG VIDEO (BAN PORTABLE) ===

CACH CHAY TREN MAY KHAC:
1. Copy toan bo thu muc "PersonVisionAI" (hoac giai nen file PersonVisionAI_Portable.zip) sang may Windows 10/11 khac.
2. Mo thu muc va chay file: PersonVisionAI.exe
   (Khong can cai dat Python, FFmpeg hay thu vien nao khac).

LUU TRU DU LIEU & LICH SU:
- Toan bo lich su quet va anh chup nguoi duoc luu trong thu muc "data" nam canh PersonVisionAI.exe.
- Khi ban copy thu muc nay sang may khac, lich su va anh chup se di kem cung ung dung.
- Neu video goc tren may moi nam o duong dan / o dia khac, trong trinh phat hay nhan "Chon lai file video..." de cap nhat vi tri moi.

YEU CAU HE THONG:
- Windows 10 / Windows 11 (64-bit).
- Ho tro WebView2 (co san tren hau het Windows 10/11; neu may cu khong co, ung dung tu dong mo bang trinh duyet Edge/Chrome).
"@
    Set-Content -Path (Join-Path $BuiltApp "HUONG_DAN_SU_DUNG.txt") -Value $Readme -Encoding UTF8

    $FinalDistRoot = Join-Path $Root "dist"
    New-Item -ItemType Directory -Force -Path $FinalDistRoot | Out-Null
    $ZipPath = Join-Path $FinalDistRoot "PersonVisionAI_Portable.zip"
    if (Test-Path $ZipPath) {
        Remove-Item $ZipPath -Force
    }
    Write-Host "==> Dang nen file ZIP de de dang chuyen sang may khac..." -ForegroundColor Cyan
    Compress-Archive -Path $BuiltApp -DestinationPath $ZipPath -CompressionLevel Optimal

    $FinalDistApp = Join-Path $FinalDistRoot "PersonVisionAI"
    if (-not (Test-Path $FinalDistApp)) {
        Copy-Item -Path $BuiltApp -Destination $FinalDistApp -Recurse -Force
    } else {
        # Giu nguyen thu muc data cua nguoi dung khi cap nhat thu muc dist
        $running = Get-Process -Name "PersonVisionAI*" -ErrorAction SilentlyContinue | Where-Object {
            $_.Path -and $_.Path.StartsWith($FinalDistApp, [System.StringComparison]::OrdinalIgnoreCase)
        }
        if ($running) {
            Write-Host "CANH BAO: PersonVisionAI.exe dang mo nen khong the ghi de truc tiep thu muc dist\PersonVisionAI." -ForegroundColor Yellow
            Write-Host "File ZIP moi da duoc cap nhat xong tai: $ZipPath" -ForegroundColor Green
            Write-Host "Hay dong app dang mo va chay lai script hoac giai nen file ZIP de dung ban moi nhat." -ForegroundColor Yellow
        } else {
            & robocopy $BuiltApp $FinalDistApp /MIR /XD data /R:2 /W:1 | Out-Null
            if ($LASTEXITCODE -ge 8) {
                throw "Robocopy cap nhat dist that bai (code $LASTEXITCODE)"
            }
        }
    }

    Write-Host "==> HOAN TAT!" -ForegroundColor Green
    Write-Host "Thu muc chay truc tiep: $FinalDistApp"
    Write-Host "File ZIP mang sang may khac: $ZipPath"
}
finally {
    Remove-Item -Recurse -Force $TempWork, $TempDist -ErrorAction SilentlyContinue
}
