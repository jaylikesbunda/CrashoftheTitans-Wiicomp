# Upstream sources

Upstream code is not committed. Check it out here at the pinned revision; the build verifies the pin and refuses
any other checkout.

| Dependency | Path | Pinned revision |
| --- | --- | --- |
| [NWiiRecomp](https://github.com/BlackLineInteractive/NWiiRecomp) | `vendor/NWiiRecomp` | `595f176f1d24cc54ff2e8389feed12d7fb553cc2` |

```powershell
git clone https://github.com/BlackLineInteractive/NWiiRecomp.git vendor/NWiiRecomp
git -C vendor/NWiiRecomp checkout 595f176f1d24cc54ff2e8389feed12d7fb553cc2
```

The checkout stays pristine. `tools/build_native.py` copies it to `<storage>/nwii-full-source` and the stages patch
that copy. A builder pack carries the copy without `.git` and records the revision in `PINNED_COMMIT`.

CMake fetches these during the first build. `tools/make_builder_pack.py` bundles them, cloning them at these tags if
no local copy exists, so launcher builds work offline:

| Dependency | Version |
| --- | --- |
| [SDL2](https://github.com/libsdl-org/SDL) | `release-2.28.5` |
| [toml++](https://github.com/marzer/tomlplusplus) | `v3.4.0` |
| [glad](https://github.com/Dav1dde/glad) | `v0.1.36` |

The Dolphin files the audio backend is adapted from are kept, with their hashes, in `reference/dolphin-ax/`.
