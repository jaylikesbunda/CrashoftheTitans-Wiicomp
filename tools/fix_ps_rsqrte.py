"""Implement paired-single ps_rsqrte (xo 26) and ps_res (xo 24).

Previously the emitter generated a std::exit(1) for these (UNIMPLEMENTED), killing the process when the level loader
reached ps_rsqrte at 80281CDC. Results use the exact reciprocal (a superset of the Gekko estimate precision)."""
from pathlib import Path
import json, re
PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp'
s = p.read_bytes().decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
anchor = '    } else if (xo_10 == 1014) {' + nl + '      out << "    // dcbz_l (no-op)\\n";'
add = ('    } else if (xo == 26) {' + nl +
       '      out << "    ctx.fpr[" << frD << "] = (float)(1.0 / std::sqrt(ctx.fpr[" << frB << "])); ctx.ps1[" << frD << "] = (float)(1.0 / std::sqrt(ctx.ps1[" << frB << "])); // ps_rsqrte\\n";' + nl +
       '    } else if (xo == 24) {' + nl +
       '      out << "    ctx.fpr[" << frD << "] = (float)(1.0 / ctx.fpr[" << frB << "]); ctx.ps1[" << frD << "] = (float)(1.0 / ctx.ps1[" << frB << "]); // ps_res\\n";' + nl)
if 'ps_rsqrte' in s:
    pass
elif anchor in s:
    p.write_bytes(s.replace(anchor, add + anchor).encode('utf-8'))
else:
    raise SystemExit('ps emitter context changed')

pat = re.compile(
    r'(    // 0x([0-9A-F]{8}): ([0-9A-F]{8})\n(?:    if \(ctx\.require_fpu[^\n]*\n)?)'
    r'    std::cerr << "UNIMPLEMENTED Opcode 4 \(ps_\*\) XO " << (26|24) << " XO_10 " << (?:26|24) << [^\n]*\n')


def fn(m):
    w = int(m[3], 16)
    d = (w >> 21) & 31
    b = (w >> 11) & 31
    if m[4] == '26':
        return m[1] + (f'    ctx.fpr[{d}] = (float)(1.0 / std::sqrt(ctx.fpr[{b}])); '
                       f'ctx.ps1[{d}] = (float)(1.0 / std::sqrt(ctx.ps1[{b}])); // ps_rsqrte\n')
    return m[1] + (f'    ctx.fpr[{d}] = (float)(1.0 / ctx.fpr[{b}]); '
                   f'ctx.ps1[{d}] = (float)(1.0 / ctx.ps1[{b}]); // ps_res\n')


changes = []
for q in sorted((PORT / 'build/crash-generated').glob('output_*.cpp')):
    t = q.read_text()
    t2, n = pat.subn(fn, t)
    if n:
        q.write_text(t2)
        changes.append({'file': q.name, 'sites': n})
rep = PORT / 'reports/ps-rsqrte-patch.json'
if changes or not rep.exists():
    rep.write_text(json.dumps({'chunks': changes, 'sites': sum(x['sites'] for x in changes)}, indent=2))
print('ps_rsqrte/ps_res staging:', sum(x['sites'] for x in changes), 'sites')
