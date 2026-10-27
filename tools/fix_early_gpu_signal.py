"""Signal PE token / finish interrupts when a drain's draws have been parsed and decoded, not after they have been submitted to OpenGL.

Capture frame-cost-20261007-154936-463000 (same scene as the 35 FPS capture): the guest thread idled 8.7 ms per frame and the renderer 12.6 ms,
because the game synchronises with the GPU (GXSetDrawSync / GXDrawDone) and the renderer only signalled after RendererGL::Render had
GL-submitted every earlier draw of the drain (3.8 ms for ~40 draws, gx.render + upload/driver), after parse (0.4 ms) and decode (1.5 ms).
The guest therefore waited out the whole render phase at every sync, and the two threads could not overlap frame N's rendering with
frame N+1's game logic (frame = guest CPU + waits instead of max(guest, renderer)).

By the end of decode everything the guest can observe through RAM has been consumed: display lists are expanded, draw payloads and XF
banks are snapshotted. What remains is GL submission and texture binding. So every PE token / finish of the drain is signalled right after
decode, in command order and with exactly the value the renderer would have produced (same BP shadow/mask rule as NativeBpRegisterValue,
including the 0xFE mask register), the command is flagged, and RendererGL skips its own signal for flagged commands. Textures are still
read from RAM while rendering, so the window in which the guest could overwrite a texture before its draw is bound is one drain's render
phase (a few ms), the same trade Dolphin's dual-core mode makes. NWII_SYNC_GPU=1 restores the previous behaviour (signal after submission).

Parts re-apply independently if another stage rewrites their surroundings. Idempotent.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime'
MARK = 'NATIVE_EARLY_PE_SIGNAL_V1'


def apply(path, old, new, probe=None):
    s = path.read_text(errors='surrogateescape')
    if (probe or new) in s:
        return
    assert s.count(old) == 1, (path.name, old[:70], s.count(old))
    path.write_text(s.replace(old, new, 1), errors='surrogateescape')


# 1. flag on the command
apply(rt / 'include/runtime/gx_command.h',
      '    std::vector<float> payload;\n};',
      '    std::vector<float> payload;\n    bool native_signalled=false; // NATIVE_EARLY_PE_SIGNAL_V1: PE token/finish already delivered to the guest\n};',
      'native_signalled')

gx = rt / 'src/hle/gx.cpp'
helper = '''// NATIVE_EARLY_PE_SIGNAL_V1: see tools/fix_early_gpu_signal.py.
// NATIVE_EARLY_PE_SIGNAL_BEGIN
static uint32_t s_early_bp_shadow[4]={0,0,0,0};   // BP 0x45, 0x47, 0x48 and the 0xFE mask register
static uint32_t s_early_bp_mask=0xffffff;
// Same resolution rule as the renderer's NativeBpRegisterValue (shadow, one-shot mask), applied in command order.
static void NativeSignalGpuCompletionEarly(std::vector<nwii::runtime::gx::GXCommand>& frame) {
    for (auto& c : frame) {
        if (c.type != nwii::runtime::gx::GXCommandType::BPRegister) continue;
        const uint8_t reg = uint8_t(c.reg);
        const int slot = reg == 0x45 ? 0 : reg == 0x47 ? 1 : reg == 0x48 ? 2 : reg == 0xfe ? 3 : -1;
        uint32_t resolved = 0;
        if (slot >= 0) {
            resolved = ((s_early_bp_shadow[slot] & ~s_early_bp_mask) | (c.val & s_early_bp_mask)) & 0xffffff;
            s_early_bp_shadow[slot] = resolved;
        }
        s_early_bp_mask = reg == 0xfe ? resolved : 0xffffff;
        if (reg == 0x45 && (resolved & 0xffu) == 2u) {
            native_trace::mark("pe.early_signal", 0x45, resolved);
            nwii::runtime::hw::pe_signal_finish();
            c.native_signalled = true;
        } else if (reg == 0x47 || reg == 0x48) {
            native_trace::mark("pe.early_signal", reg, resolved & 0xffffu);
            nwii::runtime::hw::pe_signal_token(resolved & 0xffffu, reg == 0x48);
            c.native_signalled = true;
        }
    }
}
// NATIVE_EARLY_PE_SIGNAL_END
'''
apply(gx, 'void ProcessGXFifo() {', helper + 'void ProcessGXFifo() {', 'NATIVE_EARLY_PE_SIGNAL_BEGIN')

# 2. call it after decode, before GL submission
apply(gx,
      '    if (g_window) {\n        if (!g_renderer) {\n            g_renderer = nwii::runtime::gx::IRenderer::Create();',
      '    { // NATIVE_EARLY_PE_SIGNAL_V1: the guest sees every token/finish of this drain now; GL submission continues without it\n'
      '        static const bool early_signal = std::getenv("NWII_SYNC_GPU") == nullptr;\n'
      '        if (early_signal) NativeSignalGpuCompletionEarly(frame);\n'
      '    }\n'
      '    if (g_window) {\n        if (!g_renderer) {\n            g_renderer = nwii::runtime::gx::IRenderer::Create();',
      'NativeSignalGpuCompletionEarly(frame);')

# 3. headless branch and renderer skip flagged commands
apply(gx,
      '        for(const auto &c:frame)if(c.type==nwii::runtime::gx::GXCommandType::BPRegister) {\n            if(c.reg==0x45',
      '        for(const auto &c:frame)if(c.type==nwii::runtime::gx::GXCommandType::BPRegister && !c.native_signalled) {\n            if(c.reg==0x45',
      'BPRegister && !c.native_signalled')
apply(rt / 'src/hle/gx/renderer_gl.cpp',
      '        NativeGpuCompletion((uint8_t)cmd.reg,bp_value,true);',
      '        if(!cmd.native_signalled)NativeGpuCompletion((uint8_t)cmd.reg,bp_value,true); // NATIVE_EARLY_PE_SIGNAL_V1: already delivered after decode',
      'if(!cmd.native_signalled)NativeGpuCompletion')
print('early GPU completion signal installed')

# 4. (V2) TLUT loads read guest RAM when the renderer executes BP 0x65, i.e. after the early signal released the guest. Palette
#    animation (menu highlight, HUD meter colours) then rewrote the palette before it was copied: visible colour flicker. Snapshot
#    every TLUT load at signal time, resolving BP 0x64/0x65 through the same shadow/mask rule, and let the renderer use the snapshot.
apply(rt / 'include/runtime/gx_command.h',
      '    bool native_signalled=false;',
      '    bool native_signalled=false;\n    bool native_tlut_snapshot=false; // NATIVE_EARLY_TLUT_SNAPSHOT_V1: native_tlut holds the BP 0x65 source bytes read before the early signal\n    std::vector<uint8_t> native_tlut;',
      'native_tlut_snapshot')
s = gx.read_text(errors='surrogateescape')
if 'NATIVE_EARLY_TLUT_SNAPSHOT_V1' not in s:
    a = s.index('// NATIVE_EARLY_PE_SIGNAL_BEGIN'); b = s.index('// NATIVE_EARLY_PE_SIGNAL_END', a)
    helper2 = '''// NATIVE_EARLY_PE_SIGNAL_BEGIN
// NATIVE_EARLY_TLUT_SNAPSHOT_V1: full BP shadow (same rule as the renderer's NativeBpRegisterValue) so BP 0x64/0x65 resolve identically.
static uint32_t s_early_bp_shadow[256]={};
static uint32_t s_early_bp_mask=0xffffff;
// Same resolution rule as the renderer's NativeBpRegisterValue (shadow, one-shot mask), applied in command order.
static void NativeSignalGpuCompletionEarly(std::vector<nwii::runtime::gx::GXCommand>& frame) {
    for (auto& c : frame) {
        if (c.type != nwii::runtime::gx::GXCommandType::BPRegister) continue;
        const uint8_t reg = uint8_t(c.reg);
        const uint32_t resolved = ((s_early_bp_shadow[reg] & ~s_early_bp_mask) | (c.val & s_early_bp_mask)) & 0xffffff;
        s_early_bp_shadow[reg] = resolved;
        s_early_bp_mask = reg == 0xfe ? resolved : 0xffffff;
        if (reg == 0x65) { // TLUT load: copy the source now, exactly as ApplyBPRegister(0x65) would
            uint32_t src = (s_early_bp_shadow[0x64] & 0xFFFFFF) << 5;
            if (nwii::runtime::Config::get().platform == nwii::runtime::Platform::GameCube) src &= 0x01FFFFFF;
            const uint32_t dst = (resolved & 0x3FF) << 9, bytes = ((resolved >> 10) & 0x7FF) * 32;
            if (nwii::runtime::g_mmu && dst + bytes <= sizeof(nwii::runtime::gx::g_state.tlutMem)) {
                c.native_tlut.resize(bytes);
                if (const uint8_t* p = nwii::runtime::g_mmu->get_ptr(src)) { if (bytes) std::memcpy(c.native_tlut.data(), p, bytes); }
                else for (uint32_t i = 0; i < bytes; ++i) c.native_tlut[i] = nwii::runtime::g_mmu->read8(src + i);
                c.native_tlut_snapshot = true;
            }
        }
        if (reg == 0x45 && (resolved & 0xffu) == 2u) {
            native_trace::mark("pe.early_signal", 0x45, resolved);
            nwii::runtime::hw::pe_signal_finish();
            c.native_signalled = true;
        } else if (reg == 0x47 || reg == 0x48) {
            native_trace::mark("pe.early_signal", reg, resolved & 0xffffu);
            nwii::runtime::hw::pe_signal_token(resolved & 0xffffu, reg == 0x48);
            c.native_signalled = true;
        }
    }
}
'''
    s = s[:a] + helper2 + s[b:]
    gx.write_text(s, errors='surrogateescape')
apply(rt / 'src/hle/gx/renderer_gl.cpp',
      '        ApplyBPRegister((uint8_t)cmd.reg, bp_value);\n',
      '        if (cmd.native_tlut_snapshot && cmd.reg == 0x65) { // NATIVE_EARLY_TLUT_SNAPSHOT_V1: bytes read before the early signal\n'
      '          const uint32_t dst = (bp_value & 0x3FF) << 9;\n'
      '          if (!cmd.native_tlut.empty()) std::memcpy(&g_state.tlutMem[dst], cmd.native_tlut.data(), cmd.native_tlut.size());\n'
      '        } else ApplyBPRegister((uint8_t)cmd.reg, bp_value);\n',
      'NATIVE_EARLY_TLUT_SNAPSHOT_V1: bytes read before')
print('early TLUT snapshot installed')

# 5. gx.cpp needs Config for the TLUT address rule (GameCube mask), like fifo_parser.cpp.
apply(gx, '// NATIVE_EARLY_PE_SIGNAL_BEGIN', '#include "runtime/config.h" // NATIVE_EARLY_TLUT_SNAPSHOT_V1\n// NATIVE_EARLY_PE_SIGNAL_BEGIN','#include "runtime/config.h" // NATIVE_EARLY_TLUT_SNAPSHOT_V1')
print('early TLUT snapshot include installed')
