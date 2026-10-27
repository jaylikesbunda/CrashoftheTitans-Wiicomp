# Fast loop: debug build (incremental, seconds) run against the local builder pack. No installer, no compression.
if (-not $env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR = Join-Path $PSScriptRoot "..\src-tauri\target" }
$env:CRASH_BUILDER_PACK = (Resolve-Path "$PSScriptRoot\..\src-tauri\builder-pack").Path
Push-Location "$PSScriptRoot\..\src-tauri"
cargo build; if ($?) { & "$env:CARGO_TARGET_DIR\debug\crash-launcher.exe" }
Pop-Location
