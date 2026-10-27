"""NATIVE_MEM_PROOF_V1 stage: region-hoisted address proofs in the promoted hot units (build/crash-hot).

Transform: tools/guest_mem_proofs.py. Runtime: runtime-overlays/native_mem_proof.h.
Order: apply after fix_guest_batch.py (it reads the final hot text); restore before fix_guest_batch.py --restore
and fix_hot_split.py, so every other stage sees its own text. Each rewritten definition carries its original
text (base64), so --restore is exact and the stage is idempotent. Effect-excluded addresses are skipped.
NWII_BUILD_MEM_PROOFS=0 stages nothing (and restores). With the guest arena on (tools/fix_guest_arena.py, which runs next)
the stage also stages nothing: the arena makes every plain accessor one movbe on the arena, so a proof would only add a
Span check and route the access through the 0x20000000 window instead.
"""
from pathlib import Path
from collections import Counter
import argparse, base64, json, os, re, shutil
import guest_mem_proofs as M
from fix_guest_batch import scope_end
import fix_guest_arena

MARK = M.MARK
BLOCK = re.compile(r'/\* ' + MARK + r'_FN ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_' + MARK + r'_FN \*/', re.S)
INCLUDE = '#include "runtime/native_mem_proof.h" // ' + MARK + '\n'


def wrap(old, new):
    return '/* ' + MARK + '_FN ' + base64.b64encode(old.encode('utf-8', 'surrogateescape')).decode() + ' */\n' + new + '\n/* END_' + MARK + '_FN */'


def restore(text):
    text = BLOCK.sub(lambda m: base64.b64decode(m[1]).decode('utf-8', 'surrogateescape'), text)
    return text.replace(INCLUDE, '')


def source(text, excluded=()):
    text = restore(text)
    stats = Counter(); edits = []; counter = [0]
    for head in M.HEAD.finditer(text):
        if head[1] in excluded:
            continue
        end = scope_end(text, head.end() - 1)
        old = text[head.start():end]
        new, st = M.transform_function(old, counter)
        if new != old:
            edits.append((head.start(), end, wrap(old, new)))
            stats.update(st); stats['functions'] += 1
    for a, b, new in reversed(edits):
        text = text[:a] + new + text[b:]
    if edits:
        # After the unit's native_guest_fast.h include: that one must follow its NWII_V13_INLINE_INDIRECT define.
        m = re.search(r'^#include "runtime/native_guest_fast\.h"[^\n]*\n', text, re.M)
        assert m, 'hot unit without runtime/native_guest_fast.h'
        text = text[:m.end()] + INCLUDE + text[m.end():]
    return text, dict(stats)


def apply(port, undo=False):
    enabled = os.environ.get('NWII_BUILD_MEM_PROOFS', '1') != '0' and not undo
    if enabled and fix_guest_arena.stage_enabled(port):
        enabled = False
        print('Guest memory proofs: guest arena on, hot units keep the plain (arena) accessors')
    excluded = set(json.loads((port / 'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
    stats = Counter()
    for path in sorted((port / 'build/crash-hot').glob('hot_*.cpp')):
        old = path.read_text(errors='surrogateescape')
        if enabled:
            new, st = source(old, excluded); stats.update(st)
        else:
            new = restore(old)
        if new != old:
            path.write_text(new, errors='surrogateescape')
    if enabled:
        rt = port / 'build/nwii-full-source/nWiiRuntime/include/runtime'
        if rt.is_dir():
            shutil.copy2(port / 'runtime-overlays/native_mem_proof.h', rt / 'native_mem_proof.h')
    print('Guest memory proofs ' + ('staged ' + json.dumps(dict(stats), sort_keys=True) if enabled else 'restored/disabled'))
    return dict(stats)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument('--restore', action='store_true')
    a = ap.parse_args()
    apply(a.port, a.restore)
