"""Guarded native kernel for the descriptor-driven GX vertex-attribute emitter func_803143EC.

Why: frame-cost census puts func_803143EC (0x2140, 2128 guest instructions, 26 polls, 53 calls) at the top of the
guest-thread self-time list. It walks a vertex-format descriptor (`lwz r0, 4(r26)` then `rlwinm.` tests on bits
0x1b/0x14/0x13/0x1a/0x11/0x17), emits 3-float attribute groups and packed words to the write-gather pipe at
0xCC008000 (396 of its 523 stores), computes an AABB into the object, and calls the plane tests / sqrt wrapper.

What this stage installs, in the chunk that owns the function (build/crash-hot when the hot split has already moved
it, else build/crash-split, else build/crash-generated):

  * `native_vertex_emit_803143EC` - the register-local kernel. Derived with tools/promote_regs.py from the same
    generated body, with the interior-entry switch dropped (the kernel is only ever entered at the function entry;
    every resume address still runs the untouched original body), the two `bctrl` sites nested through
    `nwii::runtime::native_call_indirect` when the nest predicate allows it, and the write-gather stores rewritten
    onto `native_vertex_emit_wgp_f32/u32`.
  * a guarded dispatch as the first statement of the generated body:
        if (ctx.pc == 0x803143EC && (ctx.msr & 0x2000u) && native_vertex_emit_guard_803143EC(ctx)) {
            native_vertex_emit_803143EC(ctx); return;
        }
    The guard requires gx.cpp's ordinary write-gather mode (g_native_fifo_ok, no WPAR redirect, no GX diagnostics),
    no watch address, no call tracing, and every descriptor/format field the emitter walks before its first FIFO
    byte to be MEM1-resident. On any failure the untouched generated body runs.

The write-gather rewrite is exactly the inline mirror path of MMU::write_f32/write32 (cpu_context.h), which only
differs from GX_WGPIPE_WriteF32/32 when a watch observer may hit the page; the helper re-tests the address and the
watch so any other base or an active watch keeps the original call. No byte is written to guest RAM or to the FIFO
outside the existing GX_WGPIPE_Write* helpers.

gx.cpp gains `native_gx_emitter_ready()` so the environment predicate has one definition (it is `native_wgp_diagnostics()`
plus `g_native_fifo_ok` and `g_wpar_redirect`, all file-local to gx.cpp); cpu_context.h declares it.

Scope: func_803143EC only. func_802FDB74 passes the same mechanical derivation (promote_regs accepts its body, the nest
predicate inlines all 7 of its vtable dispatches, 198 of its stores are write-gather stores), but it is not the same
shape: it is a per-material draw path that takes its object in r4, gets the descriptor from vtable[0x50], keeps 7
virtual dispatches and calls 802A9300/802A94C4/80281A94/803A3480/803A3074, where 803143EC is a descriptor-driven
vertex-format walker (object in r3, 2 virtual dispatches, 53 calls, 78 `mtspr CTR` loops). Qualifying it needs its own
seeded object graph and driver in the oracle, which this stage does not have; installing a kernel the oracle cannot
exercise would break the stage's own rule, so it is left out.

Idempotent (each run removes its own block first, so the kernel is always derived from the pristine body).
Rollback: NWII_BUILD_VERTEX_EMIT=0 or --restore.
"""
from pathlib import Path
import argparse
import os
import re
import sys

MARK = 'NATIVE_VERTEX_EMIT_V1'
TARGET = 0x803143EC
NAME = 'func_%08X' % TARGET
KERNEL = 'native_vertex_emit_%08X' % TARGET
GUARD = 'native_vertex_emit_guard_%08X' % TARGET
NL = '\n'
ROOT = Path(__file__).resolve().parents[1]


def helpers(addr=TARGET):
    """Guard + write-gather store helpers, inserted immediately before the generated body."""
    return '''// %s: guarded write-gather emitter for func_%08X (see tools/vertex_emit_kernel.py).
static inline void native_vertex_emit_wgp_f32(CPUContext& ctx, uint32_t addr, double value) {
    if (__builtin_expect(addr == 0xCC008000u && !nwii::runtime::native_watch_span_may_hit(addr, 4), 1)) {
        NWII_CTX_BARRIER(); nwii::runtime::GX_WGPIPE_WriteF32(float(value)); return;
    }
    ctx.mmu.write_f32(addr, float(value));
}
static inline void native_vertex_emit_wgp_u32(CPUContext& ctx, uint32_t addr, uint32_t value) {
    if (__builtin_expect(addr == 0xCC008000u && !nwii::runtime::native_watch_span_may_hit(addr, 4), 1)) {
        NWII_CTX_BARRIER(); nwii::runtime::GX_WGPIPE_Write32(value); return;
    }
    ctx.mmu.write32(addr, value);
}
// The kernel runs only while the write-gather pipe is in its ordinary mode, nothing observes the writes, and every
// descriptor/format field the emitter walks before its first FIFO byte is MEM1-resident (so no MMIO, watch, redirect
// or HW-register path can run inside it).
static inline bool native_vertex_emit_guard_%08X(CPUContext& ctx) {
    if (!nwii::runtime::native_gx_emitter_ready()) return false;
    // A watch only matters when it can hit an address the kernel writes. The
    // kernel's own write-gather store re-tests the watch for the address it
    // writes (the MMU's own predicate), so a watch elsewhere - the runtime's
    // default watch is the scheduler recorder word at 0x80597790, a MEM1
    // address the kernel never writes - cannot change what the kernel does.
    if (nwii::runtime::native_watch_span_may_hit(0xCC008000u, 4)) return false;
    if (nwii::runtime::g_trace_calls) return false;
    const uint32_t ram = 24u * 1024u * 1024u;
    const uint32_t object = ctx.gpr[3];
    if ((object & 0x3FFFFFFFu) > ram - 0x60u) return false;              // lwz r4, 0x48(r3)
    const uint32_t descriptor = ctx.mmu.read32(object + 0x48);
    if ((descriptor & 0x3FFFFFFFu) > ram - 0x14u) return false;          // lwz r28, 0x10(r4)
    const uint32_t vertices = ctx.mmu.read32(descriptor + 0x10);
    if ((vertices & 0x3FFFFFFFu) > ram - 12u) return false;              // the vertex array
    if ((ctx.gpr[13] & 0x3FFFFFFFu) > ram - 0x2B90u) return false;       // the small-data block
    if (ctx.mmu.read32(ctx.gpr[13] - 0x2B7Cu) > 0xFFFFu) return false;   // triangle count (the index is masked to 16 bits)
    return true;
}
''' % (MARK, addr, addr)


def dispatch(addr=TARGET):
    return ('    if (ctx.pc == 0x%08X && (ctx.msr & 0x2000u) && %s(ctx)) { // %s\n'
            '        %s(ctx);\n'
            '        return;\n'
            '    }\n' % (addr, GUARD, MARK, KERNEL))


def function_span(text, name):
    head = re.search(r'(?:^|\n)[^\n]*\bvoid ' + name + r'\(NWII_CTX_PARAM ctx\) \{', text)
    if not head:
        raise SystemExit('the generated definition of %s was not found' % name)
    a = text.rfind(NL, 0, head.start()) + 1
    b = text.index(NL + '}' + NL, head.end())
    return a, b + 2


def entry_anchor(text, addr=TARGET):
    """The line the dispatch follows: the TRACE_CALL line while the body still begins with it
    (pristine chunk), otherwise the opening brace itself. The owning chunk may be a promoted hot
    unit whose entry was rewritten by NATIVE_GUEST_ENTRY_V1 (guest_entry_specialize), so the
    TRACE_CALL line is not always there; the dispatch is still the first statement either way."""
    head = re.search(r'(?:^|\n)[^\n]*\bvoid ' + NAME + r'\(NWII_CTX_PARAM ctx\) \{', text)
    assert head, 'the generated entry of func_%08X was not found' % addr
    start = text.rfind(NL, 0, head.start()) + 1
    trace = (text[start:head.end()] + NL
             + '    NWII_TRACE_CALL(0x%08X, ctx);' % addr + NL)
    brace = text[start:head.end()] + NL
    return trace if text.count(trace) == 1 else brace


def definition_start(text, name=NAME):
    """Offset of the generated definition line, moved up over a per-function __attribute__ line so
    the inserted block cannot steal `__attribute__((optimize("O2")))` from the body."""
    head = re.search(r'(?:^|\n)[^\n]*\bvoid ' + name + r'\(NWII_CTX_PARAM ctx\) \{', text)
    assert head, 'the generated definition of %s was not found' % name
    i = text.rfind(NL, 0, head.start()) + 1
    while True:
        prev = text.rfind(NL, 0, i - 1)
        if prev < 0:
            return i
        if text[prev + 1:i - 1].startswith('__attribute__'):
            i = prev + 1
        else:
            return i


DISPATCH = re.compile(
    r'^    if \(ctx\.pc == 0x%08X && \(ctx\.msr & 0x2000u\) && %s\(ctx\)\) \{ // %s\n'
    r'        %s\(ctx\);\n        return;\n    \}\n' % (TARGET, GUARD, MARK, KERNEL), re.M)


def uninstall(text):
    """Remove this stage's block and dispatch line; returns (text, found)."""
    found = False
    if MARK in text:
        found = True
        a = text.index('// ' + MARK + ': guarded write-gather emitter for ')
        b = definition_start(text)
        if b < a:   # a block written before the attribute-aware ordering sits inside the body
            b = text.index(NL + 'void ' + NAME + '(NWII_CTX_PARAM ctx) {', a) + 1
        text = text[:a] + text[b:]
    text, n = DISPATCH.subn('', text)
    found = found or n > 0
    return text, found


def specialise_wgp(kernel):
    """Rewrite the promoted write-gather stores onto the mirror-page helper (byte-identical)."""
    counts = [0, 0]

    def f32(m):
        counts[0] += 1
        return 'native_vertex_emit_wgp_f32(ctx, %s, %s); // %s' % (m.group(1), m.group(2), m.group(3))

    def u32(m):
        counts[1] += 1
        return 'native_vertex_emit_wgp_u32(ctx, %s, %s); // %s' % (m.group(1), m.group(2), m.group(3))

    kernel = re.sub(r'ctx\.mmu\.write_f32\(([^;]*?), ([^;]*?)\); // (stfs [^\n]*?-32768\(r\d+\))', f32, kernel)
    kernel = re.sub(r'ctx\.mmu\.write32\(([^;]*?), ([^;]*?)\); // (stw [^\n]*?-32768\(r\d+\))', u32, kernel)
    return kernel, counts


def strip_entry_switch(body, addr=TARGET):
    """Drop the `if (ctx.pc != ADDR) { switch (ctx.pc) { ... } }` preamble: the kernel is entry-only."""
    head = '    if (ctx.pc != 0x%08X) {' % addr
    a = body.index(head)
    depth = 0
    j = body.index('{', a)
    while True:
        if body[j] == '{':
            depth += 1
        elif body[j] == '}':
            depth -= 1
            if depth == 0:
                break
        j += 1
    return body[:a] + body[j + 1:].lstrip(NL)


def build_kernel(body, nest):
    """Register-local, entry-only, write-gather-specialised kernel for one generated body."""
    sys.path.insert(0, str(ROOT / 'tools'))
    import promote_regs as P
    kernel, stats = P.promote(strip_entry_switch(body), True, True, nest)
    assert kernel, 'promote_regs refused func_%08X: %s' % (TARGET, stats)
    kernel = kernel.replace('void ' + NAME + '(', 'void ' + KERNEL + '(', 1)
    kernel, counts = specialise_wgp(kernel)
    return kernel, stats, counts


def nest_predicate():
    sys.path.insert(0, str(ROOT / 'tools'))
    from fix_hot_split import make_nest_predicate
    return make_nest_predicate()


def install(text, body, nest=None):
    """Return (staged_text, kernel_text, stats, counts) for the target file `text`; `body` is the pristine generated body."""
    if nest is None:
        nest = nest_predicate()
    pristine, _ = uninstall(text)
    kernel, stats, counts = build_kernel(body, nest)
    anchor = entry_anchor(pristine)
    assert pristine.count(anchor) == 1, 'generated entry anchor not unique'
    staged = pristine.replace(anchor, anchor + dispatch(), 1)
    kbody = kernel.split('{', 1)[1].rstrip(NL)
    assert kbody.endswith('}'), 'kernel body is not a closed function'
    kbody = kbody[:-1].rstrip(NL)
    assert kbody.count('{') == kbody.count('}'), 'kernel body braces are unbalanced'
    start = definition_start(staged)
    staged = (staged[:start] + helpers()
              + 'static void ' + KERNEL + '(NWII_CTX_PARAM ctx) {\n'
              + kbody + NL + '}\n' + NL + staged[start:])
    return staged, kernel, stats, counts


# Explicit roots for the check driver. The staged tree and the runtime source
# tree may live outside the repository (an isolated copy of a completed build),
# so the driver can point the stage at them instead of the repository's own
# build/ junctions.
GENERATED_DIR = None
RUNTIME_DIR = None


def generated_paths(root):
    """The generated chunks to search, in production order (hot, split, pristine)."""
    if GENERATED_DIR is not None:
        return sorted(Path(GENERATED_DIR).glob('*.cpp'))
    out = []
    for pattern in ('build/crash-hot/hot_*.cpp', 'build/crash-split/output_*.cpp',
                    'build/crash-generated/output_*.cpp'):
        out.extend(sorted(root.glob(pattern)))
    return out


def pristine_paths(root):
    """The pristine chunks only (crash-generated is never edited by the hot split)."""
    if GENERATED_DIR is not None:
        return sorted(Path(GENERATED_DIR).glob('*.cpp'))
    return sorted(root.glob('build/crash-generated/output_*.cpp'))


def chunk_path(root, patterns=None):
    """The file that currently owns the generated function: hot unit, split chunk, then pristine chunk."""
    for path in generated_paths(root):
        if re.search(r'void ' + NAME + r'\(NWII_CTX_PARAM ctx\) \{',
                     path.read_text(errors='surrogateescape')):
            return path
    raise SystemExit('func_%08X not found in any staged chunk' % TARGET)


def source_body(root):
    """The pristine generated body (crash-generated is never edited by the hot split), so the kernel does not depend on it."""
    for path in pristine_paths(root):
        text, _ = uninstall(path.read_text(errors='surrogateescape'))
        m = re.search(r'(?:^|\n)[^\n]*\bvoid ' + NAME + r'\(NWII_CTX_PARAM ctx\) \{', text)
        if not m:
            continue
        a = m.start() + (1 if text[m.start()] == NL else 0)
        b = text.index(NL + '}' + NL, a)
        return text[a:b + 2]
    raise SystemExit('pristine generated body for func_%08X not found' % TARGET)


def apply_runtime(root):
    """Single-source the write-gather environment predicate (gx.cpp) and declare it (cpu_context.h)."""
    src = Path(RUNTIME_DIR) if RUNTIME_DIR is not None else root / 'build/nwii-full-source/nWiiRuntime'
    gx = src / 'src/hle/gx.cpp'
    s = gx.read_text(errors='surrogateescape')
    if MARK not in s:
        anchor = 'void GX_WGPIPE_Write8(uint8_t val) {'
        assert s.count(anchor) == 1, 'GX_WGPIPE_Write8 anchor not unique'
        s = s.replace(anchor, '// ' + MARK + ': single-source the write-gather environment predicate for guarded emitters.\n'
                      'bool native_gx_emitter_ready() {\n'
                      '    return g_native_fifo_ok && !g_wpar_redirect && !native_wgp_diagnostics();\n'
                      '}\n\n' + anchor, 1)
        gx.write_text(s, errors='surrogateescape')
    header = src / 'include/runtime/cpu_context.h'
    h = header.read_text(errors='surrogateescape')
    if MARK not in h:
        anchor = 'void GX_WGPIPE_WriteF64(double val);\n'
        assert h.count(anchor) == 1, 'GX_WGPIPE_WriteF64 declaration anchor not unique'
        h = h.replace(anchor, anchor + 'bool native_gx_emitter_ready(); // ' + MARK + ': guarded write-gather emitters\n', 1)
        header.write_text(h, errors='surrogateescape')


def restore_runtime(root):
    src = Path(RUNTIME_DIR) if RUNTIME_DIR is not None else root / 'build/nwii-full-source/nWiiRuntime'
    gx = src / 'src/hle/gx.cpp'
    if not gx.exists():
        return
    s = gx.read_text(errors='surrogateescape')
    if MARK in s:
        a = s.index('// ' + MARK + ': single-source')
        gx.write_text(s[:a] + s[s.index('void GX_WGPIPE_Write8(uint8_t val) {', a):], errors='surrogateescape')
    header = src / 'include/runtime/cpu_context.h'
    if not header.exists():
        return
    h = header.read_text(errors='surrogateescape')
    line = 'bool native_gx_emitter_ready(); // ' + MARK + ': guarded write-gather emitters\n'
    if line in h:
        header.write_text(h.replace(line, '', 1), errors='surrogateescape')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--restore', action='store_true')
    args = ap.parse_args()
    root = args.root.resolve()
    enabled = os.environ.get('NWII_BUILD_VERTEX_EMIT', '1') != '0'
    if args.restore or not enabled:
        # A fresh storage stages into an empty tree; the restore phase runs before the
        # generator produces the chunks, so there may be nothing to restore yet.
        try:
            path = chunk_path(root)
        except SystemExit:
            restore_runtime(root)
            print('Vertex emit kernel: no generated tree to restore')
            return
        text = path.read_text(errors='surrogateescape')
        text, found = uninstall(text)
        if found:
            path.write_text(text, errors='surrogateescape')
        restore_runtime(root)
        print('Vertex emit kernel removed (%s).' % path.name)
        return
    path = chunk_path(root)
    text = path.read_text(errors='surrogateescape')
    staged, kernel, stats, counts = install(text, source_body(root))
    apply_runtime(root)
    if staged != text:
        path.write_text(staged, errors='surrogateescape')
    assert staged.count('void ' + KERNEL + '(') == 1
    assert staged.count('// ' + MARK + ': guarded write-gather emitter') == 1
    assert staged.count('        ' + KERNEL + '(ctx);') == 1, 'kernel dispatch is not unique'
    print('Vertex emit kernel installed in %s: %d kernel lines (%d polls, %d barriers, '
          '%d promoted registers, %d nested indirect calls, %d write-gather stores specialised)'
          % (path.name, kernel.count(NL), stats.get('polls'), stats.get('barriers'),
             stats.get('promoted'), stats.get('nested'), counts[0] + counts[1]))


if __name__ == '__main__':
    main()
