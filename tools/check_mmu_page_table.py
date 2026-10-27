"""Inline accessors (page-table fast path) vs the original out-of-line slow paths, on random addresses (no game launch).

native_read{8,16,32,64}_slow / native_write{8,16,32,64,_f32,_f64}_slow are the pre-existing complete implementations (MEM1, MEM2,
physical 0x0C special case, holes, outside RAM); the inline accessors must be indistinguishable from them. Reads compare returned
values on one memory image; writes are applied to two MMUs (inline vs slow) and the whole of MEM1 and MEM2 is compared periodically
and at the end. Address mix: MEM1, MEM2, page 0 (including 0x0C), the holes between banks, everything above MEM2, the exact bank and
page boundaries (straddling accesses), and the 0x80000000 / 0xC0000000 mirrors. Hardware-register pages and the write-gather page are
excluded (they call into the hardware models; their dispatch is unchanged and is covered by the gather/MMIO checks). The write-side watch
test and the 'watch may hit' fall-back are covered by check_watch_span_guards.py (which exercises these same accessors).
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];work=p/'build/gap-emit/fixture';work.mkdir(parents=True,exist_ok=True)
code=r'''
#include "runtime/cpu_context.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <memory>
using namespace nwii::runtime;
namespace nwii::runtime {MMU* g_mmu=nullptr;CPUContext* g_ctx_ptr=nullptr;}
extern "C" void run_game(CPUContext&){abort();}
static uint32_t pick(std::mt19937& r){
  switch(r()%10){
    case 0:case 1:return r()%0x01800000u;                                  // MEM1
    case 2:case 3:return 0x10000000u+r()%0x04000000u;                      // MEM2
    case 4:return r()%0x10000u;                                            // page 0 (incl. 0x0C)
    case 5:return 0x01800000u+r()%(0x0C000000u-0x01800000u);               // hole between MEM1 and MMIO
    case 6:return 0x0D010000u+r()%(0x10000000u-0x0D010000u);               // hole between MMIO and MEM2
    case 7:return 0x14000000u+r()%(0x40000000u-0x14000000u);               // above MEM2
    case 8:{static const uint32_t edges[]={0x017FFFF0,0x01800000,0x0000FFF0,0x00010000,0x13FFFFF0,0x14000000,0x0FFFFFF0,0x10000000,0x0000000C,0x00000008,0x00000010,0x0001FFFC};
            return edges[r()%12]+(r()%24)-8;}
    default:return r()%0x40000000u;}
}
static bool excluded(uint32_t a,uint32_t bytes){
  // hardware-register windows and the write-gather page call into the hardware models
  for(uint32_t i=0;i<bytes;++i){uint32_t q=(a+i)&0x3fffffffu;
    if((q>=0x0C000000u&&q<0x0C010000u)||(q>=0x0D000000u&&q<0x0D010000u)||(q&0xfffff000u)==0x0c008000u)return true;}
  return false;
}
int main(){
  g_watch_addr=0;
  auto A=std::make_unique<CPUContext>(),B=std::make_unique<CPUContext>();MMU& m=A->mmu;
  std::mt19937 rng(8675309);
  for(size_t i=0;i<m.mem1.size();i+=4)*reinterpret_cast<uint32_t*>(m.mem1.data()+i)=rng();
  for(size_t i=0;i<m.mem2.size();i+=4)*reinterpret_cast<uint32_t*>(m.mem2.data()+i)=rng();
  std::memcpy(B->mmu.mem1.data(),m.mem1.data(),m.mem1.size());std::memcpy(B->mmu.mem2.data(),m.mem2.data(),m.mem2.size());
  unsigned long reads=0,write_ops=0,bad=0;
  for(unsigned n=0;n<4000000;++n){
    const uint32_t base=pick(rng),mirror=(rng()&1)?0x80000000u:((rng()&1)?0xC0000000u:0u),addr=base|mirror;
    const unsigned op=rng()%10;
    if(op<5){ // read
      const unsigned w=op%4;static const unsigned bytes[]={1,2,4,8};if(excluded(addr,bytes[w]))continue;++reads;
      bool same=true;
      switch(w){case 0:same=m.read8(addr)==m.native_read8_slow(addr);break;case 1:same=m.read16(addr)==m.native_read16_slow(addr);break;
                case 2:same=m.read32(addr)==m.native_read32_slow(addr);break;default:same=m.read64(addr)==m.native_read64_slow(addr);}
      if(op==4){float f=m.read_f32(addr);uint32_t u=m.native_read32_slow(addr);float g;std::memcpy(&g,&u,4);same=same&&std::memcmp(&f,&g,4)==0;}
      if(!same){if(!bad)printf("READ MISMATCH addr=%08X width=%u\n",addr,bytes[w]);++bad;}
    } else { // write the same value through both paths
      const unsigned w=op-5;static const unsigned bytes[]={1,2,4,8,4};if(w>4)continue;if(excluded(addr,bytes[w]))continue;++write_ops;
      const uint64_t v=(uint64_t(rng())<<32)|rng();
      switch(w){case 0:A->mmu.write8(addr,uint8_t(v));B->mmu.native_write8_slow(addr,uint8_t(v));break;
                case 1:A->mmu.write16(addr,uint16_t(v));B->mmu.native_write16_slow(addr,uint16_t(v));break;
                case 2:A->mmu.write32(addr,uint32_t(v));B->mmu.native_write32_slow(addr,uint32_t(v));break;
                case 3:A->mmu.write64(addr,v);B->mmu.native_write64_slow(addr,v);break;
                default:{uint32_t u=uint32_t(v);float f;std::memcpy(&f,&u,4);A->mmu.write_f32(addr,f);B->mmu.native_write_f32_slow(addr,f);}}
      if((write_ops&0xFFFF)==0&&(std::memcmp(A->mmu.mem1.data(),B->mmu.mem1.data(),A->mmu.mem1.size())||std::memcmp(A->mmu.mem2.data(),B->mmu.mem2.data(),A->mmu.mem2.size()))){
        if(!bad)printf("WRITE MISMATCH after op %lu (addr=%08X width=%u)\n",write_ops,addr,bytes[w]);++bad;}
    }
  }
  if(std::memcmp(A->mmu.mem1.data(),B->mmu.mem1.data(),A->mmu.mem1.size())||std::memcmp(A->mmu.mem2.data(),B->mmu.mem2.data(),A->mmu.mem2.size())){++bad;printf("FINAL IMAGE MISMATCH\n");}
  // write_f64 against the slow path
  for(unsigned n=0;n<200000;++n){uint32_t addr=pick(rng)|((rng()&1)?0x80000000u:0u);if(excluded(addr,8))continue;double d=double(int64_t(rng())<<16)/3.0;A->mmu.write_f64(addr,d);B->mmu.native_write_f64_slow(addr,d);}
  if(std::memcmp(A->mmu.mem1.data(),B->mmu.mem1.data(),A->mmu.mem1.size())||std::memcmp(A->mmu.mem2.data(),B->mmu.mem2.data(),A->mmu.mem2.size())){++bad;printf("F64 IMAGE MISMATCH\n");}
  printf("MMU_PAGE_TABLE reads=%lu writes=%lu mismatching=%lu\n",reads,write_ops,bad);
  return bad?1:0;
}
'''
(work/'mmu_test.cpp').write_text(code)
cm=p/'build/nwii-full-source/CMakeLists.txt';t=cm.read_text();marker='# NATIVE_MMU_TEST_V1'
if marker not in t:
    t+='\n'+marker+'\nadd_executable(crash_mmu_tests "'+(work/'mmu_test.cpp').as_posix()+'")\ntarget_link_libraries(crash_mmu_tests PRIVATE nwiiruntime)\ntarget_compile_options(crash_mmu_tests PRIVATE -O2 -fwrapv -fno-strict-aliasing)\n'
    cm.write_text(t)
print('mmu page-table fixture written')
