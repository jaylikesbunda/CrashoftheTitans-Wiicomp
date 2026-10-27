"""NATIVE_FASTMEM_STATIC_BASE_V1: make the shipping build compile with NWII_STATIC_RAM_BASE.

The accessor plumbing (NativeFixedRam members, the commit overlay and the CMake source) is staged by
tools/fix_static_ram_base.py, but the macro that turns it on was only ever passed by the optional
-toolchain-restrict experiment build. This stage flips it on for the real build:

    target_compile_definitions(nwiiruntime PUBLIC NWII_STATIC_RAM_BASE)

PUBLIC so CrashNative, the hot/split chunks and every fixture that links nwiiruntime all see the same
MMU layout (mem1/mem2 are NativeFixedRam, so mem1.data()/mem2.data() are compile-time constants and
each RAM load/store becomes a single disp32-addressed access with no base-pointer load and no
page_bias table).

Must run after fix_static_ram_base.py (it owns the header block and the overlay source). Revert with
NWII_BUILD_FASTMEM_STATIC_BASE=0 or by dropping the appended block; the header keeps the dynamic
NativeWatchedRam path under #else. Idempotent.
"""
from pathlib import Path
import argparse, os

MARKER = 'NATIVE_FASTMEM_STATIC_BASE_V1'

ap = argparse.ArgumentParser()
ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
ap.add_argument('--storage', type=Path, default=None)
args = ap.parse_args()
p = args.port.resolve()

if os.environ.get('NWII_BUILD_FASTMEM_STATIC_BASE', '1') == '0':
    print('fastmem static base disabled')
    raise SystemExit(0)

src = p / 'build/nwii-full-source'
cm = src / 'CMakeLists.txt'
t = cm.read_text()

hdr = src / 'nWiiRuntime/include/runtime/cpu_context.h'
h = hdr.read_text()
assert 'NATIVE_STATIC_RAM_V1' in h and 'NativeFixedRam' in h, \
    'fix_static_ram_base.py must run first (NativeFixedRam block missing)'
assert 'static_ram_base.cpp' in t, \
    'fix_static_ram_base.py must run first (overlay source missing from CMakeLists)'

if MARKER not in t:
    t += ('\n# NATIVE_FASTMEM_STATIC_BASE_V1\n'
          '# NWII_STATIC_RAM_BASE: mem1/mem2 are NativeFixedRam at 0x20000000/0x30000000, committed once by\n'
          '# runtime-overlays/static_ram_base.cpp. PUBLIC so the generated chunks and fixtures agree on the layout.\n'
          'if(MINGW)\n'
          '  target_compile_definitions(nwiiruntime PUBLIC NWII_STATIC_RAM_BASE)\n'
          'endif()\n')
    cm.write_text(t)
    print('fastmem static base enabled (NWII_STATIC_RAM_BASE)')
else:
    print('fastmem static base already enabled')
