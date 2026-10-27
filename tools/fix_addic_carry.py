"""Preserve PPC 32-bit addic immediate carry semantics in 64-bit host sums."""
from pathlib import Path
import json, re

PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp'
s = p.read_text()
old = 'out << "        uint64_t res = (uint64_t)ctx.gpr[" << rA << "] + " << simm\n        << ";\\n";'
new = 'out << "        uint64_t res = (uint64_t)ctx.gpr[" << rA << "] + (uint64_t)(uint32_t)" << simm\n        << ";\\n";'
count = s.count(old)
if count:
    if count != 2:
        raise SystemExit(f'Unexpected addic emitter contexts: {count}')
    p.write_text(s.replace(old, new))
elif s.count(new) != 2:
    raise SystemExit('Addic emitter patch context changed')
changes = []
pattern = re.compile(r'(uint64_t res = \(uint64_t\)ctx\.gpr\[\d+\] \+ )(-?\d+);')
for p in (PORT / 'build/crash-generated').glob('output_*.cpp'):
    s = p.read_text()
    updated, n = pattern.subn(r'\1(uint64_t)(uint32_t)\2;', s)
    if n:
        p.write_text(updated)
        changes.append({'file': p.name, 'sites': n})
report = PORT / 'reports/addic-carry-patch.json'
if changes or not report.exists():
    report.write_text(json.dumps({'emitter_contexts': 2, 'chunks': changes,
                                 'instruction_sites': sum(x['sites'] for x in changes)}, indent=2))
print(f'Addic carry staging: {len(changes)} chunks, {sum(x["sites"] for x in changes)} sites')
