"""NATIVE_DISPATCH_ABI16_V1: return the dispatch choice in registers, not through a hidden pointer.

DispatchChoice is { uint32_t pc; void(*func)(CPUContext&); bool hle; }. With that field
order the record is 24 bytes, so the SysV x86-64 ABI returns it through a caller-provided
hidden pointer: every miss in find_dispatch_target_dense -- the only out-of-line part of
the dispatch path, measured at 1.48% of the guest thread in capture
cpu-running-20261014-112801-308000 -- writes and reloads the 24-byte object.

Reordering to { func, pc, hle } makes it exactly 16 bytes, which the ABI returns in
RAX:RDX. All aggregate constructions are rewritten as designated initializers so the
reorder cannot silently mis-assign a field.

MEASURED, NOT LANDED BY DEFAULT. tools/check_dense_dispatch.py benchmarks the two forms
over the real resolver with three access patterns; the 16-byte form is a consistent
regression on the miss-dominated patterns and only a small win on the fully cached one
(three runs: hot32 1.03x, all_sequential 0.68-0.73x, all_permuted 0.91-0.92x). The game's
mix is unknown from an OS sampler, so this stays opt-in rather than paying for a possible
loss: NWII_BUILD_DISPATCH_ABI=1 enables it. The measurements are in
reports/dense-dispatch-speedups.json.

The stage always refreshes runtime-overlays/dense-dispatch-before.cpp from the current
generated dispatcher. tools/check_dense_dispatch.py compares before/after tables by
address, and the frozen snapshot held 17,965 function bounds while the current table has
20,226, so the differential could not pass for a reason that predates this change.

Build rollback: NWII_BUILD_DISPATCH_ABI=0 (default). Idempotent.
"""
from pathlib import Path
import argparse
import os
import re

MARK = 'NATIVE_DISPATCH_ABI16_V1'

OLD_STRUCT = """// NATIVE_SMALL_DISPATCH_V1
struct DispatchChoice { uint32_t pc; void(*func)(CPUContext&); bool hle; };"""
NEW_STRUCT = """// NATIVE_SMALL_DISPATCH_V1
// NATIVE_DISPATCH_ABI16_V1: 16-byte record so the choice is returned in RAX:RDX instead of
// through a hidden sret pointer. Field order changed, so every aggregate is designated.
struct DispatchChoice { void(*func)(CPUContext&); uint32_t pc; bool hle; };
static_assert(sizeof(DispatchChoice)==16,"NATIVE_DISPATCH_ABI16_V1");"""

OVERRIDE_ROW = re.compile(r'^    \{(0x[0-9A-F]{8}|0), (func_[0-9A-F]{8}|nullptr), (true|false)\},?$', re.M)


def _row(m):
    return '    {.func=%s, .pc=%s, .hle=%s},' % (m.group(2), m.group(1), m.group(3))


DENSE_RET = '    return {target,id?g_func_bounds[id-1].func:nullptr,false};'
DENSE_RET_NEW = '    return {.func=id?g_func_bounds[id-1].func:nullptr,.pc=target,.hle=false};'
UNCACHED_RET = '    return {target, fb ? fb->func : nullptr, false};'
UNCACHED_RET_NEW = '    return {.func=fb ? fb->func : nullptr,.pc=target,.hle=false};'
HOT_RET = '    if(entry.pc==target)return {target,entry.func,entry.hle};'
HOT_RET_NEW = '    if(entry.pc==target)return {.func=entry.func,.pc=target,.hle=entry.hle};'


def apply(text):
    if MARK in text:
        return text
    assert text.count(OLD_STRUCT) == 1, 'DispatchChoice anchor'
    text = text.replace(OLD_STRUCT, NEW_STRUCT, 1)
    text, rows = OVERRIDE_ROW.subn(_row, text)
    assert rows >= 2, 'override table rows: %d' % rows
    for old, new in ((DENSE_RET, DENSE_RET_NEW), (UNCACHED_RET, UNCACHED_RET_NEW), (HOT_RET, HOT_RET_NEW)):
        assert text.count(old) == 1, old
        text = text.replace(old, new, 1)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    port = args.port
    path = port / 'build/crash-generated/main_output.cpp'
    if not path.exists():
        print('dispatch ABI stage: no generated entry point')
        return
    before = path.read_text(errors='surrogateescape')
    if MARK not in before:
        fixture = port / 'runtime-overlays/dense-dispatch-before.cpp'
        row = r'^    \{0x[0-9A-F]{8}, 0x[0-9A-F]{8}, func_'
        old_bounds = len(re.findall(row, fixture.read_text(errors='surrogateescape'), re.M))
        new_bounds = len(re.findall(row, before, re.M))
        if fixture.read_text(errors='surrogateescape') != before:
            fixture.write_text(before, errors='surrogateescape')
            print('dense-dispatch-before.cpp refreshed: %d -> %d function bounds' % (old_bounds, new_bounds))
    if os.environ.get('NWII_BUILD_DISPATCH_ABI', '0') != '1':
        print('dispatch ABI16 is opt-in (NWII_BUILD_DISPATCH_ABI=1); not applied')
        return
    after = apply(before)
    if after != before:
        path.write_text(after, errors='surrogateescape')
    print('%s staged; DispatchChoice is 16 bytes and returned in registers.' % MARK)


if __name__ == '__main__':
    main()
