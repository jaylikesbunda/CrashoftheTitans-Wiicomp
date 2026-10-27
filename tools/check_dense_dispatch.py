"""Exhaustive word-PC map equivalence and realistic cold/hot lookup timing."""
from pathlib import Path
from port_paths import default_storage
import json,re,subprocess,statistics
p=Path(__file__).resolve().parents[1];w=default_storage()/'dense-dispatch-tests';w.mkdir(exist_ok=True)
before=(p/'runtime-overlays/dense-dispatch-before.cpp').read_text();after=(p/'build/crash-generated/main_output.cpp').read_text()
names=sorted(set(re.findall(r'\bfunc_[0-9A-Fa-f]{8}\b',before)))
def extract(s):
 a=s.index('struct FuncBound')
 # The host-only inlined-indirect block is not part of the resolver and references
 # CPUContext, which this fixture deliberately does not model; stop before it (it
 # follows the hot cache). Same cut as tools/check_dispatch_switch.py.
 end='// NATIVE_CALL_INDIRECT_V1' if '// NATIVE_CALL_INDIRECT_V1' in s[a:] else '// --- Entry Point Wrapper ---'
 b=s.index(end,a);s=s[a:b]
 s=re.sub(r'namespace nwii::runtime \{ bool native_wpad_dispatch\(CPUContext&, uint32_t\); \}\n','',s)
 s=s.replace('void(*func)(CPUContext&)', 'uintptr_t func').replace('void (*func)(CPUContext&)', 'uintptr_t func').replace('nullptr','0')
 s=s.replace('void(*)(CPUContext&)','uintptr_t').replace('void (*)(CPUContext&)','uintptr_t')
 for i,name in enumerate(names):s=s.replace(name,str(i+1))
 return s
old=extract(before);new=extract(after)
main=r'''
int main(){unsigned checks=0,failed=0;auto verify=[&](uint32_t pc){auto a=before::find_dispatch_target(pc);auto b=after::find_dispatch_target(pc);++checks;if(a.pc!=b.pc||a.func!=b.func||a.hle!=b.hle)++failed;};
uint32_t low=before::g_func_bounds[0].start,high=low;for(auto& b:before::g_func_bounds)high=std::max(high,b.end);
for(uint32_t pc=low;pc<high;pc+=4)verify(pc);
for(auto& b:before::g_func_bounds)for(uint32_t pc:{b.start-1,b.start+1,b.end-1,b.end,b.end+1})verify(pc);
uint32_t rng=123;for(unsigned i=0;i<100000;++i){rng=rng*1664525+1013904223;verify(rng);}verify(0);verify(0xffffffff);
std::cout<<"DENSE_STATES "<<checks<<" failed="<<failed<<'\n';if(failed)return 1;
std::vector<uint32_t> stream;for(auto& b:before::g_func_bounds)if(b.start)stream.push_back(b.start);
for(unsigned pattern=0;pattern<3;++pattern)for(unsigned repeat=0;repeat<5;++repeat)for(unsigned mode=0;mode<2;++mode){
auto begin=std::chrono::steady_clock::now();uint64_t sum=0;for(unsigned i=0;i<3000000;++i){const auto pc=stream[pattern==0?i%32:pattern==1?i%stream.size():(size_t(i)*7919)%stream.size()];
if(mode){auto c=after::find_dispatch_target(pc);sum+=c.func+c.hle;}else{auto c=before::find_dispatch_target(pc);sum+=c.func+c.hle;}}
std::cout<<"DENSE_TIME pattern="<<pattern<<" mode="<<mode<<" ns="<<std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-begin).count()<<" hash="<<sum<<'\n';}}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text('#include <array>\n#include <vector>\n#include <algorithm>\n#include <cstdint>\n#include <cstddef>\n#include <iostream>\n#include <chrono>\nnamespace before{\n'+old+'}\nnamespace after{\n'+new+'}\n'+main)
rows=[]
for cmd in (['g++','-std=c++20','-O2','-static',str(cpp),'-o',str(exe)],[str(exe)]):
 r=subprocess.run(cmd,capture_output=True,text=True,timeout=180);rows.append(dict(command=cmd,exit_code=r.returncode,output=r.stdout+r.stderr));(p/'reports/dense-dispatch-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print((r.stdout+r.stderr)[-4000:],flush=True)
 if r.returncode:raise SystemExit(r.returncode)
results=[]
for pattern in range(3):
 times=[statistics.median(map(int,re.findall('DENSE_TIME pattern='+str(pattern)+' mode='+str(mode)+r' ns=(\d+)',r.stdout))) for mode in (0,1)]
 results.append(dict(pattern=['hot32','all_sequential','all_permuted'][pattern],before_ns=times[0],after_ns=times[1],speedup=times[0]/times[1]))
(p/'reports/dense-dispatch-speedups.json').write_text(json.dumps(results,indent=2)+'\n');print(results)
