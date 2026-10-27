"""Replace the FIFO publish/drain std::mutex with an SRW lock.

g_fifo_mutex is taken on every FIFO publication (~6000/frame on the guest thread)
and in the drain path. MinGW std::mutex is winpthreads; the SRW lock removes the
pthread self/TLS round trip from the lock/unlock. Semantics are identical
(non-recursive, blocking) and the mutex is not paired with a condition variable.
Build rollback: NWII_BUILD_FIFO_SRWLOCK=0. Idempotent.
"""
from pathlib import Path
import argparse
import os
import re

MARK = 'NATIVE_FIFO_SRWLOCK_V1'


def apply(s):
    if MARK in s:
        return s
    anchor = 'std::mutex g_fifo_mutex;'
    if s.count(anchor) != 1:
        raise SystemExit('g_fifo_mutex anchor not unique: %d' % s.count(anchor))
    s = s.replace(anchor, 'nwii::runtime::NativeFifoLock g_fifo_mutex; // ' + MARK, 1)
    pat = re.compile(r'std::lock_guard<std::mutex>(\s+\w+\(g_fifo_mutex\))')
    s, n = pat.subn(lambda m: 'nwii::runtime::NativeFifoLockGuard' + m[1], s)
    if n < 1:
        raise SystemExit('no g_fifo_mutex lock_guard sites found')
    inc = '#include "runtime/native_fifo_lock.h" // ' + MARK + '\n'
    inc_anchor = '#include <condition_variable>\n'
    if inc_anchor not in s:
        raise SystemExit('include anchor not found')
    s = s.replace(inc_anchor, inc_anchor + inc, 1)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_FIFO_SRWLOCK', '1') == '0':
        print('fifo srwlock disabled')
        return
    port = args.port
    overlay = port / 'runtime-overlays/native_fifo_lock.h'
    target = port / 'build/nwii-full-source/nWiiRuntime/include/runtime/native_fifo_lock.h'
    if overlay.exists():
        value = overlay.read_text()
        if not target.exists() or target.read_text() != value:
            target.write_text(value)
    path = port / 'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('fifo srwlock staged')


if __name__ == '__main__':
    main()
