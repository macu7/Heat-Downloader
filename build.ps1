# Build a one-file HeatDownloader.exe and a release zip with heat_probes.json.
# Usage:  powershell -File .\build.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Installing build dependencies..."
py -m pip install -q -r requirements.txt pyinstaller

if (-not (Test-Path "heat_probes.json")) {
    throw "heat_probes.json is required for the release package"
}

Write-Host "Building HeatDownloader.exe..."
py -m PyInstaller --noconfirm --clean HeatDownloader.spec

$exe = Join-Path $PSScriptRoot "dist\HeatDownloader.exe"
if (-not (Test-Path $exe)) {
    throw "Build failed: $exe not found"
}

Copy-Item -Force "heat_probes.json" (Join-Path $PSScriptRoot "dist\heat_probes.json")

$zip = Join-Path $PSScriptRoot "dist\HeatDownloader-windows.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path @(
    (Join-Path $PSScriptRoot "dist\HeatDownloader.exe"),
    (Join-Path $PSScriptRoot "dist\heat_probes.json")
) -DestinationPath $zip

$size = (Get-Item $exe).Length / 1MB
Write-Host "OK: $exe ($([math]::Round($size, 1)) MB)"
Write-Host "OK: $zip"
Write-Host "Run with: .\dist\HeatDownloader.exe"
