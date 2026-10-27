"""Set ctx.pc to the instruction after `sc` before handle_syscall.

handle_syscall polls callbacks for non-IOS syscall numbers (DCFlushRange ends with `sc`, r0 arbitrary). With ctx.pc
still at the previous bdnz, an interrupt dispatched there saved the bdnz as the resume point; after the handler
the loop re-ran with CTR=0, wrapped to 0xFFFFFFFF and spun ~4G iterations (the intermittent loading stall)."""
from pathlib import Path
import json, re
PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp'
s = p.read_bytes().decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
BS = chr(92)
old = ('    out << "    // sc (System Call)' + BS + 'n";' + nl + '    out << "    nwii::runtime::handle_syscall(ctx);' + BS + 'n";')
new = ('    out << "    // sc (System Call)' + BS + 'n";' + nl +
       '    out << "    ctx.pc = 0x" << std::hex << std::uppercase << (inst.address + 4) << std::dec << "; // resume after sc' + BS + 'n";' + nl +
       '    out << "    nwii::runtime::handle_syscall(ctx);' + BS + 'n";')
if new in s: pass
elif old in s: p.write_bytes(s.replace(old, new).encode('utf-8'))
else: raise SystemExit('sc emitter context changed')
pat = re.compile(r'(    // 0x([0-9A-F]{8}): 44000002\n)(    // sc \(System Call\)\n)(?!    ctx\.pc = )')
changes = []
for q in sorted((PORT / 'build/crash-generated').glob('output_*.cpp')):
    t = q.read_text()
    def fn(m): return m[1] + m[3] + f'    ctx.pc = 0x{int(m[2],16)+4:08X}; // resume after sc\n'
    t2, n = pat.subn(fn, t)
    if n: q.write_text(t2); changes.append({'file': q.name, 'sites': n})
rep = PORT / 'reports/sc-resume-pc-patch.json'
if changes or not rep.exists():
    rep.write_text(json.dumps({'chunks': changes, 'sites': sum(x['sites'] for x in changes)}, indent=2))
print('sc resume-pc staging:', sum(x['sites'] for x in changes), 'sites')
