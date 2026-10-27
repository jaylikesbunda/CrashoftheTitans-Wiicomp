// Production idle polling and real Windows wake primitive; never boots the game.
#include "runtime/cpu_context.h"
#include "runtime/event_scheduler.h"
#include "runtime/config.h"
#include "runtime/hw/hw.h"
#include <chrono>
#include <thread>
#include <memory>
#include <bit>
namespace nwii::runtime {CPUContext*g_ctx_ptr=nullptr;MMU*g_mmu=nullptr;}
extern "C" void run_game(nwii::runtime::CPUContext&){std::abort();}
using namespace nwii::runtime;
#if defined(__MINGW64__)
// Match main_output.cpp's non-unwinding guest dispatcher ABI. Ordinary setjmp
// asks UCRT to unwind a GCC frame and is unsuitable for these guest switches.
#define IDLE_FIXTURE_SETJMP(buffer) _setjmp(buffer, nullptr)
#else
#define IDLE_FIXTURE_SETJMP(buffer) setjmp(buffer)
#endif
static unsigned checks=0,failed=0;
static void check(bool pass,const char*label){++checks;if(!pass){++failed;std::cout<<"FAIL "<<label<<'\n';}}
static __attribute__((noinline)) bool dispatch_callback(CPUContext& c) {
  const int jumped=IDLE_FIXTURE_SETJMP(c.exception_jmp_buf);
  if(!jumped)for(unsigned i=0;i<32;++i)process_pending_callbacks(c);
  return jumped==1;
}
static __attribute__((noinline)) bool dispatch_timer(CPUContext& c) {
  const auto stop=std::chrono::steady_clock::now()+std::chrono::milliseconds(100);
  const int jumped=IDLE_FIXTURE_SETJMP(c.exception_jmp_buf);
  if(!jumped)while(std::chrono::steady_clock::now()<stop){process_pending_callbacks(c);c.inst_count+=2;}
  return jumped==1;
}
int main(){
  using namespace native_idle;
  constexpr uint64_t freq=40500000;
  for(uint64_t delta=0;delta<=12000;delta+=73){
    auto t=budget_ticks(100000,100000+delta,false,0,0,freq);
    check(t==std::min(delta,freq/4000),"event deadline and cap");
    t=budget_ticks(100000,UINT64_MAX,true,99000,1000+delta,freq);
    check(t==std::min(delta,freq/4000),"DEC deadline and cap");
  }
  check(budget_ticks(100,99,false,0,0,freq)==0,"overdue scheduler never waits");
  check(budget_ticks(100,UINT64_MAX,true,80,19,freq)==0,"overdue DEC never waits");
  check(budget_ticks(0,UINT64_MAX,false,0,0,1)==0,"tiny clock fails open");
  check(budget_ticks(10,UINT64_MAX,true,UINT64_MAX-5,16,freq)==0,"wrapped DEC elapsed");
  EventScheduler::get().reset();check(EventScheduler::get().next_due()==UINT64_MAX,"empty event heap");
  auto id=EventScheduler::get().schedule_after(234,[](CPUContext&,uint64_t){});
  check(EventScheduler::get().next_due()==234,"heap earliest deadline");
  EventScheduler::get().cancel(id);check(EventScheduler::get().next_due()==UINT64_MAX,"cancel deadline");
#ifdef _WIN32
  // Pre-wait signals must survive; pending work prevents sleeping.
  ResetEvent(wake().event);
  bool slept=wait(10000,freq,[]{return true;});check(!slept,"already-pending work bypasses wait");
  notify();check(WaitForSingleObject(wake().event,0)==WAIT_OBJECT_0,"signal remembered before waiter");
  for(unsigned i=0;i<64;++i){notify();auto start=std::chrono::steady_clock::now();
    check(wait(10000,freq,[]{return false;}),"pre-signaled native wait runs");
    check(std::chrono::steady_clock::now()-start<std::chrono::milliseconds(100),"pre-signal returns bounded");
  }
  // Race the source against arming, with a long fixture deadline to expose lost wakes.
  for(unsigned i=0;i<32;++i){ResetEvent(wake().event);std::atomic<bool>pending{false};
    std::thread producer([&]{std::this_thread::sleep_for(std::chrono::milliseconds(1));pending.store(true);notify();});
    auto start=std::chrono::steady_clock::now();wait(freq/10,freq,[&]{return pending.load();});
    producer.join();check(pending.load(),"producer publishes wake");
    check(std::chrono::steady_clock::now()-start<std::chrono::milliseconds(80),"producer wakes before 100ms timer");
  }
#endif
  auto p=std::make_unique<CPUContext>();auto&c=*p;g_ctx_ptr=&c;g_mmu=&c.mmu;
  Config::get().game_profile.ext_interrupt_dispatch=0;
  c.pc=0x803bbfa0;c.lr=0x803bbf98;c.cr[0].eq=true;c.msr=0x8000;c.gpr[13]=0x8059a0e0;
  c.inst_count=1;c.mmu.write32(0x800000e4,0);c.mmu.write32(c.gpr[13]-10568,0);
  c.mmu.write32(0x800000d4,0x80610000);hw::pi_intmr=0;hw::pi_intsr=0;
  const auto gprs=c.gpr;const auto fprs=c.fpr;const auto ps=c.ps1;
  const auto start=std::chrono::steady_clock::now();uint64_t polls=0;
#ifdef _WIN32
  ULONG64 cycles0=0,cycles1=0;QueryThreadCycleTime(GetCurrentThread(),&cycles0);
#endif
  while(std::chrono::steady_clock::now()-start<std::chrono::milliseconds(200)){
    if(process_pending_callbacks(c))return 3;c.inst_count+=2;++polls;
  }
#ifdef _WIN32
  QueryThreadCycleTime(GetCurrentThread(),&cycles1);
  std::cout<<"IDLE_BENCH polls="<<polls<<" cycles="<<(cycles1-cycles0)<<" enabled="<<(std::getenv("NWII_NO_IDLE_WAIT")==nullptr)<<'\n';
#endif
  check(c.gpr==gprs&&c.fpr==fprs&&c.ps1==ps,"idle waiting preserves registers");
  check(c.pc==0x803bbfa0&&c.lr==0x803bbf98&&c.cr[0].eq,"idle continuation preserved");
  check(c.inst_count==1+polls*2,"no synthetic guest instructions");
  check(c.mmu.read32(c.gpr[13]-10568)==0,"does not fabricate runnable work");
  // Only the proven interruptible SDK idle state can park the CPU.
  auto reject=[&](const char* label){auto before=waits;
    for(unsigned i=0;i<32;++i)process_pending_callbacks(c);
    check(waits==before,label);
  };
  c.pc=0x80005fd8;reject("arbitrary busy loop cannot sleep");c.pc=0x803bbfa0;
  c.cr[0].eq=false;reject("non-idle branch cannot sleep");c.cr[0].eq=true;
  c.msr=0;reject("interrupt-disabled guest cannot sleep");c.msr=0x8000;
  c.mmu.write32(0x800000e4,0x80610000);reject("runnable thread cannot sleep");c.mmu.write32(0x800000e4,0);
  c.mmu.write32(c.gpr[13]-10568,1);reject("SDK ready flag prevents sleeping");c.mmu.write32(c.gpr[13]-10568,0);
  c.in_callback=true;reject("active callback prevents sleeping");c.in_callback=false;
  c.dispatch_saved_ctx=0x80610000;reject("saved interrupt context prevents sleeping");c.dispatch_saved_ctx=0;
  c.gpr[13]=0x70000000;reject("invalid idle flag address prevents sleeping");c.gpr[13]=0x8059a0e0;
  // A queued callback must escape the real poll through its original dispatcher.
  c.queue_callback(0x803f9294,7,8);const bool jump=dispatch_callback(c);
  check(jump&&c.pc==0x803f9294,"queued callback delivered after idle");
  check(c.gpr[3]==7&&c.gpr[4]==8,"callback arguments intact");
  c.pc=0xfffffffc;process_pending_callbacks(c);
  c.pc=0x803bbfa0;c.cr[0].eq=true;c.msr=0x8000;
  c.mmu.write32(0x80003020,0x803b4570);
  c.write_dec(freq/500);const uint64_t origin=c.dec_written_tb;
  const bool timer_jump=dispatch_timer(c);
  check(timer_jump&&c.pc==0x803b4570,"wall-clock DEC wakes idle and dispatches original handler");
  check(c.read_timebase()-origin>=freq/500,"DEC never delivered before its deadline");
  std::cout<<"IDLE_CHECKS "<<checks-failed<<'/'<<checks<<'\n';return failed?1:0;
}
