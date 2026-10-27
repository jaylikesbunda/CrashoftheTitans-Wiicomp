"""NATIVE_GX_PUBLISH_GATE_V1: the poll path pays only the staging-buffer emptiness test.

native_gx_publish() is 0.60% of the guest thread in capture cpu-running-20261014-112801-308000
and is called once per real poll (~60k/frame) plus once per 4096-byte publish (~6k/frame).
The poll-path call almost always finds g_cpu_fifo empty: it pays an out-of-line call, a
prologue and two vector loads for a `return`. runtime-overlays/native_gx_publish.h moves the
emptiness test into an inline wrapper and keeps the real work (g_fifo_mutex, append, pending
accounting, early-drain wake) out of line behind the same function. Ordering, lock scope,
4096-byte publish boundaries and byte order are untouched: the wrapper either does nothing or
calls exactly the function the poll used to call.

Evaluated and deliberately NOT changed here:
  * native_gx_batching::ShouldRequest's atomic_thread_fence(seq_cst) is the SC half of a
    Dekker pair with native_gx_batching::Park, but the whole header is unreferenced by the
    runtime (no translation unit calls ShouldRequest, NeedArm, Park or Record), so the fence
    costs nothing at run time and weakening it would be an unverifiable change.
  * Inlining the staging buffer's *writers* (wgp_push / native_wgp_bulk) into a header would
    duplicate the 4096-boundary split at every write site; the writers already sit in gx.cpp
    next to native_gx_publish and gcc already inlines the publish call there.

Build rollback: NWII_BUILD_PUBLISH_GATE=0. Idempotent.

SUPERSEDED (kept for the record, stage disabled in build_native.py): the pipeline changed under this
stage. NATIVE_CPU_FIFO_FIXED_V1 (tools/fix_guest_runtime_trim.py, which runs earlier) replaced the
`std::vector<uint8_t> g_cpu_fifo` this stage moves into the header with a fixed NativeCpuFifo, and
NATIVE_EMPTY_PUBLISH_CALL_V1 (tools/fix_poll_empty_publish.py, also earlier) already rewrote the poll
call to `if (*g_native_gx_staged_size) native_gx_publish();` - i.e. the poll path already pays only the
emptiness test and the call is only made when the buffer is non-empty. Re-pointing this stage at the
current text would mean moving NativeCpuFifo into the header for no measurable gain, so the stage is
left disabled; apply() recognises the superseded form and reports it instead of asserting.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_GX_PUBLISH_GATE_V1'

GX_OLD = 'static std::vector<uint8_t> g_cpu_fifo;'
GX_NEW = ('std::vector<uint8_t> g_cpu_fifo; // NATIVE_GX_PUBLISH_GATE_V1: declared in runtime/native_gx_publish.h')

IOS_CALL_OLD = '  native_gx_publish();'
IOS_CALL_NEW = '  nwii::runtime::native_gx_publish_staged(); // NATIVE_GX_PUBLISH_GATE_V1'

INC_ANCHOR = '#include "runtime/hw/hw.h"\n'
INC = '#include "runtime/native_gx_publish.h" // NATIVE_GX_PUBLISH_GATE_V1\n'


def _include(text, tag):
    if INC in text:
        return text
    n = text.count(INC_ANCHOR)
    if n != 1:
        raise SystemExit('%s: %s include anchor matched %d times' % (MARK, tag, n))
    return text.replace(INC_ANCHOR, INC_ANCHOR + INC, 1)


def superseded(rt):
    """True when the fixed staging buffer and the already-inlined poll test are both in place."""
    gx = (rt / 'src/hle/gx.cpp').read_text(errors='surrogateescape')
    ios = (rt / 'src/hle/ios.cpp').read_text(errors='surrogateescape')
    return GX_OLD not in gx and 'g_native_gx_staged_size' in gx + ios


def apply(port):
    rt = port / 'build/nwii-full-source/nWiiRuntime'
    gx = rt / 'src/hle/gx.cpp'
    if MARK not in gx.read_text(errors='surrogateescape') and superseded(rt):
        print('%s superseded: staging buffer is NATIVE_CPU_FIFO_FIXED_V1 and the poll path already '
              'inlines its emptiness test (NATIVE_EMPTY_PUBLISH_CALL_V1); nothing to do' % MARK)
        return
    overlay = port / 'runtime-overlays/native_gx_publish.h'
    dst = rt / 'include/runtime/native_gx_publish.h'
    value = overlay.read_text()
    if not dst.exists() or dst.read_text() != value:
        dst.write_text(value)

    changed = []
    text = gx.read_text(errors='surrogateescape')
    if MARK not in text:
        assert text.count(GX_OLD) == 1, 'g_cpu_fifo definition anchor'
        text = _include(text.replace(GX_OLD, GX_NEW, 1), 'gx.cpp')
        gx.write_text(text, errors='surrogateescape')
        changed.append('gx.cpp')

    ios = rt / 'src/hle/ios.cpp'
    text = ios.read_text(errors='surrogateescape')
    if 'native_gx_publish_staged' not in text:
        assert text.count(IOS_CALL_OLD) == 1, 'poll publish call anchor'
        text = _include(text.replace(IOS_CALL_OLD, IOS_CALL_NEW, 1), 'ios.cpp')
        ios.write_text(text, errors='surrogateescape')
        changed.append('ios.cpp')
    print('%s staged (%s)' % (MARK, ','.join(changed) or 'no change'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_PUBLISH_GATE', '1') == '0':
        print('publish gate disabled')
        return
    apply(args.port)


if __name__ == '__main__':
    main()
