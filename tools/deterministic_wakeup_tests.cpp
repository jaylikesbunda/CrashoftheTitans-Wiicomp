// Production callback/DEC execution, driven by virtual ticks; no game boot or sleeps.
#include "runtime/cpu_context.h"
#include "runtime/native_frame_trace.h"
#include "runtime/config.h"
#include "runtime/hw/hw.h"
#include <memory>
#include <iostream>
#include <cstdlib>
namespace nwii::runtime {CPUContext* g_ctx_ptr=nullptr;MMU* g_mmu=nullptr;}
extern "C" void run_game(nwii::runtime::CPUContext&){std::abort();}
using namespace nwii::runtime;
static unsigned checks=0,failed=0;
static void check(bool ok,const char* label){++checks;if(!ok){++failed;std::cout<<"FAIL "<<label<<'\n';}}
static std::unique_ptr<CPUContext> make(){auto p=std::make_unique<CPUContext>();auto& c=*p;
g_ctx_ptr=&c;g_mmu=&c.mmu;c.inst_count=1;c.pc=0x803d3048;c.lr=0x803b9574;c.msr=0x8000;
c.gpr[1]=0x80630000;c.gpr[3]=0x12345678;c.gpr[4]=0x87654321;c.gpr[13]=0x8059a0e0;
c.mmu.write32(0x800000D4,0x80610000);c.mmu.write32(0x800000E4,0x80610000);
hw::pi_intsr=0;hw::pi_intmr=0;return p;}
// NATIVE_TRACED_CALLBACK_FIXTURE_V1: exercise both production wrappers and longjmp closure.
static const bool traced_fixture=std::getenv("NWII_TRACED_WAKEUP_FIXTURE")!=nullptr;
static bool poll_fixture(CPUContext& c){
 if(traced_fixture){static const unsigned seed=[](){for(unsigned n=1;;++n){uint32_t x=n*0x9e3779b9u;x^=x>>16;if(!(x&4095u))return n-1;}}();native_trace::CallbackSample::counter=seed;}
 return process_pending_callbacks(c);
}
static bool callback(CPUContext& c){c.queue_callback(0x803f9294,7,8);
int jump=setjmp(c.exception_jmp_buf);if(!jump)poll_fixture(c);
return jump==1&&c.pc==0x803f9294;}
int main(){if(traced_fixture){native_trace::active.store(true);native_trace::phase.store(2);}Config::get().game_profile.ext_interrupt_dispatch=0;
// A context restore is not an mtdec. Twenty callbacks cannot extend an alarm.
{auto p=make();auto&c=*p;c.inst_count=1;c.write_dec(5000);auto origin=c.dec_written_tb;
for(unsigned i=1;i<=20;++i){c.inst_count=1+i*200;check(callback(c),"timer witness callback entered");
c.pc=0xfffffffc;poll_fixture(c);check(c.dec_written_tb==origin,"callback must not reset DEC origin");}
c.inst_count=origin+5001;check(c.dec_expired(),"DEC due at original deadline despite callbacks");
c.mmu.write32(0x80003020,0x803b4570);
int jump=setjmp(c.exception_jmp_buf);if(!jump)for(unsigned i=0;i<32;++i)poll_fixture(c);
check(jump==1&&c.pc==0x803b4570,"expired DEC reaches alarm handler");}
// A real guest mtdec inside a callback must replace the previous deadline.
{auto p=make();auto&c=*p;c.write_dec(5000);
c.inst_count=101;check(callback(c),"rearm witness callback entered");
c.inst_count=201;c.write_dec(8000);auto origin=c.dec_written_tb;
c.inst_count=301;c.pc=0xfffffffc;poll_fixture(c);
check(c.dec_written_tb==origin,"callback preserves guest mtdec origin");
c.inst_count=origin+7999;check(!c.dec_expired(),"rearmed DEC does not fire early");
c.inst_count=origin+8000;check(c.dec_expired(),"rearmed DEC expires at its own deadline");}
// SDK interrupt handlers use a temporary OSContext while CurrentThread stays unchanged.
for(unsigned scenario=0;scenario<4;++scenario){auto p=make();auto&c=*p;
const uint32_t temp=0x80620000,thread=0x80610000,other=0x80640000;
const uint32_t saved=scenario==0?thread:temp;
c.mmu.write32(0x800000D4,saved);if(scenario==2)c.mmu.write32(0x800000E4,0);
c.mmu.write32(thread+0x198,0x81234560);c.mmu.write32(thread+12,0xdeadbeef);
c.mmu.write32(other+0x198,0x81235560);c.mmu.write32(other+0x19c,0x8000);
check(callback(c),"context witness callback entered");
if(scenario==3)c.mmu.write32(0x800000E4,other); // genuine scheduler thread switch
c.pc=0xfffffffc;poll_fixture(c);
check(c.pc==(scenario==3?0x81235560u:0x803d3048u),"restore interrupted ISR unless thread actually changed");
if(scenario!=3)check(c.gpr[3]==0x12345678&&c.lr==0x803b9574,"interrupted ISR registers survive callback");
check(c.mmu.read32(thread+0x198)==(scenario==0?0x803d3048u:0x81234560u),"thread saved context stays independent of ISR context");
check(c.mmu.read32(0x800000D4)==(scenario==3?other:saved),"current OSContext returns to actual saved owner");}
// Idle is a legitimate repeated exception PC. Every programmed DEC must dispatch.
// A future alarm-shaped RAM block must never be force-expired by a host scan.
{auto p=make();auto&c=*p;c.mmu.write32(0x80003020,0x803b4570);
const uint32_t future=c.gpr[13]-128;
c.mmu.write32(future+8,0);c.mmu.write32(future+12,1000000);
c.mmu.write32(future+24,0x803f9294);
for(unsigned i=0;i<130;++i){c.pc=0x803bbfa0;c.msr=0x8000;c.write_dec(15);c.inst_count+=16;
int jump=setjmp(c.exception_jmp_buf);if(!jump)for(unsigned poll=0;poll<32;++poll)poll_fixture(c);
check(jump==1&&c.pc==0x803b4570,"repeated idle DEC always reaches original alarm handler");
check(c.mmu.read32(future+12)==1000000,"host does not fabricate guest alarm expiration");
if(jump){c.pc=0xfffffffc;poll_fixture(c);}}
}
std::cout<<"RESULT "<<checks-failed<<'/'<<checks<<'\n';if(traced_fixture){if(native_trace::open_scopes()!=0)return 2;std::cout<<"TRACE_CALLBACK_BRANCH all polls sampled, no open scopes\n";}return failed?1:0;}
