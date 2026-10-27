"""Differential actual gather implementation, including redirects and watches."""
from pathlib import Path
from port_paths import default_storage
import subprocess,json,re,statistics
p=Path(__file__).resolve().parents[1]
w=default_storage()/'gather-span-tests';w.mkdir(exist_ok=True)
header=r'''
#include "runtime/cpu_context.h"
#include <bit>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <algorithm>
// NATIVE_DIAGNOSTIC_CACHE: the staged gx.cpp keeps its launch-fixed diagnostic helpers.
#ifndef NATIVE_LAUNCH_CACHE_NWII_SAMPLE
#define NATIVE_LAUNCH_CACHE_NWII_SAMPLE
static inline char *NativeLaunchFlag_NWII_SAMPLE() { static char *const value = std::getenv("NWII_SAMPLE"); return value; }
#endif
namespace nwii::runtime {
MMU* g_mmu=nullptr; CPUContext* g_ctx_ptr=nullptr; uint32_t g_watch_addr=0;bool g_trace_calls=false;
uint64_t watches=0;
void watch_hit(uint32_t a,uint32_t v,int n){watches=(watches^a^v^n)*1099511628211ull;}
void trace_call(uint32_t,CPUContext&){}void interpret_step(CPUContext&){std::abort();}
uint32_t HW_Reg_Read32(uint32_t){return 0;}uint16_t HW_Reg_Read16(uint32_t){return 0;}
void HW_Reg_Write32(uint32_t,uint32_t){}void HW_Reg_Write16(uint32_t,uint16_t){}
void native_gather_invalidate(); // NATIVE_GATHER_REDIRECT_CACHE_V1 (declared in runtime/hw/hw.h)
namespace hw {
// production gx.cpp reads these directly (NATIVE_GATHER_ORDINARY_FAST_V1,
// NATIVE_GATHER_REDIRECT_CACHE_V1); the wptr is the shared storage for both the
// accessor pair and the direct read.
uint32_t pi_fifo_base=0,pi_fifo_end=0,pi_fifo_wptr=0,g_cp_fifo_base=0;
uint32_t &base=pi_fifo_base,&end=pi_fifo_end,&cpbase=g_cp_fifo_base;
uint32_t &ptr=pi_fifo_wptr; // must alias the register the staged span reads directly
void pi_fifo_get(uint32_t&b,uint32_t&e,uint32_t&p){b=base;e=end;p=ptr;}
void pi_fifo_set_wptr(uint32_t p){ptr=p;}
uint32_t cp_fifo_base_reg(){return cpbase;}
}
}
using namespace nwii::runtime;
#ifdef FIFO_FIXED
// same staging buffer type as production gx.cpp (NATIVE_CPU_FIFO_FIXED_V1)
struct NativeCpuFifo{alignas(64) uint8_t data[4160];size_t n=0;bool empty()const{return n==0;}size_t size()const{return n;}void clear(){n=0;}
  const uint8_t* begin()const{return data;}const uint8_t* end()const{return data+n;}void push_back(uint8_t b){data[n++]=b;}};
static NativeCpuFifo g_cpu_fifo;static std::vector<uint8_t> g_hw_fifo;
#else
static std::vector<uint8_t> g_cpu_fifo,g_hw_fifo;
#endif
static bool g_native_fifo_ok=false; // NATIVE_FIFO_OK_FLAG_V1: the fixture clears it where it changes the FIFO configuration, like the production register writers
static std::vector<size_t> boundaries;
static void native_gx_publish(){boundaries.push_back(g_cpu_fifo.size());g_hw_fifo.insert(g_hw_fifo.end(),g_cpu_fifo.begin(),g_cpu_fifo.end());g_cpu_fifo.clear();}
static void wgp_push(uint8_t b){g_cpu_fifo.push_back(b);if(g_cpu_fifo.size()>=4096)native_gx_publish();}
'''
main=r'''
int main(){MMU memory;g_mmu=&memory;uint64_t hash=1469598103934665603ull;unsigned checks=0;
auto add=[&](uint64_t v){hash=(hash^v)*1099511628211ull;};
auto run=[&](unsigned mode,uint32_t ptr,unsigned width,unsigned seed,bool watch){
g_cpu_fifo.clear();g_hw_fifo.clear();boundaries.clear();watches=0;g_watch_addr=watch?ptr:0;
std::fill(memory.mem1.begin(),memory.mem1.end(),0xa5);std::fill(memory.mem2.begin(),memory.mem2.end(),0x5a);
hw::base=0;hw::end=0;hw::ptr=ptr;hw::cpbase=0;g_native_fifo_ok=false;native_wpar_write(0x0c008000);
switch(mode){case 1:hw::end=0x04000000;break;case 2:hw::base=ptr;hw::end=ptr+seed%9;hw::cpbase=0x100000;break;
case 3:native_wpar_write(ptr&~31u);for(unsigned i=0;i<(ptr&31u);++i)GX_WGPIPE_Write8(0);break;
case 4:hw::base=ptr;hw::end=ptr+32;hw::cpbase=ptr;break;case 5:g_mmu=nullptr;break;}g_native_fifo_ok=false;
// NATIVE_GATHER_REDIRECT_CACHE_V1: production writes these registers through hw_pi.cpp and
// hw_cp.cpp, which invalidate the cached classification; the fixture writes the globals
// directly, so it must invalidate at the same point.
#ifdef GATHER_CACHE
native_gather_invalidate();
#endif
for(unsigned i=0;i<(seed%13?seed%13:4093);++i)GX_WGPIPE_Write8(uint8_t(i));
uint64_t v=0x7fc0123401234567ull^uint64_t(seed)*0xdeadbeefull;
switch(width){case 1:GX_WGPIPE_Write8(v);break;case 2:GX_WGPIPE_Write16(v);break;case 4:GX_WGPIPE_Write32(v);break;
case 5:GX_WGPIPE_WriteF32(std::bit_cast<float>(uint32_t(v)));break;default:GX_WGPIPE_WriteF64(std::bit_cast<double>(v));}
native_gx_publish();add(hw::ptr);add(native_wpar_read());add(watches);
for(auto v:g_hw_fifo)add(v);for(auto v:boundaries)add(v);
// Hash changed memory and boundary guards across both RAM banks.
auto changed=[&](const auto& bank,uint32_t base,uint8_t fill){
uint8_t sentinel[1024];std::memset(sentinel,fill,sizeof(sentinel));
for(size_t a=0;a<bank.size();a+=1024){size_t n=std::min<size_t>(1024,bank.size()-a);
if(std::memcmp(bank.data()+a,sentinel,n)==0)continue;
for(size_t i=0;i<n;++i)if(bank[a+i]!=fill){add(base+a+i);add(bank[a+i]);}}};
changed(memory.mem1,0,0xa5);changed(memory.mem2,0x10000000,0x5a);
g_mmu=&memory;++checks;
};
for(unsigned mode=0;mode<6;++mode)for(uint32_t a:{0x10000u,0x017ffffcu,0x10000100u,0x13fffffcu,0x04000000u,0x1ffffffcu,0xfffffffcu})
for(unsigned width:{1u,2u,4u,5u,8u})for(unsigned seed=1;seed<=5;++seed)for(bool watch:{false,true})run(mode,a,width,seed,watch);
std::cout<<"GATHER_SPAN_STATE hash="<<hash<<" checks="<<checks<<'\n';
g_watch_addr=0;
for(unsigned mode=0;mode<3;++mode){for(unsigned repeat=0;repeat<5;++repeat){g_cpu_fifo.clear();g_hw_fifo.clear();boundaries.clear();
hw::base=0;hw::end=mode==1?0x04000000:0;hw::ptr=0x10000;hw::cpbase=0;g_native_fifo_ok=false;native_wpar_write(mode==2?0x80010000:0x0c008000);
auto start=std::chrono::steady_clock::now();for(unsigned i=0;i<1000000;++i){GX_WGPIPE_Write32(i);if(mode==0 && (i&1023)==1023){g_hw_fifo.clear();boundaries.clear();}}
auto us=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-start).count();
std::cout<<"GATHER_SPAN_TIME mode="<<mode<<" us="<<us<<'\n';}}
}
'''
rows=[];out={}
for version,path in [('before',p/'runtime-overlays/gather-span-before.cpp'),('after',p/'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp')]:
    s=path.read_text();a=s.index('static uint32_t g_wpar_cur');b=s.index('\n} ',s.index('void GX_WGPIPE_WriteF64',a))
    cpp=w/(version+'.cpp');exe=w/(version+'.exe');cpp.write_text(header+'namespace nwii::runtime {\n'+s[a:b]+'\n}\n'+main)
    for cmd in (['g++','-std=c++20','-O2','-fwrapv','-fno-strict-aliasing','-static']+(['-DGATHER_CACHE'] if version=='after' else [])+['-I',str(p/'build/nwii-full-source/nWiiRuntime/include'),str(cpp),'-o',str(exe)],[str(exe)]):
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=240)
        rows.append(dict(version=version,command=cmd,exit_code=r.returncode,output=r.stdout+r.stderr))
        (p/'reports/gather-span-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print(rows[-1]['output'][-2400:],flush=True)
        if r.returncode:raise SystemExit(r.returncode)
    out[version]=r.stdout
assert re.search('GATHER_SPAN_STATE.*',out['before'])[0]==re.search('GATHER_SPAN_STATE.*',out['after'])[0]
timings=[]
for mode in range(3):
    values=[statistics.median(map(int,re.findall(r'GATHER_SPAN_TIME mode='+str(mode)+r' us=(\d+)',out[v]))) for v in ('before','after')]
    timings.append(dict(mode=['ordinary','pi_redirect','wpar_redirect'][mode],before_us=values[0],after_us=values[1],speedup=values[0]/values[1]))
(p/'reports/gather-span-speedups.json').write_text(json.dumps(timings,indent=2)+'\n');print(json.dumps(timings,indent=2))
