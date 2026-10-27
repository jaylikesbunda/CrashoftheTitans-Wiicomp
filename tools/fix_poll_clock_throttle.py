"""Stop reading the host clock several times per guest poll.

Profiling the busiest host thread in a live cinematic showed roughly a quarter
of its samples in the clock read (plus unresolved QPC time) from
process_pending_callbacks: WPAD poll, event scheduler and decrementer expiry
each read the wall clock on every poll. Evaluate those on a fixed poll phase;
guest-visible time (mftb/mfdec) is unchanged and still monotonic.
"""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
path=port/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp'
text=path.read_text()
marker='NATIVE_POLL_CLOCK_THROTTLE_V1'
switch='NWII_NO_POLL_THROTTLE'
wpad_marker='NATIVE_WPAD_POLL_THROTTLE_V1'
if wpad_marker not in text and 'NATIVE_WPAD_POLL_THROTTLE_V2' not in text:
    old='''  if (!s.enabled || ctx.in_callback || !(ctx.msr & 0x8000)) return;
  const auto ms='''
    new='''  if (!s.enabled || ctx.in_callback || !(ctx.msr & 0x8000)) return;
  { static uint32_t wpad_phase=0; if ((++wpad_phase & 31u)!=0) return; } // NATIVE_WPAD_POLL_THROTTLE_V1
  const auto ms='''
    assert text.count(old)==1
    text=text.replace(old,new,1)
if marker not in text:
    old='''      nwii::runtime::EventScheduler::get().advance(ctx, ctx.read_timebase());
  }
'''
    new='''      if (poll_clock_due)
        nwii::runtime::EventScheduler::get().advance(ctx, ctx.read_timebase());
  }
'''
    assert text.count(old)==1
    text=text.replace(old,new,1)
    old='  if (ctx.dec_expired() && (ctx.msr & 0x8000) && !ctx.in_callback) {'
    new='  if (poll_clock_due && ctx.dec_expired() && (ctx.msr & 0x8000) && !ctx.in_callback) {'
    assert text.count(old)==1
    text=text.replace(old,new,1)
    # Anchor on the function head only: earlier stages (idle drain) may already have added code before the probe call.
    old='''bool process_pending_callbacks(CPUContext &ctx) {
'''
    new='''bool process_pending_callbacks(CPUContext &ctx) {
  static uint32_t poll_clock_phase = 0; // NATIVE_POLL_CLOCK_THROTTLE_V1
  const bool poll_clock_due = ((++poll_clock_phase) & 15u) == 0;
'''
    assert text.count(old)==1
    text=text.replace(old,new,1)
# The rollback switch is installed per line: a file-wide `switch not in text` test is already true
# when any other stage leaves NWII_NO_POLL_THROTTLE in ios.cpp (fix_poll_guard_outline.py's prelude
# does), so on a re-stage the freshly inserted throttle line was left without the rollback
# (NWII_NO_POLL_THROTTLE then no longer restored the unthrottled poll).
a='  { static uint32_t wpad_phase=0; if ((++wpad_phase & 31u)!=0) return; }'
if 'no_thr && (++wpad_phase' not in text and a in text:
    b='  { static const bool no_thr=std::getenv("NWII_NO_POLL_THROTTLE")!=nullptr; static uint32_t wpad_phase=0; if (!no_thr && (++wpad_phase & 31u)!=0) return; }'
    assert text.count(a)==1; text=text.replace(a,b,1)
a='  const bool poll_clock_due = ((++poll_clock_phase) & 15u) == 0;'
if 'poll_no_throttle || ((++poll_clock_phase)' not in text and a in text:
    b='  static const bool poll_no_throttle = std::getenv("NWII_NO_POLL_THROTTLE") != nullptr;\n  const bool poll_clock_due = poll_no_throttle || ((++poll_clock_phase) & 15u) == 0;'
    assert text.count(a)==1; text=text.replace(a,b,1)
path.write_text(text)
print('Poll clock reads throttled (scheduler/DEC every 16th poll, WPAD every 32nd).')
