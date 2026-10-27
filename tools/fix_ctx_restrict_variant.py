"""Optional non-aliasing guest context (build with -DNWII_CTX_RESTRICT; default expands to the old signature).

Experiment B for the translated code: every generated function takes `CPUContext& __restrict ctx` in the variant
build, so the compiler may keep guest registers and the RAM base pointer in host registers between guest
memory accesses (guest RAM is separate heap memory, never ctx). Out-of-line callees that receive ctx or a member of it
already force a flush/reload. The only calls without a ctx-derived argument in the inline accessors are the write-gather
handlers, which get a compiler memory barrier first (NWII_CTX_BARRIER). The default build is byte-for-byte the same code.
Fixture on the audio routine: 6,958 -> 5,579 host instructions, no change in warm-cache time; the real effect
(instruction-cache footprint across a whole frame) can only be seen in a game capture, hence a second executable.
"""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];gen=p/'build/crash-generated';marker='NATIVE_CTX_RESTRICT_VARIANT_V1'
f=p/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h';s=f.read_text()
if marker not in s:
    anchor='#include <cstdlib>\n// NATIVE_DIAGNOSTIC_CACHE_BEGIN'
    assert s.count(anchor)==1
    s=s.replace(anchor,'''#include <cstdlib>
// '''+marker+''': see tools/fix_ctx_restrict_variant.py
#ifdef NWII_CTX_RESTRICT
#define NWII_CTX_PARAM ::nwii::runtime::CPUContext& __restrict
#define NWII_CTX_BARRIER() __asm__ volatile("" ::: "memory")
#else
#define NWII_CTX_PARAM ::nwii::runtime::CPUContext&
#define NWII_CTX_BARRIER() ((void)0)
#endif
// NATIVE_DIAGNOSTIC_CACHE_BEGIN''',1)
    n=0
    for name in ('GX_WGPIPE_Write8','GX_WGPIPE_Write16','GX_WGPIPE_Write32','GX_WGPIPE_WriteF32','GX_WGPIPE_WriteF64'):
        old='{'+name+'(value);return;}'
        c=s.count(old);assert c==1,(name,c)
        s=s.replace(old,'{NWII_CTX_BARRIER();'+name+'(value);return;}');n+=1
    f.write_text(s)
pat=re.compile(r'^void (func_[0-9A-F]{8})\(CPUContext& ctx\) \{$',re.M)
total=0;files=0
for path in sorted(gen.glob('output_*.cpp')):
    t=path.read_text(errors='surrogateescape')
    u,n=pat.subn(r'void \1(NWII_CTX_PARAM ctx) {',t)
    if n:path.write_text(u,errors='surrogateescape');total+=n;files+=1
print(f'Context parameter macro installed ({total} function definitions rewritten in {files} chunks this pass); default build unchanged.')
