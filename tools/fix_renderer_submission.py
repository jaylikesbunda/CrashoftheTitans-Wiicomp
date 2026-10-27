"""Retain order while batching compatible topology and harmless repeated BP state."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime';f=rt/'src/hle/gx/renderer_gl.cpp';s=f.read_text()
if 'NATIVE_SUBMISSION_BATCH_V1' not in s:
    (p/'runtime-overlays/submission-before-renderer.cpp').write_text(s)
    shader=(rt/'src/hle/gx/tev_shader_gen.cpp').read_text()
    assert shader.count('prim_type')==1,'Requalify shaders if topology changes their generated code'
    s=s.replace('uint64_t hash = state.GetShaderHash(primitive);','uint64_t hash = state.GetShaderHash(0x90); // vertex/fragment source is topology-independent',1)
    helper='''// NATIVE_SUBMISSION_BATCH_V1: indexed quads/strips/fans all submit ordered triangles.
static uint8_t NativeBatchTopology(uint8_t primitive,size_t count){
 if(primitive==0x80 || primitive==0x98 || primitive==0xA0 || (primitive==0x90 && count%3==0))return 0x80;
 return primitive;
}
// Conservative whitelist: repeated commands with side effects ALWAYS remain boundaries.
static bool NativeRedundantBp(const GXCommand& cmd){
 if(cmd.type!=GXCommandType::BPRegister)return false;
 const unsigned reg=cmd.reg;
 const bool ordinary=reg==0 || (reg>=0x20 && reg<=0x2f) || (reg>=0x40 && reg<=0x43) ||
     (reg>=0xc0 && reg<=0xe7) || (reg>=0xe9 && reg<=0xfd);
 if(!ordinary)return false;
 const uint32_t resolved=((s_native_bp_shadow[reg]&~s_native_bp_mask)|(cmd.val&s_native_bp_mask))&0xffffff;
 return resolved==s_native_bp_shadow[reg];
}
'''
    a=s.index('static void DrawPrimitive(');s=s[:a]+helper+s[a:]
    s=s.replace('  if (!s_batch_open || s_batch_prim != (uint8_t)cmd.prim_type) {','  const uint8_t topology=NativeBatchTopology(cmd.prim_type,n);\n  if (!s_batch_open || s_batch_prim != topology) {',1)
    s=s.replace('    s_batch_prim = (uint8_t)cmd.prim_type;','    s_batch_prim = topology;',1)
    s=s.replace('cmd.type != GXCommandType::CPRegister && s_batch_open','cmd.type != GXCommandType::CPRegister && !NativeRedundantBp(cmd) && s_batch_open',1)
    f.write_text(s)
s=f.read_text()
# Rebinding even an identical texture register can expose changed guest texels.
s=s.replace('(reg>=0x80 && reg<=0x9f) || (reg>=0xa0 && reg<=0xbf) || ','')
if 'NATIVE_MIXED_INDEX_PREFIX_V1' not in s:
    anchor='  auto index=[&](size_t i){return base+uint32_t(reused?cmd.native_vertex_indices[i]:i);};'
    assert anchor in s
    s=s.replace(anchor,anchor+'\n  // NATIVE_MIXED_INDEX_PREFIX_V1: retain a preceding DrawArrays triangle prefix.\n  if(cmd.prim_type!=0x90 && s_indices.empty())for(uint32_t i=0;i<base;++i)s_indices.push_back(i);',1)
if f.read_text()!=s:f.write_text(s)
print('Compatible triangle topology and conservative identical BP state batching staged.')
