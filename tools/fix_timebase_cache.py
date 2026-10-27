"""NATIVE_TIMEBASE_CACHE_V1: refresh the wall timebase at most once per N guest instructions.

read_timebase() (NWII_WALLTB=1, the launcher default) reads the TSC on every call; the
fresh guest profile shows __rdtsc() as the single hottest line (1.41%). Guest time advances
only as fast as the emulated instruction stream, so the wall timebase can be refreshed every
N instructions (well under a poll's granularity) without changing any observable deadline:
the DEC and event scheduler already tolerate the 16-poll clock throttle. On x86-64 an aligned
uint64 load/store is atomic, so a concurrent reader can only see a slightly stale (monotonic)
value, never a torn one. Rollback NWII_BUILD_TIMEBASE_CACHE=0.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_TIMEBASE_CACHE_V1'

OLD = """  uint64_t read_timebase() const {
    static const bool wall = std::getenv("NWII_WALLTB") != nullptr;
    if (wall) {
      uint64_t us = (uint64_t)std::chrono::duration_cast<std::chrono::microseconds>(
                        std::chrono::steady_clock::now() - tb_start)
                        .count();
      return native_scale_timebase(us, tb_freq);
    }
    return inst_count;
  }"""

NEW = """  mutable uint64_t tb_cache_inst=0,tb_cache_val=0;
  mutable bool tb_cache_valid=false;
  uint64_t read_timebase() const {
    static const bool wall = std::getenv("NWII_WALLTB") != nullptr;
    if (wall) {
      // NATIVE_TIMEBASE_CACHE_V1: at most one TSC read per 512 guest instructions.
      if (tb_cache_valid && inst_count - tb_cache_inst < 512u) return tb_cache_val;
      uint64_t us = (uint64_t)std::chrono::duration_cast<std::chrono::microseconds>(
                        std::chrono::steady_clock::now() - tb_start)
                        .count();
      tb_cache_inst=inst_count;tb_cache_val=native_scale_timebase(us, tb_freq);tb_cache_valid=true;
      return tb_cache_val;
    }
    return inst_count;
  }"""


def apply(s):
    if MARK in s:
        return s
    if s.count(OLD) != 1:
        raise SystemExit('timebase anchor matched %d times (expected 1)' % s.count(OLD))
    return s.replace(OLD, NEW, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_TIMEBASE_CACHE', '1') == '0':
        print('timebase cache disabled')
        return
    path = args.port / 'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('timebase cache staged')


if __name__ == '__main__':
    main()
