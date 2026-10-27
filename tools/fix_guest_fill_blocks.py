"""Combine one fill iteration's eight ordinary RAM stores; retain every poll.

Only the proven 32-byte store group changes. All guest bookkeeping, labels,
backward-branch polls and fallback stores remain. Restore before codegen stages.
"""
from pathlib import Path
import argparse
import re
import json
import shutil

MARKER = 'NATIVE_GUEST_FILL_BLOCK_V1'
HELPER = '''// NATIVE_GUEST_FILL_BLOCK_V1 begin
#include "runtime/native_guest_fill_block.h"
// NATIVE_GUEST_FILL_BLOCK_V1 end
'''
DECL = '    bool native_fill_combined = false; // NATIVE_GUEST_FILL_BLOCK_V1\n'
FIRST = '    ctx.mmu.write32(ctx.gpr[3] + 4, ctx.gpr[7]); // stw r7, 4(r3)'
PATCH_FIRST = '''    native_fill_combined = native_guest_fill_block(ctx.mmu, ctx.gpr[3] + 4u, ctx.gpr[7]); // NATIVE_GUEST_FILL_BLOCK_V1
    if (!native_fill_combined) ctx.mmu.write32(ctx.gpr[3] + 4, ctx.gpr[7]); // stw r7, 4(r3)'''

def apply(source):
    if MARKER in source:
        assert source.count(HELPER) == 1 and source.count(DECL) == 1 and source.count(PATCH_FIRST) == 1
        return source
    head = re.search(r'void func_80006004\([^\n]+\) \{\n', source)
    assert head, 'missing fill function'
    end = source.find('\nvoid func_', head.end())
    end = len(source) if end < 0 else end
    body = source[head.start():end]
    assert body.count(FIRST) == 1
    # The compiler exposes only back-edge resume entries, never the middle of
    # this group. A new interior entry would need its own fallback treatment.
    for address in ('80006060','80006064','80006068','8000606C','80006070','80006074','80006078','8000607C'):
        assert 'goto loc_'+address not in body and 'case 0x'+address not in body
    body = body.replace(head.group(0), head.group(0)+DECL, 1).replace(FIRST, PATCH_FIRST, 1)
    a = body.index('loc_80006064: ;'); z = body.index('loc_80006080: ;')
    stores = body[a:z]
    assert stores.count('ctx.mmu.write32(') == 7
    stores = stores.replace('ctx.mmu.write32(', 'if (!native_fill_combined) ctx.mmu.write32(')
    body = body[:a]+stores+body[z:]
    source = source[:head.start()]+body+source[end:]
    return HELPER+'\n'+source

def restore(source):
    if MARKER not in source: return source
    assert source.count(HELPER) == 1 and source.count(DECL) == 1 and source.count(PATCH_FIRST) == 1
    source = source.replace(HELPER+'\n', '', 1).replace(DECL, '', 1).replace(PATCH_FIRST, FIRST, 1)
    head = source.index('void func_80006004(')
    a = source.index('loc_80006064: ;', head); z = source.index('loc_80006080: ;', a)
    stores = source[a:z]
    assert stores.count('if (!native_fill_combined) ctx.mmu.write32(') == 7
    return source[:a]+stores.replace('if (!native_fill_combined) ctx.mmu.write32(', 'ctx.mmu.write32(')+source[z:]

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--port', type=Path)
    args = parser.parse_args()
    root = args.port or Path(__file__).resolve().parents[1]
    path = root/'build/crash-generated/output_0.cpp'
    if args.restore and not path.exists():
        print('Guest fill block: no generated input to restore')
        raise SystemExit(0)
    excluded = json.loads((root/'reference/guest-regions/effect-exclusions.json').read_text())['addresses']
    assert '80006004' not in excluded
    if not args.restore:
        destination = root/'build/nwii-full-source/nWiiRuntime/include/runtime/native_guest_fill_block.h'
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root/'runtime-overlays/native_guest_fill_block.h', destination)
    source = path.read_text(errors='surrogateescape')
    updated = restore(source) if args.restore else apply(source)
    if updated != source: path.write_text(updated, errors='surrogateescape')
    print('Guest fill block:', 'restored' if args.restore else 'staged')
