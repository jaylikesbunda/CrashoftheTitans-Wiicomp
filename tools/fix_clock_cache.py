"""NATIVE_CLOCK_CACHE_V1: bound how often get_os_time() pays for a clock read.

get_os_time() is the microsecond clock the AI register model and the VI VCOUNT read use,
and it is an out-of-line call that forwards to the wrapped steady_clock and then converts
to microseconds. The guest thread calls it in bursts (every AI MMIO access and every VI
VCOUNT read), and the value it needs moves at microsecond resolution while the guest
retires roughly 450 million instructions per second.

The stage caches the value per thread and refreshes it every NWII_CLOCK_CACHE_INST guest
instructions -- the same 512-instruction span NATIVE_TIMEBASE_CACHE_V1 already uses for
read_timebase(), so the two guest clocks share one staleness bound (about 1.1 us of guest
execution). The cache is invalidated after the OS idle park, which is the one place where
guest time can jump without the instruction count moving.

The VI VCOUNT path keeps the fresh reader: it is the only real-time consumer of this
clock, so hw_vi.cpp now calls get_os_time_fresh(). Nothing else changes: same epoch, same
unit, same monotonicity, same call sites.

Build rollback: NWII_BUILD_CLOCK_CACHE=0. Idempotent.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_CLOCK_CACHE_V1'

OLD = """uint64_t nwii::runtime::get_os_time() {
  auto now = std::chrono::steady_clock::now();
  return std::chrono::duration_cast<std::chrono::microseconds>(
             now.time_since_epoch())
      .count();
}"""

NEW = """namespace nwii::runtime { extern CPUContext* g_ctx_ptr; } // NATIVE_CLOCK_CACHE_V1

namespace {
// NATIVE_CLOCK_CACHE_V1: per-thread microsecond clock with a guest-instruction guard.
struct NativeClockCache { uint64_t inst = 0; uint64_t us = 0; bool valid = false; };
thread_local NativeClockCache g_native_clock_cache;
uint64_t native_os_time_fresh() {
  auto now = std::chrono::steady_clock::now();
  return std::chrono::duration_cast<std::chrono::microseconds>(
             now.time_since_epoch())
      .count();
}
} // namespace

void nwii::runtime::native_clock_invalidate() {
  g_native_clock_cache.valid = false;
}

uint64_t nwii::runtime::get_os_time_fresh() { return native_os_time_fresh(); }

uint64_t nwii::runtime::get_os_time() {
  CPUContext* ctx = nwii::runtime::g_ctx_ptr;
  if (!ctx) return native_os_time_fresh();
  const uint64_t inst = ctx->inst_count;
  NativeClockCache& cache = g_native_clock_cache;
  // NATIVE_TIMEBASE_CACHE_V1 uses the same span for read_timebase(), so both guest
  // clocks share one staleness bound. NWII_CLOCK_CACHE_INST=1 restores a read per call.
  static const uint64_t span = [] {
    const char* e = std::getenv("NWII_CLOCK_CACHE_INST");
    const long v = e ? std::strtol(e, nullptr, 10) : 512;
    return v < 1 ? 1ull : (v > 4096 ? 4096ull : uint64_t(v));
  }();
  if (cache.valid && inst - cache.inst < span) return cache.us;
  const uint64_t us = native_os_time_fresh();
  cache.inst = inst;
  cache.us = us;
  cache.valid = true;
  return us;
}"""

HW_OLD = """namespace nwii::runtime {
uint64_t get_os_time();
"""
HW_NEW = """namespace nwii::runtime {
uint64_t get_os_time();
uint64_t get_os_time_fresh();   // NATIVE_CLOCK_CACHE_V1: never cached
void native_clock_invalidate(); // NATIVE_CLOCK_CACHE_V1
"""

VI_OLD = 'if(offset==0x2c)return uint16_t((nwii::runtime::get_os_time()/64)%313+1);'
VI_NEW = ('if(offset==0x2c)return uint16_t((nwii::runtime::get_os_time_fresh()/64)%313+1);'
          ' // NATIVE_CLOCK_CACHE_V1: the only real-time consumer')

IOS_OLD = '        native_idle::wait(ticks, ctx.tb_freq, pending);'
IOS_NEW = ('        native_idle::wait(ticks, ctx.tb_freq, pending);\n'
           '        nwii::runtime::native_clock_invalidate(); // NATIVE_CLOCK_CACHE_V1: guest time jumped')


def _patch(path, edits, tag):
    text = path.read_text(errors='surrogateescape')
    if MARK in text:
        return False
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            raise SystemExit('%s: %s anchor matched %d times (expected 1): %r' % (MARK, tag, n, old[:70]))
        text = text.replace(old, new, 1)
    path.write_text(text, errors='surrogateescape')
    return True


def apply(port):
    rt = port / 'build/nwii-full-source/nWiiRuntime'
    changed = []
    if _patch(rt / 'include/runtime/hw/hw.h', [(HW_OLD, HW_NEW)], 'hw.h'):
        changed.append('hw.h')
    if _patch(rt / 'src/core/os_hle_stubs.cpp', [(OLD, NEW)], 'os_hle_stubs.cpp'):
        changed.append('os_hle_stubs.cpp')
    if _patch(rt / 'src/hw/hw_vi.cpp', [(VI_OLD, VI_NEW)], 'hw_vi.cpp'):
        changed.append('hw_vi.cpp')
    ios = rt / 'src/hle/ios.cpp'
    text = ios.read_text(errors='surrogateescape')
    if IOS_OLD in text and 'NATIVE_CLOCK_CACHE_V1' not in text:
        assert text.count(IOS_OLD) == 1
        ios.write_text(text.replace(IOS_OLD, IOS_NEW, 1), errors='surrogateescape')
        changed.append('ios.cpp')
    print('%s staged (%s)' % (MARK, ','.join(changed) or 'no change'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_CLOCK_CACHE', '1') == '0':
        print('clock cache disabled')
        return
    apply(args.port)


if __name__ == '__main__':
    main()
