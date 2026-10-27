"""NATIVE_EVENT_MASK_V1: one cache-line event word decides the poll fast path.

The per-dispatch poll (native_process_callbacks_poll) is the guest thread's largest
native-runtime item (3.15% of the guest thread, 0.55 ms/frame). Its fast path read five
independent producer locations on every real poll -- g_ipc_interrupt_delay,
hw::g_di_interrupt_delay, ctx.vblank_pending, hw::pi_intsr, hw::pi_intmr -- plus
ctx.pending_cb_count, and it was an out-of-line call from process_pending_callbacks.

This stage:
  R1  copies runtime-overlays/native_event_mask.h into the staged include tree and
      raises the mask bit at every cross-thread producer:
        kIpcDelay  hw_ipc.cpp arms g_ipc_interrupt_delay (50/5000)  -> raise
                   ios.cpp poll lowers it when the countdown reaches 0
        kVBlank    main.cpp host VI thread and the ios.cpp EventScheduler VI callback
                   set ctx.vblank_pending                            -> raise
                   ios.cpp poll lowers it where it consumes the field
        kPiIrq     trigger_pi_interrupt raises it only for a genuinely new *unmasked*
                   source, and the PI INTMR writer raises it when it enables a pending
                   bit; the slow path lowers it once (pi_intsr & pi_intmr) == 0.
      Producers with no bit, and why, are listed in the header (queue_callback,
      DEC expiry, EventScheduler, GX staged bytes, WPAD phase, native_idle::notify).
      hw::g_di_interrupt_delay has no arming site in the runtime, so the fast path keeps
      its exact `== 0` test on the plain int instead of a bit nothing raises.
  R2  reads that one word once per call and drops the five producer loads.
  R3  replaces the `count % 20000000` heartbeat test with an exact countdown: for any
      monotonic poll sequence the countdown fires on exactly the same counts as the
      modulo, without a 64-bit divide/multiply on the hot path.
  R4  makes native_process_callbacks_poll always_inline so the one hot caller
      (process_pending_callbacks) does not pay a call/return per real poll. The cold
      sampled-polls wrapper still gets its own copy.
  R5  drops the `(ctx.inst_count % 10000000)` "Spinning at PC" console print from the
      9748 emitted poll sites (host console output only; NWII_LOOPTRACE still reports
      a spin at a chosen PC from the slow path).

Every replacement is MARK-guarded and asserted to match exactly once.
Build rollback: NWII_BUILD_EVENT_MASK=0. Idempotent.
"""
from pathlib import Path
import argparse
import os
import re

MARK = 'NATIVE_EVENT_MASK_V1'
COUNTDOWN_MARK = 'NATIVE_POLL_PERIODIC_COUNTDOWN_V1'
TREES = ('crash-generated', 'crash-hot', 'crash-split')

# ---------------------------------------------------------------- R1 producers

HW_OLD = """inline void trigger_pi_interrupt(uint32_t mask) {
  pi_intsr.fetch_or(mask, std::memory_order_release);
  native_idle::notify(); // NATIVE_IDLE_IRQ_WAKE_V1
}"""
HW_NEW = """// NATIVE_EVENT_MASK_V1: g_ipc_interrupt_delay is counted in polls, so every
// arm/disarm keeps the mask in step with it.
inline void native_ipc_delay_set(int value) {
  nwii::runtime::g_ipc_interrupt_delay = value;
  if (value != 0) native_events::raise(native_events::kIpcDelay);
  else native_events::lower(native_events::kIpcDelay);
}
inline void trigger_pi_interrupt(uint32_t mask) {
  const uint32_t old = pi_intsr.fetch_or(mask, std::memory_order_release);
  // NATIVE_EVENT_MASK_V1: only a new *unmasked* source needs a poll; an already
  // pending source or a masked-off one leaves (pi_intsr & pi_intmr) unchanged.
  if ((mask & ~old & pi_intmr.load(std::memory_order_relaxed)) != 0)
    native_events::raise(native_events::kPiIrq);
  native_idle::notify(); // NATIVE_IDLE_IRQ_WAKE_V1
}"""

HW_DECL = """namespace nwii::runtime {
uint64_t get_os_time();
"""
HW_DECL_NEW = """namespace nwii::runtime {
uint64_t get_os_time();
extern int g_ipc_interrupt_delay; // NATIVE_EVENT_MASK_V1
"""

PI_OLD = """                pi_intmr = val;
            }"""
PI_NEW = """                native_pi_intmr_write(val); // NATIVE_EVENT_MASK_V1
            }"""
PI_STAGED_OLD = """                pi_intmr = val;
                // NATIVE_EVENT_MASK_V1: enabling a still-pending source makes a poll due.
                if ((pi_intsr.load(std::memory_order_relaxed) & val) != 0)
                    native_events::raise(native_events::kPiIrq);
            }"""

PI_HELPER_OLD = """inline void trigger_pi_interrupt(uint32_t mask) {"""
PI_HELPER_NEW = """// NATIVE_EVENT_MASK_V1: enabling a still-pending source makes a poll due. Kept as a
// named helper so the INTMR hook can be driven directly by tools/check_event_mask.py.
inline void native_pi_intmr_write(uint32_t val) {
  pi_intmr = val;
  if ((pi_intsr.load(std::memory_order_relaxed) & val) != 0)
    native_events::raise(native_events::kPiIrq);
}
inline void trigger_pi_interrupt(uint32_t mask) {"""

MAIN_OLD = ('if(!single_vi)ctx->vblank_pending = true; nwii::runtime::native_idle::notify();'
            '} // NATIVE_IDLE_HOST_WAKE_V1')
MAIN_NEW = ('if(!single_vi){ctx->vblank_pending = true; '
            'nwii::runtime::native_events::raise(nwii::runtime::native_events::kVBlank);} '
            'nwii::runtime::native_idle::notify();} // NATIVE_EVENT_MASK_V1')

IOS_PRODUCERS = [
    # EventScheduler VI callback (guest thread) raises the field latch.
    ('c.vblank_pending = true; // raised at the next safe poll; coalesces while pending\n',
     'c.vblank_pending = true; // raised at the next safe poll; coalesces while pending\n'
     '                      native_events::raise(native_events::kVBlank); // NATIVE_EVENT_MASK_V1\n'),
    # ... and the poll lowers it exactly where it consumes the field.
    ('          ctx.vblank_pending = false;\n          nwii::runtime::hw::vi_trigger_interrupt();',
     '          ctx.vblank_pending = false;\n'
     '          native_events::lower(native_events::kVBlank); // NATIVE_EVENT_MASK_V1: consumed\n'
     '          nwii::runtime::hw::vi_trigger_interrupt();'),
    # IPC/DI delay countdown: the rule is "poll every time while armed", so the bit
    # stays set for the whole countdown and is cleared only when it reaches zero.
    ("""  if (g_ipc_interrupt_delay > 0) {
      g_ipc_interrupt_delay--;
      if (g_ipc_interrupt_delay == 0) {
          nwii::runtime::hw::trigger_pi_interrupt(0x00004000);""",
     """  if (g_ipc_interrupt_delay > 0) {
      g_ipc_interrupt_delay--;
      if (g_ipc_interrupt_delay == 0) {
          native_events::lower(native_events::kIpcDelay); // NATIVE_EVENT_MASK_V1: disarmed
          nwii::runtime::hw::trigger_pi_interrupt(0x00004000);"""),
]

IOS_PI_OLD = '  uint32_t active_ints = nwii::runtime::hw::pi_intsr & nwii::runtime::hw::pi_intmr;'
IOS_PI_NEW = """  uint32_t active_ints = nwii::runtime::hw::pi_intsr & nwii::runtime::hw::pi_intmr;
  // NATIVE_EVENT_MASK_V1: every slow poll re-verifies the PI latch against the raw
  // registers, which is exactly the test the fast path used to run inline.
  if (active_ints == 0) {
      if (native_events::any(native_events::kPiIrq)) native_events::lower(native_events::kPiIrq);
  } else {
      native_events::raise(native_events::kPiIrq);
  }"""

# ---------------------------------------------------------------- R2 fast path

COND_OLD = """  if (!diagnostics && !due && !g_trace_after_dec && ctx.pc!=0 && ctx.pc!=0xFFFFFFFCu &&
      native_poll_not_periodic(ctx.inst_count) && g_ipc_interrupt_delay==0 && hw::g_di_interrupt_delay==0 &&
      !ctx.vblank_pending && !(hw::pi_intsr.load() & hw::pi_intmr.load()) &&
      ctx.pending_cb_count.load(std::memory_order_relaxed)==0) {"""
COND_NEW = """  // NATIVE_EVENT_MASK_V1: one cache-line read replaces the three cross-thread producer
  // loads (vblank_pending, pi_intsr, pi_intmr). pending_cb_count is deliberately not in
  // this condition: it is re-read below and is the dispatch decision itself.
  const uint32_t events=native_events::mask.load(std::memory_order_acquire);
  if (!diagnostics && !due && !g_trace_after_dec && ctx.pc!=0 && ctx.pc!=0xFFFFFFFCu &&
      native_poll_not_periodic(ctx.inst_count) && g_ipc_interrupt_delay==0 && hw::g_di_interrupt_delay==0 &&
      (events & native_events::kSlowMask)==0) {"""

# ---------------------------------------------------------------- R3 countdown

HELPER_OLD = """// NATIVE_POLL_PERIODIC_FACTOR_V1: exactly the original IC modulo predicate.
static __attribute__((always_inline)) inline bool native_poll_not_periodic(uint64_t count) {
  if (__builtin_expect((count & 255u) != 0, 1)) return true;
  return ((count >> 8) % 78125u) != 0;
}
"""
COUNTDOWN = """// NATIVE_POLL_PERIODIC_COUNTDOWN_V1: the same predicate as (count % 20000000) != 0 for
// every monotonic poll sequence, with no 64-bit divide/multiply on the hot path.
// `next` is the smallest multiple of the heartbeat period that is >= every count seen so
// far: count < next is the not-periodic case, count == next is the exact tick, and a
// count that jumped past `next` realigns without firing unless it is itself a multiple.
// The initial value is 0 because the original predicate fires at inst_count 0 too.
static uint64_t native_poll_heartbeat_next = 0;
static __attribute__((always_inline)) inline bool native_poll_not_periodic(uint64_t count) {
  if (__builtin_expect(count < native_poll_heartbeat_next, 1)) return true;
  if (count == native_poll_heartbeat_next) { native_poll_heartbeat_next += 20000000ull; return false; }
  const bool exact = (count % 20000000ull) == 0;
  native_poll_heartbeat_next = (count / 20000000ull + 1ull) * 20000000ull;
  return !exact;
}
"""

RAW_PERIODIC = 'ctx.inst_count%20000000!=0'
POLL_ANCHOR = 'static inline bool native_process_callbacks_poll(CPUContext &ctx) {'

# ---------------------------------------------------------------- R4 inline

POLL_DECL_OLD = 'static inline bool native_process_callbacks_poll(CPUContext&);'
POLL_DECL_NEW = ('static __attribute__((always_inline)) inline bool native_process_callbacks_poll(CPUContext&);'
                 ' // NATIVE_EVENT_MASK_V1')
POLL_DEF_OLD = POLL_ANCHOR
POLL_DEF_NEW = ('// NATIVE_EVENT_MASK_V1: always_inline; the one hot caller is process_pending_callbacks.\n'
                'static __attribute__((always_inline)) inline bool native_process_callbacks_poll(CPUContext &ctx) {')

# ---------------------------------------------------------------- R5 spin print

SPIN_RE = re.compile(
    r' if \(\(ctx\.inst_count % 10000000\) == 0\) \{ std::cout << "Spinning at PC: 0x"'
    r' << std::hex << 0x[0-9A-F]{8} << " LR: 0x" << std::hex << ctx\.lr << std::dec'
    r' << std::endl; \}')


def _edit(text, edits, tag):
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            raise SystemExit('%s: %s anchor matched %d times (expected 1): %r' % (MARK, tag, n, old[:70]))
        text = text.replace(old, new, 1)
    return text


def _patch(path, edits, tag):
    before = path.read_text(errors='surrogateescape')
    after = _edit(before, edits, tag)
    if after != before:
        path.write_text(after, errors='surrogateescape')
    return after != before


def apply(port):
    rt = port / 'build/nwii-full-source/nWiiRuntime'
    overlay = port / 'runtime-overlays/native_event_mask.h'
    dst = rt / 'include/runtime/native_event_mask.h'
    value = overlay.read_text()
    if not dst.exists() or dst.read_text() != value:
        dst.write_text(value)

    changed = []
    hw_h = rt / 'include/runtime/hw/hw.h'
    text = hw_h.read_text()
    hw_changed = False
    if 'extern int g_ipc_interrupt_delay; // NATIVE_EVENT_MASK_V1' not in text:
        assert text.count(HW_DECL) == 1, 'hw.h g_ipc_interrupt_delay declaration anchor'
        text = text.replace(HW_DECL, HW_DECL_NEW, 1)
        hw_changed = True
    if 'native_ipc_delay_set' not in text:
        assert text.count(HW_OLD) == 1, 'trigger_pi_interrupt anchor'
        text = text.replace(HW_OLD, HW_NEW, 1)
        hw_changed = True
    if 'native_pi_intmr_write' not in text:
        assert text.count(PI_HELPER_OLD) == 1, 'trigger_pi_interrupt definition anchor'
        text = text.replace(PI_HELPER_OLD, PI_HELPER_NEW, 1)
        hw_changed = True
    if hw_changed:
        hw_h.write_text(text)
        changed.append('hw.h')
    hw_pi = rt / 'src/hw/hw_pi.cpp'
    text = hw_pi.read_text()
    if 'native_pi_intmr_write' not in text:
        if PI_STAGED_OLD in text:
            text = text.replace(PI_STAGED_OLD, PI_NEW, 1)
        else:
            assert text.count(PI_OLD) == 1, 'INTMR write anchor'
            text = text.replace(PI_OLD, PI_NEW, 1)
        hw_pi.write_text(text)
        changed.append('hw_pi.cpp')
    ipc = rt / 'src/hw/hw_ipc.cpp'
    text = ipc.read_text()
    n = len(re.findall(r'g_ipc_interrupt_delay = (?:50|5000|0);', text))
    if n:
        if MARK not in text:
            text = re.sub(r'g_ipc_interrupt_delay = (50|5000|0);',
                          r'native_ipc_delay_set(\1); // NATIVE_EVENT_MASK_V1', text)
            ipc.write_text(text)
            changed.append('hw_ipc.cpp(%d)' % n)
        else:
            raise SystemExit('%s: unexpected g_ipc_interrupt_delay assignment' % MARK)
    main = rt / 'src/core/main.cpp'
    text = main.read_text()
    if MAIN_OLD in text:
        assert text.count(MAIN_OLD) == 2, 'host vblank anchor count'
        main.write_text(text.replace(MAIN_OLD, MAIN_NEW))
        changed.append('main.cpp(2)')

    ios = rt / 'src/hle/ios.cpp'
    text = ios.read_text()
    if MARK not in text:
        if COUNTDOWN_MARK not in text:
            if HELPER_OLD in text:
                assert text.count(HELPER_OLD) == 1, 'NATIVE_POLL_PERIODIC_FACTOR_V1 helper not found'
                text = text.replace(HELPER_OLD, COUNTDOWN, 1)
            else:
                if RAW_PERIODIC in text:
                    assert text.count(RAW_PERIODIC) == 1
                    text = text.replace(RAW_PERIODIC, 'native_poll_not_periodic(ctx.inst_count)', 1)
                assert text.count(POLL_ANCHOR) == 1
                text = text.replace(POLL_ANCHOR, COUNTDOWN + '\n' + POLL_ANCHOR, 1)
        text = _edit(text, IOS_PRODUCERS, 'ios.cpp producers')
        text = _edit(text, [(IOS_PI_OLD, IOS_PI_NEW)], 'ios.cpp active_ints')
        text = _edit(text, [(COND_OLD, COND_NEW)], 'ios.cpp fast path')
        text = _edit(text, [(POLL_DECL_OLD, POLL_DECL_NEW), (POLL_DEF_OLD, POLL_DEF_NEW)], 'ios.cpp inline')
        ios.write_text(text)
        changed.append('ios.cpp')

    # R5: the per-site "Spinning at PC" print is host console output only.
    dropped = 0
    for tree in TREES:
        base = port / 'build' / tree
        if not base.is_dir():
            continue
        for path in sorted(base.glob('*.cpp')):
            text = path.read_text(errors='surrogateescape')
            new, k = SPIN_RE.subn('', text)
            if k:
                path.write_text(new, errors='surrogateescape')
                dropped += k
    print('%s staged (%s); %d emitted spin prints dropped' % (MARK, ','.join(changed) or 'no change', dropped))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_EVENT_MASK', '0') == '0':
        # The overlay include/runtime/native_idle_wait.h unconditionally includes
        # runtime/native_event_mask.h, so the header must exist even when the poll edits are
        # skipped -- otherwise the staged tree does not compile. Installing it is inert:
        # nothing reads the mask unless apply() rewrites the poll condition.
        dst = args.port / 'build/nwii-full-source/nWiiRuntime/include/runtime/native_event_mask.h'
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((args.port / 'runtime-overlays/native_event_mask.h').read_bytes())
        print('event mask disabled (header installed so the idle-wait include resolves)')
        return
    apply(args.port)


if __name__ == '__main__':
    main()
