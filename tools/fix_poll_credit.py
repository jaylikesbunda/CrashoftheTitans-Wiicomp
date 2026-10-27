"""Poll callbacks every Nth backward branch instead of every one (NWII_POLL_CREDIT=1 restores every branch).

Capture frame-cost-20261006-102126-770700, guest thread (48.8 ms CPU/frame, 8.0 M guest instructions/frame):
about 524,000 polls per frame (one sampled guest.pc event per 4,096 polls, 15,363 events in the capture)
cost about 9 ms/frame: native_process_callbacks_poll 3.14 ms + process_pending_callbacks 1.93 ms +
native_process_callbacks_slow 1.02 ms + the clock reads it triggers (399 steady_clock stacks, 3.3 ms) +
WPAD poll. Four loop sites account for 42% of them: 803A3724/803A379C (software sqrt), 803B5144/803B5118
(DCFlushRange/DCInvalidateRange, whose cache instructions are no-ops here).

All 9,755 generated poll sites are the same statement on a backward branch. Each now first decrements a
global credit and only calls process_pending_callbacks when it reaches zero or when a host producer raised
the attention flag (native_idle::notify: queued callbacks, vblank, PI interrupts, PE signals). The credit is
reloaded to NWII_POLL_CREDIT (default 64) by every real poll, and to 1 while an IPC/DI interrupt delay is
counting down, because those delays are counted in polls. Delivery order and poll side effects are
unchanged; a time-driven event (decrementer, scheduler, WPAD) can be observed up to N-1 backward branches
later, about 1-2 microseconds at the measured 6.2 M polls/s. Idle-loop sites still poll.
"""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
marker='NATIVE_POLL_CREDIT_V1'

f=rt/'include/runtime/native_idle_wait.h';s=f.read_text()
if marker not in s:
    old='inline void notify() {if (wake().event) SetEvent(wake().event);}'
    assert s.count(old)==1
    s=s.replace(old,'// '+marker+': any host producer asks the guest to take its next poll.\ninline std::atomic<uint32_t> attention{0};\ninline void notify() {attention.store(1,std::memory_order_relaxed);if (wake().event) SetEvent(wake().event);}')
    old='inline void notify() {}'
    assert s.count(old)==1
    s=s.replace(old,'inline std::atomic<uint32_t> attention{0};\ninline void notify() {attention.store(1,std::memory_order_relaxed);}')
    if '#include <atomic>' not in s:s=s.replace('#pragma once\n','#pragma once\n#include <atomic>\n',1)
    f.write_text(s)

f=rt/'include/runtime/cpu_context.h';s=f.read_text()
if marker not in s:
    old='bool process_pending_callbacks(CPUContext &ctx);\n'
    assert s.count(old)==1
    s=s.replace(old,old+'''// NATIVE_POLL_CREDIT_V1: generated backward-branch polls are taken every Nth site.
extern int32_t g_native_poll_credit;
__attribute__((always_inline)) inline bool native_poll_due() {
  return __builtin_expect(--g_native_poll_credit<=0 || native_idle::attention.load(std::memory_order_relaxed)!=0,0);
}
''')
    f.write_text(s)

f=rt/'src/hle/ios.cpp';s=f.read_text()
if marker not in s:
    old='bool process_pending_callbacks(CPUContext &ctx) {\n  native_fp_context_probe(ctx,"poll");'
    assert s.count(old)==1
    new='''int32_t g_native_poll_credit = 1; // NATIVE_POLL_CREDIT_V1
bool process_pending_callbacks(CPUContext &ctx) {
  { // NATIVE_POLL_CREDIT_V1: IPC/DI delays count polls, so they keep every-site polling.
    static const int32_t reload=[](){const char* e=std::getenv("NWII_POLL_CREDIT");int v=e?std::atoi(e):64;
      if(std::getenv("NWII_NO_POLL_THROTTLE"))v=1;return int32_t(v<1?1:v>1024?1024:v);}();
    g_native_poll_credit=(g_ipc_interrupt_delay!=0 || hw::g_di_interrupt_delay!=0)?1:reload;
    native_idle::attention.store(0,std::memory_order_relaxed);
  }
  native_fp_context_probe(ctx,"poll");'''
    s=s.replace(old,new)
    f.write_text(s)

old='\n    if (process_pending_callbacks(ctx)) return;'
new='\n    if (nwii::runtime::native_poll_due() && process_pending_callbacks(ctx)) return;'
double='native_poll_due()) { if (nwii::runtime::native_poll_due() && process_pending_callbacks(ctx)) return;'
changed=0;total=0
for path in sorted((p/'build/crash-generated').glob('*.cpp')):
    s=path.read_text(errors='surrogateescape')
    n=s.count(old)
    if n or double in s:
        path.write_text(s.replace(old,new).replace(double,'native_poll_due()) { if (process_pending_callbacks(ctx)) return;'),errors='surrogateescape');changed+=1;total+=n
# The 'Spinning at PC' diagnostic (inst_count % 10000000 on every site, a 64-bit modulo) only matters when a real poll runs.
fold=re.compile(r'if \(nwii::runtime::native_poll_due\(\) && process_pending_callbacks\(ctx\)\) return;\n(\s*)(if \(\(ctx\.inst_count % 10000000\) == 0\) \{[^\n]*\})')
folded=0
for path in sorted((p/'build/crash-generated').glob('*.cpp')):
    s=path.read_text(errors='surrogateescape')
    t,n=fold.subn(lambda m:'if (nwii::runtime::native_poll_due()) { if (process_pending_callbacks(ctx)) return; '+m[2]+' }',s)
    if n:path.write_text(t,errors='surrogateescape');folded+=n
print(f'Poll credit installed; {total} generated poll sites rewritten in {changed} chunks this pass; {folded} diagnostic checks folded into the due branch.')
