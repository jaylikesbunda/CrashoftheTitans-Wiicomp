"""Set XER[CA] for subfic and sraw (upstream emitter omitted it; found via 64-bit divide helper 803939AC)."""
from pathlib import Path
import json, re
PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp'
s = p.read_text()
old_subfic = r'''    out << "    ctx.gpr[" << ppc_inst.rd() << "] = (uint32_t)("
        << ppc_inst.simm() << " - (int32_t)ctx.gpr[" << ppc_inst.ra()
        << "]); // subfic\n";'''
new_subfic = r'''    out << "    { uint32_t a_ = ctx.gpr[" << ppc_inst.ra() << "]; uint32_t i_ = (uint32_t)("
        << ppc_inst.simm() << "); ctx.gpr[" << ppc_inst.rd()
        << "] = i_ - a_; ctx.xer = (ctx.xer & ~(1u << 29)) | ((i_ >= a_) ? (1u << 29) : 0u); } // subfic\n";'''
old_sraw = r'''      out << "    ctx.gpr[" << rA << "] = (uint32_t)((ctx.gpr[" << rB
          << "] & 0x20) ? ((int32_t)ctx.gpr[" << rS
          << "] >> 31) : ((int32_t)ctx.gpr[" << rS << "] >> (ctx.gpr[" << rB
          << "] & 0x1F))); // sraw\n";'''
new_sraw = r'''      out << "    { int32_t sv_ = (int32_t)ctx.gpr[" << rS << "]; uint32_t sh_ = ctx.gpr[" << rB
          << "]; uint32_t ca_; if (sh_ & 0x20) { ctx.gpr[" << rA
          << "] = (uint32_t)(sv_ >> 31); ca_ = sv_ < 0; } else { sh_ &= 0x1F; ctx.gpr[" << rA
          << "] = (uint32_t)(sv_ >> sh_); ca_ = (sv_ < 0 && sh_ && (sv_ & ((1u << sh_) - 1))) ? 1 : 0; }"
             " ctx.xer = (ctx.xer & ~(1u << 29)) | (ca_ << 29); } // sraw\n";'''
for name, old, new in (('subfic', old_subfic, new_subfic), ('sraw', old_sraw, new_sraw)):
    if old in s: s = s.replace(old, new)
    elif new not in s: raise SystemExit(f'{name} emitter context changed')
p.write_text(s)
sub = re.compile(r'ctx\.gpr\[(\d+)\] = \(uint32_t\)\((-?\d+) - \(int32_t\)ctx\.gpr\[(\d+)\]\); // subfic')
sra = re.compile(r'ctx\.gpr\[(\d+)\] = \(uint32_t\)\(\(ctx\.gpr\[(\d+)\] & 0x20\) \? \(\(int32_t\)ctx\.gpr\[(\d+)\] >> 31\) : \(\(int32_t\)ctx\.gpr\[\3\] >> \(ctx\.gpr\[\2\] & 0x1F\)\)\); // sraw')
def f_sub(m): return (f'{{ uint32_t a_ = ctx.gpr[{m[3]}]; uint32_t i_ = (uint32_t)({m[2]}); ctx.gpr[{m[1]}] = i_ - a_; '
                      f'ctx.xer = (ctx.xer & ~(1u << 29)) | ((i_ >= a_) ? (1u << 29) : 0u); }} // subfic')
def f_sra(m): return (f'{{ int32_t sv_ = (int32_t)ctx.gpr[{m[3]}]; uint32_t sh_ = ctx.gpr[{m[2]}]; uint32_t ca_; '
                      f'if (sh_ & 0x20) {{ ctx.gpr[{m[1]}] = (uint32_t)(sv_ >> 31); ca_ = sv_ < 0; }} '
                      f'else {{ sh_ &= 0x1F; ctx.gpr[{m[1]}] = (uint32_t)(sv_ >> sh_); ca_ = (sv_ < 0 && sh_ && (sv_ & ((1u << sh_) - 1))) ? 1 : 0; }} '
                      f'ctx.xer = (ctx.xer & ~(1u << 29)) | (ca_ << 29); }} // sraw')
changes = []
for p in sorted((PORT / 'build/crash-generated').glob('output_*.cpp')):
    s = p.read_text()
    s, a = sub.subn(f_sub, s)
    s, b = sra.subn(f_sra, s)
    if a or b:
        p.write_text(s); changes.append({'file': p.name, 'subfic': a, 'sraw': b})
report = PORT / 'reports/subfic-sraw-carry-patch.json'
if changes or not report.exists():
    report.write_text(json.dumps({'chunks': changes, 'subfic_sites': sum(x['subfic'] for x in changes),
                                  'sraw_sites': sum(x['sraw'] for x in changes)}, indent=2))
print('subfic/sraw carry staging:', sum(x['subfic'] for x in changes), 'subfic,', sum(x['sraw'] for x in changes), 'sraw sites')
