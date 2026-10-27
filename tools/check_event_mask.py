"""Exercise the NATIVE_EVENT_MASK_V1 producer/bit table against the staged runtime.

Two halves:
  * behaviour: compile hw_ipc.cpp + mmio_dispatcher.cpp (the same recipe as
    check_ipc_irq.py) and drive the real delay arm and the real PI raise/clear helpers,
    asserting which mask bits they set;
  * audit: every documented producer must raise its bit and every bit the poll can lower
    must have a raise site, so a producer added later cannot silently leave the mask
    stale. The poll fast path must no longer read the three cross-thread producers it
    replaced, and must still read pending_cb_count before dispatching.
"""
from pathlib import Path
from port_paths import default_storage
import json
import re
import subprocess

PORT = Path(__file__).resolve().parents[1]
WORK = default_storage()/'event-mask-tests'
WORK.mkdir(parents=True, exist_ok=True)
rt = PORT / 'build/nwii-full-source/nWiiRuntime'

cpp = WORK / 'event_mask.cpp'
cpp.write_text(r'''#include "runtime/cpu_context.h"
#include "runtime/hw/hw.h"
#include <iostream>
namespace nwii::runtime {
CPUContext *g_ctx_ptr=nullptr; MMU *g_mmu=nullptr;
int g_ipc_interrupt_delay=0; bool g_trace_calls=false; uint32_t g_watch_addr=0;
void watch_hit(uint32_t,uint32_t,int) { std::abort(); }
uint32_t HW_Reg_Read32(uint32_t) { std::abort(); }
}
namespace nwii::runtime::hw { std::atomic<uint32_t> pi_intsr{0}; std::atomic<uint32_t> pi_intmr{0}; }
extern "C" int32_t handle_ios_ipc(nwii::runtime::CPUContext&,uint32_t) { return 0; }
int main(){
  using namespace nwii::runtime;
  namespace ev = nwii::runtime::native_events;
  namespace hw = nwii::runtime::hw;
  int total=0,failed=0;
  auto check=[&](bool ok,const char*n){++total;if(!ok)++failed;std::cout<<(ok?"PASS ":"FAIL ")<<n<<std::endl;};
  auto reset=[&](){ ev::mask.store(0,std::memory_order_relaxed); hw::pi_intsr=0; hw::pi_intmr=0; g_ipc_interrupt_delay=0; };

  // kIpcDelay: armed by the helper and by the real IPC control-register path.
  reset();
  check((ev::mask.load()&ev::kSlowMask)==0,"idle mask is clear");
  hw::native_ipc_delay_set(50);
  check(g_ipc_interrupt_delay==50 && (ev::mask.load()&ev::kIpcDelay),"helper arms kIpcDelay");
  hw::native_ipc_delay_set(0);
  check(g_ipc_interrupt_delay==0 && !(ev::mask.load()&ev::kIpcDelay),"helper disarms kIpcDelay");
  reset();
  auto &d=MMIODispatcher::get(); hw::register_ipc(d);
  d.write32(0xcd000000,0x13e00400); d.write32(0xcd000004,0x31);
  check(g_ipc_interrupt_delay!=0 && (ev::mask.load()&ev::kIpcDelay),"IPC control write arms kIpcDelay");

  // kPiIrq: only a new, unmasked source needs a poll.
  reset();
  hw::trigger_pi_interrupt(0x00004000);
  check((hw::pi_intsr&0x4000)!=0 && !(ev::mask.load()&ev::kPiIrq),"masked source does not raise kPiIrq");
  reset();
  hw::pi_intmr=0x00004000;
  hw::trigger_pi_interrupt(0x00004000);
  check((ev::mask.load()&ev::kPiIrq),"unmasked new source raises kPiIrq");
  const uint32_t after_first=ev::mask.load();
  hw::trigger_pi_interrupt(0x00004000);
  check(ev::mask.load()==after_first,"re-raising a pending source does not re-enter the mask");
  // Enabling a still-pending source is the other way the active set can appear.
  reset();
  hw::pi_intsr=0x00000100; hw::pi_intmr=0;
  check(!(ev::mask.load()&ev::kPiIrq),"pending but masked is not latched");
  hw::native_pi_intmr_write(0x00000100);
  check((hw::pi_intmr&0x100) && (ev::mask.load()&ev::kPiIrq),"INTMR enable latches the pending source");
  // The poll is what clears it, once the active set is empty.
  reset();
  hw::pi_intmr=0x00004000; hw::trigger_pi_interrupt(0x00004000);
  check((ev::mask.load()&ev::kPiIrq),"latch set before clear");
  hw::clear_pi_interrupt(0x00004000);
  check((ev::mask.load()&ev::kPiIrq),"clearing the source alone does not clear the latch");
  if(!(hw::pi_intsr&hw::pi_intmr)) ev::lower(ev::kPiIrq);
  check(!(ev::mask.load()&ev::kPiIrq),"verified-empty active set clears the latch");

  std::cout<<"EVENT_MASK_BEHAVIOUR "<<total-failed<<"/"<<total<<" passed"<<std::endl;
  return failed?1:0;
}
''')

rows = []
for cmd in (['g++', '-std=c++20', '-O2', '-static', '-I', str(rt / 'include'), str(cpp),
             str(rt / 'src/hw/hw_ipc.cpp'), str(rt / 'src/hw/mmio_dispatcher.cpp'), '-o', str(WORK / 'event_mask.exe')],
            [str(WORK / 'event_mask.exe')]):
    r = subprocess.run(cmd, capture_output=True, text=True)
    rows.append(dict(stage='compile' if len(rows) == 0 else 'execute', command=cmd,
                     exit_code=r.returncode, output=r.stdout + r.stderr))
    (PORT / 'reports/event-mask-tests.json').write_text(json.dumps(rows, indent=2) + '\n')
    print((r.stdout + r.stderr)[-2500:], flush=True)
    if r.returncode:
        raise SystemExit(r.returncode)

# ---------------------------------------------------------------- source audit
ios = (rt / 'src/hle/ios.cpp').read_text()
hw_h = (rt / 'include/runtime/hw/hw.h').read_text()
hw_pi = (rt / 'src/hw/hw_pi.cpp').read_text()
hw_ipc = (rt / 'src/hw/hw_ipc.cpp').read_text()
main_cpp = (rt / 'src/core/main.cpp').read_text()
audit = []


def must(ok, label):
    audit.append(dict(check=label, ok=bool(ok)))
    if not ok:
        raise SystemExit('event-mask audit failed: ' + label)


must('native_events::raise(native_events::kPiIrq)' in hw_h, 'trigger_pi_interrupt raises kPiIrq')
must('inline void native_pi_intmr_write(uint32_t val)' in hw_h, 'INTMR helper is defined once, in hw.h')
must('native_pi_intmr_write(val)' in hw_pi, 'INTMR writer uses the helper')
must('pi_intmr = val' in hw_h, 'INTMR helper stores the mask')
must(hw_ipc.count('native_ipc_delay_set(') >= 6, 'every IPC delay assignment goes through the helper')
must('g_ipc_interrupt_delay = 50' not in hw_ipc and 'g_ipc_interrupt_delay = 5000' not in hw_ipc,
     'no raw IPC delay assignment remains')
must('raise(nwii::runtime::native_events::kVBlank)' in main_cpp, 'host vblank raises kVBlank')
must('native_events::raise(native_events::kVBlank)' in ios, 'EventScheduler vblank raises kVBlank')
must('native_events::lower(native_events::kVBlank)' in ios, 'vblank consumption lowers kVBlank')
must('native_events::lower(native_events::kIpcDelay)' in ios, 'IPC countdown lowers kIpcDelay')
must('native_events::lower(native_events::kPiIrq)' in ios, 'slow poll re-verifies and lowers kPiIrq')
must('native_events::raise(native_events::kPiIrq)' in ios, 'slow poll re-raises kPiIrq while active')

a = ios.index('static __attribute__((always_inline)) inline bool native_process_callbacks_poll')
b = ios.index('// NATIVE_OS_IDLE_WAIT_V1', a)
fast = ios[a:b]
must('native_events::mask.load' in fast, 'fast path reads the mask once')
must('ctx.vblank_pending' not in fast, 'fast path no longer reads vblank_pending')
must('hw::pi_intsr' not in fast, 'fast path no longer reads pi_intsr')
must('hw::pi_intmr' not in fast, 'fast path no longer reads pi_intmr')
must('ctx.pending_cb_count.load' in fast, 'fast path still reads pending_cb_count')
must(fast.index('ctx.pending_cb_count.load') < fast.index('native_process_callbacks_slow'),
     'pending_cb_count is read before the slow path is entered')
must('ctx.inst_count%20000000' not in ios, 'raw heartbeat modulo is gone')
must('NATIVE_POLL_PERIODIC_COUNTDOWN_V1' in ios, 'heartbeat countdown installed')
must('always_inline)) inline bool native_process_callbacks_poll' in ios, 'poll body is always_inline')

report = dict(behaviour=rows[-1]['output'].strip(), audit=audit)
(PORT / 'reports/event-mask-audit.json').write_text(json.dumps(report, indent=2) + '\n')
print('EVENT_MASK_AUDIT %d/%d checks' % (sum(1 for x in audit if x['ok']), len(audit)))
