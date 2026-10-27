"""NATIVE_HOT_DIAG_GATES_V1 -- stop paying for opt-in diagnostics on the hot path.

R1: the per-draw g_draw_ring store is read only under NWII_GX_DESYNC_PROBE.
R2: NativeEfbProbe built its output prefix string before its "probe disabled"
    early-out; hoist the !prefix test.
R8: the guest clears the idle-attention latch unconditionally every poll; only
    clear it when it is actually set (avoids a bouncing cache-line store).
Idempotent, marker based; no guest/GL behaviour change.
"""
from pathlib import Path

MARK = 'NATIVE_HOT_DIAG_GATES_V1'
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime'


def patch(rel, edits):
    f = rt / rel
    s = f.read_text()
    changed = False
    for marker, old, new in edits:
        if marker in s:
            continue
        assert s.count(old) == 1, (rel, marker, s.count(old))
        s = s.replace(old, new, 1)
        changed = True
    if changed:
        f.write_text(s)


# R8 -- guest idle-attention latch.
patch('src/hle/ios.cpp', [
    (MARK + '_R8',
     '  native_idle::attention.store(0,std::memory_order_relaxed);',
     '  if(native_idle::attention.load(std::memory_order_relaxed))native_idle::attention.store(0,std::memory_order_relaxed); // ' + MARK + '_R8'),
])

# R2 -- EFB probe early-out before building its prefix string.
patch('src/hle/gx/renderer_gl.cpp', [
    (MARK + '_R2',
     '  static const char *prefix = std::getenv("NWII_EFB_PROBE");\n  static unsigned count = 0;',
     '  static const char *prefix = std::getenv("NWII_EFB_PROBE");\n'
     '  if (!prefix) return; // ' + MARK + '_R2\n  static unsigned count = 0;'),
])

# R1 -- per-draw desync ring store only when the desync probe is on.
patch('src/hle/gx/fifo_parser.cpp', [
    (MARK + '_R1',
     '            g_draw_ring[g_draw_ring_n++ % 32] = DrawLogEntry{cmd, vtx_count, (uint32_t)vsize, vat_idx, (int)vat.posMask, (int)vat.posType,\n'
     '                (int)vat.posElements, (int)vat.nrmMask, (int)vat.nrmElements, (int)vat.clrMask[0], (int)vat.texMask[0], fifo_size - offset};',
     '            { static const bool s_draw_ring_on=std::getenv("NWII_GX_DESYNC_PROBE")!=nullptr; // ' + MARK + '_R1\n'
     '              if(s_draw_ring_on) g_draw_ring[g_draw_ring_n++ % 32] = DrawLogEntry{cmd, vtx_count, (uint32_t)vsize, vat_idx, (int)vat.posMask, (int)vat.posType,\n'
     '                (int)vat.posElements, (int)vat.nrmMask, (int)vat.nrmElements, (int)vat.clrMask[0], (int)vat.texMask[0], fifo_size - offset}; }'),
])
print('Hot-path diagnostics gated: draw ring, EFB probe prefix, idle-attention latch.')
