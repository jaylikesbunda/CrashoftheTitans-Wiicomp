"""GL render thread and batch queue (NATIVE_RENDER_QUEUE_V1).

The main thread keeps the SDL window, events, input, pacing and the drain servicing (ProcessGXFifo: parse, decode, texture
CPU prepass, early PE signal). Everything that touches GL moves to one GL thread that owns the context:
  * RendererGL::Render for each drain (a batch), in publish order;
  * the frame's composite, present, HUD, thumbnail and screenshot work (a task), after the batches published before it.

A bounded OrderedQueue (runtime-overlays/native_render_queue.h, depth 3, NWII_RENDER_QUEUE_DEPTH=1..8) carries them. A batch
carries everything the front end decided for it: its command stream, its parse snapshot (RenderBatchSnapshot), the texture ops
queued by the prepass (pending_ops -> gl_ops) and its draw list (s_draw_textures_ring[seq]). The GL thread never reads state the
front end is writing, and the front end never reads state the GL thread is writing:
  * Renderer state (RenderState, the renderer BP shadow, g_renderer) and the texture GL side are GL-only. The prepass reads its own
    BP shadow copy (FrontBpShadow, from the early pass) in queue mode.
  * Frame reads of the XFB latch (GX_GetXfb) move to the GL thread in queue mode.
  * SDL_SetWindowTitle is applied by the main thread (TakeTitle), so the GL thread never sends window messages.
  * native_force_present is atomic: the main thread sets it on window and key events, the frame task clears it.
Ordering: a drain whose batch contains an EFB copy (BP 0x52) flushes the queue before its early PE signal, so the guest is
released only after earlier batches have run; native_ss_gx_sync flushes before a savestate; the context is released and
the thread joined at shutdown.

The queue is used only when the build has the render-state split (this stage is gated by NWII_BUILD_RENDER_STATE_SPLIT=1) and at
runtime unless NWII_NO_RENDER_QUEUE is set, and never when headless, NWII_XFB, NWII_RUNTIME_DIAG, NWII_PROMPT_PACK or
NWII_SYNC_GPU is set (those keep GL work or texture reloads on the main thread).

Edits are anchored and count-checked. --restore reverts every edit byte-exactly and removes the copied overlays.
Runs after the texture CPU/GL split stage (it uses its ops and draw lists). Harness: tools/check_render_queue.py.
"""
import re
import shutil
import sys
from pathlib import Path

p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime'
overlays = p / 'runtime-overlays'
MARK = 'NATIVE_RENDER_QUEUE_V1'
RESTORE = '--restore' in sys.argv

GX = rt / 'src/hle/gx.cpp'
REN = rt / 'src/hle/gx/renderer_gl.cpp'
TEX = rt / 'src/hle/texture_cache_gl.cpp'
TEX_H = rt / 'include/runtime/texture_cache_gl.h'
PROMPT = rt / 'include/runtime/native_prompt_textures.h'
MAIN = rt / 'src/core/main.cpp'

COPIES = [
    (overlays / 'native_render_queue.h', rt / 'include/runtime/native_render_queue.h'),
    (overlays / 'render_queue_glue.inc', rt / 'src/hle/render_queue_glue.inc'),
    (overlays / 'native_render_queue_stats.h', rt / 'include/runtime/native_render_queue_stats.h'),
]


def read(path):
    with open(path, 'r', encoding='utf-8', errors='surrogateescape', newline='') as f:
        return f.read()


def write(path, text):
    with open(path, 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
        f.write(text)


def crlf(text, s):
    # Runtime sources may be CRLF; anchors are written with LF.
    return s.replace('\n', '\r\n') if '\r\n' in text else s


# ---- edits: (file, old, new, expected occurrences of old, label). Apply in order; restore runs them in reverse. ----------
EDITS = []


def edit(path, old, new, count, label):
    EDITS.append((path, old, new, count, label))


# gx.cpp -----------------------------------------------------------------------------------------------------------------
edit(GX, 'static std::vector<uint8_t> s_parse_buf; // NATIVE_SAVESTATE_V1',
     '#include "render_queue_glue.inc" // ' + MARK + '\n'
     'static std::vector<uint8_t> s_parse_buf; // NATIVE_SAVESTATE_V1', 1, 'gx:glue')
edit(GX, '    { // NATIVE_EARLY_PE_SIGNAL_V1: the guest sees every token',
     '    if (g_window && nwii::runtime::render_queue::Active() && nwii::runtime::render_queue::BatchNeedsFlush(frame))\n'
     '        nwii::runtime::render_queue::FlushForCopy(); // ' + MARK + ': an EFB copy in this drain is guest-visible; earlier batches run first\n'
     '    { // NATIVE_EARLY_PE_SIGNAL_V1: the guest sees every token', 1, 'gx:flush_before_signal')
edit(GX, '    if (g_window) {\n        if (!g_renderer) {\n            g_renderer = nwii::runtime::gx::IRenderer::Create();\n            g_renderer->Initialize(nullptr);',
     '    if (g_window && nwii::runtime::render_queue::Active()) {\n'
     '        nwii::runtime::render_queue::SubmitBatch(frame); // ' + MARK + ': the GL thread renders it, in publish order\n'
     '    } else if (g_window) {\n        if (!g_renderer) {\n            g_renderer = nwii::runtime::gx::IRenderer::Create();\n            g_renderer->Initialize(nullptr);', 1, 'gx:submit')
edit(GX, '            if (empty && s_native_ss_gx_busy.load(std::memory_order_acquire) == 0) return true;',
     '            if (empty && s_native_ss_gx_busy.load(std::memory_order_acquire) == 0) { nwii::runtime::render_queue::FlushForSavestate(); return true; } // '
     + MARK + ': the GL thread has completed every batch', 1, 'gx:savestate_flush')

# renderer_gl.cpp (the texture walk is already in this file, from the texture split stage) -------------------------------
edit(REN, '#include "runtime/config.h" // NATIVE_TEXTURE_CPU_GL_SPLIT_V1',
     '#include "runtime/config.h" // NATIVE_TEXTURE_CPU_GL_SPLIT_V1\n'
     'namespace nwii::runtime::render_queue { // ' + MARK + '\n'
     'bool Active();\n'
     'void FrontBpShadow(uint32_t* bp, uint32_t& mask);\n'
     'nwii::runtime::gx::ParseSnapshot& BatchSnapshotForRender();\n'
     '}\n'
     'namespace nwii::runtime::gx { // ' + MARK + '\n'
     'uint32_t NativeLastPrepassSeq();\n'
     'void NativeSelectDrawTextures(uint32_t seq);\n'
     '}', 1, 'ren:decls')
edit(REN, '    RenderStateBegin(RenderBatchSnapshot()); // NATIVE_RENDERER_STATE_SPLIT_V1: parse-owned view of this batch',
     '    RenderStateBegin(nwii::runtime::render_queue::BatchSnapshotForRender()); // NATIVE_RENDERER_STATE_SPLIT_V1: parse-owned view of this batch (' + MARK + ': the batch copy on the GL thread)', 1, 'ren:snapshot')
edit(REN, 's_draw_index < s_draw_textures.size() ? s_draw_textures[s_draw_index] : no_textures;',
     's_draw_index < s_draw_textures->size() ? (*s_draw_textures)[s_draw_index] : no_textures; // ' + MARK + ': the batch draw list', 1, 'ren:draws')

# texture_cache_gl.h ---------------------------------------------------------------------------------------------------
edit(TEX_H, '        enum Kind : uint8_t { Slot, Efb, Direct } kind = Slot;',
     '        enum Kind : uint8_t { Slot, Efb, Direct, Prompt } kind = Slot; // ' + MARK + ': Prompt names a glyph texture, made on the GL thread', 1, 'hdr:kind')
edit(TEX_H, '    struct TextureOp {',
     '    // ' + MARK + ': the GL half of the queue. The batch carries its ops; see render_queue_glue.inc.\n'
     '    public:\n    struct TextureOp {', 1, 'hdr:public_op')
edit(TEX_H, '    std::unordered_set<uint32_t> efb_addrs; // EFB copy destinations, as of prepare time',
     '    private:\n    std::unordered_set<uint32_t> efb_addrs; // EFB copy destinations, as of prepare time', 1, 'hdr:private_efb')
edit(TEX_H, '    std::vector<TextureOp> ops;\n    size_t ops_next = 0;\n',
     '    // ' + MARK + ': pending_ops is written by the front end (prepare_texture); gl_ops is run by the GL thread. A batch hands its\n'
     '    // ops over with take_pending_ops (queue) or adopt_pending_ops (sync path, same thread).\n'
     '    std::vector<TextureOp> pending_ops;\n    std::vector<TextureOp> gl_ops;\n    size_t ops_next = 0; // gl_ops cursor, GL thread only\n'
     '    public:\n'
     '    void take_pending_ops(std::vector<TextureOp>& out); // front end\n'
     '    void adopt_ops(std::vector<TextureOp>& batch);      // GL thread\n'
     '    void adopt_pending_ops();                           // sync path\n'
     '    void drop_pending_ops();                            // front end\n'
     '    private:\n', 1, 'hdr:ops')
edit(TEX_H, 'r.ops_end = ops.size();', 'r.ops_end = pending_ops.size();', 3, 'hdr:ops_end')
edit(TEX_H, '    TextureRef direct_reference(GLuint tex) { TextureRef r; r.kind = TextureRef::Direct; r.direct = tex; r.ops_end = pending_ops.size(); return r; }',
     '    TextureRef direct_reference(GLuint tex) { TextureRef r; r.kind = TextureRef::Direct; r.direct = tex; r.ops_end = pending_ops.size(); return r; }\n'
     '    TextureRef prompt_reference(int glyph) { TextureRef r; r.kind = TextureRef::Prompt; r.slot = glyph; r.ops_end = pending_ops.size(); return r; }',
     1, 'hdr:prompt_ref')

# texture_cache_gl.cpp -------------------------------------------------------------------------------------------------
edit(TEX, '    ops.push_back(std::move(op));', '    pending_ops.push_back(std::move(op));', 2, 'tex:push')
edit(TEX, 'void TextureCache::run_ops(size_t end) {\n    while (ops_next < end && ops_next < ops.size()) {\n        execute(ops[ops_next]);',
     'void TextureCache::run_ops(size_t end) {\n    while (ops_next < end && ops_next < gl_ops.size()) {\n        execute(gl_ops[ops_next]);', 1, 'tex:run')
edit(TEX, 'void TextureCache::flush_ops() {\n    run_ops(ops.size());\n    ops.clear();\n    ops_next = 0;\n}',
     'void TextureCache::flush_ops() {\n    run_ops(gl_ops.size());\n    gl_ops.clear();\n    ops_next = 0;\n}\n\n'
     '// ' + MARK + ': hand-over of queued ops. take/drop/adopt_pending run on the front end (or the same thread on the sync path);\n'
     '// adopt_ops runs on the GL thread. Each batch starts with an empty gl_ops and ends with flush_ops in Render.\n'
     'void TextureCache::take_pending_ops(std::vector<TextureOp>& out) {\n    out.clear();\n    out.swap(pending_ops);\n}\n\n'
     'void TextureCache::adopt_ops(std::vector<TextureOp>& batch) {\n    gl_ops.swap(batch);\n    batch.clear();\n    ops_next = 0;\n}\n\n'
     'void TextureCache::adopt_pending_ops() {\n    gl_ops.swap(pending_ops);\n    pending_ops.clear();\n    ops_next = 0;\n}\n\n'
     'void TextureCache::drop_pending_ops() {\n    pending_ops.clear();\n}', 1, 'tex:flush')
edit(TEX, '        case TextureRef::Direct: return ref.direct;',
     '        case TextureRef::Direct: return ref.direct;\n'
     '        case TextureRef::Prompt: return native_prompts::textures().texture(ref.slot); // ' + MARK + ': the glyph texture is made here', 1, 'tex:resolve_prompt')
edit(TEX, 'if(GLuint direct=prompts.texture(glyph)) return direct_reference(direct);',
     'if(prompts.wants_texture(glyph)) return prompt_reference(glyph); // ' + MARK + ': decided here, made on the GL thread', 1, 'tex:prompt')
edit(TEX, 'static bool NativeS3tcAvailable() {',
     'static bool NativeS3tcAvailable();\n'
     'void NativeTexturePrimeGl() { (void)NativeS3tcAvailable(); } // ' + MARK + ': the one-time GL query runs on the main thread before the GL thread starts\n'
     'static bool NativeS3tcAvailable() {', 1, 'tex:prime')

# native_prompt_textures.h (the glyph list is reloaded only with NWII_PROMPT_PACK, which turns the queue off) -----------------
edit(PROMPT, '    bool enabled() const { return !glyphs.empty(); }',
     '    bool enabled() const { return !glyphs.empty(); }\n'
     '    // ' + MARK + ': whether texture(glyph) names a texture. Decided on the front end; the texture is made on the GL thread.\n'
     '    bool wants_texture(int glyph) const {\n'
     '        if(glyph<0 || static_cast<size_t>(glyph)>=glyphs.size()*2)return false;\n'
     '        return device.load(std::memory_order_relaxed)!=3;\n'
     '    }', 1, 'prompt:wants')

# main.cpp ---------------------------------------------------------------------------------------------------------------
edit(MAIN, 'extern bool NativeConsumeGxDrain(); extern void NativeArmGxParkLocked();',
     'extern bool NativeConsumeGxDrain(); extern void NativeArmGxParkLocked();\n'
     '} // ' + MARK + ': leave nwii::runtime; the names below are file scope\n'
     '#include <functional> // ' + MARK + '\n'
     '#include <string> // ' + MARK + '\n'
     'namespace nwii::runtime::render_queue { // ' + MARK + '\n'
     'bool Active();\n'
     'void Start(std::function<void()> make_current, std::function<void()> release, std::function<void()> bind_frame);\n'
     'void Flush();\n'
     'void Stop();\n'
     'void Post(std::function<void()> task);\n'
     'void SetTitle(const std::string& title);\n'
     'bool TakeTitle(std::string& out);\n'
     '}\n'
     'namespace nwii::runtime::hle { void NativeTexturePrimeGl(); } // ' + MARK + '\n'
     '// ' + MARK + ': the XFB latch as the composite reads it: GX_GetXfb, then the VI top field when one is set.\n'
     'static void NativeReadXfbLatch(uint32_t& addr, unsigned& w, unsigned& h, unsigned& stride) {\n'
     '    extern void GX_GetXfb(uint32_t*, unsigned*, unsigned*, unsigned*);\n'
     '    GX_GetXfb(&addr, &w, &h, &stride);\n'
     '    if (nwii::runtime::hw::g_vi_top_field_base != 0) addr = nwii::runtime::hw::g_vi_top_field_base;\n'
     '}\n'
     'namespace nwii::runtime { // ' + MARK + ': back inside the runtime namespace\n', 1, 'main:decls')
edit(MAIN, 'bool native_force_present=true;uint64_t native_shown_generation=0;',
     '/* ' + MARK + ': set by the main thread, cleared by the frame task */ std::atomic<bool> native_force_present{true};uint64_t native_shown_generation=0;', 1, 'main:force')
edit(MAIN, '        if (!headless) { glBindFramebuffer(GL_FRAMEBUFFER, efb_fbo);',
     '        if (!headless && !nwii::runtime::render_queue::Active()) { glBindFramebuffer(GL_FRAMEBUFFER, efb_fbo);', 1, 'main:pace_bind')
edit(MAIN, '    GLint saved_fbo = 0, saved_viewport[4] = {0, 0, 0, 0};',
     '    if (nwii::runtime::render_queue::Active()) { // ' + MARK + ': the GL thread binds the EFB for each batch\n'
     '        nwii::runtime::native_render_wait::DrainStart();\n'
     '        ProcessGXFifo();\n'
     '        return;\n'
     '    }\n'
     '    GLint saved_fbo = 0, saved_viewport[4] = {0, 0, 0, 0};', 1, 'main:drain_before_host')
edit(MAIN, '      {native_trace::Scope inputs("host.input_update");nwii::runtime::input::InputManager::get().update();}\n\n'
     '      if (!headless) {\n        glBindFramebuffer(GL_FRAMEBUFFER, efb_fbo);',
     '      {native_trace::Scope inputs("host.input_update");nwii::runtime::input::InputManager::get().update();}\n'
     '      { std::string title; if (window && nwii::runtime::render_queue::TakeTitle(title)) SDL_SetWindowTitle(window, title.c_str()); } // ' + MARK + '\n\n'
     '      if (!headless && !nwii::runtime::render_queue::Active()) {\n        glBindFramebuffer(GL_FRAMEBUFFER, efb_fbo);', 1, 'main:loop_bind')
edit(MAIN, 'extern void GX_GetXfb(uint32_t*, unsigned*, unsigned*, unsigned*);\n'
     '      uint32_t xfb_addr; unsigned xw, xh, xstride;\n'
     '      GX_GetXfb(&xfb_addr, &xw, &xh, &xstride);\n'
     '      if (nwii::runtime::hw::g_vi_top_field_base != 0) {\n'
     '        xfb_addr = nwii::runtime::hw::g_vi_top_field_base;\n'
     '      }',
     '      uint32_t xfb_addr = 0; unsigned xw = 0, xh = 0, xstride = 0;\n'
     '      if (!nwii::runtime::render_queue::Active()) NativeReadXfbLatch(xfb_addr, xw, xh, xstride); // ' + MARK + ': the GL thread reads it in queue mode', 1, 'main:xfb_read')
edit(MAIN, '      if (!headless) {\n        glBindFramebuffer(GL_FRAMEBUFFER, 0);\n        int draw_w = 0, draw_h = 0;',
     '      if (!headless) {\n'
     '      auto frame_work = [&, xfb_addr, xw, xh, xstride]() mutable { // ' + MARK + ': the frame GL work (a task on the GL thread in queue mode)\n'
     '        if (nwii::runtime::render_queue::Active()) NativeReadXfbLatch(xfb_addr, xw, xh, xstride);\n'
     '        glBindFramebuffer(GL_FRAMEBUFFER, 0);\n        int draw_w = 0, draw_h = 0;', 1, 'main:frame_open')
edit(MAIN, '        }\n      }\n\n      // audio is pulled by the SDL callback (NATIVE_AUDIO_JITTER_BUFFER_V1)',
     '        }\n      };\n'
     '      if (nwii::runtime::render_queue::Active()) nwii::runtime::render_queue::Post(frame_work); else frame_work(); // ' + MARK + '\n'
     '      }\n\n      // audio is pulled by the SDL callback (NATIVE_AUDIO_JITTER_BUFFER_V1)', 1, 'main:frame_close')
edit(MAIN, 'if(window)SDL_SetWindowTitle(window,title);',
     'if(window){if(nwii::runtime::render_queue::Active())nwii::runtime::render_queue::SetTitle(title);else SDL_SetWindowTitle(window,title);}', 1, 'main:title')
edit(MAIN, '\n  if (headless) {\n',
     '\n  // ' + MARK + ': the GL thread takes the context. The queue is off (today\'s path) when headless, when NWII_NO_RENDER_QUEUE is set,\n'
     '  // or when a debug path keeps GL work on the main loop (NWII_XFB, NWII_RUNTIME_DIAG, NWII_SYNC_GPU). The live prompt pack is\n'
     '  // frozen while the GL thread runs (native_prompt_textures.h), so it does not turn the queue off.\n'
     '  if (!headless && window && gl_ctx && !std::getenv("NWII_NO_RENDER_QUEUE") && !std::getenv("NWII_XFB") &&\n'
     '      !std::getenv("NWII_SYNC_GPU") && !(std::getenv("NWII_RUNTIME_DIAG") && !std::getenv("NWII_NO_DIAG"))) {\n'
     '    native_hud::front_tid = native_hud::thread_id(); // ' + MARK + ': F3 shows the front end as [M]\n'
     '    nwii::runtime::hle::NativeTexturePrimeGl(); // the one-time S3TC query needs the context, so it runs before the hand-off\n'
     '    SDL_GL_MakeCurrent(window, nullptr);\n'
     '    nwii::runtime::render_queue::Start(\n'
     '        [window, gl_ctx] { SDL_GL_MakeCurrent(window, gl_ctx); },\n'
     '        [window] { SDL_GL_MakeCurrent(window, nullptr); },\n'
     '        [&efb_fbo] { glBindFramebuffer(GL_FRAMEBUFFER, efb_fbo); NativeViewport(0, 0, NativeEfbWidth(), NativeEfbHeight()); });\n'
     '  }\n'
     '\n  if (headless) {\n', 1, 'main:start')
edit(MAIN, '      SDL_GL_DeleteContext(gl_ctx);',
     '      nwii::runtime::render_queue::Stop(); // ' + MARK + ': flush, stop and join the GL thread before the context goes\n'
     '      SDL_GL_DeleteContext(gl_ctx);', 1, 'main:stop')


# native_prompt_textures.h: with the queue on, the glyph list is loaded once, at construction, and never reloaded. The front end
# reads it (enabled, probe, record); a reload on the GL thread would race with those reads.
edit(PROMPT, 'namespace native_prompts {\nstruct Glyph {',
     'namespace nwii::runtime::render_queue { bool Active(); } // ' + MARK + '\n'
     'namespace native_prompts {\nstruct Glyph {', 1, 'prompt:decl')
edit(PROMPT, '    void refresh() {\n',
     '    void refresh(bool initial=false) {\n'
     '        if(!initial && nwii::runtime::render_queue::Active()) return; // ' + MARK + ': frozen while the GL thread runs\n', 1, 'prompt:refresh')
edit(PROMPT, '    Textures() {\n        refresh();\n',
     '    Textures() {\n        refresh(true); // ' + MARK + ': the one load\n', 1, 'prompt:ctor')

# native_hud.h / native_hud_gl.h: the front end is a labelled thread in F3, and the queue counters are shown.
HUD = rt / 'include/runtime/native_hud.h'
HUD_GL = rt / 'include/runtime/native_hud_gl.h'
edit(HUD, 'inline unsigned guest_tid = 0, render_tid = 0;',
     'inline unsigned guest_tid = 0, render_tid = 0, front_tid = 0; // ' + MARK + ': the front end (main) thread, shown as [M]', 1, 'hud:front')
edit(HUD_GL, '#include "runtime/native_viewport_shadow.h"',
     '#include "runtime/native_render_queue_stats.h" // ' + MARK + '\n#include "runtime/native_viewport_shadow.h"', 1, 'hud_gl:include')
edit(HUD_GL, 'inline const char* role(const native_hud::Row& r) { return r.tid == native_hud::guest_tid ? "G" : r.tid == native_hud::render_tid ? "R" : "h"; }',
     'inline const char* role(const native_hud::Row& r) { return r.tid == native_hud::guest_tid ? "G" : r.tid == native_hud::render_tid ? "R" : r.tid == native_hud::front_tid ? "M" : "h"; }', 1, 'hud_gl:role')
edit(HUD_GL, '([G] guest thread, [R] render thread, [h] helper threads):',
     '([G] guest thread, [R] render thread, [M] front end, [h] helper threads):', 1, 'hud_gl:legend_text')
edit(HUD_GL, 'c.text(x,y,"[G] guest   [R] render   [h] helper",dim);',
     'c.text(x,y,"[G] guest   [R] render   [M] front   [h] helper",dim);', 1, 'hud_gl:legend_overlay')
edit(HUD_GL, '  t += fmt("Format loaders: %llu draws; retention ineligible: %llu (since F3/F9 reset)\\n",',
     '  { using namespace nwii::runtime::native_render_queue_stats; // ' + MARK + '\n'
     '    const uint64_t samples = depth_samples.load();\n'
     '    t += fmt("Render queue: %s | batches %llu (drains queued behind an earlier batch %llu) | frame tasks %llu | executed %llu | depth max %llu avg %.2f | flushes: EFB copy %llu, savestate %llu | waits %llu (%.2f ms)\\n",\n'
     '             enabled.load() ? "on" : "off", (unsigned long long)batches.load(), (unsigned long long)drains_queued.load(),\n'
     '             (unsigned long long)tasks.load(), (unsigned long long)executed.load(), (unsigned long long)depth_max.load(),\n'
     '             samples ? double(depth_sum.load()) / double(samples) : 0.0, (unsigned long long)flush_copy.load(),\n'
     '             (unsigned long long)flush_savestate.load(), (unsigned long long)flush_waits.load(), double(flush_wait_us.load()) / 1000.0); }\n'
     '  t += fmt("Format loaders: %llu draws; retention ineligible: %llu (since F3/F9 reset)\\n",', 1, 'hud_gl:queue_line')


def apply_edit(path, old, new, count, label, restore):
    # A new text may contain the old text (prepends), so "already applied" is decided by the new text, which is absent
    # from the pristine tree. The build restores before it applies, so a second apply is the only no-op case.
    if restore and not path.exists():
        return  # a fresh tree has no prompt header yet (fix_live_prompts copies it later); nothing to revert
    text = read(path)
    o, n = crlf(text, old), crlf(text, new)
    if restore:
        if n in text:
            assert text.count(n) == count, (label, 'restore count', text.count(n))
            write(path, text.replace(n, o))
        return
    if n in text:
        return  # already applied
    assert text.count(o) == count, (label, path.name, 'anchor count', text.count(o), count)
    write(path, text.replace(o, n))


def copy_overlays():
    for src, dest in COPIES:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)


def remove_overlays():
    for _, dest in COPIES:
        if dest.exists():
            dest.unlink()


if RESTORE:
    for path, old, new, count, label in reversed(EDITS):
        apply_edit(path, old, new, count, label, restore=True)
    remove_overlays()
    print('restored ' + MARK)
else:
    copy_overlays()
    for path, old, new, count, label in EDITS:
        apply_edit(path, old, new, count, label, restore=False)
    print('applied ' + MARK)
