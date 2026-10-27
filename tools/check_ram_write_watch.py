"""Guest-RAM write tracking (MEM_WRITE_WATCH + per-page generations) with the production MMU and overlay (no game launch).

Checks, with the real MMU::write8/16/32/64 accessors writing guest RAM:
  * a never-written range reports a generation <= the sampled one (clean);
  * any single-byte write through the MMU makes exactly its page(s) newer than a previously sampled generation;
  * writes to other pages never make an unrelated range look dirty (isolation), including ranges that share no page;
  * two entries verified at different generations on the same page both see a later write (the reset in one query must not hide a
    write from another entry), and an entry verified after a write sees it as clean;
  * a writer thread that stores and then publishes a flag is always observed by a following query (no lost write) over 200,000 rounds;
  * unsupported memory (plain VirtualAlloc) reports UINT64_MAX so callers fall back to the byte comparison;
  * the query cost for a 64-page texture range.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];work=p/'build/gap-emit/fixture';work.mkdir(parents=True,exist_ok=True)
code=r'''
#include "runtime/cpu_context.h"
#include <windows.h>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <thread>
using namespace nwii::runtime;
namespace nwii::runtime {MMU* g_mmu=nullptr;CPUContext* g_ctx_ptr=nullptr;}
extern "C" void run_game(CPUContext&){abort();}
static unsigned failures=0;
#define CHECK(c,msg) do{if(!(c)){++failures;std::printf("FAIL %s (line %d)\n",msg,__LINE__);}}while(0)
int main(){
  auto ctx=std::make_unique<CPUContext>();MMU& m=ctx->mmu;
  const uint8_t* base=m.mem1.data();const size_t bank=m.mem1.size();
  auto newest=[&](uint32_t offset,uint32_t bytes){return native_ram_newest_write(base,bank,offset,bytes);};
  // 1. clean range
  uint64_t n=newest(0x100000,0x10000);CHECK(n!=UINT64_MAX,"write watch available");
  uint64_t g0=native_ram_generation();CHECK(n<=g0,"clean range <= generation");
  CHECK(newest(0x100000,0x10000)<=g0,"still clean");
  // 2. any write through the MMU is seen
  const uint32_t guest=0x80100000u+0x2345u; // physical 0x102345
  m.write8(guest,0x5a);
  uint64_t after=newest(0x100000,0x10000);CHECK(after>g0,"byte write seen");
  uint64_t g1=native_ram_generation();CHECK(newest(0x100000,0x10000)<=g1,"clean again after sampling");
  m.write16(guest&~1u,0x1234);CHECK(newest(0x100000,0x10000)>g1,"write16 seen");
  uint64_t g2=native_ram_generation();m.write32(guest&~3u,0xdeadbeef);CHECK(newest(0x100000,0x10000)>g2,"write32 seen");
  uint64_t g3=native_ram_generation();m.write64(guest&~7u,0x0123456789abcdefull);CHECK(newest(0x100000,0x10000)>g3,"write64 seen");
  // 3. isolation: a write elsewhere does not dirty this range
  uint64_t g4=native_ram_generation();(void)newest(0x100000,0x10000);
  m.write32(0x80400000u,1);CHECK(newest(0x100000,0x10000)<=g4,"unrelated write does not dirty range");
  CHECK(newest(0x400000,4)>g4,"the written page is dirty");
  // 4. two entries on the same page
  const uint32_t page=0x200000;m.write32(0x80000000u+page,7);
  (void)newest(page,0x1000);uint64_t entryA=native_ram_generation();      // A verified here
  m.write32(0x80000000u+page+8,8);
  uint64_t seenByB=newest(page,0x1000);CHECK(seenByB>entryA,"A's entry sees the write after B's query reset the OS bits");
  uint64_t entryB=native_ram_generation();CHECK(newest(page,0x1000)<=entryB,"B verified after the write sees it as clean");
  CHECK(newest(page,0x1000)>entryA,"A still sees the write (its generation is older)");
  // 5. cross-thread: store then flag; the next query must see it
  std::atomic<int> turn{0};std::atomic<bool> stop{false};unsigned lost=0;const uint32_t target=0x300000;
  std::thread writer([&](){for(int i=0;i<200000&&!stop;++i){while(turn.load(std::memory_order_acquire)!=1)std::this_thread::yield();
      m.write32(0x80000000u+target+(i&0xFFC),uint32_t(i));turn.store(2,std::memory_order_release);}});
  for(int i=0;i<200000;++i){(void)newest(target,0x1000);uint64_t before=native_ram_generation();
    turn.store(1,std::memory_order_release);while(turn.load(std::memory_order_acquire)!=2)std::this_thread::yield();
    if(newest(target,0x1000)<=before)++lost;}
  stop=true;writer.join();CHECK(lost==0,"no lost writes across threads");
  // 6. unsupported memory falls back
  uint8_t* plain=static_cast<uint8_t*>(VirtualAlloc(nullptr,1<<20,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
  CHECK(native_ram_newest_write(plain,1<<20,0,4096)==UINT64_MAX,"plain memory reports unsupported");
  CHECK(native_ram_newest_write(plain,1<<20,0,4096)==UINT64_MAX,"and stays unsupported");
  VirtualFree(plain,0,MEM_RELEASE);
  // 7. cost of a 64-page query
  auto t0=std::chrono::steady_clock::now();for(int i=0;i<20000;++i)(void)newest(0x500000,64*4096);
  double us=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-t0).count()/20000;
  std::printf("RAM_WRITE_WATCH failures=%u query_64_pages_us=%.2f\n",failures,us);
  return failures?1:0;
}
'''
(work/'ramwatch_test.cpp').write_text(code)
cm=p/'build/nwii-full-source/CMakeLists.txt';t=cm.read_text();marker='# NATIVE_RAMWATCH_TEST_V1'
if marker not in t:
    t+='\n'+marker+'\nadd_executable(crash_ramwatch_tests "'+(work/'ramwatch_test.cpp').as_posix()+'")\ntarget_link_libraries(crash_ramwatch_tests PRIVATE nwiiruntime)\ntarget_compile_options(crash_ramwatch_tests PRIVATE -O2 -fwrapv -fno-strict-aliasing)\n'
    cm.write_text(t)
print('ram write-watch fixture written')
