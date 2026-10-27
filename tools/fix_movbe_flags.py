"""NATIVE_X86_64_V3_V1 -- target the Haswell baseline ISA on the runtime library.

The build already requires FMA3 (`-mfma` on the guest chunks), i.e. Haswell-class
hardware, but was otherwise targeting the SSE2-only x86-64 baseline. Enabling
x86-64-v3 (the exact Haswell baseline) unlocks, verified by disassembly:
  * `lzcnt` for `cntlzw` (5 instructions -> 1),
  * `andn` for `andc` (3 -> 1),
  * `movbe` for guest big-endian load/store (2 -> 1),
  * AVX2 `vpshufb` for bulk 32-byte byte swaps.
This stage applies it to the runtime library; the generated guest units get it
via fix_chunk_size_flags.py / fix_hot_split.py. FP semantics are untouched
(-ffp-contract=off). Idempotent.
"""
from pathlib import Path

p = Path(__file__).resolve().parents[1]
cm = p / 'build/nwii-full-source/CMakeLists.txt'
t = cm.read_text()
if 'NATIVE_X86_64_V3_V1' not in t:
    t += ('\n# NATIVE_X86_64_V3_V1\n'
          'if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")\n'
          '  target_compile_options(nwiiruntime PRIVATE -march=x86-64-v3)\n'
          'endif()\n')
    cm.write_text(t)
print('x86-64-v3 enabled for the runtime library.')
