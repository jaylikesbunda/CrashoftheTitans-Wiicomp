# Changelog

Notable changes to Crash of the Titans WiiComp. The launcher uses semantic versions; the builder revision is a
date-serial that pins the exact bytes of every build input (`builder-revision.json`), so a runtime built from different
inputs is always rebuilt rather than reused.

## [0.1.3] - 2026-10-28

### Changed

- **The internal-resolution buttons show the real buffer size**: `1x · 640×528`, `2x · 1280×1056`, `3x · 1920×1584`,
  `4x · 2560×2112`. The old `≈1080p / ≈1440p / ≈4K` labels counted lines and overstated the width: 2x read as 1080p while
  rendering 1280×1056, which is 65% of 1080p's pixels.
- The row now derives a recommendation from the window height (2x for 720p, 3x for 1080p, 4x for 1440p) and says so when a
  window is taller than 4x can cover, since 4x tops out at 2112 lines.
- Launcher `0.1.3`; builder unchanged at `2026.10.28-2`.

## [0.1.2] - 2026-10-28

### Fixed

- **A launcher build failed at the native-lowering stage.** That stage pins every lowered function to the decompilation
  source it was written from, but the builder pack ships no decompilation tree (and must not ship one), so a build from
  the installer stopped with `IDENTITY: source missing: ...` and *"the native lowering qualification is stale"* before
  compiling anything. The source hashes are now skipped when that tree is absent; the `main.dol` pin and the
  `builder-revision.json` hashes over the qualification data still apply.
- Launcher `0.1.2`; builder revision `2026.10.28-2`.

## [0.1.1] - 2026-10-28

### Fixed

- **The installer aborted while extracting the builder pack.** It shipped the entire SDL2 checkout; the first release
  stopped on `deps/sdl2-src/android-project/ant/src/org/libsdl/app/HIDDevice.java`, and SDL also ships a second Android
  tree (`android-project-ant`) that Tauri's resource glob then reported as a missing file. The pack now carries only what
  a CMake build reads (`CMakeLists.txt`, `cmake/`, `include/`, `src/`) and drops SDL's `android-project`,
  `android-project-ant`, `Xcode`, `Xcode-iOS`, `VisualC`, `VisualC-GDK`, `VisualC-WinRT`, `visualtest`, `acinclude`,
  `mingw`, `docs` and `.github`, plus the equivalent extras in toml++ and glad. SDL2 is down to 1,434 files.

### Changed

- Tagged releases are published as stable ("Latest") unless the tag itself contains a hyphen (`v0.2.0-rc1`), instead of
  every release being a pre-release.
- Launcher `0.1.1`; builder revision `2026.10.28-1`.

### Added

- The release workflow now installs the installer it just built (silently), checks that the packaged builder pack is
  complete, that the pruned trees are gone and that the pack revision matches, and re-configures the pruned SDL2 source
  with the bundled toolchain before anything is published.
- `CHANGELOG.md`.

## [0.1.0] - 2026-10-23

### Added

- First public pre-release: launcher `0.1.0`, builder `2026.10.23-10`. The runtime is compiled on the player's machine
  from their own disc; no game code, recompiled code or linked binary is distributed.
- PAL (`RQJP7D`) and NTSC USA (`RQJE7D`) support.
- Two-player local co-op, Wii Remote + Nunchuk controls with motion and pointer aiming, per-player layouts.
- Save states (F1 and **Play from here**), episode select including the PAL disc's unfinished test levels, soundtrack
  export, 1x-4x internal resolution, 16:9, frame pacing and a shader cache.

### Known issues

- Save states do not load on the default (arena) build.
- Performance on the reference machine (Ryzen 5 3600, RTX 3060, 2x internal, PAL): 75-100 FPS in most gameplay, about 60
  in the busiest fights. The game thread is single-threaded and sets the pace.
