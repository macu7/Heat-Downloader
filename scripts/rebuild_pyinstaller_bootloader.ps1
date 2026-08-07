# Rebuild PyInstaller's Windows bootloader from source so the EXE does not
# share the widely fingerprintable prebuilt bootloader used by malware.
# Requires: git, MSVC build tools (present on GitHub windows-latest), Python.
# Usage:  powershell -File .\scripts\rebuild_pyinstaller_bootloader.ps1

$ErrorActionPreference = "Stop"

python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed in the active Python environment"
}

$ver = python -c "import PyInstaller; print(PyInstaller.__version__)"
Write-Host "Rebuilding PyInstaller $ver bootloader from source..."

$src = Join-Path ([System.IO.Path]::GetTempPath()) "pyinstaller-src-$ver"
if (Test-Path $src) {
    Remove-Item -Recurse -Force $src
}

git clone --depth 1 --branch "v$ver" https://github.com/pyinstaller/pyinstaller.git $src
if ($LASTEXITCODE -ne 0) {
    throw "Failed to clone PyInstaller v$ver source"
}

Push-Location (Join-Path $src "bootloader")
try {
    python ./waf distclean all --target-arch=64bit
    if ($LASTEXITCODE -ne 0) {
        throw "Bootloader rebuild failed"
    }
}
finally {
    Pop-Location
}

# PyInstaller 6.x installs into ../PyInstaller/bootloader/Windows-64bit-intel
# (older trees used Windows-64bit). Prefer the package tree, then fall back.
$candidates = @(
    (Join-Path $src "PyInstaller\bootloader\Windows-64bit-intel"),
    (Join-Path $src "PyInstaller\bootloader\Windows-64bit"),
    (Join-Path $src "bootloader\Windows-64bit-intel"),
    (Join-Path $src "bootloader\Windows-64bit")
)

$built = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $built) {
    $bootloaderRoot = Join-Path $src "PyInstaller\bootloader"
    $listing = if (Test-Path $bootloaderRoot) {
        (Get-ChildItem $bootloaderRoot -Directory | ForEach-Object { $_.Name }) -join ", "
    } else {
        "(missing PyInstaller/bootloader)"
    }
    throw "Rebuilt bootloader directory not found. Saw: $listing"
}

$runExe = Join-Path $built "run.exe"
if (-not (Test-Path $runExe)) {
    throw "Rebuilt bootloader incomplete (run.exe missing in $built)"
}

$destRoot = python -c "import PyInstaller, pathlib; print(pathlib.Path(PyInstaller.__file__).resolve().parent / 'bootloader')"
$destName = Split-Path -Leaf $built
$dest = Join-Path $destRoot $destName

# Also refresh the legacy folder name if the installed package still uses it.
$legacyDest = Join-Path $destRoot "Windows-64bit"

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Force (Join-Path $built "*") $dest
if ($destName -ne "Windows-64bit") {
    New-Item -ItemType Directory -Force -Path $legacyDest | Out-Null
    Copy-Item -Force (Join-Path $built "*") $legacyDest
}

Write-Host "Installed rebuilt bootloader from $built into $dest"
Get-ChildItem $dest | ForEach-Object { Write-Host "  $($_.Name)  $($_.Length) bytes" }
