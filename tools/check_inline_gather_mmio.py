"""Actual MMU headers: whole store/watch/RAM state, mirrored gather page and bits."""
from pathlib import Path
from port_paths import default_storage
import subprocess,json,re,statistics,shutil
p=Path(__file__).resolve().parents[1];w=default_storage()/'gather-inline-tests';w.mkdir(exist_ok=True)
inc=w/'before/runtime';inc.mkdir(parents=True,exist_ok=True);shutil.copyfile(p/'runtime-overlays/gather-inline-before.h',inc/'cpu_context.h')
cpp=w/'test.cpp';cpp.write_text(r'''
#include "runtime/cpu_context.h"
#include <bit>
#include <iostream>
#include <chrono>
namespace nwii::runtime {
bool g_trace_calls=false;uint32_t g_watch_addr=0;uint64_t observations=1469598103934665603ull;bool timing=false;uint64_t fifo_count=0;
void observe(uint32_t a,uint64_t v,unsigned w){observations=(observations^a^v^w)*1099511628211ull;}
void watch_hit(uint32_t a,uint32_t v,int w){observe(a,v,w);}
void trace_call(uint32_t,CPUContext&){}void interpret_step(CPUContext&){std::abort();}
__attribute__((noinline)) void GX_WGPIPE_Write8(uint8_t v){observe(0xcc008000,v,8);}
__attribute__((noinline)) void GX_WGPIPE_Write16(uint16_t v){observe(0xcc008000,v,16);}
__attribute__((noinline)) void GX_WGPIPE_Write32(uint32_t v){if(timing){fifo_count+=v;return;}observe(0xcc008000,v,32);}
__attribute__((noinline)) void GX_WGPIPE_WriteF32(float v){observe(0xcc008000,std::bit_cast<uint32_t>(v),32);}
__attribute__((noinline)) void GX_WGPIPE_WriteF64(double v){observe(0xcc008000,std::bit_cast<uint64_t>(v),64);}
uint16_t HW_Reg_Read16(uint32_t a){observe(a,0,16);return a^0xaac3;}uint32_t HW_Reg_Read32(uint32_t a){observe(a,0,32);return a^0xabcdef;}
void HW_Reg_Write16(uint32_t a,uint16_t v){observe(a,v,16);}void HW_Reg_Write32(uint32_t a,uint32_t v){observe(a,v,32);}
}
using namespace nwii::runtime;
int main(){MMU m;uint64_t hash=1469598103934665603ull;unsigned checks=0;auto add=[&](uint64_t v){hash=(hash^v)*1099511628211ull;};
uint32_t rng=0x98765432;auto next=[&](){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;};
for(unsigned i=0;i<300000;++i){uint32_t a=next();switch(i%10){case 0:a=a%32768;break;case 1:a=0x80000000+a%32768;break;case 2:a=0xc0000000+a%32768;break;case 3:a=0x90000000+a%32768;break;case 4:a=0x817fffe0+a%64;break;case 5:a=0xcc004000+a%128;break;default:a=((i&3)*0x40000000u)|0x0c008000u|(a&4095);}
g_watch_addr=i%5?0:a;uint32_t value=next();uint64_t bits=uint64_t(value)<<32|next();
switch(i%6){case 0:m.write8(a,value);break;case 1:m.write16(a,value);break;case 2:m.write32(a,value);break;case 3:m.write64(a,bits);break;case 4:m.write_f32(a,std::bit_cast<float>(value));break;default:m.write_f64(a,std::bit_cast<double>(bits));}
add(m.read64(a));add(observations);++checks;}
for(auto v:m.mem1)add(v);for(auto v:m.mem2)add(v);
std::cout<<"GATHER_INLINE_STATE hash="<<hash<<" observer="<<observations<<" checks="<<checks<<'\n';g_watch_addr=0;timing=true;
for(unsigned repeat=0;repeat<7;++repeat){fifo_count=0;auto start=std::chrono::steady_clock::now();for(unsigned i=0;i<10000000;++i)m.write32(0xcc008000,i);
std::cout<<"GATHER_INLINE_TIME us="<<std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-start).count()<<" checksum="<<fifo_count<<'\n';}
}
''')
rows=[];outputs={}
for version in ('before','after'):
    exe=w/(version+'.exe');cmd=['g++','-std=c++20','-O2','-fwrapv','-fno-strict-aliasing','-ffp-contract=off','-static']
    if version=='before':cmd+=['-I',str(w/'before')]
    cmd+=['-I',str(p/'build/nwii-full-source/nWiiRuntime/include'),str(cpp),'-o',str(exe)]
    for command in (cmd,[str(exe)]):
        r=subprocess.run(command,capture_output=True,text=True,timeout=180);rows.append(dict(version=version,command=command,exit_code=r.returncode,output=r.stdout+r.stderr))
        (p/'reports/gather-inline-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print(rows[-1]['output'][-1800:],flush=True)
        if r.returncode:raise SystemExit(r.returncode)
    outputs[version]=r.stdout
assert re.search('GATHER_INLINE_STATE.*',outputs['before'])[0]==re.search('GATHER_INLINE_STATE.*',outputs['after'])[0]
v=[statistics.median(map(int,re.findall(r'GATHER_INLINE_TIME us=(\d+)',outputs[key]))) for key in ('before','after')]
result=dict(pass_=True,checks=300000,before_us=v[0],after_us=v[1],speedup=v[0]/v[1],limitation='Stubbed gather sink measures wrapper overhead; not a full FIFO or frame benchmark')
(p/'reports/gather-inline-speedup.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
