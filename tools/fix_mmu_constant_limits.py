"""Use the fixed MEM1/MEM2 sizes as compile-time limits in the inline MMU accessors.

MMU() always resizes mem1 to 24 MiB + 8 and mem2 to 64 MiB + 8 and nothing resizes them afterward, so
`paddr < mem1.size() - 8` is `paddr < 0x1800000`. Eight million guest instructions per frame run through
these accessors (capture frame-cost-20261006-102126-770700); every access reloaded the vector begin/end
pointers (the stores that precede it may alias them) just to rebuild a constant. Identical predicates.
"""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h';s=f.read_text()
marker='NATIVE_MMU_CONSTANT_LIMITS_V1'
if marker not in s:
    a=s.index('struct MMU {');b=s.index('struct CPUContext {',a)
    body=s[a:b]
    body,n1=re.subn(r'mem1\.size\(\)\s*-\s*8','kMem1Limit',body)
    body,n2=re.subn(r'mem2\.size\(\)\s*-\s*8','kMem2Limit',body)
    assert n1>=8 and n2>=8,(n1,n2)
    body=body.replace('struct MMU {\n','struct MMU {\n  // '+marker+': sizes are fixed by the constructor below.\n  static constexpr uint32_t kMem1Limit=24u*1024u*1024u, kMem2Limit=64u*1024u*1024u;\n',1)
    assert 'mem1.size()' not in body.replace('mem1.resize','') and 'mem2.size()' not in body
    f.write_text(s[:a]+body+s[b:])
    print('replaced',n1,n2)
print('MMU inline accessors use constant MEM1/MEM2 limits.')
