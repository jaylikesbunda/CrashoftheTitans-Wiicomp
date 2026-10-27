"""Outline the opt-in callback diagnostic prelude; keep runtime polls intact."""
from pathlib import Path
import argparse
import re

MARKER = 'NATIVE_CALLBACK_DIAGNOSTICS_OUTLINE_V1'
SIGNATURE = 'static bool native_process_callbacks_slow(CPUContext &ctx, bool poll_clock_due, bool wpad_polled) {'
START = '  { // NATIVE_DIAG_TIMELINE_V1'
END = '  if (ctx.pc == 0xFFFFFFFC && !ctx.in_callback) {'
CALL = '''  // NATIVE_CALLBACK_DIAGNOSTICS_OUTLINE_V1: launch options remain cached.
  static const bool diagnostic_prelude = routine_diag || callback_sample ||
      std::getenv("NWII_STARTUP_PROFILE") != nullptr ||
      std::getenv("NWII_LOOPTRACE") != nullptr;
  if (__builtin_expect(diagnostic_prelude || g_trace_after_dec > 0, 0))
    native_callback_diagnostics(ctx, poll_clock_due, routine_diag, callback_sample);

'''

def apply(source):
    # Earlier stages insert this process-fixed declaration at different places
    # on a fresh build and an incremental replay. It belongs to the slow caller:
    # the outlined helper already receives the value as a parameter.
    sample = re.compile(
        r'  // NATIVE_CALLBACK_FLAGS_V1\n'
        r'  static const bool callback_sample = '
        r'(?:NativeLaunchFlag_NWII_SAMPLE\(\)|std::getenv\("NWII_SAMPLE"\)) != nullptr;\n')
    declarations = list(sample.finditer(source))
    assert len(declarations) == 1, 'Expected one cached callback sample declaration'
    declaration = declarations[0]
    definition = source.index(SIGNATURE)
    first_use = source.index(CALL if MARKER in source else START, definition)
    if not definition < declaration.start() < first_use:
        text = declaration.group()
        source = source[:declaration.start()] + source[declaration.end():]
        definition = source.index(SIGNATURE) + len(SIGNATURE) + 1
        source = source[:definition] + text + source[definition:]
    if MARKER in source:
        assert source.count(CALL) == 1
        assert source.count('static __attribute__((noinline)) void native_callback_diagnostics(') == 1
        return source
    assert source.count(SIGNATURE) == 1
    definition = source.index(SIGNATURE)
    start = source.index(START, definition)
    end = source.index(END, start)
    prelude = source[start:end]
    assert prelude.count('NATIVE_STARTUP_PROFILE_BEGIN') == 1
    assert prelude.count('NATIVE_STARTUP_PROFILE_END') == 1
    # The extracted block only observes/debugs state. Runtime callback return,
    # clocks, event advance, delays, interrupts and dispatch stay after the call.
    assert 'native_wpad_poll(ctx)' not in prelude
    assert 'EventScheduler::get().advance' not in prelude
    assert 'longjmp(' not in prelude
    helper = ('static __attribute__((noinline)) void native_callback_diagnostics('
              'CPUContext &ctx, bool poll_clock_due, bool routine_diag, bool callback_sample) {\n'+
              prelude+'}\n\n')
    source = source[:start]+CALL+source[end:]
    source = source[:definition]+helper+source[definition:]
    return source

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ios', type=Path, default=Path(__file__).resolve().parents[1]/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp')
    args = parser.parse_args()
    source = args.ios.read_text()
    changed = apply(source)
    assert apply(changed) == changed
    if changed != source:
        args.ios.write_text(changed)
    print('Cold callback diagnostics outlined; runtime poll observations retained.')
