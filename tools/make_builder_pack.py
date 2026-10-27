"""Assemble the self-contained builder that the launcher installer bundles.

    python tools/make_builder_pack.py [--out launcher/src-tauri/builder-pack] [--mingw DIR] [--python DIR] [--deps DIR]

--deps defaults to the CMake cache of a local build; when that does not exist the three sources are cloned at their pinned tags.

Layout (what the launcher finds under its resource directory):
    builder-pack/native-builder/   tools, compiler, runtime-overlays, patches, data, reference, runtime.toml, vendor (no .git) + PINNED_COMMIT
    builder-pack/export/index.tsv  function index (addresses and names only; no game bytes)
    builder-pack/deps/             SDL2 / toml++ / glad sources, so CMake never touches the network
    builder-pack/toolchain/mingw64 WinLibs GCC (includes cmake and ninja), pruned
    builder-pack/toolchain/python  stdlib-only Python (the stage scripts use nothing else)
The pack contains no game bytes: no DOL, no disc files.
"""
from pathlib import Path
import argparse, os, shutil, stat, subprocess, sys
from builder_revision import identity
from port_paths import PORT, INDEX, default_storage, winlibs_bin
import json

WINLIBS = winlibs_bin().parent if winlibs_bin() else None
DEPS = {'sdl2-src': ('https://github.com/libsdl-org/SDL.git', 'release-2.28.5'),
        'tomlplusplus-src': ('https://github.com/marzer/tomlplusplus.git', 'v3.4.0'),
        'glad-src': ('https://github.com/Dav1dde/glad.git', 'v0.1.36')}
GAME_CODE = ('bounded-blocks', 'leaf-before-*.cpp', 'hot-before-*.cpp', 'hot_fp_bank_candidate.cpp', 'sdk_sqrt_baseline.cpp', 'sdk_sqrt_local_loops.inc')
ap = argparse.ArgumentParser()
ap.add_argument('--out', type=Path, default=PORT / 'launcher/src-tauri/builder-pack')
ap.add_argument('--mingw', type=Path, default=WINLIBS, required=WINLIBS is None, help='WinLibs mingw64 folder')
ap.add_argument('--python', type=Path, default=Path(sys.base_prefix))
ap.add_argument('--deps', type=Path, default=default_storage() / 'nwii-full/_deps')
ap.add_argument('--index', type=Path, default=INDEX)
args = ap.parse_args()
out = args.out.resolve()
PIN = '595f176f1d24cc54ff2e8389feed12d7fb553cc2'   # must equal the pin in tools/build_native.py

junk = shutil.ignore_patterns('__pycache__', '*.pyc', '*.log', '.git', *GAME_CODE)
# Machine-local snapshots the stage scripts take on first use (see .gitignore) and developer dumps never belong in the pack.
LOCAL = {'runtime-overlays': ('*-before*', '*_baseline.*'), 'reference': ('user',)}
if out.exists():
    assert out.name == 'builder-pack', 'refusing to wipe ' + str(out)
    shutil.rmtree(out)
nb = out / 'native-builder'

for d in ('tools', 'compiler', 'runtime-overlays', 'patches', 'data', 'reference'):
    shutil.copytree(PORT / d, nb / d, ignore=lambda src, names, d=d: set(junk(src, names)) | (set(shutil.ignore_patterns(*LOCAL[d])(src, names)) if d in LOCAL and Path(src) == PORT / d else set()))
(nb / 'reports').mkdir()
shutil.copy2(PORT / 'runtime.toml', nb / 'runtime.toml')
(nb/'builder-revision.json').write_text(json.dumps(identity(PORT),indent=2)+'\n')
shutil.copytree(PORT / 'vendor', nb / 'vendor', ignore=junk)
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=PORT / 'vendor/NWiiRecomp', text=True).strip()
assert head == PIN, f'vendor/NWiiRecomp is at {head}, expected {PIN}'
(nb / 'vendor/NWiiRecomp/PINNED_COMMIT').write_text(head + '\n')
(out / 'export').mkdir(parents=True)
shutil.copy2(args.index, out / 'export/index.tsv')

for dep, (url, tag) in DEPS.items():
    if (args.deps / dep).is_dir():
        shutil.copytree(args.deps / dep, out / 'deps' / dep, ignore=junk)
    else:
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', tag, url, str(out / 'deps' / dep)], check=True)
        shutil.rmtree(out / 'deps' / dep / '.git', onexc=lambda fn, path, err: (os.chmod(path, stat.S_IWRITE), fn(path)))   # git packs are read-only on Windows

tc = out / 'toolchain'
prune = ('lto-dump.exe', 'doxygen.exe', 'ctest.exe', 'cpack.exe', 'cmake-gui.exe', 'cppcheck*', 'gdb*', 'gfortran*', '*-gfortran.exe',
         'libgfortran*', 'libpython3*', 'x86_64-w64-mingw32-gfortran.exe')
shutil.copytree(args.mingw, tc / 'mingw64', ignore=shutil.ignore_patterns(*prune, 'doc', 'man', 'info'))
py = tc / 'python'
py.mkdir(parents=True)
for f in args.python.glob('*.dll'): shutil.copy2(f, py / f.name)
shutil.copy2(args.python / 'python.exe', py / 'python.exe')
if (args.python / 'LICENSE.txt').is_file(): shutil.copy2(args.python / 'LICENSE.txt', py / 'LICENSE.txt')
for notice in ('LICENSE', 'THIRD_PARTY_NOTICES.md'): shutil.copy2(PORT / notice, out / notice)
if (args.python / 'DLLs').is_dir(): shutil.copytree(args.python / 'DLLs', py / 'DLLs', ignore=shutil.ignore_patterns('tcl*', 'tk*', '_tkinter*', 'test*'))
shutil.copytree(args.python / 'Lib', py / 'Lib', ignore=shutil.ignore_patterns('site-packages', 'test', 'tests', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', '__pycache__', 'lib2to3'))

size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
print(f'builder pack ready: {out}  ({size / 1e6:.0f} MB, {sum(1 for f in out.rglob("*") if f.is_file())} files)')
