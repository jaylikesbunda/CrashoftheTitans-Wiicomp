"""Install bounded opt-in tracing; no generated game code or CPUContext changes."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
for src,dst in [('frame_trace.h','native_frame_trace.h'),('frame_trace_gl.h','native_frame_trace_gl.h')]:
    target=rt/'include/runtime'/dst;text=(p/'runtime-overlays'/src).read_text()
    if not target.exists() or target.read_text()!=text:target.write_text(text)
def edit(rel,patches,gl=False):
    path=rt/rel;s=path.read_text();include='#include "runtime/native_frame_trace'+('_gl' if gl else '')+'.h"\n'
    # Earlier staging tools can insert probes between our anchor and its scope.
    # Normalize these unique scopes before installation instead of duplicating
    # a declaration merely because the complete adjacent text changed.
    if rel=='src/hle/ios.cpp':
        s=s.replace('  native_trace::CallbackSample trace_callback(ctx.pc,ctx.lr,ctx.inst_count);\n','')
    if rel=='src/hle/gx/fifo_parser.cpp':
        a=s.index('void FifoParser::DecodeDraw(GXCommand& c) {');b=s.index('\nvoid FifoParser::Parse(',a)
        wrapper=s[a:b].replace('    native_trace::Scope trace_decode("gx.decode");\n','')
        s=s[:a]+wrapper+s[b:]
    if rel=='src/hle/gx/renderer_gl.cpp':
        s=s.replace('  native_trace::Scope trace_state("gx.state");\n','')
        s=s.replace('  native_trace::Scope trace_upload_draw("gx.upload_draw");\n','')
        s=s.replace('  native_trace::mark("batch.vertices",s_batch.size(),s_indices.size());\n','')
        s=s.replace('    native_trace::Scope trace_render("gx.render");\n','')
        s=s.replace('    native_trace::GPUScope trace_gpu("gpu.render_region_us");\n','')
    if rel=='src/core/main.cpp':
        s=s.replace('  if(native_trace::enabled()) { native_trace::finish("shutdown"); native_trace::dump(); }\n','')
        s=s.replace('  if(native_trace::enabled() && native_trace::phase.load()!=4) { native_trace::finish("shutdown"); native_trace::dump(); }\n','')
    if include not in s:s=include+s
    for old,new in patches:
        if rel=='src/hle/gx/renderer_gl.cpp' and old=='    auto src = GenerateTEVShader(g_state, cmd.prim_type);' and 'NATIVE_SHADER_STUTTER_V1' in s:continue
        # NATIVE_DRAW_SUBMISSION_REUSE_V1 turns the Render parameter into CommandStream; keep the
        # file's own spelling so a re-staged tree gets its traces back at the same place.
        if old not in s:
            alt={'  void Render(const std::vector<GXCommand> &commands) override {':
                 '  void Render(const CommandStream &commands) override {'}.get(old,'')
            if alt and alt in s:
                new=new.replace(old,alt,1);old=alt
        if new in s:continue
        extra=[x for x in new.replace(old,'',1).split(chr(10)) if x.strip()]
        if extra and extra[0].strip() in s:continue   # already inserted; a later stage may have rewritten the text around it
        assert s.count(old)==1,(rel,old)
        s=s.replace(old,new,1)
    if rel=='src/hle/gx/renderer_gl.cpp':
        # one batch.vertices mark, counting what is really in the batch (NativeBatchSize exists once the fused-batch stage has run)
        if 'static inline size_t NativeBatchSize()' in s:
            s=s.replace('native_trace::mark("batch.vertices",s_batch.size(),s_indices.size());','native_trace::mark("batch.vertices",NativeBatchSize(),s_indices.size());')
        mark='  native_trace::mark("batch.vertices",NativeBatchSize(),s_indices.size());'+chr(10)
        while s.count(mark)>1:
            i=s.rindex(mark);s=s[:i]+s[i+len(mark):]
    if path.read_text()!=s:path.write_text(s)
edit('src/core/main.cpp',[
 ('  run_game(*ctx);','  native_trace::guest_thread();\n  run_game(*ctx);'),
 ('    want.callback = [](void*, Uint8* stream, int len) {','    want.callback = [](void*, Uint8* stream, int len) {\n      if(native_trace::enabled())native_trace::local_buffer();\n      native_trace::Scope trace_audio_callback("audio.output_pull");'),
 ('  auto pace_video_field = [&]() {','  auto pace_video_field = [&]() {\n    native_trace::control();\n    native_trace::Scope trace_wait("host.field_wait");'),
 ('    while (!quit && ctx->is_running) {','    while (!quit && ctx->is_running) {\n      native_trace::Scope trace_loop("host.loop");'),
 ('          t0 = now;\n          extern uint64_t g_stat_frames, g_stat_draws;','          t0 = now;\n          native_trace::Scope trace_diagnostics("host.diagnostic_readback");\n          extern uint64_t g_stat_frames, g_stat_draws;'),
 ('if(std::getenv("NWII_OFFSCREEN")==nullptr)SDL_GL_SwapWindow(window);','if(std::getenv("NWII_OFFSCREEN")==nullptr) { native_trace::Scope trace_present("host.present"); SDL_GL_SwapWindow(window); }'),
 ('        // NATIVE_XFB_MAIN_PRESENT: retain the VI-selected completed copy while EFB changes.','        native_trace::Scope trace_composite("host.composite");\n        native_trace::GPUScope trace_gpu_present("gpu.presentation_region_us");\n        // NATIVE_XFB_MAIN_PRESENT: retain the VI-selected completed copy while EFB changes.'),
 ('  nwii::runtime::shutdown();','  if(native_trace::enabled() && native_trace::phase.load()>0 && native_trace::phase.load()!=4) { native_trace::finish("shutdown"); native_trace::dump(); }\n  nwii::runtime::shutdown();')],True)
edit('src/hle/gx.cpp',[
 ('void ProcessGXFifo() {','void ProcessGXFifo() {\n    native_trace::Scope trace_drain("gx.drain");'),
 ('    nwii::runtime::gx::FifoParser::Parse(s_parse_buf, commands);','    { native_trace::Scope trace_parse("gx.parse"); native_trace::mark("fifo.bytes",s_parse_buf.size());\n    nwii::runtime::gx::FifoParser::Parse(s_parse_buf, commands); }'),
 ('    if (g_cpu_fifo.empty()) return;','    if (g_cpu_fifo.empty()) return;\n    native_trace::Scope trace_publish("gx.publish");')])
edit('src/hle/gx/fifo_parser.cpp',[
 ('void FifoParser::DecodeDraw(GXCommand& c) {','void FifoParser::DecodeDraw(GXCommand& c) {\n    native_trace::Scope trace_decode("gx.decode");'),
 ('    c.vertices.reserve(r.count);','    native_trace::mark("decode.vertices",r.count,r.bytes.size());\n    c.vertices.reserve(r.count);')])
edit('src/hle/gx/renderer_gl.cpp',[
 ('static void EmitPrimitive(const GXCommand &cmd) {','static void EmitPrimitive(const GXCommand &cmd) {\n  native_trace::Scope trace_emit("gx.vertices");'),
 ('static void SetupDrawState(const GXCommand &cmd) {','static void SetupDrawState(const GXCommand &cmd) {\n  native_trace::Scope trace_state("gx.state");'),
 ('    auto src = GenerateTEVShader(g_state, cmd.prim_type);','    native_trace::Scope trace_shader("gx.shader_compile");\n    auto src = GenerateTEVShader(g_state, cmd.prim_type);'),
 ('  static const bool s_glerr = std::getenv("NWII_GL_ERRTRACE") != nullptr;','  native_trace::Scope trace_upload_draw("gx.upload_draw");\n  native_trace::mark("batch.vertices",s_batch.size(),s_indices.size());\n  static const bool s_glerr = std::getenv("NWII_GL_ERRTRACE") != nullptr;'),
 ('  ++s_native_xfb_copies;','  ++s_native_xfb_copies;\n  native_trace::boundary(s_native_xfb_copies);'),
 ('  void Render(const std::vector<GXCommand> &commands) override {','  void Render(const std::vector<GXCommand> &commands) override {\n    native_trace::Scope trace_render("gx.render");\n    native_trace::GPUScope trace_gpu("gpu.render_region_us");'),
 ('static void NativeCopyEfbTexture(GLuint texture, int x, int y, int width, int height,int scale=1) {','static void NativeCopyEfbTexture(GLuint texture, int x, int y, int width, int height,int scale=1) {\n  native_trace::Scope trace_copy("gx.efb_texture_copy");'),
 ('static void NativeLatchXfb(uint32_t address,int x,int y,int width,int height,int scale=1) {','static void NativeLatchXfb(uint32_t address,int x,int y,int width,int height,int scale=1) {\n  native_trace::Scope trace_copy("gx.xfb_copy");')],True)
edit('src/hle/texture_cache_gl.cpp',[
 ('GLuint TextureCache::get_texture(CPUContext& ctx, const gx::TexStage& stage) {','GLuint TextureCache::get_texture(CPUContext& ctx, const gx::TexStage& stage) {\n    native_trace::Scope trace_texture("texture.lookup_hash");'),
 ('    if (it != cache.end()) {','    if (it != cache.end()) {\n        native_trace::mark("texture.hit");'),
 ('    Image img = decode_texture(ctx, stage);','    native_trace::mark("texture.miss",stage.width*stage.height,stage.format);\n    Image img = decode_texture(ctx, stage);'),
 ('Image TextureCache::decode_texture(CPUContext& ctx, const gx::TexStage& stage) {','Image TextureCache::decode_texture(CPUContext& ctx, const gx::TexStage& stage) {\n    native_trace::Scope trace_texture_decode("texture.decode");'),
 ('    glGenTextures(1, &tex);','    native_trace::Scope trace_texture_upload("texture.upload");\n    glGenTextures(1, &tex);')])
if 'NATIVE_CALLBACK_TRACE_BRANCH_V1' not in (rt/'src/hle/ios.cpp').read_text():
    edit('src/hle/ios.cpp',[
     ('bool process_pending_callbacks(CPUContext &ctx) {','bool process_pending_callbacks(CPUContext &ctx) {\n  native_trace::CallbackSample trace_callback(ctx.pc,ctx.lr,ctx.inst_count);')])
edit('src/hw/hw_dsp.cpp',[
 ('        auto& processor=native_ax();processor.Send(mail);','        native_trace::Scope trace_audio("audio.ax_mail");\n        auto& processor=native_ax();processor.Send(mail);')])
path=p/'tools/build_native.py';s=path.read_text()
if "run('native-frame-trace-stage'" not in s:
    anchor="run('native-game-build',";assert s.count(anchor)==1
    path.write_text(s.replace(anchor,"run('native-frame-trace-stage',[sys.executable,PORT/'tools/fix_frame_trace.py'])\n"+anchor,1))
print('Opt-in CPU/frame/deferred-GPU trace installed.')
