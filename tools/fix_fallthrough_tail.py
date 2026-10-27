"""Mapped-boundary fall-through runs the next function directly instead of returning to the dispatcher.

Capture frame-cost-20261006-180351-850400 (guest thread 33.1 ms/frame): run_game exclusive 2.49 ms, process_pending_callbacks +
native_process_callbacks_poll 1.6 ms and the dispatch/interpreter family 2.9 ms. The compiler's __savegpr_N/__restgpr_N millicode
(0x80393634.., called by almost every function prologue/epilogue, e.g. func_803143EC `bl 0x80393638`) is exported as one function per
instruction (size 4). Each ends with `ctx.pc = next; // mapped boundary fall-through` and returns to run_game, so a save of N
registers costs N round trips through the dispatch loop (callback poll, function lookup, indirect call).

Each fall-through whose target is a generated function now continues with `func_<target>(ctx);` (a sibling call, so no stack growth).
ctx.pc is still set before the call, so the entry switch/trace check behave exactly as they did after the dispatcher lookup. Callers
that compare ctx.pc with their return address see the same value. What changes is that the dispatcher's per-iteration
process_pending_callbacks is skipped inside the chain; backward-branch polls and the dispatcher between real calls still run.
output_74.cpp (bounded/pinned functions) and gap chunks are left alone. Idempotent.
"""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];gen=p/'build/crash-generated'
files=sorted(gen.glob('output_*.cpp'))+[gen/'main_output.cpp']
defs=set()
for f in files:defs|=set(re.findall(r'^void (func_[0-9A-F]{8})\(',f.read_text(errors='surrogateescape'),re.M))
site=re.compile(r'(    ctx\.pc = (0x[0-9A-F]{8}); // mapped boundary fall-through\n)(?!    func_)(?=\})')
changed=0;skipped=0
for f in files:
    if f.name=='output_74.cpp' or f.name.startswith('output_gap_'):continue
    s=f.read_text(errors='surrogateescape')
    def sub(m):
        global skipped
        name='func_'+m[2][2:]
        if name not in defs:skipped+=1;return m[0]
        return m[1]+f'    {name}(ctx); // NATIVE_FALLTHROUGH_TAIL_V1\n'
    t,n=site.subn(sub,s)
    if t!=s:f.write_text(t,errors='surrogateescape');changed+=sum(1 for _ in re.finditer('NATIVE_FALLTHROUGH_TAIL_V1',t))-s.count('NATIVE_FALLTHROUGH_TAIL_V1')
print(f'Fall-through tail calls installed: {changed} sites this pass; {skipped} targets are not function starts and still return to the dispatcher.')
