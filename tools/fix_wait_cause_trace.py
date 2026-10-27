"""Add low-volume signal and observed-resume markers; never change delivery."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
for relative,anchor,marker in [
 ('src/hw/hw_pe.cpp','    ++native_pe_tokens;', '    native_trace::mark("pe.token_signal",token,raise_interrupt);'),
 ('src/hw/hw_pe.cpp','    ++native_pe_finishes; // NATIVE_WAIT_FINISH','    native_trace::mark("pe.finish_signal");'),
 ('src/hw/hw_vi.cpp','  ++native_vi_events;', '  native_trace::mark("vi.signal",native_vi_events.load(std::memory_order_relaxed));')]:
 f=rt/relative;s=f.read_text()
 if marker not in s:
  assert s.count(anchor)==1;s=s.replace(anchor,anchor+'\n'+marker,1)
  if '#include "runtime/native_frame_trace.h"' not in s:s='#include "runtime/native_frame_trace.h"\n'+s
  f.write_text(s)
f=rt/'src/hle/ios.cpp';s=f.read_text()
if 'NATIVE_IDLE_RESUME_CAUSE_V1' not in s:
 anchor='        native_idle::wait(ticks, ctx.tb_freq, pending);';assert s.count(anchor)==1
 code='''
        // NATIVE_IDLE_RESUME_CAUSE_V1: observed state, not a guessed wake cause.
        if(native_trace::active.load(std::memory_order_relaxed)){
          const uint32_t irq=hw::pi_intsr.load(std::memory_order_acquire)&hw::pi_intmr.load(std::memory_order_relaxed);
          const uint64_t flags=(!ctx.is_running.load()?1u:0u)|(ctx.vblank_pending.load()?2u:0u)|
            (ctx.pending_cb_count.load()?4u:0u)|(irq?8u:0u)|(ctx.read_timebase()>=now+ticks?16u:0u);
          native_trace::mark("guest.idle_resume",flags|(uint64_t(irq)<<16),
            ((hw::native_pe_tokens.load()&0xffffffffull)<<32)|(hw::native_pe_finishes.load()&0xffffffffull));
        }
'''
 s=s.replace(anchor,anchor+code,1);f.write_text(s)
print('Trace records PE/VI signal timestamps and observed idle-resume flags; delivery unchanged.')
