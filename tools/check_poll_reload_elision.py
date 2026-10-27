"""Oracle for NATIVE_POLL_RELOAD_ELISION_V1: the proof obligation plus a sensitivity fixture.

1. Source audit (the obligation).  The elision is only sound while a *false* return from
   `process_pending_callbacks` leaves every promoted register unchanged.  This reads the staged
   runtime sources and fails if
     * a write to ctx.gpr/ctr/inst_count/xer/fpr/ps1/cr inside native_process_callbacks_slow is
       not followed by longjmp or `return true` before the next return,
     * hle_load_context_from_guest writes promoted state before its early `return false`,
     * any *other* function in the runtime writes promoted state - the allow-list below records
       every writer that is not on the poll path, so a new writer has to be reviewed here.
2. Sensitivity fixture (no game launch).  Compiles the emitted poll site in its stock form and in
   the trimmed form against (a) a callee that returns false and touches nothing and (b) a callee
   that returns false after writing a guest register.  Case (a) must agree on the whole register
   file; case (b) must disagree, which proves the fixture would catch a wrong elision.
"""
from pathlib import Path
from port_paths import builds_dir
import argparse
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MARK = 'NATIVE_POLL_RELOAD_ELISION_V1'

PROMOTED_WRITE = re.compile(
    r'ctx\.(gpr(?:\[[^\]]*\])?|ctr|inst_count|xer|fpr(?:\[[^\]]*\])?|ps1(?:\[[^\]]*\])?|'
    r'cr(?:\[[^\]]*\])?\.(?:lt|gt|eq|so))\s*'
    r'(?:=(?!=)|[-+*/&|^]=|<<=|>>=|\+\+|--)')
PROMOTED_CTX = re.compile(r'ctx\.(gpr|ctr|inst_count|xer|fpr|ps1|cr)\b')

# Files that write promoted guest state anywhere in the staged runtime, with the reason each one is
# not on the poll path.  A new file here fails the audit until it is reviewed.
ALLOWED_WRITER_FILES = {
    'ios.cpp': 'HLE syscall handlers plus the two audited poll functions (checked line by line below)',
    'interpreter.cpp': 'interpreter body, reached from the generated entry switch',
    'memory.cpp': 'HLE heap syscalls',
    'dvd.cpp': 'HLE DI syscalls',
    'pad.cpp': 'HLE WPAD/KPAD syscalls',
    'ax.cpp': 'HLE AX syscall',
    'gc_platform.cpp': 'platform HLE stubs',
    'wii_platform.cpp': 'platform HLE stubs',
    'os_hle_stubs.cpp': 'MMIO write handler (HW_Reg_Write16), reached from guest stores',
}
# Files that are on the poll path and must never write promoted state.
POLL_PATH_FILES = ('gx.cpp', 'event_scheduler.cpp', 'main.cpp', 'hw_ipc.cpp', 'hw_pi.cpp', 'hw_di.cpp',
                   'hw_vi.cpp', 'hw_ai.cpp', 'hw_cp.cpp', 'hw_pe.cpp', 'hw_dsp.cpp', 'hw_all.cpp',
                   'native_hud.h', 'native_idle_wait.h', 'cpu_context.h', 'native_event_mask.h',
                   'native_gx_publish.h', 'native_perf_census.h')
# Functions that are actually reachable from the poll and may write promoted state.
POLL_PATH_FUNCS = ('native_process_callbacks_slow', 'hle_load_context_from_guest')


def span(lines, name):
    for i, line in enumerate(lines):
        if re.search(r'\b' + name + r'\s*\(', line) and line.rstrip().endswith('{'):
            depth = 0
            for j in range(i, len(lines)):
                depth += lines[j].count('{') - lines[j].count('}')
                if depth == 0 and j > i:
                    return i, j
    return None


def audit(root):
    rt = root / 'build/nwii-full-source/nWiiRuntime'
    files = sorted((rt / 'src').rglob('*.cpp')) + sorted((rt / 'src').rglob('*.h')) + sorted((rt / 'include').rglob('*.h'))
    writers = {}
    problems = []
    for path in files:
        lines = path.read_text(errors='surrogateescape').split('\n')
        hits = [(i + 1, line.strip()[:70]) for i, line in enumerate(lines) if PROMOTED_WRITE.search(line)]
        if hits:
            writers[path.name] = hits
    unknown = sorted(name for name in writers if name not in ALLOWED_WRITER_FILES)
    if unknown:
        problems.append('promoted-state writers outside the reviewed file allow-list: %s' % unknown)
    for name in POLL_PATH_FILES:
        if name in writers:
            problems.append('poll-path collaborator %s writes promoted state (%d sites)' % (name, len(writers[name])))
    # The two functions on the poll path that do write: every write must escape or return true.
    ios = rt / 'src/hle/ios.cpp'
    lines = ios.read_text(errors='surrogateescape').split('\n')
    for name in POLL_PATH_FUNCS:
        s = span(lines, name)
        if not s:
            problems.append('%s not found in ios.cpp' % name)
            continue
        a, b = s
        for i in range(a, b + 1):
            if not PROMOTED_WRITE.search(lines[i]):
                continue
            if name == 'hle_load_context_from_guest':
                early = next((j for j in range(a, b + 1) if re.search(r'return\s+false', lines[j])), None)
                # A write *before* the early `return false` also executes on the false-return path,
                # and the elision removes the caller's reloads precisely on a false return -- so
                # such a write makes the elision unsound.  (This case used to be skipped.)
                if early is not None and i < early:
                    problems.append('%s:%d write is reachable when the function returns false '
                                    '(it precedes the early return false)' % (name, i + 1))
                    continue
                tail = next((j for j in range(i, b + 1) if re.search(r'\breturn\b', lines[j])), None)
                if tail is None or not re.search(r'return\s+true', lines[tail]):
                    problems.append('%s:%d write is not followed by return true' % (name, i + 1))
                continue
            tail = ''
            for j in range(i, b + 1):
                if 'longjmp' in lines[j]:
                    tail = 'longjmp'
                    break
                if re.search(r'\breturn\b', lines[j]):
                    tail = 'return true' if re.search(r'return\s+true', lines[j]) else 'return false'
                    break
            if tail not in ('longjmp', 'return true'):
                problems.append('%s:%d %r is followed by %s' % (name, i + 1, lines[i].strip()[:60], tail or 'nothing'))
    return dict(ok=not problems, problems=problems,
                writer_files={k: len(v) for k, v in sorted(writers.items())},
                allowed_files=sorted(ALLOWED_WRITER_FILES))


FIXTURE = r'''
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <array>
struct CPUContext { uint32_t gpr[32]{}; uint32_t ctr=0; uint64_t inst_count=0; uint32_t xer=0; double fpr[32]{}; double ps1[32]{}; bool cr[8][4]{}; };
static bool g_write_on_false = false;
// Callee: returns false.  Case (a) touches nothing, case (b) writes a guest register.
bool process_pending_callbacks(CPUContext& ctx) {
  if (g_write_on_false) { ctx.gpr[5] = 0xDEADBEEFu; }
  return false;
}
namespace nwii { namespace runtime {
extern bool poll_due;
inline bool native_poll_due() { return poll_due; }
} }
// The emitted site, stock: spill the dirty set, call, reload the live set.
void stock(CPUContext& ctx) {
  uint32_t g5 = ctx.gpr[5], g7 = ctx.gpr[7];
  double f3 = ctx.fpr[3];
  g5 += 1; g7 += 2; f3 = f3 + 1.0;
  if (nwii::runtime::native_poll_due()) { ctx.gpr[5] = g5; ctx.gpr[7] = g7; ctx.fpr[3] = f3; if (process_pending_callbacks(ctx)) return; g5 = ctx.gpr[5]; g7 = ctx.gpr[7]; f3 = ctx.fpr[3]; }
  ctx.gpr[5] = g5; ctx.gpr[7] = g7; ctx.fpr[3] = f3;
}
// The same site with the reload removed (NATIVE_POLL_RELOAD_ELISION_V1).
void trimmed(CPUContext& ctx) {
  uint32_t g5 = ctx.gpr[5], g7 = ctx.gpr[7];
  double f3 = ctx.fpr[3];
  g5 += 1; g7 += 2; f3 = f3 + 1.0;
  if (nwii::runtime::native_poll_due()) { ctx.gpr[5] = g5; ctx.gpr[7] = g7; ctx.fpr[3] = f3; if (process_pending_callbacks(ctx)) return; }
  ctx.gpr[5] = g5; ctx.gpr[7] = g7; ctx.fpr[3] = f3;
}
namespace nwii { namespace runtime { bool poll_due = true; } }
static bool same(const CPUContext& a, const CPUContext& b) {
  if (std::memcmp(a.gpr, b.gpr, sizeof(a.gpr))) return false;
  if (std::memcmp(a.fpr, b.fpr, sizeof(a.fpr))) return false;
  if (std::memcmp(a.ps1, b.ps1, sizeof(a.ps1))) return false;
  return a.ctr == b.ctr && a.inst_count == b.inst_count && a.xer == b.xer;
}
int main() {
  CPUContext a, b;
  for (int i = 0; i < 32; ++i) { a.gpr[i] = 0x1000u + i; b.gpr[i] = a.gpr[i]; a.fpr[i] = i + 0.5; b.fpr[i] = a.fpr[i]; }
  a.ctr = 7; b.ctr = 7; a.inst_count = 11; b.inst_count = 11; a.xer = 3; b.xer = 3;
  g_write_on_false = false;
  stock(a); trimmed(b);
  const bool quiet_agree = same(a, b);
  CPUContext c, d;
  for (int i = 0; i < 32; ++i) { c.gpr[i] = 0x2000u + i; d.gpr[i] = c.gpr[i]; c.fpr[i] = i + 0.25; d.fpr[i] = c.fpr[i]; }
  g_write_on_false = true;
  stock(c); trimmed(d);
  const bool loud_disagree = !same(c, d);
  printf("FIXTURE quiet_agree=%d loud_disagree=%d\n", quiet_agree ? 1 : 0, loud_disagree ? 1 : 0);
  return (quiet_agree && loud_disagree) ? 0 : 1;
}
'''


def fixture(root):
    from fix_poll_reload_elision import trim
    # Exercise the actual transformer on the emitted diagnostic form. The print is
    # retained, the reload goes away, and a diagnostic that writes state is rejected.
    site = '  if (nwii::runtime::native_poll_due()) { ctx.gpr[5] = g5; if (process_pending_callbacks(ctx)) return; g5 = ctx.gpr[5]; }'
    diagnostic = ' if ((ctx.inst_count % 10000000) == 0) { std::cout << "Spinning at PC: 0x" << std::hex << 0x803147A4 << " LR: 0x" << std::hex << ctx.lr << std::dec << std::endl; }'
    emitted = site.replace('return;', 'return;' + diagnostic)
    transformed, sites, loads = trim(emitted)
    assert sites == loads == 1 and diagnostic in transformed and 'g5 = ctx.gpr[5]' not in transformed
    assert trim(transformed) == (transformed, 0, 0)
    unsafe = emitted.replace('std::cout <<', 'ctx.gpr[5] = 0; std::cout <<')
    assert trim(unsafe) == (unsafe, 0, 0)
    assert trim(site)[1:] == (1, 1)
    work = builds_dir()/'poll-reload-elision-tests'
    work.mkdir(parents=True, exist_ok=True)
    src = work / 'poll_reload_elision.cpp'
    compiled = FIXTURE.replace('#include <array>', '#include <array>\n#include <iostream>')
    compiled = compiled.replace('uint32_t ctr=0;', 'uint32_t lr=0; uint32_t ctr=0;')
    compiled = compiled.replace('if (process_pending_callbacks(ctx)) return;', 'if (process_pending_callbacks(ctx)) return;' + diagnostic)
    begin = compiled.index('void stock(')
    end = compiled.index('// The same site', begin)
    production, sites, loads = trim(compiled[begin:end].replace('void stock(', 'void trimmed('))
    assert sites == 1 and loads == 3
    trimmed_begin = compiled.index('void trimmed(', end)
    trimmed_end = compiled.index('namespace nwii { namespace runtime { bool poll_due', trimmed_begin)
    compiled = compiled[:trimmed_begin] + production + compiled[trimmed_end:]
    src.write_text(compiled, encoding='utf-8')
    exe = work / 'poll_reload_elision.exe'
    build = subprocess.run(['g++', '-std=c++20', '-O2', '-g0', '-fwrapv', '-fno-strict-aliasing',
                            str(src), '-o', str(exe)], capture_output=True, text=True)
    if build.returncode:
        return dict(ok=False, problems=['fixture did not compile: ' + build.stderr[-400:]], output='')
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    return dict(ok=run.returncode == 0, output=run.stdout.strip(), problems=[] if run.returncode == 0 else [run.stdout.strip()])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--report', type=Path, default=None)
    args = ap.parse_args()
    root = args.root.resolve()
    source = audit(root)
    fix = fixture(root)
    ok = source['ok'] and fix['ok']
    report = dict(schema=1, check='poll_reload_elision', marker=MARK, ok=ok,
                  source_audit=source, fixture=fix, game_launched=False)
    out = args.report or (root / 'reports/poll-reload-elision-tests.json')
    out.write_text(json.dumps(report, indent=2) + '\n')
    print('poll reload elision: source audit %s, fixture %s' % ('ok' if source['ok'] else 'FAILED',
                                                               fix.get('output', 'FAILED')))
    for problem in source['problems'] + fix['problems']:
        print('  ' + problem)
    print('Report: %s' % out)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
