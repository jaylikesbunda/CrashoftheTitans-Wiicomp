"""Park only the verified, interruptible OS idle loop, using wall-clock deadlines.

Original instructions/polls and timer handlers remain; virtual-time mode never
waits. Timed waits are bounded to 250us and wake on IRQ/callback/VI/shutdown.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime'
for name in ('native_hud.h','native_stutter.h'):
    target=rt/'include/runtime'/name;value=(p/'runtime-overlays'/name).read_text()
    if not target.exists() or target.read_text()!=value:target.write_text(value)
dst = rt/'include/runtime/native_idle_wait.h'
src = (p/'runtime-overlays/idle_wait.h').read_text()
if not dst.exists() or dst.read_text() != src: dst.write_text(src)
def edit(path, old, new, marker):
    s = path.read_text()
    if marker in s:return
    assert s.count(old)==1, (str(path),old)
    path.write_text(s.replace(old,new,1))
edit(rt/'include/runtime/event_scheduler.h', '    uint64_t now() const { return m_now; }',
     '    uint64_t now() const { return m_now; }\n'
     '    uint64_t next_due() const { return m_heap.empty() ? UINT64_MAX : m_heap.front().due; } // NATIVE_EVENT_NEXT_DUE_V1',
     'NATIVE_EVENT_NEXT_DUE_V1')
edit(rt/'include/runtime/cpu_context.h', '#include <array>',
     '#include "runtime/native_idle_wait.h" // NATIVE_IDLE_QUEUE_INCLUDE_V1\n#include <array>', 'NATIVE_IDLE_QUEUE_INCLUDE_V1')
edit(rt/'include/runtime/cpu_context.h', '    pending_cb_count.fetch_add(1, std::memory_order_relaxed);',
     '    pending_cb_count.fetch_add(1, std::memory_order_release);\n'
     '    native_idle::notify(); // NATIVE_IDLE_QUEUE_WAKE_V1', 'NATIVE_IDLE_QUEUE_WAKE_V1')
edit(rt/'include/runtime/hw/hw.h', '#pragma once',
     '#pragma once\n#include "runtime/native_idle_wait.h" // NATIVE_IDLE_IRQ_INCLUDE_V1', 'NATIVE_IDLE_IRQ_INCLUDE_V1')
edit(rt/'include/runtime/hw/hw.h', '  pi_intsr.fetch_or(mask, std::memory_order_relaxed);',
     '  pi_intsr.fetch_or(mask, std::memory_order_release);\n'
     '  native_idle::notify(); // NATIVE_IDLE_IRQ_WAKE_V1', 'NATIVE_IDLE_IRQ_WAKE_V1')
path=rt/'src/core/main.cpp';s=path.read_text()
# Applied-probes for the host wake: this stage's own marker, plus the two later stages that
# rewrite the same host-loop line and drop it (fix_single_vi_source.py wraps the site in its
# single_vi block; fix_event_mask.py rewrites it again). Without them a second pass re-inserts
# `notify(); // NATIVE_IDLE_HOST_WAKE_V1` into the middle of the already-rewritten line, and the
# `//` comment swallows the rest of it -- including the closing braces of the surrounding blocks,
# which is a hard compile error ("expected '}' at end of input").
HOST_WAKE_APPLIED = ('NATIVE_IDLE_HOST_WAKE_V1',      # this stage
                     'NATIVE_SINGLE_VI_SOURCE_V1',    # tools/fix_single_vi_source.py
                     'NATIVE_EVENT_MASK_V1')          # tools/fix_event_mask.py
if not any(marker in s for marker in HOST_WAKE_APPLIED):
    assert s.count('ctx->vblank_pending = true;')==2
    s=s.replace('ctx->vblank_pending = true;', 'ctx->vblank_pending = true; nwii::runtime::native_idle::notify(); // NATIVE_IDLE_HOST_WAKE_V1')
    s=s.replace('ctx->is_running = false;', 'ctx->is_running = false; nwii::runtime::native_idle::notify();')
    path.write_text(s)
path=rt/'src/hle/ios.cpp';s=path.read_text()
if 'NATIVE_OS_IDLE_WAIT_V1' not in s:
    (p/'runtime-overlays/idle_poll_baseline.cpp').write_text(s)
    old='  return native_process_callbacks_slow(ctx,due,false);'
    new='''  // NATIVE_OS_IDLE_WAIT_V1: verified SDK idle branch, never a general spin heuristic.
  static const bool idle_wait = std::getenv("NWII_WALLTB") && !std::getenv("NWII_NO_IDLE_WAIT");
  if (idle_wait && due && !diagnostics && !g_trace_after_dec &&
      ctx.pc == 0x803BBFA0u && ctx.cr[0].eq && !ctx.in_callback &&
      (ctx.msr & 0x8000u) && ctx.dispatch_saved_ctx == 0 &&
      g_ipc_interrupt_delay == 0 && hw::g_di_interrupt_delay == 0 &&
      ctx.mmu.read32(0x800000E4) == 0 &&
      ((ctx.gpr[13] - 10568u) & 0x3fffffffu) < 0x01800000u &&
      ctx.mmu.read32(ctx.gpr[13] - 10568u) == 0) {
    auto pending = [&] {
      return !ctx.is_running.load(std::memory_order_acquire) ||
        ctx.vblank_pending.load(std::memory_order_acquire) ||
        ctx.pending_cb_count.load(std::memory_order_acquire) != 0 ||
        (hw::pi_intsr.load(std::memory_order_acquire) & hw::pi_intmr.load(std::memory_order_relaxed)) != 0;
    };
    if (!pending()) {
      const uint64_t now = ctx.read_timebase();
      const uint64_t ticks = native_idle::budget_ticks(now, EventScheduler::get().next_due(),
          ctx.dec_irq_pending, ctx.dec_written_tb, ctx.dec_value, ctx.tb_freq);
      if (ticks) {
        native_trace::Scope scope("guest.idle_wait");
        native_idle::wait(ticks, ctx.tb_freq, pending);
      }
    }
  }
  return native_process_callbacks_slow(ctx,due,false);'''
    assert s.count(old)==1
    path.write_text(s.replace(old,new,1))
print('Deadline-bounded wall-clock OS idle wait staged; original polls retained.')
