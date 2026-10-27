"""Preinitialize launch-fixed poll flags without changing any poll observation point.

Flags are fixed by the launcher before process startup. Attention clearing, wake
checks, callback order, delay decrements and WPAD checks remain untouched.
Build rollback: NWII_BUILD_POLL_GUARD_OUTLINE=0 (restores canonical local statics).
"""
from pathlib import Path
import argparse
import json
import os
import re

MARKER = 'NATIVE_POLL_GUARD_OUTLINE_V1'
PRELUDE = '''
// NATIVE_POLL_GUARD_OUTLINE_V1 variant: poll-path launch flags pre-initialized at namespace scope
// (guard loads removed). Values identical to the staged guarded statics for a
// fixed environment; fixture driver verifies identical observation traces.
static const int32_t native_poll_launch_poll_reload = [](){const char* e=std::getenv("NWII_POLL_CREDIT");int v=e?std::atoi(e):64;
  if(std::getenv("NWII_NO_POLL_THROTTLE"))v=1;return int32_t(v<1?1:v>1024?1024:v);}();
static const bool native_poll_launch_idle_drain = std::getenv("NWII_NO_GX_IDLE_DRAIN")==nullptr;
static const bool native_poll_launch_no_throttle = std::getenv("NWII_NO_POLL_THROTTLE")!=nullptr;
static const bool native_poll_launch_poll_diagnostics = NativeLaunchFlag_NWII_SAMPLE()!=nullptr ||
    std::getenv("NWII_STARTUP_PROFILE")!=nullptr || g_fp_context_probe_enabled ||
    std::getenv("NWII_LOOPTRACE")!=nullptr || std::getenv("NWII_PEEK")!=nullptr ||
    std::getenv("NWII_RESCHED")!=nullptr;
static const bool native_poll_launch_routine_diag = std::getenv("NWII_RUNTIME_DIAG") && !std::getenv("NWII_NO_DIAG");
static const bool native_poll_launch_callback_sample = NativeLaunchFlag_NWII_SAMPLE() != nullptr;
static const bool native_poll_launch_diagnostic_prelude = native_poll_launch_routine_diag || native_poll_launch_callback_sample ||
    std::getenv("NWII_STARTUP_PROFILE") != nullptr ||
    std::getenv("NWII_LOOPTRACE") != nullptr;
// END NATIVE_POLL_GUARD_OUTLINE_V1 variant
'''

def _transform(text: str):
    changes = []
    substitutions = []

    def sub1(pattern, replacement, tag, count=1):
        nonlocal text
        matches = list(re.finditer(pattern, text))
        new, n = re.subn(pattern, replacement, text, count=count)
        if n != count:
            raise SystemExit(f"pattern for {tag} matched {n} times (expected {count})")
        substitutions.extend((m.group(), replacement) for m in matches[:count])
        text = new
        changes.append(tag)

    # 1. reload lambda inside process_pending_callbacks
    sub1(r"\{ // NATIVE_POLL_CREDIT_V1: IPC/DI delays count polls, so they keep every-site polling\.\n"
         r"    static const int32_t reload=\[\]\(\)\{const char\* e=std::getenv\(\"NWII_POLL_CREDIT\"\);int v=e\?std::atoi\(e\):64;\n"
         r"      if\(std::getenv\(\"NWII_NO_POLL_THROTTLE\"\)\)v=1;return int32_t\(v<1\?1:v>1024\?1024:v\);\}\(\);\n"
         r"    g_native_poll_credit=\(g_ipc_interrupt_delay!=0 \|\| hw::g_di_interrupt_delay!=0\)\?1:reload;",
         "{ // NATIVE_POLL_CREDIT_V1: IPC/DI delays count polls, so they keep every-site polling.\n"
         "    const int32_t reload=native_poll_launch_poll_reload; // NATIVE_POLL_GUARD_OUTLINE_V1: pre-initialized\n"
         "    g_native_poll_credit=(g_ipc_interrupt_delay!=0 || hw::g_di_interrupt_delay!=0)?1:reload;",
         "reload")

    # 2. idle_drain
    sub1(r"\{ static const bool idle_drain=std::getenv\(\"NWII_NO_GX_IDLE_DRAIN\"\)==nullptr;\n"
         r"    if\(idle_drain && ctx\.pc==0x803BBFA0u",
         "{ const bool idle_drain=native_poll_launch_idle_drain; // NATIVE_POLL_GUARD_OUTLINE_V1: pre-initialized\n"
         "    if(idle_drain && ctx.pc==0x803BBFA0u",
         "idle_drain")

    # 3. no_throttle (uses the same line as the phase increment)
    sub1(r"  static const bool no_throttle=std::getenv\(\"NWII_NO_POLL_THROTTLE\"\)!=nullptr;",
         "  const bool no_throttle=native_poll_launch_no_throttle; // NATIVE_POLL_GUARD_OUTLINE_V1: pre-initialized",
         "no_throttle")

    # 4. diagnostics disjunction in native_process_callbacks_poll
    sub1(r"  static const bool diagnostics=NativeLaunchFlag_NWII_SAMPLE\(\)!=nullptr \|\|\n"
         r"      std::getenv\(\"NWII_STARTUP_PROFILE\"\)!=nullptr \|\| g_fp_context_probe_enabled \|\|\n"
         r"      std::getenv\(\"NWII_LOOPTRACE\"\)!=nullptr \|\| std::getenv\(\"NWII_PEEK\"\)!=nullptr \|\|\n"
         r"      std::getenv\(\"NWII_RESCHED\"\)!=nullptr;",
         "  const bool diagnostics=native_poll_launch_poll_diagnostics; // NATIVE_POLL_GUARD_OUTLINE_V1: pre-initialized",
         "diagnostics")

    # 5. routine_diag in native_process_callbacks_slow
    sub1(r"  static const bool routine_diag=std::getenv\(\"NWII_RUNTIME_DIAG\"\) && !std::getenv\(\"NWII_NO_DIAG\"\); // NATIVE_ROUTINE_DIAG_FLAG_V1",
         "  const bool routine_diag=native_poll_launch_routine_diag; // NATIVE_POLL_GUARD_OUTLINE_V1: pre-initialized",
         "routine_diag")

    # 6. callback_sample in native_process_callbacks_slow
    sub1(r"  static const bool callback_sample = NativeLaunchFlag_NWII_SAMPLE\(\) != nullptr;",
         "  const bool callback_sample = native_poll_launch_callback_sample; // NATIVE_POLL_GUARD_OUTLINE_V1: pre-initialized",
         "callback_sample")

    # 7. diagnostic_prelude in native_process_callbacks_slow
    sub1(r"  static const bool diagnostic_prelude = routine_diag \|\| callback_sample \|\|\n"
         r"      std::getenv\(\"NWII_STARTUP_PROFILE\"\) != nullptr \|\|\n"
         r"      std::getenv\(\"NWII_LOOPTRACE\"\) != nullptr;",
         "  const bool diagnostic_prelude = native_poll_launch_diagnostic_prelude; // NATIVE_POLL_GUARD_OUTLINE_V1: pre-initialized",
         "diagnostic_prelude")

    # Insert the prelude once, after the fp-probe enable flag definition.
    anchor = "static const bool g_fp_context_probe_enabled = std::getenv(\"NWII_FP_CONTEXT_PROBE\") != nullptr;\n"
    if anchor not in text:
        raise SystemExit("fp probe anchor not found")
    text = text.replace(anchor, anchor + PRELUDE, 1)
    changes.append("prelude")
    return text, substitutions

def restore(source):
    pattern = r'// NATIVE_POLL_GUARD_OUTLINE_RESTORE ([^\n]+)\n'
    metadata = re.search(pattern, source)
    if not metadata:
        assert MARKER not in source, 'poll flag metadata missing'
        return source
    substitutions = json.loads(metadata.group(1))
    source = re.sub(pattern, '', source, count=1)
    for old, new in substitutions:
        assert source.count(new) == 1, ('poll flag restoration', new)
        source = source.replace(new, old, 1)
    source = source.replace(PRELUDE, '', 1)
    return source

def apply(source):
    if 'NATIVE_POLL_GUARD_OUTLINE_RESTORE' in source:
        # Verify the transformed source is reversible rather than layering edits.
        restored = restore(source)
        regenerated, _ = _transform(restored)
        assert source.replace(re.search(r'// NATIVE_POLL_GUARD_OUTLINE_RESTORE [^\n]+\n', source).group(), '', 1) == regenerated
        return source
    transformed, substitutions = _transform(source)
    return transformed + '// NATIVE_POLL_GUARD_OUTLINE_RESTORE ' + json.dumps(substitutions, separators=(',', ':')) + '\n'

def stage(port, enabled=True):
    path = port / 'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp'
    before = path.read_text()
    after = apply(before) if enabled else restore(before)
    if before != after:
        path.write_text(after)
    print('Poll launch flags preinitialized.' if enabled else 'Poll launch flags restored.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    stage(Path(__file__).resolve().parents[1], not args.restore and os.getenv('NWII_BUILD_POLL_GUARD_OUTLINE', '1') != '0')
