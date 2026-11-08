[CmdletBinding()]
param([switch]$Dev)
$ErrorActionPreference = 'Stop'
$manifest = Join-Path $PSScriptRoot 'plugin\rsblender\Cargo.toml'
$profile = if ($Dev) { 'debug' } else { 'release' }
$cargoArgs = @('build', '--locked', '--manifest-path', $manifest)
if (-not $Dev) { $cargoArgs += '--release' }
& cargo @cargoArgs
if ($LASTEXITCODE -ne 0) { throw 'Rust compilation failed' }
$targetDir = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $PSScriptRoot 'plugin\rsblender\target' }
$binary = Join-Path $targetDir "$profile\_rsblender.dll"
$binDir = Join-Path $PSScriptRoot 'plugin\LoL_Blender\bin'
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
Copy-Item -LiteralPath $binary -Destination (Join-Path $binDir '_rsblender.pyd') -Force
Write-Host 'Native module built. Run python tools/package_addon.py to package.'
