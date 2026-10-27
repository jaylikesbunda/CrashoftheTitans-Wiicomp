"""Implement the Gekko write-gather-pipe address register (WPAR, SPR 921) redirect.

GXRedirectWriteGatherPipe/GXRestoreWriteGatherPipe point WPAR at a RAM buffer so that stores to the gather pipe
(0xCC008000) are collected in 32-byte chunks and written to that buffer instead of the GX FIFO. Radical's CPU skinning
uses it to write skinned positions/normals into vertex arrays. The emitter treated `mtspr 921` as unhandled and
`mfspr 921` as 0, so the skinned vertex data was fed to the GX command parser: it lost sync, the draw-done BP never
parsed, GXDrawDone never completed and loading stalled with the frame counter frozen.

Model: bytes written to the pipe while WPAR != 0x0C008000 are stored at the WPAR address (physical) and the address
advances; BNE (bit 0 of mfspr WPAR) reports a partially filled 32-byte chunk so the SDK's zero-padding loop works."""
from pathlib import Path
import json, re
PORT = Path(__file__).resolve().parents[1]
SRC = PORT / 'build/nwii-full-source'


def load(path):
    s = path.read_bytes().decode('utf-8')
    nl = '\r\n' if '\r\n' in s else '\n'
    return s.replace('\r\n', '\n'), nl


def save(path, t, nl):
    path.write_bytes(t.replace('\n', nl).encode('utf-8'))


def replace_once(path, old, new, marker):
    t, nl = load(path)
    if marker in t:
        return 'already staged'
    if old not in t:
        raise SystemExit(f'{path.name}: context changed: {old[:60]!r}')
    save(path, t.replace(old, new, 1), nl)
    return 'staged'


# 1. declarations
print('cpu_context.h:', replace_once(
    SRC / 'nWiiRuntime/include/runtime/cpu_context.h',
    'bool handle_syscall(CPUContext &ctx);',
    'bool handle_syscall(CPUContext &ctx);\nuint32_t native_wpar_read();\nvoid native_wpar_write(uint32_t value);',
    'native_wpar_read'))

# 2. emitter
rec = SRC / 'nWiiRecomp/src/recompiler.cpp'
BS = chr(92)
print('recompiler mfspr:', replace_once(
    rec,
    '    } else if (spr >= 920 && spr <= 924) {\n      out << "    ctx.gpr[" << rD << "] = 0; // mfspr HID " << spr << "' + BS + 'n";',
    '    } else if (spr == 921) {\n      out << "    ctx.gpr[" << rD << "] = nwii::runtime::native_wpar_read(); // mfspr WPAR' + BS + 'n";\n'
    '    } else if (spr >= 920 && spr <= 924) {\n      out << "    ctx.gpr[" << rD << "] = 0; // mfspr HID " << spr << "' + BS + 'n";',
    'native_wpar_read'))
print('recompiler mtspr:', replace_once(
    rec,
    '    } else if (spr == 1) {\n      out << "    ctx.xer = ctx.gpr[" << rS << "]; // mtxer r" << rS << "' + BS + 'n";\n'
    '    } else {\n      out << "    // unhandled mtspr " << spr',
    '    } else if (spr == 1) {\n      out << "    ctx.xer = ctx.gpr[" << rS << "]; // mtxer r" << rS << "' + BS + 'n";\n'
    '    } else if (spr == 921) {\n      out << "    nwii::runtime::native_wpar_write(ctx.gpr[" << rS << "]); // mtspr WPAR' + BS + 'n";\n'
    '    } else {\n      out << "    // unhandled mtspr " << spr',
    'native_wpar_write'))

# 3. interpreter
interp = SRC / 'nWiiRuntime/src/hle/interpreter.cpp'
print('interpreter mfspr:', replace_once(
    interp,
    '            else if (spr >= 912 && spr <= 919) ctx.gpr[rD] = ctx.gqr[spr - 912];\n            else ctx.gpr[rD] = 0;',
    '            else if (spr >= 912 && spr <= 919) ctx.gpr[rD] = ctx.gqr[spr - 912];\n            else if (spr == 921) ctx.gpr[rD] = native_wpar_read();\n            else ctx.gpr[rD] = 0;',
    'native_wpar_read'))
print('interpreter mtspr:', replace_once(
    interp,
    '            else if (spr >= 912 && spr <= 919) ctx.gqr[spr - 912] = ctx.gpr[rD];\n            break;',
    '            else if (spr >= 912 && spr <= 919) ctx.gqr[spr - 912] = ctx.gpr[rD];\n            else if (spr == 921) native_wpar_write(ctx.gpr[rD]);\n            break;',
    'native_wpar_write'))

# 4. gather pipe: state + redirect hook
gx = SRC / 'nWiiRuntime/src/hle/gx.cpp'
state = '''
// Write-gather pipe address register (WPAR, SPR 921) redirect; see tools/fix_wpar_gather_redirect.py.
static uint32_t g_wpar_cur = 0x0C008000u;
static unsigned g_wpar_fill = 0;
static bool g_wpar_redirect = false;
uint32_t native_wpar_read() {
    return ((g_wpar_redirect ? g_wpar_cur : 0x0C008000u) & 0xFFFFFFE0u) | (g_wpar_fill ? 1u : 0u);
}
void native_wpar_write(uint32_t value) {
    const uint32_t a = value & 0xFFFFFFE0u;
    g_wpar_cur = a;
    g_wpar_fill = 0;
    g_wpar_redirect = (a & 0x3FFFFFFFu) != 0x0C008000u && (a & 0x3FFFFFFFu) != 0u;
}
static bool wpar_redirect_byte(uint8_t b) {
    if (!g_wpar_redirect || !nwii::runtime::g_mmu) return false;
    const uint32_t phys = g_wpar_cur & 0x3FFFFFFFu;
    uint32_t virt = 0;
    if (phys < 0x01800000u) virt = 0x80000000u | phys;
    else if (phys >= 0x10000000u && phys < 0x14000000u) virt = 0x90000000u | (phys & 0x03FFFFFFu);
    else return true; // outside RAM: swallow like the bus would
    nwii::runtime::g_mmu->write8(virt, b);
    ++g_wpar_cur;
    g_wpar_fill = (g_wpar_fill + 1) & 31u;
    return true;
}
'''
t, nl = load(gx)
if 'g_wpar_redirect' in t:
    print('gx.cpp: already staged')
else:
    anchor = 'static bool wgp_redirect(uint8_t b) {'
    if anchor not in t:
        raise SystemExit('gx.cpp: wgp_redirect anchor missing')
    t = t.replace(anchor, state + anchor + '\n    if (wpar_redirect_byte(b)) return true;', 1)
    save(gx, t, nl)
    print('gx.cpp: staged')

# 5. generated code
mf = re.compile(r'ctx\.gpr\[(\d+)\] = 0; // mfspr HID 921\n')
mt = re.compile(r'(    // 0x[0-9A-F]{8}: ([0-9A-F]{8})\n)    // unhandled mtspr 921\n')


def fn_mt(m):
    rs = (int(m[2], 16) >> 21) & 31
    return m[1] + f'    nwii::runtime::native_wpar_write(ctx.gpr[{rs}]); // mtspr WPAR\n'


changes = []
for q in sorted((PORT / 'build/crash-generated').glob('output_*.cpp')):
    text = q.read_text()
    t1, a = mf.subn(lambda m: f'ctx.gpr[{m[1]}] = nwii::runtime::native_wpar_read(); // mfspr WPAR\n', text)
    t2, b = mt.subn(fn_mt, t1)
    if a or b:
        q.write_text(t2)
        changes.append({'file': q.name, 'mfspr': a, 'mtspr': b})
rep = PORT / 'reports/wpar-redirect-patch.json'
if changes or not rep.exists():
    rep.write_text(json.dumps({'chunks': changes, 'mfspr_sites': sum(x['mfspr'] for x in changes),
                               'mtspr_sites': sum(x['mtspr'] for x in changes)}, indent=2))
print('WPAR generated-code staging:', sum(x['mfspr'] for x in changes), 'mfspr,', sum(x['mtspr'] for x in changes), 'mtspr sites')
