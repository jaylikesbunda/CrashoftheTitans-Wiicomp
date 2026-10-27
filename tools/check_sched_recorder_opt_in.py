"""Check NATIVE_SCHED_RECORDER_OPT_IN_V2: the two copies of the default-watch policy agree.

The staged g_watch_addr initialiser (from tools/fix_sched_recorder.py) and native_launch_flags::watch
(runtime-overlays/native_launch_flags.h) must produce the same address for every environment, or the MMU's
`g_watch_addr!=native_launch_flags::watch` tests would see a runtime watch change that never happened.
Compiles both into one probe and runs it under each environment combination. No staging, no game.
"""
from pathlib import Path
import ast, os, subprocess, sys, tempfile
from port_paths import winlibs_bin

PORT = Path(__file__).resolve().parents[1]


def stage_initialiser():
    tree = ast.parse((PORT / 'tools/fix_sched_recorder.py').read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == 'v2':
            return ast.literal_eval(node.value)
    raise SystemExit('v2 initialiser not found in fix_sched_recorder.py')


PROBE = r'''
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include "native_launch_flags.h"
static uint32_t stage_watch = []() -> uint32_t {
  const char *env = std::getenv("NWII_WATCH");
%s
int main(){std::printf("%%08x %%08x\n",stage_watch,native_launch_flags::watch);}
'''

CASES = [  # environment, expected address
    ({}, 0),
    ({'NWII_RUNTIME_DIAG': '1'}, 0x80597790),
    ({'NWII_RUNTIME_DIAG': '1', 'NWII_NO_DIAG': '1'}, 0),
    ({'NWII_SCHED_RECORDER': '1'}, 0x80597790),
    ({'NWII_SCHED_RECORDER': '0', 'NWII_RUNTIME_DIAG': '1'}, 0),
    ({'NWII_WATCH': '0c008000'}, 0x0c008000),
    ({'NWII_WATCH': '0', 'NWII_SCHED_RECORDER': '1'}, 0),
]


def main():
    body = stage_initialiser()
    gxx = str(winlibs_bin() / 'g++.exe') if winlibs_bin() else 'g++'
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        (work / 'probe.cpp').write_text(PROBE % body)
        exe = work / 'probe.exe'
        subprocess.run([gxx, '-std=c++20', '-O2', '-I', str(PORT / 'runtime-overlays'), str(work / 'probe.cpp'),
                        '-o', str(exe)], check=True)
        failures = 0
        for extra, expected in CASES:
            env = {k: v for k, v in os.environ.items() if k not in ('NWII_WATCH', 'NWII_RUNTIME_DIAG', 'NWII_NO_DIAG', 'NWII_SCHED_RECORDER')}
            env.update(extra)
            stage, flags = (int(x, 16) for x in subprocess.run([str(exe)], env=env, capture_output=True, text=True, check=True).stdout.split())
            ok = stage == flags == expected
            failures += not ok
            print('%-62s stage=%08x flags=%08x expected=%08x %s' % (extra or 'default', stage, flags, expected, 'ok' if ok else 'FAIL'))
    if failures:
        raise SystemExit('%d case(s) failed' % failures)
    print('Scheduler recorder opt-in: both policy copies agree in %d environments.' % len(CASES))


if __name__ == '__main__':
    main()
