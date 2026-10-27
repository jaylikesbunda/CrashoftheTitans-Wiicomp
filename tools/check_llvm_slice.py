"""LLVM-IR slice spike: prove the C++ guest emitter can be replaced by LLVM IR.

Picks one small integer-only guest leaf from the staged recompiled tree, emits
LLVM IR for it with `native-port/compiler/emit_llvm_ir.py`, and proves exact
equivalence against the real generated C++.

What "exact" means here, per trial, comparing the C++ build and the IR build:
  * the full guest register file  (memcmp of CPUContext[0 .. backup_stack), i.e.
    gpr/fpr/ps1/cr/pc/lr/ctr/xer/hid2/msr/fpscr/srr0/srr1/gqr/sprg)
  * inst_count and exception_pc
  * raw guest RAM bytes (a MEM1 window and a MEM2 window)
  * the GX write-gather-pipe bytes

The same harness also reports static host instruction counts and interleaved
cycles/call.  Three binaries are built:
  h_clang : clang C++ ref + clang IR objects          (libc++, same backend)
  h_gcc   : gcc   C++ ref                             (production toolchain)
  h_mixed : gcc   C++ ref + clang-compiled IR object  (proves the IR object only
            uses the C ABI and links into the existing libstdc++ runtime)

Everything is written to --work (outside the repo); nothing here launches the game.

usage:
    python check_llvm_slice.py [--storage DIR] [--work DIR] [--trials N]
"""
from __future__ import annotations

import argparse
from port_paths import builds_dir
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = HERE.parent
EMITTER = PORT / 'compiler' / 'emit_llvm_ir.py'
TEMPL = HERE / 'llvm_slice'

DEFAULT_STORAGE = builds_dir()/'clean-room-gx-20261013/storage'
DEFAULT_WORK = builds_dir()/'llvm-spike'
LLVM_BIN = Path(os.environ.get(
    'LLVM_MINGW_BIN',
    str(Path.home() / 'AppData/Local/llvm-mingw/bin')))
GCC_BIN = Path(os.environ.get(
    'WINLIBS_BIN',
    str(Path.home() / 'AppData/Local/Microsoft/WinGet/Packages') +
    r'\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin'))

FUNC = '8023B080'
ENTRY = 0x8023B080

FUNC_RE = re.compile(r'^void func_([0-9A-F]{8})\(NWII_CTX_PARAM ctx\) \{', re.M)
COMMON = ['-std=c++20', '-march=x86-64-v3', '-mfma', '-ffp-contract=off',
          '-fwrapv', '-fno-strict-aliasing', '-DNWII_WATCH_PAGE_MARKING']


def run(cmd, cwd=None, quiet=True):
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=str(cwd or HERE))
    if p.returncode:
        print('FAILED:', ' '.join(map(str, cmd)))
        print((p.stdout + p.stderr)[-4000:])
        raise SystemExit(p.returncode)
    return p.stdout


def extract_function(storage: Path, addr: str) -> str:
    gen = storage / 'crash-generated'
    for f in sorted(gen.glob('output_*.cpp')):
        t = f.read_text(errors='replace')
        pos = [(m.start(), m.group(1)) for m in FUNC_RE.finditer(t)]
        d = {a: s for s, a in pos}
        if addr in d:
            s = d[addr]
            e = min([p for p, a in pos if p > s] or [len(t)])
            return '// extracted from %s\n%s' % (f.name, t[s:e])
    raise SystemExit('function func_%s not found in %s' % (addr, gen))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--storage', type=Path, default=DEFAULT_STORAGE)
    ap.add_argument('--work', type=Path, default=DEFAULT_WORK)
    ap.add_argument('--trials', type=int, default=2000)
    a = ap.parse_args()
    storage = a.storage.resolve()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    os.environ['PATH'] = str(LLVM_BIN) + os.pathsep + str(GCC_BIN) + os.pathsep + os.environ['PATH']
    inc = storage / 'nwii-full-source' / 'nWiiRuntime' / 'include'
    if not (LLVM_BIN / 'clang.exe').exists():
        raise SystemExit('clang not found at %s (set LLVM_MINGW_BIN)' % LLVM_BIN)
    if not (GCC_BIN / 'g++.exe').exists():
        raise SystemExit('g++ not found at %s (set WINLIBS_BIN)' % GCC_BIN)

    # 0. materialise the sources in the work dir
    (work / 'func_8023B080.ref.cpp').write_text(extract_function(storage, FUNC), encoding='utf-8')
    for name in ('harness.cpp', 'v_ref.cpp', 'offsets.cpp', 'count_insns.py'):
        (work / name).write_text((TEMPL / name).read_text(encoding='utf-8'), encoding='utf-8')

    # guard-free hot-only C++ (for the allocator comparison)
    src = (work / 'func_8023B080.ref.cpp').read_text(encoding='utf-8')
    guard = '''    NWII_TRACE_CALL(0x8023B080, ctx);
    if (ctx.pc != 0x8023B080) {
        native_perf_census::GuestSwitch(ctx.pc,1); /* NATIVE_PERF_CENSUS_V1 */
        switch (ctx.pc) {
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
'''
    (work / 'func_8023B080.hot.cpp').write_text(src.replace(guard, '    // hot path only\n'), encoding='utf-8')
    (work / 'ref_hot_only.cpp').write_text(
        '#include "runtime/cpu_context.h"\nnamespace REFNS {\n#include "func_8023B080.hot.cpp"\n}\n', encoding='utf-8')

    clang = LLVM_BIN / 'clang.exe'
    clangxx = LLVM_BIN / 'clang++.exe'
    gxx = GCC_BIN / 'g++.exe'
    objdump = LLVM_BIN / 'llvm-objdump.exe'
    common = COMMON + ['-I', str(inc)]
    syms = {'ref_o2': '_ZN6ref_o213func_8023B080ERN4nwii7runtime10CPUContextE',
            'ref_os': '_ZN6ref_os13func_8023B080ERN4nwii7runtime10CPUContextE'}
    sys.path.insert(0, str(work))
    from count_insns import count

    # 1. layout probes
    for tag, cc in (('', clangxx), ('_gcc', gxx)):
        run([cc] + common + ['-Wno-invalid-offsetof', 'offsets.cpp', '-o', 'offsets%s.exe' % tag], work)
        (work / ('offsets%s.json' % tag)).write_text(
            run([work / ('offsets%s.exe' % tag)], work), encoding='ascii')

    # 2. IR modules
    for name, out, extra in (('func_ir_o2', 'ir_o2.ll', []), ('func_ir_os', 'ir_os.ll', []),
                             ('func_8023B080_ir', 'hot.ll', ['--no-guard']),
                             ('func_ir_o2', 'ir_gcc_abi.ll', []), ('func_ir_os', 'ir_gcc_abi_os.ll', [])):
        off = 'offsets_gcc.json' if 'gcc' in out else 'offsets.json'
        run([sys.executable, str(EMITTER), '--source', 'func_8023B080.ref.cpp', '--name', name,
             '--offsets', off, '-o', out] + extra, work)

    # 3. objects
    for tag in ('clang', 'gcc'):
        cc = clangxx if tag == 'clang' else gxx
        for opt, ns in (('-O2', 'ref_o2'), ('-Os', 'ref_os')):
            run([cc] + common + [opt, '-DREFNS=' + ns, '-c', 'v_ref.cpp', '-o', 'ref_%s_%s.o' % (tag, ns)], work)
            run([cc] + common + [opt, '-DREFNS=' + ns, '-c', 'ref_hot_only.cpp', '-o', 'refhot_%s_%s.o' % (tag, ns)], work)
    irflags = ['-march=x86-64-v3', '-ffp-contract=off', '-fwrapv']
    for opt, tag in (('-O2', 'o2'), ('-Os', 'os')):
        run([clang, '-c'] + [opt] + irflags + ['ir_%s.ll' % tag, '-o', 'ir_%s.o' % tag], work)
        run([clang, '-c'] + [opt] + irflags + ['hot.ll', '-o', 'irhot_%s.o' % tag], work)
        run([clang, '-c'] + [opt] + irflags + ['ir_gcc_abi%s.ll' % ('' if tag == 'o2' else '_os'),
             '-o', 'ir_gcc_abi_%s.o' % tag], work)

    # 4. harness binaries
    run([clangxx] + common + ['-O2', '-DHAVE_IR', 'harness.cpp', 'ref_clang_ref_o2.o', 'ref_clang_ref_os.o',
         'ir_o2.o', 'ir_os.o', '-o', 'h_clang.exe', '-static'], work)
    run([gxx] + common + ['-O2', 'harness.cpp', 'ref_gcc_ref_o2.o', 'ref_gcc_ref_os.o',
         '-o', 'h_gcc.exe', '-static'], work)
    run([gxx] + common + ['-O2', '-DHAVE_IR', 'harness.cpp', 'ref_gcc_ref_o2.o', 'ref_gcc_ref_os.o',
         'ir_gcc_abi_o2.o', 'ir_gcc_abi_os.o', '-o', 'h_mixed.exe', '-static'], work)

    # 5. report
    print('\n== static host instruction count (whole guest function) ==')
    rows = []
    for tag in ('clang', 'gcc'):
        for ns in ('ref_o2', 'ref_os'):
            rows.append(('%-9s %s C++' % (tag, '-O2' if ns.endswith('o2') else '-Os'),
                         count(str(work / ('ref_%s_%s.o' % (tag, ns))), syms[ns], str(objdump))))
    rows.append(('clang     -O2 IR ', count(str(work / 'ir_o2.o'), 'func_ir_o2', str(objdump))))
    rows.append(('clang     -Os IR ', count(str(work / 'ir_os.o'), 'func_ir_os', str(objdump))))
    for n, v in rows:
        print('  %-18s %s' % (n, v))

    print('\n== static host instruction count (guard-free hot body) ==')
    rows = []
    for tag in ('clang', 'gcc'):
        for ns in ('ref_o2', 'ref_os'):
            rows.append(('%-9s %s C++ hot' % (tag, '-O2' if ns.endswith('o2') else '-Os'),
                         count(str(work / ('refhot_%s_%s.o' % (tag, ns))), syms[ns], str(objdump))))
    rows.append(('clang     -O2 IR hot', count(str(work / 'irhot_o2.o'), 'func_8023B080_ir', str(objdump))))
    rows.append(('clang     -Os IR hot', count(str(work / 'irhot_os.o'), 'func_8023B080_ir', str(objdump))))
    for n, v in rows:
        print('  %-22s %s' % (n, v))

    for exe in ('h_clang.exe', 'h_gcc.exe', 'h_mixed.exe'):
        print('\n== %s ==' % exe)
        print(run([work / exe, str(a.trials)], work).strip())


if __name__ == '__main__':
    main()
