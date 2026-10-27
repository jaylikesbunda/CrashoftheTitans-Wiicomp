"""Exact integer scaling for common guest clocks, preserving clock reads/rounding."""
from pathlib import Path
import argparse
MARKER='NATIVE_TIMEBASE_SCALE_V1'
HELPER='''  // NATIVE_TIMEBASE_SCALE_V1: same floor and uint64 wrap as the original expression.
  static __attribute__((always_inline)) inline uint64_t native_scale_timebase(uint64_t us, uint64_t frequency) {
    if (__builtin_expect(frequency == 40500000u, 1)) return us * 40u + (us >> 1);
    if (frequency == 60750000u) return us * 61u - (us >> 2) - ((us & 3u) != 0);
    return us / 1000000u * frequency + us % 1000000u * frequency / 1000000u;
  }
'''

def apply(source):
    if MARKER in source:
        assert HELPER in source and source.count('return native_scale_timebase(us, tb_freq);')==1
        return source
    anchor='  uint64_t read_timebase() const {'
    old='      return us / 1000000 * tb_freq + us % 1000000 * tb_freq / 1000000;'
    assert source.count(anchor)==1 and source.count(old)==1
    return source.replace(anchor,HELPER+anchor,1).replace(old,'      return native_scale_timebase(us, tb_freq);',1)

if __name__=='__main__':
    p=Path(__file__).resolve().parents[1]
    ap=argparse.ArgumentParser();ap.add_argument('--header',type=Path,default=p/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h')
    args=ap.parse_args();source=args.header.read_text();changed=apply(source)
    assert apply(changed)==changed
    if changed!=source:args.header.write_text(changed)
    print('Common guest-clock scaling simplified; reads, epochs, rounding and other frequencies retained.')
