#include "runtime/cpu_context.h"
#include "runtime/config.h"
#include "runtime/hw/hw.h"
#include <memory>
#include <iostream>
#include <cstdlib>
namespace nwii::runtime {CPUContext* g_ctx_ptr=nullptr;MMU* g_mmu=nullptr;}
extern "C" void run_game(nwii::runtime::CPUContext&){std::abort();}
namespace nwii::runtime { void interpret_step(CPUContext&); }
int main(){using namespace nwii::runtime;unsigned checks=0,failed=0;
auto check=[&](bool ok,const char*label){++checks;if(!ok){++failed;std::cout<<"FAIL "<<label<<'\n';}};
for(unsigned mode=0;mode<3;++mode){auto p=std::make_unique<CPUContext>();auto&c=*p;
g_ctx_ptr=&c;g_mmu=&c.mmu;c.pc=0x80005ffc;c.lr=0x81234560;c.msr=mode==2?0:0x8000;
c.inst_count=1;c.gpr[1]=0x80620000;c.gpr[3]=123;c.gpr[4]=456;
c.mmu.write32(0x800000D4,0x80610000);c.mmu.write32(0x800000E4,0x80610000);
c.dispatch_saved_ctx=mode==0?0x80612340:0;c.in_callback=mode==1;
c.queue_callback(0x80012340,77,88);auto saved=c.dispatch_saved_ctx;
const int jumped=setjmp(c.exception_jmp_buf);
if(!jumped)process_pending_callbacks(c);
check(jumped==0,"in-flight/masked callback deferred");check(c.dispatch_saved_ctx==saved,"saved context slot preserved");
check(c.pending_cb_count.load()==1,"callback stays queued");check(c.pc==0x80005ffc&&c.lr==0x81234560,"return PC/LR preserved");
check(c.gpr[3]==123&&c.gpr[4]==456,"interrupted registers preserved");
// New handler becomes eligible when the prior one has completed / EE restored.
if(jumped==0){c.dispatch_saved_ctx=0;c.in_callback=false;c.msr=0x8000;
const int dispatched=setjmp(c.exception_jmp_buf);if(!dispatched)process_pending_callbacks(c);
check(dispatched==1&&c.pc==0x80012340&&c.gpr[3]==77&&c.gpr[4]==88,"queued callback dispatches after release");
check(c.pending_cb_count.load()==0&&c.dispatch_saved_ctx==0x80610000,"queue consumed exactly once");
c.pc=0xfffffffc;process_pending_callbacks(c);
check(c.dispatch_saved_ctx==0&&c.pc==0x80005ffc&&c.lr==0x81234560&&c.gpr[3]==123&&c.gpr[4]==456,"callback return restores caller");}
}
// A DEC rearmed inside an ISR must not overwrite the single caller-restore slot.
for(unsigned mode=0;mode<2;++mode){auto p=std::make_unique<CPUContext>();auto&c=*p;
g_ctx_ptr=&c;g_mmu=&c.mmu;c.pc=0x803b475c;c.lr=0x803b475c;c.msr=0x8000;
c.inst_count=1;c.gpr[1]=0x80620000;c.gpr[3]=123;c.gpr[4]=456;
c.mmu.write32(0x800000D4,mode?0x80620008:0x80610000);
c.mmu.write32(0x800000E4,0x80610000);c.mmu.write32(0x80003020,0x803b4570);
c.mmu.write32(0x80610000+0x198,0x81234560);c.dispatch_saved_ctx=0x80610000;
c.dec_value=0;c.dec_irq_pending=true;hw::pi_intsr=0;hw::pi_intmr=0;
const int jumped=setjmp(c.exception_jmp_buf);
if(!jumped)for(unsigned i=0;i<32;++i)process_pending_callbacks(c);
check(jumped==0,"nested DEC deferred with occupied restore slot");
check(c.dispatch_saved_ctx==0x80610000,"DEC preserves outer restore slot");
check(c.mmu.read32(0x80610000+0x198)==0x81234560,"DEC preserves interrupted guest PC");
check(c.pc==0x803b475c&&c.lr==0x803b475c&&c.gpr[3]==123&&c.gpr[4]==456,"DEC preserves active handler registers");
check(c.dec_irq_pending,"deferred DEC remains pending");
if(!jumped){c.dispatch_saved_ctx=0;c.mmu.write32(0x800000D4,0x80610000);
const int dispatched=setjmp(c.exception_jmp_buf);
if(!dispatched)for(unsigned i=0;i<32;++i)process_pending_callbacks(c);
check(dispatched==1&&c.pc==0x803b4570,"pending DEC dispatches after outer handler release");
check(!c.dec_irq_pending&&c.dispatch_saved_ctx==0x80610000,"DEC consumes pending once and saves new caller");
c.pc=0xfffffffc;process_pending_callbacks(c);
check(c.pc==0x803b475c&&c.lr==0x803b475c&&c.gpr[3]==123&&c.gpr[4]==456,"DEC sentinel restores released caller");}
}
// Unmapped callbacks are interpreted. Their blr must yield the host sentinel
// with EE clear, before any instruction is fetched at that sentinel.
for(unsigned mode=0;mode<2;++mode){auto p=std::make_unique<CPUContext>();auto&c=*p;
g_ctx_ptr=&c;g_mmu=&c.mmu;c.pc=0x80001000;c.lr=0xfffffffc;c.msr=0;
c.gpr[3]=0x12345678;c.srr0=0x81234560;c.dispatch_saved_ctx=mode?0x80610000:0;
c.in_callback=mode==0;hw::pi_intsr=0;hw::pi_intmr=0;
c.mmu.write32(c.pc,0x4e800020); // blr in interpreter-only memory
interpret_step(c);
check(c.pc==0xfffffffc,"interpreted callback yields return sentinel");
check(c.inst_count==1,"sentinel is never fetched as guest instruction");
check(c.is_running.load(),"callback return does not abort interpreter");
check(c.srr0==0x81234560&&c.gpr[3]==0x12345678,"callback return preserves interrupted state");
check(c.dispatch_saved_ctx==(mode?0x80610000u:0u)&&c.in_callback==(mode==0),"dispatcher retains caller restoration ownership");
}
std::cout<<"RESULT "<<checks-failed<<'/'<<checks<<'\n';return failed?1:0;}
