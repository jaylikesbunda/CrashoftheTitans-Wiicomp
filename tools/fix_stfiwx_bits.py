"""Store raw low FPR bits, matching stfiwx hardware and the runtime interpreter."""
from pathlib import Path
import json
import re
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp'
s=p.read_text()
old=r'''      out << "    ctx.mmu.write32(" << (rA ? "ctx.gpr[" + std::to_string(rA) + "]" : "0u") << " + ctx.gpr[" << rB
          << "], (uint32_t)(int32_t)ctx.fpr[" << ppc_inst.rs()
          << "]); // stfiwx\n";'''
new=r'''      // NATIVE_STFIWX_BITS_V1
      out << "    { uint64_t bits; std::memcpy(&bits, &ctx.fpr[" << ppc_inst.rs() << "], 8); ctx.mmu.write32("
          << (rA ? "ctx.gpr[" + std::to_string(rA) + "]" : "0u") << " + ctx.gpr[" << rB
          << "], (uint32_t)bits); } // stfiwx\n";'''
if '// NATIVE_STFIWX_BITS_V1' not in s:
    assert s.count(old)==1,'stfiwx emitter context changed'
    p.write_text(s.replace(old,new,1))
pattern=re.compile(r'^(\s*)ctx\.mmu\.write32\(([^\n]+), \(uint32_t\)\(int32_t\)ctx\.fpr\[(\d+)\]\); // stfiwx$',re.M)
changes=[]
for q in sorted((PORT/'build/crash-generated').glob('output_*.cpp')):
    t=q.read_text()
    t,n=pattern.subn(lambda m:m[1]+'{ uint64_t bits; std::memcpy(&bits, &ctx.fpr['+m[3]+'], 8); ctx.mmu.write32('+m[2]+', (uint32_t)bits); } // stfiwx',t)
    if n:q.write_text(t);changes.append(dict(file=q.name,sites=n))
report=PORT/'reports/stfiwx-bits-patch.json'
if changes or not report.exists():report.write_text(json.dumps(dict(chunks=changes,sites=sum(x['sites'] for x in changes)),indent=2)+'\n')
print('stfiwx raw-bit storage staged:',sum(x['sites'] for x in changes),'generated sites changed')
