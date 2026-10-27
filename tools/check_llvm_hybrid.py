"""Differential oracle for the LLVM-IR hybrid backend (no game launch).

For every function in reference/backend-reports/llvm-ir-functions.json this
links a small executable that contains BOTH implementations:

  * the generated C++ (the oracle, compiled by g++ into namespace ref_o2), and
  * the clang-compiled IR object, linked under the *exact C-linkage symbol name*
    the dispatcher and every caller use (functions.h declares every guest
    function `extern "C"`, so the object replaces the C++ definition directly).

The harness (tools/llvm_slice/harness.cpp, -DNWII_IR_C_NAME=func_XXXXXXXX)
runs randomised CPUContext state through both and compares the full register
file, inst_count, exception_pc, guest RAM windows and the GX write-gather pipe.
It also proves the two objects collide (duplicate symbol) when the C++
definition is left in place, which is exactly why fix_llvm_ir_backend.py has to
strip it.

usage: python check_llvm_hybrid.py [--address 8023B080] [--trials 2000] [--work DIR]
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = HERE.parent
sys.path.insert(0, str(HERE))
import fix_llvm_ir_backend as F  # noqa: E402

TEMPL = HERE / 'llvm_slice'
COMMON = ['-std=c++20', '-march=x86-64-v3', '-mfma', '-ffp-contract=off', '-fwrapv',
          '-fno-strict-aliasing', '-DNWII_WATCH_PAGE_MARKING']


def run(cmd, cwd, expect_ok=True):
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd), capture_output=True, text=True)
    if expect_ok and p.returncode:
        print('FAILED:', ' '.join(map(str, cmd)))
        print((p.stdout + p.stderr)[-3000:])
        raise SystemExit(p.returncode)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--address')
    ap.add_argument('--trials', type=int, default=2000)
    ap.add_argument('--work', type=Path,
                    default=Path(os.environ.get('TEMP', '.')) / 'llvm-hybrid-check')
    a = ap.parse_args()

    clang = F.find_clang()
    cxx = F.find_cxx()
    if clang is None or cxx is None:
        raise SystemExit('need clang (NWII_CLANG) and g++ (NWII_CXX)')
    addrs = [a.address.upper().lstrip('0X').rjust(8, '0')] if a.address else F.load_functions()

    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    (work / 'harness.cpp').write_text((TEMPL / 'harness.cpp').read_text(encoding='utf-8'), encoding='utf-8')
    off = F.context_offsets(cxx)
    (work / 'offsets.json').write_text(json.dumps(off), encoding='ascii')

    inc = F.INC
    common = COMMON + ['-I', str(inc)]
    results = []
    for addr in addrs:
        func = 'func_' + addr
        found = F.find_definition(func)
        if found is None:
            raise SystemExit('%s not found in the staged corpus' % func)
        original = found[2] if found[2] is not None else found[1].read_text(errors='surrogateescape')
        span = F.function_span(original, func)
        (work / (func + '.ref.cpp')).write_text(
            '// extracted from %s\n%s' % (found[1].name, original[span[0]:span[1]]), encoding='utf-8')
        (work / 'v_ref.cpp').write_text(
            '#include "runtime/cpu_context.h"\nnamespace REFNS {\n#include "%s.ref.cpp"\n}\n' % func,
            encoding='utf-8')

        # 1. oracle C++ objects (-O2 and -Os, matching the spike's coverage)
        for ns in ('ref_o2', 'ref_os'):
            run([cxx] + common + ['-O2', '-DREFNS=' + ns, '-c', 'v_ref.cpp',
                                  '-o', '%s.o' % ns], work)
        # 2. IR object under the real linker symbol (C linkage: func_XXXXXXXX)
        run([sys.executable, F.EMIT, '--source', func + '.ref.cpp', '--name', func,
             '--offsets', 'offsets.json', '-o', 'ir.ll'], work)
        run([clang, '-c', '-O2', '-march=x86-64-v3', '-ffp-contract=off', '-fwrapv',
             'ir.ll', '-o', 'ir.o'], work)
        F.normalize_coff_timestamp(work / 'ir.o')
        # 3. hybrid harness: link the C++ oracle *and* the IR replacement
        run([cxx] + common + ['-O2', '-DHAVE_IR', '-DNWII_IR_C_NAME=' + func,
                              '-DNWII_REF_FUNC=' + func, '-DNWII_IR_ENTRY=0x%su' % addr,
                              '-c', 'harness.cpp', '-o', 'harness.o'], work)
        run([cxx] + common + ['-O2', 'harness.o', 'ref_o2.o', 'ref_os.o', 'ir.o',
                              '-o', 'h_hybrid.exe', '-static'], work)
        p = run([work / 'h_hybrid.exe', str(a.trials), 'notime'], work, expect_ok=False)
        line = next((l for l in (p.stdout + p.stderr).splitlines() if l.startswith('EQUIV ')), '')
        ok = p.returncode == 0 and 'mismatches=0' in line
        # 4. negative control: the C++ definition (C linkage, as functions.h
        #    gives it) must collide with the IR object
        (work / 'ref_global.cpp').write_text(
            '#include "runtime/cpu_context.h"\n'
            'extern "C" void %s(::nwii::runtime::CPUContext&);\n'
            '#include "%s.ref.cpp"\n' % (func, func), encoding='utf-8')
        run([cxx] + common + ['-O2', '-c', 'ref_global.cpp', '-o', 'ref_global.o'], work)
        dup = run([cxx] + common + ['-O2', 'harness.o', 'ref_global.o', 'ir.o', '-o', 'dup.exe', '-static'],
                  work, expect_ok=False)
        dup_ok = dup.returncode != 0 and 'multiple definition' in (dup.stdout + dup.stderr)
        results.append(dict(address=addr, equiv=ok, detail=line,
                            duplicate_control=dup_ok, exit_code=p.returncode))
        print('%s func_%s  %s  (duplicate control: %s)'
              % ('PASS' if ok else 'FAIL', addr, line or ('exit %d' % p.returncode),
                 'ok' if dup_ok else 'MISSING'))

    (PORT / 'reports').mkdir(parents=True, exist_ok=True)
    (PORT / 'reports/llvm-hybrid-check.json').write_text(json.dumps(results, indent=2) + '\n')
    sys.exit(0 if all(r['equiv'] for r in results) else 1)


if __name__ == '__main__':
    main()
