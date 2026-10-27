"""Offline differential for NATIVE_MEM_PROOF_V1 (tools/guest_mem_proofs.py). No game, no staging.

Variants of the same real generated functions:
  old   the generated function as hot-split receives it (unpromoted, all entries)
  base  production guest_entry_specialize output (promoted bodies, current continuation rule)
  new   base with region-hoisted address proofs applied to every definition
The dispatcher-loop harness, stubs and comparisons are check_guest_poll_continuations.py's: re-entry after
interrupted polls and callees, five MXCSR modes, write-watched MEM1/MEM2, FIFO/MMIO/watch traces, and a
live watch inside the stack frame on every fifth trial (the proof must fail there and keep watch_hit order).
`base` vs `new` is the claim under test and must match exactly; `old` vs promoted differences are
reported separately (pre-existing classes are listed in that check's notes).
"""
from pathlib import Path
import argparse, json, os, re, shutil, subprocess
import check_guest_poll_continuations as H
import guest_mem_proofs as M
from fix_guest_batch import scope_end
from port_paths import winlibs_bin

PORT = Path(__file__).resolve().parents[1]


def with_proofs(text):
    counter = [0]; edits = []; total = dict(groups=0, accesses=0)
    for head in M.HEAD.finditer(text):
        end = scope_end(text, head.end() - 1)
        new, st = M.transform_function(text[head.start():end], counter)
        if new != text[head.start():end]:
            edits.append((head.start(), end, new))
            for k in total: total[k] += st.get(k, 0)
    for a, b, new in reversed(edits):
        text = text[:a] + new + text[b:]
    return text, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--generated', type=Path, default=H.BUILD / 'native-port/build/crash-generated')
    ap.add_argument('--include', type=Path, default=H.BUILD / 'storage/nwii-full-source/nWiiRuntime/include')
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--functions', default='803DB050,801B8B10,801B8E48,802F71E0')
    ap.add_argument('--trials', type=int, default=24)
    a = ap.parse_args()
    excluded = set(json.loads((PORT / 'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
    spec = H.hooked_specializer()
    a.work.mkdir(parents=True, exist_ok=True)
    inc = a.work / 'inc/runtime'; inc.mkdir(parents=True, exist_ok=True)
    for name in ('native_mem_proof.h', 'native_fastmem.h'):
        shutil.copy2(PORT / 'runtime-overlays' / name, inc / name)
    funcs, report = {}, {}
    for address in a.functions.split(','):
        assert address not in excluded, 'effect-excluded address: ' + address
        source = H.generated_function(a.generated, address)
        os.environ['NWII_BUILD_POLL_CONTINUATIONS'] = '0'
        base, stats = spec.specialize(source, None, continuation_allowlist={H.P.parse(source)[0]})
        os.environ.pop('NWII_BUILD_POLL_CONTINUATIONS', None)
        assert base is not None and stats.get('entry_specialized'), (address, stats)
        new, proof = with_proofs(base)
        funcs[address] = {'old': source, 'base': base, 'new': new, 'pcs': [int(x, 16) for x, _ in H.P.parse(source)[3]]}
        report[address] = proof
    gxx = str(winlibs_bin() / 'g++.exe') if winlibs_bin() else shutil.which('g++')
    common = ['-std=c++20', '-march=x86-64-v3', '-mfma', '-fwrapv', '-fno-strict-aliasing', '-ffp-contract=off',
              '-DNWII_STATIC_RAM_BASE', '-DNWII_WATCH_PAGE_MARKING', '-DNWII_BUILT_WITH_FMA=1',
              '-I', str(a.work / 'inc'), '-I', str(a.include), '-I', str(a.generated), '-w']
    prelude = H.PRELUDE.replace('#include "runtime/native_linked_region.h"',
                                '#include "runtime/native_linked_region.h"\n#include "runtime/native_mem_proof.h"')
    src = prelude + H.build_source(funcs)[len(H.PRELUDE):] + H.HARNESS + H.driver(funcs, a.trials)
    cpp = a.work / 'mem_proofs.cpp'; cpp.write_text(src, errors='surrogateescape')
    exe = a.work / 'mem_proofs.exe'
    r = subprocess.run([gxx, *common, os.environ.get('NWII_CHECK_OPT', '-Os'), '-static', str(cpp), '-o', str(exe)], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-6000:]); raise SystemExit('compile failed')
    env = dict(os.environ, FX_KEEP_GOING='1')
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=3600, env=env)
    lines = r.stdout.splitlines()
    # base (column 2 of each state/ram/trace triple) vs new is the claim under test.
    def base_differs(l):
        if not all(re.search(k + r'=\d/1', l) for k in ('state', 'ram', 'trace')):
            return True
        # polls=old/base/new, entries=old/base/new: base and new must agree.
        return any(m.group(3) != m.group(4) for m in re.finditer(r'(polls|entries)=(\d+)/(\d+)/(\d+)', l))
    base_new = sum(1 for l in lines if l.startswith('MISMATCH ') and base_differs(l))
    old_only = sum(1 for l in lines if l.startswith('MISMATCH ')) - base_new
    report['summary'] = lines[-1] if lines else r.stderr[-400:]
    report['base_vs_new_mismatches'] = base_new
    report['old_vs_promoted_only'] = old_only
    print(json.dumps(report, indent=1))
    (a.work / 'report.json').write_text(json.dumps(report, indent=1))
    raise SystemExit(0 if base_new == 0 else 1)


if __name__ == '__main__':
    main()
