"""Compile the generated guest chunks for size (-Os); keep the dispatcher (main_output.cpp) at -O2.

The executable's .text is 139 MB for a few MB of PowerPC code, the profile is a flat tail of thousands of functions, and the guest thread
is instruction-fetch bound on a CPU with a 32 KiB L1I. Measured on output_71.cpp (11,836 guest instructions, same source):
  -O2 (before)                         1,577,700 bytes  301,664 x86 instructions
  entry pruning + slimming, -O2        1,149,312 bytes  253,007
  entry pruning + slimming, -Os          790,940 bytes  202,497
-Os also drops the nop padding that -O2 adds in front of every jump-table target and loop. FP semantics are unchanged
(-ffp-contract=off, no fast-math); only inlining/alignment/unrolling decisions differ. NWII_CHUNK_OPT=O2 in the environment of the
build keeps -O2 for an A/B build. Appended last in CMakeLists.txt so it overrides earlier per-file options. Idempotent.
"""
from pathlib import Path
import os
p=Path(__file__).resolve().parents[1];gen=p/'build/crash-generated'
cm=p/'build/nwii-full-source/CMakeLists.txt';t=cm.read_text()
marker='# NATIVE_CHUNK_SIZE_FLAGS_V1'
if marker in t:t=t[:t.index(marker)].rstrip('\n')+'\n'
level=os.environ.get('NWII_CHUNK_OPT','Os')
assert level in ('Os','O2','O1'),level
split=p/'build/crash-split'
# std::fma is a libm call (ucrtbase) without hardware FMA; FMA3 is correctly rounded so results are bit-identical. NWII_HOT_FMA=0 drops it.
fma='-mfma;' if os.environ.get('NWII_HOT_FMA','1')!='0' else ''
chunks=[(split/c.name if (split/c.name).exists() else c) for c in sorted(gen.glob('output_*.cpp'))]
block='\n'+marker+'\nset_source_files_properties(\n'+''.join('    "'+c.as_posix()+'"\n' for c in chunks)+'    PROPERTIES COMPILE_OPTIONS "-'+level+';'+fma+'-march=x86-64-v3;-fwrapv;-fno-strict-aliasing;-ffp-contract=off")\n'
block+='set_source_files_properties("'+(gen/'main_output.cpp').as_posix()+'" PROPERTIES COMPILE_OPTIONS "-O2;'+fma+'-march=x86-64-v3;-fwrapv;-fno-strict-aliasing;-ffp-contract=off")\n'
if fma: block+='target_compile_definitions(CrashNative PRIVATE NWII_BUILT_WITH_FMA=1)'+chr(10)
cm.write_text(t+block)
print(f'Chunk flags: {len(chunks)} chunks at -{level}, main_output.cpp at -O2')
