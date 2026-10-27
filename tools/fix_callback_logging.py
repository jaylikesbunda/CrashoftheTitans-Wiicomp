"""Keep routine callback/IRQ output opt-in; retain errors and explicit samples."""
from pathlib import Path
import argparse

MARKER = 'NATIVE_CALLBACK_LOG_OPTIN_V1'
DISPATCH = '''  std::cout << "[HLE Callback] Dispatching cb=0x" << std::hex << cb.cb_addr
            << " arg1=0x" << cb.arg1 << " arg2=0x" << cb.arg2 << " from PC=0x"
            << ctx.pc << " LR=0x" << ctx.lr << " r1=0x" << ctx.gpr[1]
            << std::dec << std::endl;'''
QUIET_DISPATCH = ('  // '+MARKER+': formatting and flushing only when requested.\n'
                  '  if (routine_diag)\n'+
                  '\n'.join('  '+line for line in DISPATCH.splitlines()))

def apply(source):
    if MARKER in source:
        assert source.count(QUIET_DISPATCH) == 1
        assert 'if ((routine_diag || callback_sample) && (dcount++ % 500) == 0)' in source
        assert 'if ((routine_diag || callback_sample) && os_intr != 24 && (vi_dispatch_count++ % 256) == 0)' in source
        return source
    start = source.index('static bool native_process_callbacks_slow(CPUContext &ctx, bool poll_clock_due, bool wpad_polled) {')
    assert 'static const bool routine_diag=' in source[start:start+300]
    body = source[start:]
    assert body.count(DISPATCH) == 1
    body = body.replace(DISPATCH, QUIET_DISPATCH, 1)
    # These counters and table walks exist solely to print diagnostic state.
    # Preserve explicit NWII_SAMPLE output as well as NWII_RUNTIME_DIAG output.
    for old, new in [
        ('if ((dcount++ % 500) == 0)',
         'if ((routine_diag || callback_sample) && (dcount++ % 500) == 0)'),
        ('if (os_intr != 24 && (vi_dispatch_count++ % 256) == 0)',
         'if ((routine_diag || callback_sample) && os_intr != 24 && (vi_dispatch_count++ % 256) == 0)'),
    ]:
        assert body.count(old) == 1
        body = body.replace(old, new, 1)
    result = source[:start]+body
    # Fatal missing-context output, NULL handler warnings, idle diagnostics and
    # startup configuration announcements deliberately retain their guards.
    assert result.count('[HLE PI] FATAL: current_ctx is 0!') == source.count('[HLE PI] FATAL: current_ctx is 0!')
    assert result.count('null_warn_count++ < 3') == source.count('null_warn_count++ < 3')
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ios', type=Path, default=Path(__file__).resolve().parents[1]/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp')
    args = parser.parse_args()
    source = args.ios.read_text()
    result = apply(source)
    assert apply(result) == result
    if result != source:
        args.ios.write_text(result)
    print('Routine callback and scheduler logging made opt-in; errors retained.')
