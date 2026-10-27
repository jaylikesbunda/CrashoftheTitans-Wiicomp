"""Actual helper hooks: aliases, wrong-direction overlaps, edges, MMIO and watches."""
from pathlib import Path
from port_paths import default_storage
import ast,json,re,statistics,subprocess
p=Path(__file__).resolve().parents[1];w=default_storage()/'bulk-helper-tests';w.mkdir(exist_ok=True)
tree=ast.parse((p/'tools/check_inline_gather_mmio.py').read_text());fixture=next(ast.literal_eval(n.args[0]) for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='write_text')
prefix=fixture[:fixture.index('int main()')]
main=r'''
int main(){auto cp=std::make_unique<CPUContext>();auto& c=*cp;uint64_t hash=1469598103934665603ull;auto add=[&](uint64_t v){hash=(hash^v)*1099511628211ull;};uint32_t rng=123;unsigned checks=0;
auto code=[&](bool fill){g_watch_addr=0;const uint32_t copy[]={0x7C041840,0x41800028,0x3884FFFF,0x38C3FFFF,0x38A50001};const uint32_t set[]={0x28050020,0x5484063E,0x38C3FFFF,0x7C872378,0};for(unsigned j=0;j<5;++j)c.mmu.write32(0x80010000+j*4,fill?set[j]:copy[j]);c.pc=0x80010000;c.lr=0x81234560;};
for(unsigned trial=0;trial<12000;++trial){rng=rng*1664525u+1013904223u;bool fill=trial&1;code(fill);uint32_t d=0x80020000+(rng&127),r=0x80022000+(rng&127);unsigned n=rng%513;
switch(trial%12){case 0:r=d-4;break;case 1:r=d+4;break;case 2:r=(d&0x3fffffffu)+4;break;case 3:d=(r&0x3fffffffu)+4;break;case 4:r=d;break;case 5:d=0x90020000+(rng&127);break;case 6:d=0xd0020000+(rng&127);r=0x50020000;break;case 7:d=0x817ffff0;break;case 8:r=0x93fffff0;break;case 9:d=0xcc004000;break;case 10:r=0xcc004000;break;default:d=0x70000000;}
for(unsigned j=0;j<1024;++j){c.mmu.mem1[0x20000+j]=uint8_t(j^rng);c.mmu.mem1[0x22000+j]=uint8_t(j+rng);c.mmu.mem2[0x20000+j]=uint8_t(j-rng);}
c.gpr[3]=d;c.gpr[4]=fill?rng:r;c.gpr[5]=n;g_watch_addr=trial%7?0:d;add(try_native_helper(c));add(c.pc);add(c.inst_count);add(observations);for(auto v:c.gpr)add(v);
auto span=[&](uint32_t a){uint32_t off=a&0x3fffffffu;const auto* bank=&c.mmu.mem1;if(off>=0x10000000){off-=0x10000000;bank=&c.mmu.mem2;}if(off<bank->size())for(unsigned j=0;j<n+8&&off+j<bank->size();++j)add((*bank)[off+j]);};span(d);span(r);++checks;}
for(auto v:c.mmu.mem1)add(v);for(auto v:c.mmu.mem2)add(v);std::cout<<"BULK_HELPER_STATE hash="<<hash<<" checks="<<checks<<'\n';
for(unsigned fill=0;fill<2;++fill)for(unsigned repeat=0;repeat<5;++repeat){code(fill);g_watch_addr=0;auto start=std::chrono::steady_clock::now();uint64_t sum=0;for(unsigned i=0;i<2000;++i){c.pc=0x80010000;c.gpr[3]=0x90040000;c.gpr[4]=fill?uint32_t(i):0x80040000;c.gpr[5]=65536;try_native_helper(c);sum+=c.mmu.mem2[0x40000]+c.mmu.mem2[0x4ffff];}std::cout<<"BULK_HELPER_TIME fill="<<fill<<" us="<<std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-start).count()<<" hash="<<sum<<'\n';}}
'''
rows=[];outputs={}
for version,path in [('before',p/'runtime-overlays/bulk-helpers-before.cpp'),('after',p/'build/nwii-full-source/nWiiRuntime/src/hle/interpreter.cpp')]:
    s=path.read_text();a=s.index('static bool try_native_helper(CPUContext& ctx) {') if version=='before' else s.index('// NATIVE_BULK_HELPERS_V1');b=s.index('\nvoid interpret_step(',a);m='// NATIVE_INTERP_HISTOGRAM_V1';b=min(b,s.index(m,a)) if m in s[a:b] else b
    cpp=w/(version+'.cpp');exe=w/(version+'.exe');cpp.write_text(prefix+'#include <memory>\n'+s[a:b]+main)
    cmd=['g++','-std=c++20','-O2','-fwrapv','-fno-strict-aliasing','-ffp-contract=off','-static','-I',str(p/'build/nwii-full-source/nWiiRuntime/include'),str(cpp),'-o',str(exe)]
    for command in (cmd,[str(exe)]):
        r=subprocess.run(command,capture_output=True,text=True,timeout=180);rows.append(dict(version=version,command=command,exit_code=r.returncode,output=r.stdout+r.stderr));(p/'reports/bulk-helper-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print((r.stdout+r.stderr)[-2200:],flush=True)
        if r.returncode:raise SystemExit(r.returncode)
    outputs[version]=r.stdout
assert re.search('BULK_HELPER_STATE.*',outputs['before'])[0]==re.search('BULK_HELPER_STATE.*',outputs['after'])[0]
results=[]
for fill in (0,1):
    times=[statistics.median(map(int,re.findall('BULK_HELPER_TIME fill='+str(fill)+r' us=(\d+)',outputs[k]))) for k in ('before','after')]
    results.append(dict(operation='fill' if fill else 'copy',bytes=65536,before_us=times[0],after_us=times[1],speedup=times[0]/times[1]))
(p/'reports/bulk-helper-speedups.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
