"""Adopt qualified, materially faster bounded leaf kernels; retain original paths."""
from guest_leaf_kernels import PORT,candidate,PREFIX,leaf_source
import json,re,hashlib,statistics
p=PORT;rows=json.loads((p/'data/guest-plain-family-tests.json').read_text());assert all(r['exit_code']==0 for r in rows)
evidence=rows[-1]['output'];family=json.loads((p/'data/plain-leaf-family.json').read_text())
assert 'ACTUAL_FP_STATES '+str(len(family)*400)+'/'+str(len(family)*400) in evidence
edge=json.loads((p/'data/guest-plain-edge-tests.json').read_text())
assert all(r['exit_code']==0 for r in edge) and 'ACTUAL_FP_STATES '+str(len(family)*64)+'/'+str(len(family)*64) in edge[-1]['output']
qualified=[]
import region
for r in family:r['file']=region.chunk_file(r['address'],r['file']) # another release splits its functions into different chunks
special=json.loads((p/'data/plain-leaf-special-fallbacks.json').read_text()) if (p/'data/plain-leaf-special-fallbacks.json').exists() else dict(rejected=[])
for r in family:
    addr=r['address'];timings=re.findall(r'pc='+addr.lower()+r' before_us=(\d+) after_us=(\d+)',evidence)
    assert len(timings)==3,addr
    before=statistics.median(int(a) for a,b in timings);after=statistics.median(int(b) for a,b in timings)
    # Avoid speculative adoption of tiny/noisy marginal improvements.
    if before/after<1.5 or addr in special['rejected']:continue
    source=leaf_source(r)
    qualified.append(dict(**r,before_median_us=before,after_median_us=after,component_speedup=before/after))
support=p/'runtime-overlays/native_plain_leaf_support.h'
value='#pragma once\n#include "runtime/cpu_context.h"\n'+PREFIX
if not support.exists() or support.read_text()!=value:support.write_text(value)
byfile={}
for r in qualified:byfile.setdefault(r['file'],[]).append(r)
for r in family:byfile.setdefault(r['file'],[])
for file,targets in byfile.items():
    path=p/'build/crash-generated'/file;s=path.read_text()
    for r in targets:
        addr=r['address'];begin='// BEGIN_NATIVE_PLAIN_LEAF_'+addr;end='// END_NATIVE_PLAIN_LEAF_'+addr
        source=leaf_source(r)
        if begin in s:
            a=s.index(begin);z=s.index(end,a)+len(end)
        else:
            a=s.index('void func_'+addr+'(CPUContext& ctx) {');z=a+len(source.rstrip())
            assert s[a:z].rstrip()==source.rstrip(),'Generated source changed: requalify '+addr
        while z<len(s) and s[z]=='\n':z+=1
        slow=source.replace('void func_'+addr,'static void native_original_'+addr,1)
        native=candidate(source,addr,'func_'+addr,plain=True).replace('else func_'+addr+'(ctx);','else native_original_'+addr+'(ctx);')
        s=s[:a]+begin+'\n'+slow+'\n'+native+'\n'+end+'\n\n'+s[z:]
    selected={r['address'] for r in targets}
    for r in family:
        if r['file']!=file or r['address'] in selected:continue
        addr=r['address'];begin='// BEGIN_NATIVE_PLAIN_LEAF_'+addr;end='// END_NATIVE_PLAIN_LEAF_'+addr
        if begin in s:
            a=s.index(begin);z=s.index(end,a)+len(end)
            while z<len(s) and s[z]=='\n':z+=1
            source=leaf_source(r);s=s[:a]+source.rstrip()+'\n\n'+s[z:]
    include='#include "'+support.as_posix()+'"'
    if include not in s:s=s.replace('#include "functions.h"','#include "functions.h"\n'+include,1)
    if path.read_text()!=s:path.write_text(s)
(p/'reports/native-plain-leaf-adoption.json').write_text(json.dumps(dict(functions=qualified,total=len(qualified),guard='normal entry,FP enabled,GQR0=0,no trace/disable flag,bounded ordinary MEM1 roots whose conservative spans exclude the watch',fallback='original complete translated function'),indent=2)+'\n')
print('Native bounded leaf kernels staged:',len(qualified));print([(r['address'],round(r['component_speedup'],2)) for r in qualified])
