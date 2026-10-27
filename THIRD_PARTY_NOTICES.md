# Third-Party Notices

This project's own code is distributed under the NWiiRecomp licence terms in [LICENSE](LICENSE). The components below
keep their own licences.

## Recompiler and runtime

### NWiiRecomp

- Copyright (c) 2026 Volodymyr Vovchok (BlackLine Interactive)
- Licence: custom licence, a modified MIT licence with commercial restrictions. Use is non-commercial only, with
  attribution. Derivative works must use no looser terms, and use for AI training is prohibited. Full text:
  `vendor/NWiiRecomp/LICENSE`, also reproduced in [LICENSE](LICENSE).
- Pinned revision: `595f176f1d24cc54ff2e8389feed12d7fb553cc2` (see [vendor/README.md](vendor/README.md)).
- Used for: the PowerPC-to-C++ recompiler, the analyser and the HLE runtime that `CrashNative.exe` is built from. The
  launcher's About page carries the attribution required by clause 4.

NWiiRecomp bundles `third_party/dolphin-sigdb` (Dolphin's function signature database) under its own `COPYING` file.

### Dolphin

- Licence: GPL-2.0-or-later. Text: [reference/dolphin-ax/LICENSES/GPL-2.0-or-later.txt](reference/dolphin-ax/LICENSES/GPL-2.0-or-later.txt)
- Upstream revision: `562875dd60fd0ff50b65dbbb611a2096802cd1b9`. Per-file sources and hashes are in
  `reference/dolphin-ax/provenance.json`.
- Used for: `runtime-overlays/native_ax/` (AX/DSP HLE mixing, decoding, resampling and the accelerator), adapted by
  `tools/prepare_ax_backend.py` from the retained files in `reference/dolphin-ax/`. Each adapted file states its origin.

> [!WARNING]
> GPL-2.0 forbids adding restrictions to the licensed code. The NWiiRecomp terms add non-commercial and no-AI-training
> restrictions. A runtime binary that links both is therefore not cleanly redistributable. The launcher avoids shipping
> such a binary, because each user builds the runtime locally. Do not distribute a prebuilt `CrashNative.exe` until
> this conflict is resolved, either by replacing `native_ax` with independent code or by agreement with the
> rights holders.

### Fetched at build time

| Component | Version | Licence |
| --- | --- | --- |
| [SDL2](https://github.com/libsdl-org/SDL) | 2.28.5 | zlib |
| [toml++](https://github.com/marzer/tomlplusplus) | 3.4.0 | MIT |
| [glad](https://github.com/Dav1dde/glad) | 0.1.36 | MIT (generator); generated files carry the Khronos notices |

## Launcher

The launcher is a [Tauri](https://github.com/tauri-apps/tauri) 2 application (MIT or Apache-2.0). Its direct Rust
dependencies include `tauri-plugin-dialog`, `serde`, `serde_json`, `toml`, `zstd`, `sha2`, `reqwest` (rustls), `zip`,
[`gilrs`](https://gitlab.com/gilrs-project/gilrs) and [`nod`](https://github.com/encounter/nod). Each is under MIT,
Apache-2.0 or a compatible licence. The exact locked set is `launcher/src-tauri/Cargo.lock`. To produce a full
inventory with licence texts:

```powershell
cargo install cargo-about
cargo about generate about.hbs   # in launcher/src-tauri
```

## Builder pack

The release installer bundles a builder pack, assembled by `tools/make_builder_pack.py`, which redistributes:

- a pruned [WinLibs](https://winlibs.com/) MinGW-w64 toolchain, GCC 16.1.0 with MinGW-w64 14.0.0 UCRT, including CMake
  and Ninja (GCC: GPL-3.0 with the GCC Runtime Library Exception; MinGW-w64 runtime: ZPL and others; CMake:
  BSD-3-Clause; Ninja: Apache-2.0);
- the CPython interpreter and standard library (PSF License);
- the NWiiRecomp checkout and the SDL2, toml++ and glad sources listed above.

The pack root carries this file and [LICENSE](LICENSE). CPython's `LICENSE.txt` and the NWiiRecomp, SDL2, toml++ and glad
licence files are kept beside their components. The GCC, MinGW-w64, CMake and Ninja licences and the corresponding
sources are published with the toolchain at
[WinLibs 16.1.0posix-14.0.0-ucrt-r2](https://github.com/brechtsanders/winlibs_mingw/releases/tag/16.1.0posix-14.0.0-ucrt-r2).

## Artwork

No game artwork ships with the launcher. `launcher/ui/images/wiicomp.png`, `launcher/src-tauri/assets/wiicomp-title.png`
and `.github/assets/cott-recompiled.png` (the README banner)
are a "WiiComp" wordmark only; when a player prepares their own disc, the launcher reads the official *Crash of the Titans*
logo from it and places the wordmark beneath it (title screen and launcher). That logo is a trademark and copyrighted
artwork of its owners and is not covered by [LICENSE](LICENSE). Crash Bandicoot and related names and characters belong to their respective owners.
