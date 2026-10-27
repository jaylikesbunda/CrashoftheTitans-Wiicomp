"""Actual AI/IPC MMIO tests using an independently controlled microsecond clock."""
from pathlib import Path
from port_paths import default_storage
import subprocess,json
PORT=Path(__file__).resolve().parents[1];base=default_storage()
work=base/'ai-tests';work.mkdir(exist_ok=True);src=PORT/'build/nwii-full-source/nWiiRuntime'
cpp=work/'test.cpp';cpp.write_text(r'''#include "runtime/cpu_context.h"
#include "runtime/hw/hw.h"
#include <iostream>
static uint64_t clock_us=0;
namespace nwii::runtime {
uint64_t get_os_time(){return clock_us;}
CPUContext *g_ctx_ptr=nullptr;MMU *g_mmu=nullptr;int g_ipc_interrupt_delay=0;
uint32_t HW_Reg_Read32(uint32_t){std::abort();}
}
namespace nwii::runtime::hw {std::atomic<uint32_t> pi_intsr{0};std::atomic<uint32_t> pi_intmr{0};}
extern "C" int32_t handle_ios_ipc(nwii::runtime::CPUContext&,uint32_t){std::abort();}
int main(){using namespace nwii::runtime;int total=0,failed=0;
auto check=[&](bool ok,const char*n){++total;if(!ok)++failed;std::cout<<(ok?"PASS ":"FAIL ")<<n<<std::endl;};
auto &d=MMIODispatcher::get();hw::register_ipc(d);hw::register_ai(d);
d.write32(0xcd006c00,0x23);check(d.read32(0xcd006c00)==3,"reset_is_strobe");
clock_us=1000000;check(d.read32(0xcd006c08)==48000,"play_48k_counter");
check(d.read32(0xcc006c08)==48000,"gamecube_alias");
check(d.read32(0x0d006c08)==48000,"wii_physical_alias");
check(d.read32(0x0c006c08)==48000,"gamecube_physical_alias");
d.write32(0xcd006c00,2);clock_us+=1000000;check(d.read32(0xcd006c08)==48000,"stopped_counter_frozen");
d.write32(0xcd006c00,1);clock_us+=1000000;check(d.read32(0xcd006c08)==80000,"play_32k_counter");
d.write32(0xcd006c00,0x21);check(d.read32(0xcd006c08)==0,"reset_counter");
d.write32(0xcd006c0c,32);clock_us+=1000;check(d.read32(0xcd006c08)==32,"32k_threshold_tick");
check((d.read32(0xcd006c00)&8)&&!(hw::pi_intsr.load()&0x20),"irq_status_masked");
d.write32(0xcd006c00,5);check((hw::pi_intsr.load()&0x20)!=0,"irq_enable_bit2");
d.write32(0xcd006c00,0x0d);check(!(d.read32(0xcd006c00)&8)&&!(hw::pi_intsr.load()&0x20),"irq_ack_bit3");
d.write32(0xcd006c00,0x21);d.write32(0xcd006c08,0xfffffff0);d.write32(0xcd006c0c,0x10);clock_us+=1000;
check(d.read32(0xcd006c08)==0x10&&(d.read32(0xcd006c00)&8),"counter_wrap_threshold");
std::cout<<"RESULT "<<total-failed<<"/"<<total<<" passed"<<std::endl;return failed?1:0;}
''')
exe=work/'test.exe';stages=[]
for name,cmd in [('compile',['g++','-std=c++20','-O2','-static','-I',str(src/'include'),str(cpp),str(src/'src/hw/hw_ai.cpp'),str(src/'src/hw/hw_ipc.cpp'),str(src/'src/hw/mmio_dispatcher.cpp'),'-o',str(exe)]),('execute',[str(exe)])]:
    p=subprocess.run(cmd,capture_output=True,text=True);stages.append({'stage':name,'command':cmd,'exit_code':p.returncode,'output':p.stdout+p.stderr})
    (PORT/'reports/ai-register-tests.json').write_text(json.dumps(stages,indent=2));print(p.stdout+p.stderr)
    if p.returncode:raise SystemExit(p.returncode)
