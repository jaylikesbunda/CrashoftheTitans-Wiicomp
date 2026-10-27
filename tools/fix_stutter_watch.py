"""Low-overhead completed-frame history and comparative cost/work counters for F3/F4."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
for name in ('native_stutter.h','native_hud.h','native_hud_gl.h'):
 f=rt/'include/runtime'/name;value=(p/'runtime-overlays'/name).read_text()
 if not f.exists() or f.read_text()!=value:f.write_text(value)
def edit(file,old,new):
 f=rt/file;s=f.read_text()
 if new in s:return
 assert s.count(old)==1,(file,old,s.count(old));f.write_text(s.replace(old,new,1))
edit('src/hle/gx/fifo_parser.cpp',' if(NativeTryPackedDecode(c))return;',
 ' if(NativeTryPackedDecode(c)){native_hud::work_decoded(c.NativeStoredVertexCount(),c.NativeStoredVertexCount());return;}')
edit('src/hle/gx/fifo_parser.cpp',' NativePrepackDecodedGpu(c);\n}',
 ' NativePrepackDecodedGpu(c);\n native_hud::work_decoded(c.NativeStoredVertexCount(),c.native_packed.empty()?0:c.NativeStoredVertexCount());\n}')
edit('src/hle/gx/renderer_gl.cpp','  native_trace::mark("upload.bytes",stride*NativeBatchSize(),sizeof(BatchVertex)*NativeBatchSize());',
 '  native_hud::work_uploaded(stride*NativeBatchSize());\n  native_trace::mark("upload.bytes",stride*NativeBatchSize(),sizeof(BatchVertex)*NativeBatchSize());')
edit('src/hle/gx/renderer_gl.cpp','  native_trace::mark("draw.calls",1,s_indices.empty()?NativeBatchSize():s_indices.size());',
 '  native_hud::work_draw();\n  native_trace::mark("draw.calls",1,s_indices.empty()?NativeBatchSize():s_indices.size());')
edit('src/hle/texture_cache_gl.cpp','    native_trace::Scope trace_texture("texture.lookup_hash");',
 '    native_trace::Scope trace_texture("texture.lookup_hash");\n    native_hud::work_texture(false);')
edit('src/hle/texture_cache_gl.cpp','    Image img = decode_texture(ctx, stage);',
 '    native_hud::work_texture(true);\n    Image img = decode_texture(ctx, stage);')
# Separate shader generation from driver work; old nested compile scopes
# charged source generation as compilation and hid binary-cache stalls.
f=rt/'src/hle/gx/renderer_gl.cpp';s=f.read_text()
old='    native_trace::Scope trace_source("gx.shader_source");\n    native_trace::Scope trace_shader("gx.shader_compile");\n    auto src = GenerateTEVShader(g_state, cmd.prim_type);'
new='    auto src = [&]{native_trace::Scope trace_source("gx.shader_source");return GenerateTEVShader(g_state, cmd.prim_type);}(); // NATIVE_SHADER_STUTTER_V1'
if new not in s:
 # A pristine source-reuse stage has only trace_source; older repeated passes
 # also inserted the outer trace_shader. Accept both qualified stage states.
 pristine=old.replace('    native_trace::Scope trace_shader("gx.shader_compile");\n','')
 anchor=old if old in s else pristine
 assert s.count(anchor)==1,('Shader source timing context',s.count(anchor));s=s.replace(anchor,new,1)
s=s.replace('    native_trace::Scope trace_shader("gx.shader_compile");\n    static const bool s_shdump1', '    native_trace::Scope trace_shader_setup("gx.shader_setup");\n    static const bool s_shdump1')
if s!=f.read_text():f.write_text(s)
edit('src/hle/gx/renderer_gl.cpp','static GLuint CompileShader(const char *vs, const char *fs) {',
 'static GLuint CompileShader(const char *vs, const char *fs) {\n  native_trace::Scope shader_compile("gx.shader_compile"); // NATIVE_SHADER_STUTTER_V1')
edit('src/hle/gx/renderer_gl.cpp','    if(const GLuint cached=cache.load(cache_key)){native_trace::mark("shader.binary_hit");return cached;}',
 '    {native_trace::Scope binary_load("gx.shader_binary_load");\n    if(const GLuint cached=cache.load(cache_key)){native_hud::work_shader(1);native_trace::mark("shader.binary_hit");return cached;}}')
edit('src/hle/gx/renderer_gl.cpp','  GLuint v = glCreateShader(GL_VERTEX_SHADER);',
 '  native_hud::work_shader(0);\n  GLuint v = glCreateShader(GL_VERTEX_SHADER);')
edit('src/hle/gx/renderer_gl.cpp','  glLinkProgram(p);',
 '  {native_trace::Scope shader_link("gx.shader_link");glLinkProgram(p);}')
edit('src/hle/gx/renderer_gl.cpp','  if(cache.enabled)cache.store(cache_key,p);',
 '  if(cache.enabled){native_trace::Scope binary_store("gx.shader_binary_store");cache.store(cache_key,p);}')
edit('src/hle/gx/renderer_gl.cpp','      native_trace::mark("shader.source_reuse");',
 '      native_hud::work_shader(2);native_trace::mark("shader.source_reuse");')
edit('src/hle/gx/fifo_parser.cpp','            auto sn0 = std::chrono::steady_clock::now();',
 '            native_trace::Scope draw_snapshot("gx.draw_snapshot"); // NATIVE_STUTTER_PARSE_DETAIL_V1\n            auto sn0 = std::chrono::steady_clock::now();')
edit('src/hle/gx/fifo_parser.cpp','        { // NATIVE_DL_FULL_HASH_V1: whole list, 8 bytes at a time',
 '        { // NATIVE_DL_FULL_HASH_V1: whole list, 8 bytes at a time\n            native_trace::Scope dl_hash("gx.dl_hash");')
edit('src/hle/gx/fifo_parser.cpp','            g_prof_dl_hits++;',
 '            native_trace::Scope dl_replay("gx.dl_replay");\n            g_prof_dl_hits++;')
# Command recycling replaces the allocation line on replay, while preserving
# this scope. Accept that qualified form instead of requiring the old allocator.
snapshot_scope='native_trace::Scope dl_snapshot("gx.dl_snapshot");'
snapshot_source=(rt/'src/hle/gx/fifo_parser.cpp').read_text()
if snapshot_scope not in snapshot_source:
 edit('src/hle/gx/fifo_parser.cpp','                    c.raw = std::make_shared<DrawRaw>(*c.raw);',
  '                    '+snapshot_scope+'\n                    c.raw = std::make_shared<DrawRaw>(*c.raw);')
else:
 assert snapshot_source.count(snapshot_scope)==1,'Snapshot timing scope duplicated'
print('F3 stutter watch, F9 reset and F4 frame-history export staged; no periodic file writes.')
