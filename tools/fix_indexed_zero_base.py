"""Correct RA=0 on non-update indexed memory operations in emitter and staging."""
from pathlib import Path
import json,re
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp';s=p.read_text()
# These emitter cases lacked the RA==0 special case. Ordinary integer and PSQ
# indexed operations already implement it. Update forms require a nonzero RA.
forms={343:'lhax',535:'lfsx',599:'lfdx',663:'stfsx',727:'stfdx',983:'stfiwx',790:'lhbrx',534:'lwbrx',662:'stwbrx',918:'sthbrx'}
for xo,name in forms.items():
    a=s.index(f'}} else if (xo == {xo}) {{');b=s.find('} else if (',a+10)
    if b<0:raise SystemExit('Emitter case boundary changed')
    block=s[a:b]
    pattern=r'ctx\.gpr\["\s*<<\s*rA\s*<<\s*"\]'
    block,n=re.subn(pattern,'" << (rA ? "ctx.gpr[" + std::to_string(rA) + "]" : "0u") << "',block)
    if not n and '(rA ? "ctx.gpr["' not in block:raise SystemExit('Indexed address context changed: '+name)
    s=s[:a]+block+s[b:]
if p.read_text()!=s:p.write_text(s)
all_xos=set(forms)|{23,87,279,151,215,407}
counts={};chunks=[]
pattern=re.compile(r'(    // 0x[0-9A-F]{8}: ([0-9A-F]{8})\n)(.*?)(?=\nloc_|\n}\n\nvoid |\Z)',re.S)
for p in sorted((PORT/'build/crash-generated').glob('output_*.cpp')):
    original=p.read_text();changed=[0]
    def patch(m):
        word=int(m[2],16);xo=(word>>1)&1023;ra=(word>>16)&31;rb=(word>>11)&31
        if word>>26!=31 or xo not in all_xos or ra:return m[0]
        code,n=re.subn(r'ctx\.gpr\[0\] \+ ctx\.gpr\['+str(rb)+r'\]',f'0u + ctx.gpr[{rb}]',m[3])
        if n:changed[0]+=1;counts[forms.get(xo,str(xo))]=counts.get(forms.get(xo,str(xo)),0)+1
        return m[1]+code
    updated=pattern.sub(patch,original)
    if updated!=original:p.write_text(updated);chunks.append(p.name)
report=PORT/'reports/indexed-zero-base-patch.json'
result=dict(changed_instructions=sum(counts.values()),forms=counts,chunks=chunks,reason='PowerPC non-update indexed effective address uses zero when RA=0, regardless of GPR0 contents.')
if counts or not report.exists():report.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
