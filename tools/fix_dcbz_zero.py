"""Repair translated dcbz using the interpreter's eight MMU writes.

Run on canonical generated output before register promotion and hot splitting.
Held-out function bodies are excluded using the approved exclusion-only list.
No instruction counter, poll, register or PC operation is changed.
"""
from pathlib import Path
import argparse
import json
import re

MARKER = 'NATIVE_DCBZ_ZERO_V1'
STUB = '    // dcbz (data cache block zero) - stub\n'
COMMENT = re.compile(r'    // 0x([0-9A-Fa-f]{8}): ([0-9A-Fa-f]{8})\n$')
FUNCTION = re.compile(r'(?m)^void func_([0-9A-Fa-f]{8})\([^\n]*\) \{')
BLOCK = re.compile(r'    \{ // NATIVE_DCBZ_ZERO_V1 opcode=([0-9A-F]{8})\n'
                   r'.*?    \} // NATIVE_DCBZ_ZERO_V1 end\n', re.S)

def decode(opcode):
    assert opcode >> 26 == 31 and (opcode >> 1) & 1023 == 1014, ('not dcbz', hex(opcode))
    return (opcode >> 16) & 31, (opcode >> 11) & 31

def emit(opcode):
    ra, rb = decode(opcode)
    base = f'ctx.gpr[{ra}]' if ra else '0u'
    return (f'    {{ // {MARKER} opcode={opcode:08X}\n'
            f'      const uint32_t native_dcbz_ea = ({base} + ctx.gpr[{rb}]) & ~31u;\n'
            '      for (uint32_t native_dcbz_offset = 0; native_dcbz_offset < 32; native_dcbz_offset += 4)\n'
            '        ctx.mmu.write32(native_dcbz_ea + native_dcbz_offset, 0);\n'
            f'    }} // {MARKER} end\n')

def restore(source):
    def replace(match):
        assert match.group() == emit(int(match.group(1), 16)), 'dcbz repair modified'
        return STUB
    return BLOCK.sub(replace, source)

def apply(source, excluded):
    canonical = restore(source)
    functions = list(FUNCTION.finditer(canonical))
    changes = []
    for stub in re.finditer(re.escape(STUB), canonical):
        owner = next((f for f in reversed(functions) if f.start() < stub.start()), None)
        assert owner is not None, 'dcbz stub has no enclosing generated function'
        if int(owner.group(1), 16) in excluded:
            continue
        # Decode only the immediately preceding instruction comment. A stale
        # opcode or altered emitter spelling must fail rather than guess.
        comment = COMMENT.search(canonical[max(0, stub.start()-50):stub.start()])
        assert comment is not None, 'dcbz instruction comment missing'
        opcode = int(comment.group(2), 16)
        replacement = emit(opcode)
        changes.append((stub.start(), stub.end(), replacement))
    for start, end, replacement in reversed(changes):
        canonical = canonical[:start] + replacement + canonical[end:]
    assert canonical.count(MARKER) == len(changes) * 2
    return canonical, len(changes)

def stage(port, rollback=False):
    path = port / 'reference/guest-regions/effect-exclusions.json'
    excluded = {int(a, 16) for a in json.loads(path.read_text())['addresses']}
    changed = sites = 0
    for path in sorted((port / 'build/crash-generated').glob('*.cpp')):
        source = path.read_text(errors='surrogateescape')
        if STUB not in source and MARKER not in source:
            continue
        result, count = (restore(source), 0) if rollback else apply(source, excluded)
        sites += count
        if result != source:
            path.write_text(result, errors='surrogateescape')
            changed += 1
    print(f'dcbz {"restored" if rollback else "repaired"}: {sites} sites, {changed} files changed.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    stage(args.port, args.restore)
