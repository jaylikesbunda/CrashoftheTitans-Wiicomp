"""One RAM preflight for a PSQ pair; joint ordinary-FIFO emission probe."""
from pathlib import Path
from port_paths import default_storage
import subprocess,json,os
p=Path(__file__).resolve().parents[1];b=default_storage();w=b/'psq-pair-probe';w.mkdir(exist_ok=True)
code=r'''#include "runtime/cpu_context.h"
#include "runtime/hw/hw.h"
#include <bit>
#include <chrono>
#include <algorithm>
#include <memory>
#include <random>
#include <cstdio>
using namespace nwii::runtime;
namespace nwii::runtime {CPUContext*g_ctx_ptr=nullptr;MMU*g_mmu=nullptr;void native_gx_publish();extern bool g_native_fifo_ok;extern std::atomic<size_t>g_gx_pending_bytes;}
extern "C" void run_game(CPUContext&){std::abort();}
extern std::vector<uint8_t>g_hw_fifo;
__attribute__((noinline)) CPUContext::NativePsqPair LegacyLoad(CPUContext& c,uint32_t addr){return c.psq_load_pair(addr,0,0);}
__attribute__((noinline)) CPUContext::NativePsqPair PairLoad(CPUContext& c,uint32_t addr){
 uint32_t gqr=c.gqr[0],type=(gqr>>16)&7,paddr=addr&0x3fffffffu;unsigned bytes=type==0?8:type==4||type==6?2:4;
 intptr_t bias=c.mmu.page_bias[paddr>>16];
 if((type==0||type>=4) && bias>1 && (paddr&65535)<=65536-bytes){const uint8_t* ptr=reinterpret_cast<const uint8_t*>(bias+paddr);
  if(type==0){uint64_t bits;memcpy(&bits,ptr,8);bits=__builtin_bswap64(bits);return {double(std::bit_cast<float>(uint32_t(bits>>32))),double(std::bit_cast<float>(uint32_t(bits)))};}
  const int scale_index=int((gqr>>24)&63);const int signed_scale=scale_index&32?scale_index-64:scale_index;float scale=std::bit_cast<float>(uint32_t(127-signed_scale)<<23);
  if(type==4)return {ptr[0]*scale,ptr[1]*scale};if(type==6)return {int8_t(ptr[0])*scale,int8_t(ptr[1])*scale};
  uint32_t bits;memcpy(&bits,ptr,4);bits=__builtin_bswap32(bits);if(type==5)return {uint16_t(bits>>16)*scale,uint16_t(bits)*scale};return {int16_t(bits>>16)*scale,int16_t(bits)*scale};
 }
 return c.psq_load_pair(addr,0,0);
}
__attribute__((noinline)) void LegacyStore(CPUContext& c,uint32_t addr,double a,double z){c.psq_store_val(a,z,addr,0,0);}
__attribute__((noinline)) void PairStore(CPUContext& c,uint32_t addr,double a,double z){
 if((c.gqr[0]&7)!=0){c.psq_store_val(a,z,addr,0,0);return;}
 uint32_t paddr=addr&0x3fffffffu;intptr_t bias=c.mmu.page_bias[paddr>>16];
 if(bias>1 && (paddr&65535)<=65528 && !native_watch_span_may_hit(paddr,8)){
  float af=float(a),zf=float(z);uint64_t bits=(uint64_t(std::bit_cast<uint32_t>(af))<<32)|std::bit_cast<uint32_t>(zf);bits=__builtin_bswap64(bits);memcpy(reinterpret_cast<uint8_t*>(bias+paddr),&bits,8);return;
 }
 if(g_native_fifo_ok && (paddr&0xfffff000u)==0x0c008000u && (paddr&4095)<=4088 && !native_watch_span_may_hit(paddr,8)){
  float af=float(a),zf=float(z);uint64_t bits=(uint64_t(std::bit_cast<uint32_t>(af))<<32)|std::bit_cast<uint32_t>(zf);GX_WGPIPE_WriteF64(std::bit_cast<double>(bits));return;
 }
 c.psq_store_val(a,z,addr,0,0);
}
std::vector<uint8_t> TakeFifo(){native_gx_publish();auto result=g_hw_fifo;g_hw_fifo.clear();g_gx_pending_bytes=0;return result;}
int main(){auto cp=std::make_unique<CPUContext>();auto& c=*cp;g_ctx_ptr=cp.get();g_mmu=&c.mmu;std::mt19937 rng(521461);unsigned checks=0;
for(unsigned trial=0;trial<60000;++trial){uint32_t addr=trial%13==0?0x8000fffcu:trial%17==0?0x817ffffcu:trial%19==0?0x80000008u:0x80010000u+(rng()%65520);
 uint32_t type=trial%5==0?0:4+trial%4;c.gqr[0]=(type<<16)|((trial%64)<<24);for(unsigned j=0;j<8;++j)c.mmu.get_ptr(addr)[j]=uint8_t(rng());
 auto x=LegacyLoad(c,addr),y=PairLoad(c,addr);if(std::bit_cast<uint64_t>(x.f)!=std::bit_cast<uint64_t>(y.f)||std::bit_cast<uint64_t>(x.p)!=std::bit_cast<uint64_t>(y.p)){printf("PAIR_LOAD_FAIL %u\n",trial);return 1;}++checks;
 c.gqr[0]=0;double a=double(std::bit_cast<float>(uint32_t(rng()))),z=double(std::bit_cast<float>(uint32_t(rng())));LegacyStore(c,addr,a,z);uint8_t before[8];memcpy(before,c.mmu.get_ptr(addr),8);PairStore(c,addr,a,z);if(memcmp(before,c.mmu.get_ptr(addr),8)){printf("PAIR_STORE_FAIL %u\n",trial);return 2;}++checks;
}
// Include split publication at every byte offset around the 4 KiB boundary.
for(unsigned offset=4080;offset<4112;++offset){c.gqr[0]=0;for(unsigned i=0;i<offset;++i)GX_WGPIPE_Write8(uint8_t(i));LegacyStore(c,0xcc008000,.125,-.25);auto a=TakeFifo();for(unsigned i=0;i<offset;++i)GX_WGPIPE_Write8(uint8_t(i));PairStore(c,0xcc008000,.125,-.25);auto z=TakeFifo();if(a!=z)return 3;++checks;}
printf("PSQ_PAIR_STATES %u/%u\n",checks,checks);
for(unsigned kind=0;kind<7;++kind){std::vector<double> times[2],paired;uint64_t sums[2]{};
 for(unsigned round=0;round<10;++round){double ns[2]{};for(unsigned order=0;order<2;++order){unsigned mode=(round+order)%2;c.gqr[0]=kind<5?((kind==0?0:kind+3)<<16)|(7u<<24):0;uint64_t sum=0;
 auto start=std::chrono::steady_clock::now();for(unsigned i=0;i<300000;++i){uint32_t addr=0x80010000+(i&1023)*8;
 if(kind<5){auto value=mode?PairLoad(c,addr):LegacyLoad(c,addr);sum+=std::bit_cast<uint64_t>(value.f)^std::bit_cast<uint64_t>(value.p);}
 else{if(kind==6)addr=0xcc008000;if(mode)PairStore(c,addr,.125,-.25);else LegacyStore(c,addr,.125,-.25);if(kind==6 && (i&1023)==1023)TakeFifo();}}
 auto end=std::chrono::steady_clock::now();if(kind==6)TakeFifo();ns[mode]=std::chrono::duration<double,std::nano>(end-start).count()/300000;sums[mode]=sum;if(round>=2)times[mode].push_back(ns[mode]);}
 if(sums[0]!=sums[1])return 4;if(round>=2)paired.push_back(ns[0]-ns[1]);}
 for(auto& t:times)std::sort(t.begin(),t.end());std::sort(paired.begin(),paired.end());printf("PSQ_PAIR_BENCH kind=%u old_ns=%.3f pair_ns=%.3f saved_ns=%.3f\n",kind,times[0][times[0].size()/2],times[1][times[1].size()/2],paired[paired.size()/2]);
}}
'''
cpp=w/'fixture.cpp';cpp.write_text(code);cm=p/'build/nwii-full-source/CMakeLists.txt';s=cm.read_text();marker='# NATIVE_PSQ_PAIR_PROBE_V1'
if marker not in s:cm.write_text(s+'\n'+marker+'\nadd_executable(crash_psq_pair_probe "'+cpp.as_posix()+'")\ntarget_link_libraries(crash_psq_pair_probe PRIVATE nwiiruntime)\ntarget_compile_options(crash_psq_pair_probe PRIVATE -Os -mfma -fwrapv -fno-strict-aliasing -ffp-contract=off)\n')
runs=[]
for cmd in (['cmake','--build',str(b/'toolchain'),'--target','crash_psq_pair_probe','-j','4'],[str(b/'toolchain/crash_psq_pair_probe.exe')]):
 env={k:v for k,v in os.environ.items() if not k.startswith('NWII_')};env['NWII_NO_DIAG']='1';x=subprocess.run(cmd,capture_output=True,text=True,timeout=600,env=env);runs.append(dict(command=cmd,exit_code=x.returncode,output=x.stdout+x.stderr));print((x.stdout+x.stderr)[-3500:],flush=True)
 if x.returncode:break
(p/'reports/psq-pair-probe.json').write_text(json.dumps(dict(runs=runs,game_launched=False),indent=2)+'\n');raise SystemExit(x.returncode)
