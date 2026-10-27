#include "stubs.h"
#include "runtime/native_guest_arena.h"
#include <memory>
#include <random>
#include <cassert>
using namespace nwii::runtime;
#define CHECK(c) do{ if(!(c)){ std::printf("FAIL %s:%d %s\n",__FILE__,__LINE__,#c); std::exit(1);} }while(0)
int main(){
  auto ctx=std::make_unique<CPUContext>(); MMU& m=ctx->mmu; g_mmu=&m;
  CHECK(native_arena::active());
  CHECK(m.mem1.data()==(uint8_t*)0x20000000u && m.mem2.data()==(uint8_t*)0x30000000u);
  std::mt19937_64 rng(1);
  for(size_t i=0;i<m.mem1.size()-8;i+=8){uint64_t v=rng();std::memcpy(m.mem1.data()+i,&v,8);}
  for(size_t i=0;i<m.mem2.size()-8;i+=8){uint64_t v=rng();std::memcpy(m.mem2.data()+i,&v,8);}
  m.mem1[12]=m.mem1[13]=m.mem1[14]=m.mem1[15]=0;
  unsigned checks=0;
  // every mirror of every bank sees the same bytes as the quadrant-0 views (and so as MMU::mem1/mem2)
  for(unsigned q=0;q<4;++q){
    for(int bank=0;bank<2;++bank){
      const uint8_t* ref=bank?m.mem2.data():m.mem1.data();
      const uint8_t* view=(const uint8_t*)(native_arena::base()+(uintptr_t)q*0x40000000u+(bank?0x10000000u:0));
      const size_t n=bank?64u<<20:24u<<20;
      CHECK(std::memcmp(ref,view,n)==0); ++checks;
    }
  }
  // writes through a view are visible through all the others, the guest accessors, and the MMU
  for(int round=0;round<2000;++round){
    unsigned q=rng()%4; int bank=rng()%2;
    const size_t n=bank?64u<<20:24u<<20;
    size_t off=(rng()%(n-8))&~size_t(3); if(!bank&&off<4096) off+=4096; // page 0 is covered by the fault tests
    uint32_t v=(uint32_t)rng();
    uint8_t* w=(uint8_t*)(native_arena::base()+(uintptr_t)q*0x40000000u+(bank?0x10000000u:0)+off);
    std::memcpy(w,&v,4);
    for(unsigned q2=0;q2<4;++q2){
      uint32_t r; std::memcpy(&r,(uint8_t*)(native_arena::base()+(uintptr_t)q2*0x40000000u+(bank?0x10000000u:0)+off),4); CHECK(r==v); ++checks;
    }
    const uint32_t guest_phys=(bank?0x10000000u:0)+(uint32_t)off;
    for(uint32_t mirror:{0x00000000u,0x40000000u,0x80000000u,0xC0000000u}){
      CHECK(native_arena::ld32(guest_phys|mirror)==__builtin_bswap32(v)); ++checks;
      CHECK(m.read32(guest_phys|mirror)==__builtin_bswap32(v)); ++checks;
    }
    uint32_t nv=(uint32_t)rng();
    native_arena::st32(0x80000000u+guest_phys,nv);
    uint32_t r; std::memcpy(&r,bank?m.mem2.data()+off:m.mem1.data()+off,4); CHECK(r==__builtin_bswap32(nv)); ++checks;
  }
  // the 64 KiB padding page after each quadrant-0 bank is writable (MMU banks are 24 MiB + 8 / 64 MiB + 8)
  m.mem1[24u*1024*1024+7]=5; m.mem2[64u*1024*1024+7]=6; CHECK(m.mem1[24u*1024*1024+7]==5);
  auto s=native_arena::stats(); CHECK(s.unhandled==0);
  std::printf("arena alias passed: %u checks, 4 mirrors x 2 banks\n",checks);
}
