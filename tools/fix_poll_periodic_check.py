"""Reduce the common periodic-poll test without changing any firing counts.

20,000,000 = 256 * 78,125. A nonzero low byte proves non-divisibility;
only the rare aligned counts need the remaining constant-divisor check.
"""
from pathlib import Path
import argparse

MARKER='NATIVE_POLL_PERIODIC_FACTOR_V1'
# NATIVE_POLL_PERIODIC_COUNTDOWN_V1 (tools/fix_event_mask.py) supersedes this factoring
# with an exact countdown; leave its form alone so a re-staged tree stays valid.
SUPERSEDED='NATIVE_POLL_PERIODIC_COUNTDOWN_V1'
HELPER='''// NATIVE_POLL_PERIODIC_FACTOR_V1: exactly the original IC modulo predicate.
static __attribute__((always_inline)) inline bool native_poll_not_periodic(uint64_t count) {
  if (__builtin_expect((count & 255u) != 0, 1)) return true;
  return ((count >> 8) % 78125u) != 0;
}
'''

def apply(source):
    if SUPERSEDED in source:
        assert source.count('native_poll_not_periodic(ctx.inst_count)')==1
        return source
    if MARKER in source:
        assert HELPER in source and source.count('native_poll_not_periodic(ctx.inst_count)')==1
        return source
    anchor='static inline bool native_process_callbacks_poll(CPUContext &ctx) {'
    assert source.count(anchor)==1 and source.count('ctx.inst_count%20000000!=0')==1
    return source.replace(anchor,HELPER+'\n'+anchor,1).replace('ctx.inst_count%20000000!=0','native_poll_not_periodic(ctx.inst_count)',1)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--ios',type=Path,default=Path(__file__).resolve().parents[1]/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp')
    args=parser.parse_args();source=args.ios.read_text();result=apply(source)
    if result!=source:args.ios.write_text(result)
    print('Periodic poll predicate factored; identical instruction-count trigger points.')
