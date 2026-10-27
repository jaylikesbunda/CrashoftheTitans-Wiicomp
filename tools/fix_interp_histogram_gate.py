"""Gate the interpreter PC histogram behind an env flag.

interpret_step inserts into a std::unordered_map (node allocation) on 1/16 of
every interpreted instruction, with no env gate. It is pure diagnostics (only
native_interp_report reads it). Keep the step counter (cheap) but skip the map
unless NWII_INTERP_HISTOGRAM is set. Build rollback:
NWII_BUILD_INTERP_HISTOGRAM_GATE=0. Idempotent.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_INTERP_HISTOGRAM_GATE_V1'

OLD = '  if((++g_interp_steps&15u)==0)++g_interp_histogram[(uint64_t(ctx.pc)<<32)|ctx.lr];\n'
NEW = ('  static const bool interp_histogram=std::getenv("NWII_INTERP_HISTOGRAM")!=nullptr; // ' + MARK + '\n'
       '  ++g_interp_steps;\n'
       '  if(interp_histogram && (g_interp_steps&15u)==0)++g_interp_histogram[(uint64_t(ctx.pc)<<32)|ctx.lr];\n')


def apply(s):
    if MARK in s:
        return s
    if s.count(OLD) != 1:
        raise SystemExit('interp histogram anchor not unique: %d' % s.count(OLD))
    return s.replace(OLD, NEW, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_INTERP_HISTOGRAM_GATE', '1') == '0':
        print('interp histogram gate disabled')
        return
    path = args.port / 'build/nwii-full-source/nWiiRuntime/src/hle/interpreter.cpp'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('interp histogram gate staged')


if __name__ == '__main__':
    main()
