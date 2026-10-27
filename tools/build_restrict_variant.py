"""Build the optional non-aliasing-ctx executable (experiment B) into <storage>/toolchain-restrict.

Same staged sources as the normal build, compiled with -DNWII_CTX_RESTRICT (fix_ctx_restrict_variant.py: guest registers stay in host registers within a function) and -DNWII_STATIC_RAM_BASE (fix_static_ram_base.py: guest RAM at fixed addresses 0x20000000 and 0x30000000).
The normal executable is not touched. The shader program cache is copied so the first run is warm."""
from pathlib import Path
from port_paths import default_storage
import argparse,shutil,subprocess,sys
PORT=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--storage',type=Path,default=default_storage());ap.add_argument('--jobs',type=int,default=8);args=ap.parse_args()
storage=args.storage.resolve();src=PORT/'build/nwii-full-source';build=storage/'toolchain-restrict'
configure=['cmake','-S',str(src),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=RelWithDebInfo','-DCMAKE_EXE_LINKER_FLAGS=-static','-DSDL_SHARED=OFF','-DSDL_STATIC=ON','-DCMAKE_POLICY_VERSION_MINIMUM=3.5','-DCMAKE_CXX_FLAGS=-DNWII_CTX_RESTRICT -DNWII_STATIC_RAM_BASE']
for dep in ('sdl2','tomlplusplus','glad'):
    cached=storage/'nwii-full/_deps'/f'{dep}-src'
    if cached.exists():configure.append(f'-DFETCHCONTENT_SOURCE_DIR_{dep.upper()}={cached.as_posix()}')
for cmd in (configure,['cmake','--build',str(build),'--target','CrashNative','-j',str(args.jobs)]):
    print(' '.join(cmd[:4]),flush=True)
    r=subprocess.run(cmd)
    if r.returncode:sys.exit(r.returncode)
cache=storage/'toolchain/program-cache'
if cache.exists():shutil.copytree(cache,build/'program-cache',dirs_exist_ok=True)
print('Variant executable:',build/'CrashNative.exe')
