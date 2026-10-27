"""Renderer-owned GX state: RendererGL applies BP/XF writes to RenderState(), not to the parser's g_state.

Before this stage the parser (FifoParser::Parse, run on the drain thread) and RendererGL::Render shared one GXState.
Parse writes only the raw BP words and the CP/VAT/array/default-matrix fields. Everything derived from BP and XF
writes (TEV, texture stages, z/alpha modes, EFB copy, TLUT, XFB latch, XF words and matrix banks, projection) is
applied by Render in command order, and Render overwrites g_state.bp with its BP shadow at every batch start
(NativeRestoreBpSnapshot). That derived state moves into RenderState(), a GXState owned by the renderer.

The parser keeps the parse-owned fields in a ParseSnapshot captured right after Parse(); RenderStateBegin() applies
it at the start of the batch. So Parse() of the next drain cannot change what Render() of this drain observes.
Parse-side readers of renderer-written words use the parser's own copies: the display-list cache key (DLStateHash)
reads the XF words the renderer will have applied after the previous drain (g_parse_xf_committed, committed at the
start of Parse and advanced by walking the drain's XF records in command order). Savestates still serialise one
GXState: RenderStateExportToParse() / RenderStateImportFromParse() keep it equal to the split state, so the on-disk
layout is unchanged. VI/main-thread readers (GX_GetXfb, GX_GetClearColor, the F-key dump) read RenderState().

Overlay files: runtime-overlays/render_state.h and render_state.cpp (copied to include/runtime/gx and src/hle/gx).
Idempotent (marker NATIVE_RENDERER_STATE_SPLIT_V1); --restore reverses every edit.
tools/check_renderer_state_split.py is the differential harness (old and split paths must give identical
renderer-visible state at every draw and at the end of every drain).
"""
import re
import shutil
import sys
from pathlib import Path

p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime'
overlays = p / 'runtime-overlays'
MARK = 'NATIVE_RENDERER_STATE_SPLIT_V1'
RESTORE = '--restore' in sys.argv


def read(path):
    with open(path, 'r', encoding='utf-8', errors='surrogateescape', newline='') as f:
        return f.read()


def write(path, text):
    with open(path, 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
        f.write(text)


def crlf(text, s):
    # Runtime sources are CRLF; patterns are written with LF and converted to match the file.
    return s.replace('\n', '\r\n') if '\r\n' in text else s


def swap(path, old, new):
    """Replace the single occurrence of old with new. Restore replaces new with old."""
    text = read(path)
    o, n = crlf(text, old), crlf(text, new)
    if RESTORE:
        if n in text:
            assert text.count(n) == 1, (path.name, n[:70], text.count(n))
            write(path, text.replace(n, o, 1))
        return
    if n in text:
        return  # already applied
    assert text.count(o) == 1, (path.name, o[:80], text.count(o))
    write(path, text.replace(o, n, 1))


def rename(path, pattern, repl, original):
    """Regex rename of bare identifiers. Restore replaces repl with the original text (exact for this file)."""
    text = read(path)
    if RESTORE:
        if repl in text:
            write(path, text.replace(repl, original))
        return
    new, count = re.subn(pattern, repl, text)
    if count == 0:
        return  # already applied
    write(path, new)


def copy_overlays():
    targets = [('render_state.h', rt / 'include/runtime/gx/render_state.h'),
               ('render_state.cpp', rt / 'src/hle/gx/render_state.cpp')]
    for name, dest in targets:
        if RESTORE:
            if dest.exists():
                dest.unlink()
            continue
        assert (overlays / name).exists(), name
        shutil.copyfile(overlays / name, dest)


# 0. overlay sources and the build list
copy_overlays()
swap(rt / 'CMakeLists.txt',
     '    src/hle/gx/fifo_parser.cpp\n',
     '    src/hle/gx/fifo_parser.cpp\n    src/hle/gx/render_state.cpp  # ' + MARK + '\n')

# 1. parser: the BP/XF apply code is parameterised on the target state; the parser keeps its own XF and CP copies
FP = rt / 'src/hle/gx/fifo_parser.cpp'
swap(FP,
     '#include "runtime/gx/fifo_parser.h"\n',
     '#include "runtime/gx/fifo_parser.h"\n#include "runtime/gx/render_state.h" // ' + MARK + '\n')
swap(FP,
     '    void ApplyBPRegisterImpl(uint8_t reg, uint32_t val);\n',
     '''    // NATIVE_RENDERER_STATE_SPLIT_V1: XF words the renderer will have applied after the previous drain (display-list cache keys).
    uint32_t g_parse_xf_pending[256] = {};
    uint32_t g_parse_xf_committed[256] = {};
    // BP words as RendererGL resolves them (shadow + one-shot 0xFE mask). The renderer overwrites g_state.bp with its
    // shadow at each batch start, so the parser starts each drain from the shadow the previous drain left behind.
    uint32_t g_parse_bp_shadow[256] = {};
    uint32_t g_parse_bp_mask = 0xffffff;
    // Walks the drain's records in command order, exactly as RendererGL applies them: XF words in the XF address
    // range, and BP words through the shadow/mask resolution (same rule as NativeBpRegisterValue).
    void NativeRendererWalk(const CommandStream& commands) {
        for (const auto& e : commands.entries) {
            if (e.kind == (uint8_t)GXCommandType::BPRegister) {
                const uint8_t reg = uint8_t(e.reg);
                const uint32_t resolved = ((g_parse_bp_shadow[reg] & ~g_parse_bp_mask) | (e.val & g_parse_bp_mask)) & 0xffffff;
                g_parse_bp_shadow[reg] = resolved;
                g_parse_bp_mask = reg == 0xfe ? resolved : 0xffffff;
                continue;
            }
            if (e.kind != (uint8_t)GXCommandType::XFRegister || e.heavy == StreamEntry::kNoHeavy) continue;
            const GXCommand& c = commands.heavy[e.heavy];
            for (size_t i = 0; i < c.payload.size(); ++i) {
                const uint32_t a = (uint32_t)c.reg + (uint32_t)i;
                if (a >= 0x1000 && a < 0x1100) std::memcpy(&g_parse_xf_pending[a - 0x1000], &c.payload[i], 4);
            }
        }
    }
    void ApplyBPRegisterImpl(GXState& g_state, uint8_t reg, uint32_t val);
''')
swap(FP,
     '    void ApplyBPRegisterImpl(uint8_t reg, uint32_t val) {\n        g_state.bp[reg] = val;',
     '    void ApplyBPRegisterImpl(GXState& g_state, uint8_t reg, uint32_t val) {\n        g_state.bp[reg] = val;')
swap(FP,
     'void ApplyBPRegister(uint8_t reg, uint32_t val) { ApplyBPRegisterImpl(reg, val); }',
     '''void ApplyBPRegister(uint8_t reg, uint32_t val) { ApplyBPRegisterImpl(g_state, reg, val); }
// NATIVE_RENDERER_STATE_SPLIT_V1: the renderer applies BP writes to RenderState(); the parser's g_state keeps raw BP words only.
void ApplyBPRegisterTo(GXState& st, uint8_t reg, uint32_t val) { ApplyBPRegisterImpl(st, reg, val); }
const uint32_t* NativeParseXfCommitted() { return g_parse_xf_committed; }
void NativeTakeParseSnapshot(ParseSnapshot& out) {
    std::memcpy(out.cp, g_state.cp, sizeof(out.cp));
    std::memcpy(out.vat, g_state.vat, sizeof(out.vat));
    std::memcpy(out.arrayBase, g_state.arrayBase, sizeof(out.arrayBase));
    std::memcpy(out.arrayStride, g_state.arrayStride, sizeof(out.arrayStride));
    out.defPosMtxIdx = g_state.defPosMtxIdx;
    std::memcpy(out.defTexMtxIdx, g_state.defTexMtxIdx, sizeof(out.defTexMtxIdx));
}''')
swap(FP,
     '    h ^= g_state.xf[0x00E]; h *= 1099511628211ull;',
     '    h ^= g_parse_xf_committed[0x00E]; h *= 1099511628211ull;')
swap(FP,
     '    h ^= g_state.xf[0x00F]; h *= 1099511628211ull;',
     '    h ^= g_parse_xf_committed[0x00F]; h *= 1099511628211ull;')
swap(FP,
     '    size_t offset = 0;\n    ParseStream(fifo, offset, commands, 0);',
     '    std::memcpy(g_parse_xf_committed, g_parse_xf_pending, sizeof(g_parse_xf_committed)); // ' + MARK + '\n'
     '    std::memcpy(g_state.bp, g_parse_bp_shadow, sizeof(g_state.bp));\n'
     '    size_t offset = 0;\n    ParseStream(fifo, offset, commands, 0);\n'
     '    NativeRendererWalk(commands); // ' + MARK)

# 2. drain: snapshot the parse-owned fields right after Parse, for the Render that consumes this drain
GX = rt / 'src/hle/gx.cpp'
swap(GX,
     '#include "runtime/native_drain_signal.h"\n',
     '#include "runtime/native_drain_signal.h"\n#include "runtime/gx/render_state.h" // ' + MARK + '\n')
swap(GX,
     'nwii::runtime::gx::FifoParser::Parse(s_parse_buf, commands); }',
     'nwii::runtime::gx::FifoParser::Parse(s_parse_buf, commands); '
     'nwii::runtime::gx::NativeTakeParseSnapshot(nwii::runtime::gx::RenderBatchSnapshot()); } // ' + MARK)
# GX_GetClearColor, GX_GetXfb and the F-key diagnostics read the renderer's state
rename(GX, r'(?<![\w:])g_state\b', 'nwii::runtime::gx::RenderState()', 'g_state')
# savestates: the on-disk GXState is the split state; export before writing, import after reading
swap(GX,
     '    s.pod(gx::g_state);if(s.loading)native_tlut::Changed();',
     '    if(!s.loading) nwii::runtime::gx::RenderStateExportToParse(); // ' + MARK + '\n'
     '    s.pod(gx::g_state);if(s.loading){nwii::runtime::gx::RenderStateImportFromParse();native_tlut::Changed();}')

# 3. renderer: every g_state access becomes RenderState(); BP writes go through ApplyBPRegisterTo
RG = rt / 'src/hle/gx/renderer_gl.cpp'
swap(RG,
     '#include "runtime/gx/fifo_parser.h"\n',
     '#include "runtime/gx/fifo_parser.h"\n#include "runtime/gx/render_state.h" // ' + MARK + '\n')
swap(RG,
     '        } else ApplyBPRegister(native_reg, bp_value);',
     '        } else ApplyBPRegisterTo(RenderState(), native_reg, bp_value); // ' + MARK)
swap(RG,
     '    NativeRestoreBpSnapshot();\n',
     '    RenderStateBegin(RenderBatchSnapshot()); // ' + MARK + ': parse-owned view of this batch\n'
     '    NativeRestoreBpSnapshot();\n')
rename(RG, r'gx::g_state\b', 'gx::RenderState()', 'gx::g_state')
rename(RG, r'(?<![\w:])g_state\b', 'RenderState()', 'g_state')

# 4. texture cache reads the renderer's state at Render time
TC = rt / 'src/hle/texture_cache_gl.cpp'
swap(TC,
     '#include "runtime/native_prompt_slots.h"\n',
     '#include "runtime/native_prompt_slots.h"\n#include "runtime/gx/render_state.h" // ' + MARK + '\n')
swap(TC, 'using gx::g_state;', 'using gx::RenderState;')
rename(TC, r'gx::g_state\b', 'gx::RenderState()', 'gx::g_state')
rename(TC, r'(?<![\w:])g_state\b', 'RenderState()', 'g_state')

# 5. debug dump (F-keys) reads the renderer's state
MAIN = rt / 'src/core/main.cpp'
first_line = read(MAIN).split('\n', 1)[0].rstrip('\r')
swap(MAIN,
     first_line + '\n',
     first_line + '\n#include "runtime/gx/render_state.h" // ' + MARK + '\n')
rename(MAIN, r'nwii::runtime::gx::g_state\b', 'nwii::runtime::gx::RenderState()', 'nwii::runtime::gx::g_state')
