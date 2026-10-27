"""Collapse the SDK cache-range loops (DCInvalidate/Flush/Store/Touch, ICInvalidate) at their back-edge.

These loops (dcbi/dcbf/dcbst/dcbt/icbi, 32 bytes per iteration) emit no host work for the cache instruction (the emitter
turns them into comments) but cost an iteration of guest code, a poll site and two instruction counts per 32 bytes. In
capture frame-cost-20261006-102126-770700 803B5120 + 803B50F4 were 1.85 ms/frame inclusive and four loop sites
(803B5144, 803B5118, ...) were 18% of all polls. At the first back-edge (ctr = N before the decrement) all but the last
iterations are applied directly: r3 += 32*(N-2), inst_count += 2*(N-2) (cache op and addi on the looping path), ctr = 2.
The insertion is on the branch instruction, outside every qualified bounded-block region. Only a loop whose body is exactly
the cache-op comment and the +32 add is touched; dcbz (DCZeroRange) is not. An earlier revision inserted after mtctr; that
text is removed first so the original region text is intact.
"""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];gen=p/'build/crash-generated';marker='NATIVE_CACHE_RANGE_COLLAPSE_V2'
targets=['803B50F4','803B5120','803B5150','803B5180','803B51D8','803B5204','803B525C']
OLD=re.compile(r'    // NATIVE_CACHE_RANGE_COLLAPSE_V1[^\n]*\n    if \(ctx\.ctr > 1 && !nwii::runtime::g_trace_calls\)[^\n]*\n')
done=[]
for path in sorted(gen.glob('output_*.cpp')):
    s=path.read_text(errors='surrogateescape');changed=False
    s,n=OLD.subn('',s)
    changed=changed or bool(n)
    for addr in targets:
        head=f'void func_{addr}('
        a=s.find(head)
        if a<0:continue
        z=s.find('\nvoid func_',a+1);z=len(s) if z<0 else z;body=s[a:z]
        if marker in body:done.append(addr);continue
        m=re.search(r'\n    ctx\.ctr--;\n    if \(ctx\.ctr != 0\) goto (loc_[0-9A-F]+);\n',body)
        if not m:print('skip (no back-edge)',addr);continue
        start=body.index(m[1]+': ;')
        loop=body[start:m.start()]
        # loop body = label, cache-op comment, ++, label, addi, ++, label, poll-site statements
        poll=loop.index('    ctx.pc = 0x')
        stmts=[l.strip() for l in loop[:poll].splitlines() if l.strip() and not l.strip().startswith('// 0x') and not l.strip().startswith('loc_')]
        ok=stmts[0:1]==['// sync/cache instruction'] and any(x.startswith('ctx.gpr[3] = ctx.gpr[3] + 32;') for x in stmts) and stmts.count('++ctx.inst_count;')==2 and len(stmts)==4
        if not ok:print('skip (body)',addr,stmts);continue
        patch='    // '+marker+': skip all but the last two iterations at the first back-edge.\n    if (ctx.ctr > 2 && !nwii::runtime::g_trace_calls) { const uint32_t skip = ctx.ctr - 2; ctx.gpr[3] += 32u * skip; ctx.inst_count += 2ull * skip; ctx.ctr = 2; }\n'
        body=body[:m.start()+1]+patch+body[m.start()+1:]
        s=s[:a]+body+s[z:];changed=True;done.append(addr)
    if changed:path.write_text(s,errors='surrogateescape')
print('Cache-range loops collapsed:',sorted(set(done)))
