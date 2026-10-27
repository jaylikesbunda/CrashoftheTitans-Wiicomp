"""Compare actual resolver against the captured original switch+range choices."""
from pathlib import Path
from port_paths import default_storage
import argparse,bisect,hashlib,json,re,subprocess
ap=argparse.ArgumentParser();ap.add_argument('--name',default='dispatch-switch-tests');ap.add_argument('--benchmark',action='store_true');args=ap.parse_args()
PORT=Path(__file__).resolve().parents[1];work=default_storage()/'lookup-tests'
original=json.loads((work/'original-switch-cases.json').read_text())
cases={pc:(f,hle) for pc,f,hle in original}
s=(PORT/'build/crash-generated/main_output.cpp').read_text()
bounds=[(int(a,16),int(b,16),f) for a,b,f in re.findall(r'\{0x([0-9A-Fa-f]+), 0x([0-9A-Fa-f]+), (func_[0-9A-Fa-f]+)\}',s)]
names={f:i+1 for i,(a,b,f) in enumerate(bounds)}
for pc,f,hle in original:
    if f not in names:names[f]=len(names)+1
targets=set(cases)|{0,0xffffffff};high=0;ends=[]
for a,b,f in bounds:
    high=max(high,b);ends.append(high);targets.update((a-1,a,a+4,b-1,b,b+1,a+(b-a)//2))
checks=[]
for pc in sorted(targets):
    i=bisect.bisect_right(ends,pc)
    owner=bounds[i][2] if i<len(bounds) and bounds[i][0]<=pc else None
    f,hle=cases.get(pc,(owner,False));checks.append((pc,names.get(f,0),hle))
a=s.index('// NATIVE_BINARY_FUNCTION_LOOKUP_V1')
# The host-only inlined-indirect block is not part of the resolver and references CPUContext,
# which this fixture deliberately does not model; stop before it (it follows the hot cache).
end='// NATIVE_CALL_INDIRECT_V1' if '// NATIVE_CALL_INDIRECT_V1' in s[a:] else '// --- Entry Point Wrapper ---'
b=s.index(end,a)
helper=s[a:b]
helper=re.sub(r'namespace nwii::runtime \{ bool native_wpad_dispatch\(CPUContext&, uint32_t\); \}\n','',helper)
helper=helper.replace('void(*func)(CPUContext&)','uintptr_t func').replace('nullptr','0')
for name,index in sorted(names.items(),key=lambda kv:-len(kv[0])):helper=helper.replace(name,str(index))
table=''.join(f'{{0x{a:X}u,0x{b:X}u,{names[f]}}},\n' for a,b,f in bounds)+'{0,0,0}\n'
expected=''.join(f'{{0x{pc:X}u,{fn},{str(hle).lower()}}},\n' for pc,fn,hle in checks)
cpp=work/'dispatch_test.cpp'
main='''int main(){size_t failed=0,checks=0;
for(unsigned pass=0;pass<3;++pass)for(size_t i=0;i<std::size(expected);++i){
auto c=expected[pass==1?std::size(expected)-i-1:i];
for(int warm=0;warm<2;++warm){auto actual=find_dispatch_target(c.pc);++checks;
if(actual.pc!=c.pc||actual.func!=c.func||actual.hle!=c.hle)++failed;}}
std::cout<<"DISPATCH "<<checks-failed<<"/"<<checks<<std::endl;
BENCHMARK
return failed?1:0;}
'''
bench=''
if args.benchmark:
    bench='''std::array<uint32_t,32> hot{};size_t n=0;for(auto c:expected)if(c.pc>=0x803B9000 && c.pc<0x803D0000 && c.func && n<hot.size())hot[n++]=c.pc;
if(n!=hot.size())return 3;
uint64_t sum[2]={};for(int mode=0;mode<2;++mode){auto start=std::chrono::steady_clock::now();
for(size_t i=0;i<2000000;++i){auto c=mode?find_dispatch_target(hot[i%32]):find_dispatch_target_uncached(hot[i%32]);sum[mode]+=c.func+c.hle;}
std::cout<<(mode?"CACHED":"UNCACHED")<<" ns="<<std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-start).count()<<" checksum="<<sum[mode]<<std::endl;}
if(sum[0]!=sum[1])return 4;
'''
cpp.write_text('#include <array>\n#include <cstdint>\n#include <cstddef>\n#include <iostream>\n#include <chrono>\nstruct CPUContext;\nstruct FuncBound {uint32_t start,end;uintptr_t func;};\nstatic constexpr FuncBound g_func_bounds[]={\n'+table+'};\n#pragma GCC push_options\n#pragma GCC optimize("'+('O1' if args.benchmark else 'O0')+'")\n'+helper+'\n#pragma GCC pop_options\nstatic const DispatchChoice expected[]={\n'+expected+'};\n'+main.replace('BENCHMARK',bench))
exe=work/'dispatch_test.exe';p=subprocess.run(['g++','-std=c++20','-O2','-static',str(cpp),'-o',str(exe)],capture_output=True,text=True)
if p.returncode:raise SystemExit(p.stdout+p.stderr)
p=subprocess.run([str(exe)],capture_output=True,text=True);print(p.stdout+p.stderr)
report={'scope':'Actual resolver code at O0, integer identities replacing function-pointer values; compare dispatch choices, HLE return flag and target against original exact cases plus first-range fallback.',
        'original_cases':len(cases),'targets':len(checks),'exit_code':p.returncode,'output':p.stdout+p.stderr,
        'original_cases_sha256':hashlib.sha256((work/'original-switch-cases.json').read_bytes()).hexdigest()}
report['scope']='Production resolver; cold/warm lookups in ascending/descending order against original exact cases and first-range fallback. Benchmark at O1 if selected.'
report['benchmark']=args.benchmark
(PORT/'reports'/(args.name+'.json')).write_text(json.dumps(report,indent=2)+'\n')
raise SystemExit(p.returncode)
