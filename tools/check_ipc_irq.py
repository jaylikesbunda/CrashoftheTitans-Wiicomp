"""Exercise actual staged IPC MMIO with independent ACK/reply/IRQ expectations."""
from pathlib import Path
from port_paths import default_storage
import json, subprocess
PORT=Path(__file__).resolve().parents[1]
base=default_storage()
work=base/'ipc-tests';work.mkdir(exist_ok=True)
src=PORT/'build/nwii-full-source/nWiiRuntime'
cpp=work/'ipc.cpp'
cpp.write_text(r'''#include "runtime/cpu_context.h"
#include "runtime/hw/hw.h"
#include <iostream>
namespace nwii::runtime {
CPUContext *g_ctx_ptr=nullptr; MMU *g_mmu=nullptr;
int g_ipc_interrupt_delay=0;bool g_trace_calls=false;uint32_t g_watch_addr=0;
void watch_hit(uint32_t,uint32_t,int) { std::abort(); }
uint32_t HW_Reg_Read32(uint32_t) { std::abort(); }
}
namespace nwii::runtime::hw {std::atomic<uint32_t> pi_intsr{0};}
static int32_t response=0;static uint32_t completion=0;
extern "C" int32_t handle_ios_ipc(nwii::runtime::CPUContext&,uint32_t) {
 if(completion)nwii::runtime::hw::ipc_post_reply(completion);return response;
}
int main(){using namespace nwii::runtime;int total=0,failed=0;
auto check=[&](bool ok,const char*n){++total;if(!ok)++failed;std::cout<<(ok?"PASS ":"FAIL ")<<n<<std::endl;};
auto &d=MMIODispatcher::get();hw::register_ipc(d);
d.write32(0xcd000004,0x30);d.write32(0xcd000000,0x1234);d.write32(0xcd000004,0x31);
check((d.read32(0xcd000004)&0x36)==0x36,"receipt_ack_and_reply_pending");
hw::pi_intsr=0x4000;d.write32(0xcd000030,0x40000000);
check((hw::pi_intsr.load()&0x4000)==0&&g_ipc_interrupt_delay==50,"irq_ack_pending_reassert");
d.write32(0xcd000004,0x34);
check((d.read32(0xcd000004)&0x04)==0&&(d.read32(0xcd000004)&0x22)==0x22,"reply_clear_preserves_ack");
hw::pi_intsr=0x4000;d.write32(0xcd000030,0x40000000);
check(g_ipc_interrupt_delay==50,"ack_pending_reassert");
d.write32(0xcd000004,0x32);hw::pi_intsr=0x4000;d.write32(0xcd000030,0x40000000);
check((hw::pi_intsr.load()&0x4000)==0&&g_ipc_interrupt_delay==0,"all_sources_ack_no_storm");
hw::pi_intsr=0x4000;d.write32(0xcd000030,1);
check((hw::pi_intsr.load()&0x4000)!=0,"unrelated_flag_write_preserves_irq");
hw::pi_intsr=0x4000;d.write32(0x0d000030,0x40000000);
check((hw::pi_intsr.load()&0x4000)==0,"physical_alias_ack");
hw::ipc_post_reply(0x93e00100);hw::ipc_post_reply(0x93e00200);
check(d.read32(0xcd000008)==0x13e00100,"async_first_reply_not_overwritten");
d.write32(0xcd000004,0x34);
check(d.read32(0xcd000008)==0x13e00200&&(d.read32(0xcd000004)&4),"async_second_after_ack");
d.write32(0xcd000004,0x34);hw::pi_intsr=0x4000;d.write32(0xcd000030,0x40000000);
check((d.read32(0xcd000004)&6)==0&&g_ipc_interrupt_delay==0,"async_queue_drains");
CPUContext ctx;g_ctx_ptr=&ctx;completion=0x93e00300;
d.write32(0xcd000000,0x13e00400);d.write32(0xcd000004,0x31);
check(d.read32(0xcd000008)==0x13e00300,"completion_during_dispatch_not_lost");
d.write32(0xcd000004,0x34);
check(d.read32(0xcd000008)==0x13e00400,"dispatch_reply_after_completion");
d.write32(0xcd000004,0x36);response=-0x70000001;completion=0;
hw::ipc_post_reply(0x93e00500);d.write32(0xcd000004,0x31);
check(d.read32(0xcd000008)==0x13e00500&&(d.read32(0xcd000004)&6)==6,"deferred_receipt_preserves_other_reply");
d.write32(0xcd000004,0x36);g_ctx_ptr=nullptr;
std::cout<<"RESULT "<<total-failed<<"/"<<total<<" passed"<<std::endl;return failed?1:0;}
''')
exe=work/'ipc.exe';stages=[]
for name,cmd in [('compile',['g++','-std=c++20','-O2','-static','-I',str(src/'include'),str(cpp),str(src/'src/hw/hw_ipc.cpp'),str(src/'src/hw/mmio_dispatcher.cpp'),'-o',str(exe)]),('execute',[str(exe)])]:
    p=subprocess.run(cmd,capture_output=True,text=True)
    stages.append({'stage':name,'command':cmd,'exit_code':p.returncode,'output':p.stdout+p.stderr})
    (PORT/'reports/ipc-irq-tests.json').write_text(json.dumps(stages,indent=2))
    print(p.stdout+p.stderr)
    if p.returncode:raise SystemExit(p.returncode)
