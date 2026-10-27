"""Remove calls on empty FIFO and 127/128 WPAD polls without skipping events."""
from pathlib import Path
import argparse

FIFO_MARKER = 'NATIVE_EMPTY_PUBLISH_CALL_V1'
WPAD_MARKER = 'NATIVE_WPAD_PHASE_INLINE_V1'

def patch_gx(source):
    if FIFO_MARKER in source:
        return source
    anchor = 'static NativeCpuFifo g_cpu_fifo;'
    assert source.count(anchor) == 1, 'Require existing fixed guest-owner FIFO'
    # A pointer to existing state, rather than a second flag: every current
    # producer, partial publish, clear and automatic flush updates the same n.
    addition = ('\n// '+FIFO_MARKER+': existing guest-owner count, no duplicate state.\n'
                'extern const size_t* const g_native_gx_staged_size = &g_cpu_fifo.n;')
    return source.replace(anchor, anchor+addition, 1)

def patch_ios(source):
    if FIFO_MARKER not in source:
        anchor = 'extern void native_gx_publish(); // NATIVE_GX_PRIVATE_FIFO_V1'
        assert source.count(anchor) == 1
        source = source.replace(anchor, anchor+'\nextern const size_t* const g_native_gx_staged_size; // '+FIFO_MARKER, 1)
        start = source.index('static inline bool native_process_callbacks_poll(CPUContext &ctx) {')
        position = source.index('  native_gx_publish();', start)
        source = source[:position]+source[position:].replace('  native_gx_publish();',
            '  if (*g_native_gx_staged_size) native_gx_publish();', 1)
    if WPAD_MARKER not in source:
        start = source.index('static void native_wpad_poll(CPUContext& ctx) {')
        phase_end = source.index('\n', source.index('NATIVE_WPAD_POLL_THROTTLE_V3', start))
        phase = source[start+len('static void native_wpad_poll(CPUContext& ctx) {'):phase_end]
        assert '++wpad_phase & 127u' in phase
        source = source[:start]+source[start:].replace('static void native_wpad_poll(CPUContext& ctx) {',
            'static __attribute__((noinline)) void native_wpad_poll_due(CPUContext& ctx) {', 1)
        # Remove only the known phase statement; all real sampling stays intact.
        assert source.count(phase) == 1
        source = source.replace(phase, '', 1)
        at = source.index('\nbool native_wpad_dispatch(CPUContext& ctx,uint32_t target)')
        wrapper = ('\n// '+WPAD_MARKER+': same phase and sampling points.\n'
            'static __attribute__((always_inline)) inline void native_wpad_poll(CPUContext& ctx) {'+
            phase+'\n  native_wpad_poll_due(ctx);\n}\n')
        source = source[:at]+wrapper+source[at:]
    return source

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, default=Path(__file__).resolve().parents[1]/'build/nwii-full-source/nWiiRuntime')
    args = parser.parse_args()
    for name, transform in [('gx.cpp', patch_gx), ('ios.cpp', patch_ios)]:
        path = args.runtime/'src/hle'/name
        source = path.read_text()
        changed = transform(source)
        assert transform(changed) == changed, 'Patch must be idempotent'
        if changed != source:
            path.write_text(changed)
    print('Empty publication and WPAD phase calls optimized; observations unchanged.')

if __name__ == '__main__':
    main()
