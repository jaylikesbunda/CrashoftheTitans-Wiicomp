"""Cache the 'ordinary GX FIFO' decision so a write-gather store costs one flag test instead of re-deriving it every call.

Profile 154936: GX_WGPIPE_Write32/WriteF32/Write8 are ~0.7 ms/frame on the guest thread; each call re-evaluated native_ordinary_fifo_fast()
(thread-safe static guard, g_wpar_redirect, g_mmu, pi_fifo_base/end, g_cp_fifo_base). g_native_fifo_ok is true only after that predicate was
observed true, and every writer of its inputs (WPAR write, PI FIFO base/end, CP FIFO base) clears it, so the next write re-derives it.
The fast branch stores exactly what native_wgp_bulk stores (same condition first+bytes<4096, same byte order); everything else falls through
to the unchanged code. Idempotent; each part re-applies independently.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime/src'
MARK = 'NATIVE_FIFO_OK_FLAG_V1'


def apply(path, old, new, probe):
    s = path.read_text(errors='surrogateescape')
    if probe in s:
        return
    assert s.count(old) == 1, (path.name, old[:70], s.count(old))
    path.write_text(s.replace(old, new, 1), errors='surrogateescape')


gx = rt / 'hle/gx.cpp'
apply(gx, 'static NativeCpuFifo g_cpu_fifo;',
      'static NativeCpuFifo g_cpu_fifo;\nbool g_native_fifo_ok = false; // ' + MARK + ': true only while native_ordinary_fifo_fast() is known true; writers of its inputs clear it',
      'bool g_native_fifo_ok')
apply(gx, '    g_wpar_redirect = (a & 0x3FFFFFFFu) != 0x0C008000u && (a & 0x3FFFFFFFu) != 0u;\n}',
      '    g_wpar_redirect = (a & 0x3FFFFFFFu) != 0x0C008000u && (a & 0x3FFFFFFFu) != 0u;\n    g_native_fifo_ok = false; // ' + MARK + '\n}',
      'g_wpar_redirect = (a & 0x3FFFFFFFu) != 0x0C008000u && (a & 0x3FFFFFFFu) != 0u;\n    g_native_fifo_ok')
apply(gx, 'void GX_WGPIPE_Write8(uint8_t val) {\n',
      'void GX_WGPIPE_Write8(uint8_t val) {\n'
      '    if (__builtin_expect(g_native_fifo_ok, 1)) { // ' + MARK + '\n'
      '        const size_t at = g_cpu_fifo.n;\n'
      '        if (at + 1 < 4096) { g_cpu_fifo.data[at] = val; g_cpu_fifo.n = at + 1; return; }\n'
      '    } else if (native_ordinary_fifo_fast()) g_native_fifo_ok = true;\n',
      'void GX_WGPIPE_Write8(uint8_t val) {\n    if (__builtin_expect(g_native_fifo_ok, 1))',
      )
apply(gx, 'void GX_WGPIPE_Write32(uint32_t val) {\n',
      'void GX_WGPIPE_Write32(uint32_t val) {\n'
      '    if (__builtin_expect(g_native_fifo_ok, 1)) { // ' + MARK + '\n'
      '        const size_t at = g_cpu_fifo.n;\n'
      '        if (at + 4 < 4096) { const uint32_t be = __builtin_bswap32(val); std::memcpy(g_cpu_fifo.data + at, &be, 4); g_cpu_fifo.n = at + 4; return; }\n'
      '    } else if (native_ordinary_fifo_fast()) g_native_fifo_ok = true;\n',
      'void GX_WGPIPE_Write32(uint32_t val) {\n    if (__builtin_expect(g_native_fifo_ok, 1))')

pi = rt / 'hw/hw_pi.cpp'
apply(pi, 'namespace nwii::runtime::hw {',
      'namespace nwii::runtime { extern bool g_native_fifo_ok; } // ' + MARK + '\nnamespace nwii::runtime::hw {', 'extern bool g_native_fifo_ok')
apply(pi, '            if (addr == 0xCC00300C) pi_fifo_base = val;\n            if (addr == 0xCC003010) pi_fifo_end = val;',
      '            if (addr == 0xCC00300C) { pi_fifo_base = val; nwii::runtime::g_native_fifo_ok = false; }\n'
      '            if (addr == 0xCC003010) { pi_fifo_end = val; nwii::runtime::g_native_fifo_ok = false; }',
      'pi_fifo_base = val; nwii::runtime::g_native_fifo_ok')
cp = rt / 'hw/hw_cp.cpp'
apply(cp, 'namespace nwii::runtime::hw {',
      'namespace nwii::runtime { extern bool g_native_fifo_ok; } // ' + MARK + '\nnamespace nwii::runtime::hw {', 'extern bool g_native_fifo_ok')
apply(cp, '{ write_reg32(addr, val, g_cp_fifo_base); }',
      '{ write_reg32(addr, val, g_cp_fifo_base); nwii::runtime::g_native_fifo_ok = false; }',
      'g_cp_fifo_base); nwii::runtime::g_native_fifo_ok')
print('fifo ok flag installed')
