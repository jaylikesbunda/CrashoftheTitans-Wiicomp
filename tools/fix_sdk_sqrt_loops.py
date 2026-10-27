"""Promote integer sqrt loop registers, retaining original polls and entries."""
from pathlib import Path
import json,re
import guest_baselines,region
# SHA-256 of the register-local helper text that data/sdk-sqrt-tests.json qualified.
HELPERS_SHA256='fed6aad7260e0c418246ff6b42e39cdf9185a1bfe7f574782c9bbf09476a77ee'
p=Path(__file__).resolve().parents[1]
rows=json.loads((p/'data/sdk-sqrt-tests.json').read_text())
assert all(r['exit_code']==0 for r in rows)
assert 'STATES 60039/60039' in rows[-1]['output']
path=p/'build/crash-generated'/region.chunk_file('803A35F0','output_73.cpp');s=path.read_text()
marker='NATIVE_SQRT_LOCAL_LOOPS_V1'
if marker not in s:
    baseline=guest_baselines.path('sdk_sqrt_baseline.cpp')
    a=s.index('void func_803A35F0(CPUContext& ctx) {');b=s.index('\nvoid func_',a+1)
    original=s[a:b];baseline.write_text(original)
    # Re-derive helper text to reject an outdated qualification after generation.
    expected=[]
    for start,end in [('803A36F4','803A3724'),('803A3730','803A379C')]:
        x=original.index('loc_'+start+': ;');y=original.index('loc_'+end+': ;');body=original[x:y]
        assert 'process_pending_callbacks' not in body and 'mmu.' not in body
        regs=sorted(set(map(int,re.findall(r'ctx.gpr\[(\d+)\]',body))))
        before=''.join(f'uint32_t r{i}=ctx.gpr[{i}];' for i in regs)
        after=''.join(f'ctx.gpr[{i}]=r{i};' for i in regs)
        body=re.sub(r'ctx.gpr\[(\d+)\]',lambda m:'r'+m[1],body).replace('ctx.cr[0]','cr').replace('ctx.inst_count','count').replace('loc_','local_')
        expected.append('static inline void loop_'+start+'(CPUContext& ctx){'+before+'auto cr=ctx.cr[0];auto count=ctx.inst_count;\n'+body+'\n'+after+'ctx.cr[0]=cr;ctx.inst_count=count; }\n')
    helpers=''.join(expected)
    assert region.active().trusted(HELPERS_SHA256,guest_baselines.sha256(helpers),0x803A35F0),'SDK sqrt changed: requalify before patching'
    guest_baselines.path('sdk_sqrt_local_loops.inc').write_text(helpers)
    candidate=original
    for start,end in [('803A36F4','803A3724'),('803A3730','803A379C')]:
        anchor='loc_'+start+': ;';assert candidate.count(anchor)==1
        candidate=candidate.replace(anchor,anchor+'\n loop_'+start+'(ctx);goto loc_'+end+';',1)
    s=s[:a]+'// '+marker+'\n'+helpers+candidate+s[b:];path.write_text(s)
path=p/'tools/build_native.py';s=path.read_text()
if "run('native-sdk-sqrt-loops-stage'" not in s:
    anchor="run('native-game-build',";assert s.count(anchor)==1
    s=s.replace(anchor,"run('native-sdk-sqrt-loops-stage',[sys.executable,PORT/'tools/fix_sdk_sqrt_loops.py'])\n"+anchor,1);path.write_text(s)
print('SDK sqrt register-local loops installed; original polls/interior paths retained.')
