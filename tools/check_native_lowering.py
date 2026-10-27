"""Check the source-backed native lowerings.

The driver

  * extracts the translated guest function of every adopted candidate from the
    generated chunk and rewrites its memory accesses to the harness recorder,
  * compiles and runs the per-family differential tests,
  * qualifies every candidate: the guest registers the translated function
    leaves modified must either be reproduced by the native path or proven
    unobservable at every entry the native path serves, and
  * reports the result.

The qualification is the part that decides whether a lowering may be adopted:

  * `clobbered` is the set of guest registers the translated function leaves
    modified (a register it saves to its frame and reloads keeps its entry
    value),
  * `unreproduced` is `clobbered` minus the registers the native path writes
    back,
  * a `definition` entry serves every entry, including the indirect ones the
    runtime may take, so `unreproduced` must be empty for it,
  * a `call_sites_only` entry serves only the direct call sites it rewrites,
    so `unreproduced` must be unobservable at every rewritten call site; a
    caller that observes it is listed in `exclude_callers` and keeps the
    translated call.

Usage:
    python tools/check_native_lowering.py [--generated DIR] [--runtime-include DIR]
                                          [--work DIR] [--manifest FILE]
                                          [--only ADDRESS ...]
"""
from pathlib import Path
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

import native_lowering as NL
from native_lowering import PORT, load_manifest
from native_lowering_effects import (block_successors, blocks_of, function_clobbered,
                                     function_read_before_write, statement_calls,
                                     statement_effects, statement_is_opaque_call)

REFERENCE_STORAGE = Path(r'C:\Users\deki\.codex\native-port-builds\clean-room-guest-batch-20261019-2\storage')
DEFINITION = re.compile(r'NWII_GUEST_ABI void func_([0-9A-Fa-f]{8})\(NWII_CTX_PARAM ctx\) \{')
PROVIDED_FUNCTION = re.compile(r'(?m)^(?:NWII_GUEST_ABI\s+)?void func_([0-9A-Fa-f]{8})\([^;{]*\)\s*\{')


# ---------------------------------------------------------------------------
# The recorded memory accesses the harness has to see
# ---------------------------------------------------------------------------
SCALAR_REWRITES = [
    (re.compile(r'ctx\.mmu\.write8\('), 'nl::rec_write8(ctx.mmu, '),
    (re.compile(r'ctx\.mmu\.write16\('), 'nl::rec_write16(ctx.mmu, '),
    (re.compile(r'ctx\.mmu\.write32\('), 'nl::rec_write32(ctx.mmu, '),
    (re.compile(r'ctx\.mmu\.write64\('), 'nl::rec_write64(ctx.mmu, '),
    (re.compile(r'ctx\.mmu\.write_f32\('), 'nl::rec_write_f32(ctx.mmu, '),
    (re.compile(r'ctx\.mmu\.write_f64\('), 'nl::rec_write_f64(ctx.mmu, '),
    (re.compile(r'ctx\.mmu\.read8\('), 'nl::rec_read8(ctx.mmu, '),
    (re.compile(r'ctx\.mmu\.read16\('), 'nl::rec_read16(ctx.mmu, '),
    (re.compile(r'ctx\.mmu\.read32\('), 'nl::rec_read32(ctx.mmu, '),
    (re.compile(r'ctx\.mmu\.read64\('), 'nl::rec_read64(ctx.mmu, '),
    (re.compile(r'ctx\.mmu\.read_f32\('), 'nl::rec_read_f32(ctx.mmu, '),
    (re.compile(r'ctx\.mmu\.read_f64\('), 'nl::rec_read_f64(ctx.mmu, '),
    (re.compile(r'native_guest_fast::write8\(ctx\.mmu,'), 'nl::rec_write8(ctx.mmu, '),
    (re.compile(r'native_guest_fast::write16\(ctx\.mmu,'), 'nl::rec_write16(ctx.mmu, '),
    (re.compile(r'native_guest_fast::write32\(ctx\.mmu,'), 'nl::rec_write32(ctx.mmu, '),
    (re.compile(r'native_guest_fast::write64\(ctx\.mmu,'), 'nl::rec_write64(ctx.mmu, '),
    (re.compile(r'native_guest_fast::write_f32\(ctx\.mmu,'), 'nl::rec_write_f32(ctx.mmu, '),
    (re.compile(r'native_guest_fast::write_f64\(ctx\.mmu,'), 'nl::rec_write_f64(ctx.mmu, '),
    (re.compile(r'native_fastmem::read8\(ctx\.mmu,'), 'nl::rec_read8(ctx.mmu, '),
    (re.compile(r'native_fastmem::read16\(ctx\.mmu,'), 'nl::rec_read16(ctx.mmu, '),
    (re.compile(r'native_fastmem::read32\(ctx\.mmu,'), 'nl::rec_read32(ctx.mmu, '),
    (re.compile(r'native_fastmem::read64\(ctx\.mmu,'), 'nl::rec_read64(ctx.mmu, '),
    (re.compile(r'native_fastmem::read_f32\(ctx\.mmu,'), 'nl::rec_read_f32(ctx.mmu, '),
    (re.compile(r'native_fastmem::read_f64\(ctx\.mmu,'), 'nl::rec_read_f64(ctx.mmu, '),
]


def rewrite_accesses(text):
    for pattern, replacement in SCALAR_REWRITES:
        text = pattern.sub(replacement, text)
    return text


def extract_functions(generated, addresses, provided=()):
    """The translated guest bodies of `addresses`, keyed by address.

    The guest functions a candidate calls are extracted as well, so the
    differential test runs the same call graph the translated path runs. A
    function the test provides itself is not extracted.
    """
    wanted = {a.upper() for a in addresses}
    provided = {a.upper() for a in provided}
    found = {}
    includes = []
    pending = set(wanted)
    while pending:
        for path in sorted(generated.glob('*.cpp')):
            text = path.read_text(errors='surrogateescape')
            for line in text.splitlines()[:60]:
                if 'bounded-blocks' in line and line.startswith('#include'):
                    if line not in includes:
                        includes.append(line)
            for m in DEFINITION.finditer(text):
                address = m[1].upper()
                if address not in pending or address in found:
                    continue
                i = text.index('{', m.start())
                depth = 0
                for j in range(i, len(text)):
                    if text[j] == '{':
                        depth += 1
                    elif text[j] == '}':
                        depth -= 1
                        if depth == 0:
                            found[address] = text[m.start():j + 1]
                            break
        # The functions the extracted bodies call.
        called = set()
        for body in found.values():
            for m in re.finditer(r'\bfunc_([0-9A-Fa-f]{8})\(ctx\)', body):
                called.add(m[1].upper())
        pending = (called - set(found)) - provided
        if not pending:
            break
    return found, includes


def provided_functions(paths):
    """The guest functions a test file defines itself."""
    out = set()
    for path in paths:
        if not Path(path).is_file():
            continue
        for m in PROVIDED_FUNCTION.finditer(Path(path).read_text(errors='replace')):
            out.add(m[1].upper())
    return out


def referenced_functions(paths):
    """The guest functions a test file refers to."""
    out = set()
    for path in paths:
        if not Path(path).is_file():
            continue
        for m in re.finditer(r'\bfunc_([0-9A-Fa-f]{8})\b', Path(path).read_text(errors='replace')):
            out.add(m[1].upper())
    return out


def write_extracted_header(path, bodies, includes=()):
    lines = ['// Generated by tools/check_native_lowering.py. The translated guest functions, with',
             '// their memory accesses routed to the harness recorder.',
             '#pragma once',
             '#include "runtime/cpu_context.h"',
             '#include "runtime/native_guest_fast.h"',
             '#include "runtime/native_fastmem.h"',
             '#include "native_lowering_harness.h"',
             'using namespace nwii::runtime;',
             'namespace nl = nwii::runtime::native_lower_test;',
             '']
    for include in includes:
        lines.append(include)
    lines.append('')
    for address in sorted(bodies):
        body = rewrite_accesses(bodies[address])
        lines.append(body)
        lines.append('')
    path.write_text('\n'.join(lines))


# ---------------------------------------------------------------------------
# Qualification
# ---------------------------------------------------------------------------
def generated_functions(text):
    """The guest function bodies of a generated chunk.

    The generated code writes one function after another, so a function's body
    reaches to the next function's header. Splitting on the headers avoids
    matching braces over the whole tree.
    """
    headers = list(DEFINITION.finditer(text))
    out = {}
    for index, m in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        out[m[1].upper()] = text[m.start():end]
    return out


def scan_generated(generated, candidates_):
    """Qualify every candidate against the whole generated guest code.

    Returns the findings, one record per candidate.
    """
    addresses = {e['address'].upper() for e in candidates_}
    # One pass over the generated tree: keep the bodies of the functions that
    # call a candidate, and the set of functions every other function calls.
    callers = {}
    callees = {}
    for path in sorted(generated.glob('*.cpp')):
        text = path.read_text(errors='surrogateescape')
        for address, body in generated_functions(text).items():
            called = set(x.upper() for x in re.findall(r'\bfunc_([0-9A-Fa-f]{8})\(ctx\)', body))
            callees[address] = called
            if called & addresses:
                callers[address] = body
    # The bodies of the callees the callers invoke, so the analysis knows
    # which registers a later call can observe.
    needed = set()
    for body in callers.values():
        needed |= set(x.upper() for x in re.findall(r'\bfunc_([0-9A-Fa-f]{8})\(ctx\)', body))
    callee_bodies = {}
    for path in sorted(generated.glob('*.cpp')):
        if not needed:
            break
        text = path.read_text(errors='surrogateescape')
        for address, body in generated_functions(text).items():
            if address in needed and address not in callee_bodies:
                callee_bodies[address] = body
                needed.discard(address)
    # For every callee the callers invoke: the registers it reads before it
    # writes them (a call that receives the context observes those) and the
    # registers it clobbers (after such a call the candidate's value in them
    # is dead).
    callee_reads = {}
    callee_clobbers = {}
    for address, body in callee_bodies.items():
        callee_reads[address] = function_read_before_write(body)
        callee_clobbers[address] = set(function_clobbered(body))

    findings = []
    for entry in candidates_:
        address = entry['address'].upper()
        body = entry.get('_body') or callee_bodies.get(address)
        clobbered = set(function_clobbered(body)) if body else set()
        reproduced = set(entry.get('reproduced_registers', []))
        unreproduced = sorted(clobbered - reproduced)
        excluded = {c.upper() for c in entry.get('exclude_callers', [])}
        observations = {}
        for caller, caller_body in callers.items():
            seen = observed_registers(caller_body, address, set(unreproduced),
                                      callee_reads, callee_clobbers)
            if seen:
                observations[caller] = sorted(seen)
        findings.append({
            'address': address,
            'clobbered': sorted(clobbered),
            'reproduced': sorted(reproduced),
            'unreproduced': unreproduced,
            'native_entry': entry.get('native_entry', 'definition'),
            'exclude_callers': sorted(excluded),
            'callers': len([c for c, s in callees.items() if address in s]),
            'observations': observations,
        })
    return findings


def observed_registers(body, address, unreproduced, callee_reads, callee_clobbers=None):
    """The unreproduced registers a caller can observe after its call.

    The walk follows the caller's control flow from the call. A register is
    observed when a reachable path reads it before writing it again; a later
    call that receives the context observes every register that call reads
    before writing, and an opaque call observes every register that is still
    live.

    The walk is done per register: once a register is written the path can no
    longer observe it, so the state is a single bit and the search is linear in
    the size of the caller.
    """
    if not unreproduced:
        return set()
    callee_clobbers = callee_clobbers or {}
    blocks = blocks_of(body)
    labels = [label for label, _ in blocks]
    known = {label: i for i, label in enumerate(labels)}
    found = set()
    for register in sorted(unreproduced):
        if register_reachable(blocks, known, address, register, callee_reads, callee_clobbers):
            found.add(register)
    return found


def register_reachable(blocks, known, address, register, callee_reads, callee_clobbers=None):
    """True when a path from the call reads `register` before writing it.

    A later call that receives the context observes the registers it reads
    before writing, and it replaces the value of every register it clobbers,
    so the candidate's value in those registers is dead.
    """
    callee_clobbers = callee_clobbers or {}
    start = []
    for position, (label, statements) in enumerate(blocks):
        for index, statement in enumerate(statements):
            if not re.search(r'\bfunc_%s\(ctx\)' % address, statement):
                continue
            # The rest of this block, after the call.
            for rest in statements[index + 1:]:
                written, read = statement_effects(rest)
                if register in read:
                    return True
                if register in written:
                    break
                for callee in statement_calls(rest):
                    if register in callee_reads.get(callee, ()):
                        return True
                    if register in callee_clobbers.get(callee, ()):
                        break
                if statement_is_opaque_call(rest):
                    return True
            # The blocks that follow.
            for target in block_successors((label, statements), position, [l for l, _ in blocks], known):
                start.append(target)
    # Breadth first over the successors, carrying "the register has not been
    # written yet".
    seen = set()
    stack = [(target, False) for target in start]
    while stack:
        current, written = stack.pop()
        key = (current, written)
        if key in seen:
            continue
        seen.add(key)
        position = known[current]
        label, statements = blocks[position]
        stop = False
        for statement in statements:
            w, r = statement_effects(statement)
            if not written:
                if register in r:
                    return True
                for callee in statement_calls(statement):
                    if register in callee_reads.get(callee, ()):
                        return True
                    if register in callee_clobbers.get(callee, ()):
                        written = True
                        break
                if statement_is_opaque_call(statement):
                    return True
            if register in w:
                written = True
                stop = True
                break
        if stop:
            continue
        for target in block_successors((label, statements), position, [l for l, _ in blocks], known):
            stack.append((target, written))
    return False


def qualify(finding):
    """The qualification verdict of one candidate."""
    problems = []
    address = finding['address']
    unreproduced = finding['unreproduced']
    if finding['native_entry'] == 'definition':
        if unreproduced:
            problems.append(
                'the definition entry serves every entry, including the indirect ones, but the '
                'native path does not reproduce %s' % ', '.join(unreproduced))
    else:
        excluded = set(finding['exclude_callers'])
        for caller, seen in finding['observations'].items():
            if caller not in excluded:
                problems.append('the caller %s observes %s after the call and is not excluded'
                                % (caller, ', '.join(seen)))
        for caller in excluded:
            if caller not in finding['observations']:
                problems.append('the excluded caller %s does not observe anything' % caller)
    return problems


# ---------------------------------------------------------------------------
# Compilation and execution of the per-family tests
# ---------------------------------------------------------------------------
def runtime_include(explicit):
    if explicit:
        return Path(explicit)
    candidate = REFERENCE_STORAGE / 'nwii-full-source/nWiiRuntime/include'
    if candidate.is_dir():
        return candidate
    return PORT / 'build/nwii-full-source/nWiiRuntime/include'


def compile_and_run(name, source, include, work, defines):
    exe = work / (name + '.exe')
    cmd = ['g++', '-std=c++20', '-O2', '-fwrapv', '-fno-strict-aliasing',
           '-ffp-contract=off', '-static', *defines,
           '-I', str(include), '-I', str(PORT / 'tools'), '-I', str(PORT / 'runtime-overlays'),
           '-I', str(work),
           str(source), '-o', str(exe)]
    build = subprocess.run(cmd, capture_output=True, text=True)
    if build.returncode:
        return False, 'compile failed:\n' + build.stdout[-4000:] + build.stderr[-8000:]
    run = subprocess.run([str(exe)], capture_output=True, text=True, cwd=str(work))
    return run.returncode == 0, run.stdout[-8000:] + run.stderr[-4000:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--generated', type=Path, default=NL.GENERATED)
    ap.add_argument('--runtime-include', type=Path, default=None)
    ap.add_argument('--work', type=Path, default=WORK)
    ap.add_argument('--manifest', type=Path, default=NL.MANIFEST)
    ap.add_argument('--only', nargs='*', default=None)
    args = ap.parse_args()

    manifest = load_manifest(args.manifest)
    adopted = NL.candidates(manifest)
    if args.only:
        wanted = {a.upper() for a in args.only}
        adopted = [e for e in adopted if e['address'].upper() in wanted]
    if not adopted:
        print('No adopted candidates.')
        return 0

    work = args.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    include = runtime_include(args.runtime_include)
    if not include.is_dir():
        print('Runtime include directory not found: %s' % include)
        return 1

    # One extracted header per test file: a test only sees the translated
    # guest functions it exercises, so the overlays they need stay local.
    families = sorted({e.get('test', '') for e in adopted if e.get('test')})
    failures = 0
    extracted = {}
    for family in families:
        source = Path(family)
        if not source.is_absolute():
            source = PORT / 'tools' / family
        if not source.is_file():
            print('MISSING TEST %s' % source)
            failures += 1
            continue
        group = [e for e in adopted if e.get('test') == family]
        provided = provided_functions([source])
        wanted = {e['address'].upper() for e in group} | (referenced_functions([source]) - provided)
        bodies, includes = extract_functions(args.generated, wanted, provided)
        missing = [e['address'] for e in group if e['address'].upper() not in bodies]
        if missing:
            print('Translated guest function not found in the generated tree: ' + ', '.join(missing))
            failures += 1
            continue
        for entry in group:
            extracted[entry['address'].upper()] = bodies.get(entry['address'].upper())
        group_work = work / source.stem
        group_work.mkdir(parents=True, exist_ok=True)
        write_extracted_header(group_work / 'guest_lowering_extracted.h', bodies, includes)
        ok, output = compile_and_run(source.stem, source, include, group_work,
                                     ['-DNATIVE_LOWERING_TRACE_MEMORY'])
        print('=== %s: %s' % (family, 'PASS' if ok else 'FAIL'))
        print(output.rstrip())
        # The verdict is the per-group result of the adopted candidates. A
        # group that is not adopted (a candidate the qualification rejected)
        # is reported but does not fail the check.
        results = dict((m[1], (m[2], m[3])) for m in
                       re.finditer(r'NATIVE_LOWERING_TESTS\s+(\S+)\s+(\d+)/(\d+)', output))
        for entry in group:
            name = entry['implementation']
            match = name if name in results else None
            if match is None:
                for key in results:
                    if key.split('.')[-1] == name:
                        match = key
                        break
            if match is None:
                print('MISSING GROUP %s in %s' % (name, family))
                failures += 1
                continue
            passed, total = results[match]
            if passed != total:
                print('GROUP FAIL %s: %s/%s' % (name, passed, total))
                failures += 1
        for name, (passed, total) in results.items():
            if name != 'total' and passed != total and \
                    name.split('.')[-1] not in {e['implementation'] for e in group}:
                print('NOTE: %s is not an adopted candidate and its %s/%s result is not part of the verdict'
                      % (name, passed, total))
        if not ok and failures == 0:
            print('NOTE: %s returned a non-zero exit code without an adopted group failing' % family)
        # The production configuration (no memory-effect recording) is the one
        # the game runs in; its paired timings are the relevant measurement.
        _, production = compile_and_run(source.stem + '_production', source, include, group_work, [])
        for line in production.splitlines():
            if 'NATIVE_LOWERING_BENCH' in line:
                print('PRODUCTION ' + line)

    findings = scan_generated(args.generated, adopted)
    (work / 'qualification.json').write_text(json.dumps(findings, indent=2))
    for finding in findings:
        problems = qualify(finding)
        if problems:
            failures += 1
            for problem in problems:
                print('QUALIFICATION FAIL %s: %s' % (finding['address'], problem))
        else:
            print('Qualification OK %s (clobbers %d register(s), unreproduced %s, entry %s, %d direct caller(s))'
                  % (finding['address'], len(finding['clobbered']),
                     ','.join(finding['unreproduced']) or 'none',
                     finding['native_entry'], finding['callers']))

    print('Native lowering checks: %s' % ('PASS' if failures == 0 else 'FAIL (%d)' % failures))
    return 0 if failures == 0 else 1


WORK = Path(os.environ.get('NWII_LOWERING_WORK') or
            (Path(os.environ.get('TEMP') or 'C:/Users/deki/AppData/Local/Temp')) / 'opencode' / 'native-lowering' / 'work-coordinator')


if __name__ == '__main__':
    sys.exit(main())
