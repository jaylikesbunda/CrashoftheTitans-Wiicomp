"""Move qualified snapshot transforms and regular texgen to GL vertex shaders."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
def edit(relative,fn):
    path=rt/relative;s=path.read_text();baseline=p/'runtime-overlays'/('gpu-before-'+path.name)
    if not baseline.exists():baseline.write_text(s)
    n=fn(s)
    if n!=s:path.write_text(n)
header='''#pragma once
#include <cstdlib>
namespace nwii::runtime::gx {
inline bool NativeGpuVertexEnabled(){static const bool enabled=[](){
 const char* blocked[]={"NWII_NO_GPU_VERTEX","NWII_NOSHADER","NWII_NORMAL_DRAW_PROBE","NWII_EFB_PROBE","NWII_VTXTRACE","NWII_SKINDBG","NWII_DRAWLOG"};
 for(auto key:blocked)if(std::getenv(key))return false;return true;}();return enabled;}
}
'''
hp=rt/'include/runtime/native_gpu_vertex.h'
if not hp.exists() or hp.read_text()!=header:hp.write_text(header)
def command(s):
    if 'native_gpu_snapshot' not in s:s=s.replace('    std::shared_ptr<DrawRaw> raw;','    std::shared_ptr<DrawRaw> raw;\n    std::shared_ptr<DrawRaw> native_gpu_snapshot; // qualified parse-time XF banks')
    return s
edit('include/runtime/gx_command.h',command)
def parser(s):
    if 'NATIVE_GPU_DECODE_V1' in s:
        s=s.replace('    native_trace::Scope trace_decode("gx.decode");\n','')
        return s.replace('void FifoParser::DecodeDraw(GXCommand& c) {','void FifoParser::DecodeDraw(GXCommand& c) {\n    native_trace::Scope trace_decode("gx.decode");',1)
    s='#include "runtime/native_gpu_vertex.h"\n'+s
    # Both generic and common indexed decoders retain CPU fallback transforms.
    s=s.replace('if (!r.snap_pos_bank.empty() && vtx.has_pos) {','if (!c.native_gpu_snapshot && !r.snap_pos_bank.empty() && vtx.has_pos) {')
    s=s.replace('} else if (r.have_snap_mtx && vtx.has_pos) {','} else if (!c.native_gpu_snapshot && r.have_snap_mtx && vtx.has_pos) {')
    s=s.replace('void FifoParser::DecodeDraw(GXCommand& c) {','static void NativeDecodeDispatch(GXCommand& c) {',1)
    a=s.index('\nvoid FifoParser::Parse(')
    wrapper='''
// NATIVE_GPU_DECODE_V1: snapshots survive deferred decoding; RAM bytes do not.
void FifoParser::DecodeDraw(GXCommand& c) {
 if(!c.raw){return;}
 auto snapshot=c.raw;
 const bool triangles=c.prim_type==0x80 || c.prim_type==0x90 || c.prim_type==0x98 || c.prim_type==0xA0;
 if(NativeGpuVertexEnabled() && triangles && (snapshot->have_snap_mtx || snapshot->snap_pos_bank.size()==256))c.native_gpu_snapshot=snapshot;
 NativeDecodeDispatch(c);
 if(c.native_gpu_snapshot){
  bool valid=true;
  for(const auto& v:c.vertices)valid=valid && v.has_pos && !v.pre_xf && unsigned(v.posMtxIdx)*4+12<=256 && 3u*(v.posMtxIdx&31)+9<=96;
  if(snapshot->snap_pos_bank.size()==256)valid=valid && snapshot->snap_nrm_bank.size()==96;
  if(!valid){c.native_gpu_snapshot.reset();c.raw=snapshot;c.vertices.clear();c.native_vertex_indices.clear();NativeDecodeDispatch(c);}
  else {native_trace::mark("decode.gpu_vertices",c.vertices.size());}
 }
}
'''
    s=s[:a]+wrapper+s[a:]
    s=s.replace('    native_trace::Scope trace_decode("gx.decode");\n','')
    return s.replace('void FifoParser::DecodeDraw(GXCommand& c) {','void FifoParser::DecodeDraw(GXCommand& c) {\n    native_trace::Scope trace_decode("gx.decode");',1)
edit('src/hle/gx/fifo_parser.cpp',parser)
def renderer(s):
    if 'NATIVE_GPU_VERTEX_PIPELINE_V1' in s:
        return s.replace('const unsigned generators=std::min(8u,std::max(1u,unsigned(g_state.bp[0]&15u)));','const unsigned generators=s_gpu_vertex_mode?8u:std::min(8u,std::max(1u,unsigned(g_state.bp[0]&15u)));',1)
    s='#include "runtime/native_gpu_vertex.h"\n'+s
    s=s.replace('static std::vector<BatchVertex> s_batch;','static bool s_gpu_vertex_mode=false;\nstatic std::shared_ptr<DrawRaw> s_gpu_vertex_snapshot;\nstatic std::vector<BatchVertex> s_batch;',1)
    a=s.index('static void NativeWriteConvertedVertex(');b=s.index('\n',a)+1
    branch='''  // NATIVE_GPU_VERTEX_PIPELINE_V1: existing layout carries raw attributes.
  if(s_gpu_vertex_mode){
    bv.x=vtx.pos[0];bv.y=vtx.pos[1];bv.z=vtx.pos[2];
    bv.nx=vtx.has_norm?vtx.norm[0]:0;bv.ny=vtx.has_norm?vtx.norm[1]:0;bv.nz=vtx.has_norm?vtx.norm[2]:0;
    bv.u=float(vtx.posMtxIdx);bv.v=vtx.has_norm?1.f:0.f;
    for(unsigned j=0;j<4;++j){(&bv.r)[j]=vtx.has_color?vtx.color[j]:1.f;bv.color1[j]=vtx.color1[j];}
    for(unsigned t=0;t<8;++t){bv.texgen[t][0]=vtx.tex[t][0];bv.texgen[t][1]=vtx.tex[t][1];bv.texgen[t][2]=float(vtx.texMtxIdx[t]);}
    return;
  }
'''
    s=s[:b]+branch+s[b:]
    # GPU texgen can reference any input coordinate even with one output generator.
    s=s.replace('const unsigned generators=std::min(8u,std::max(1u,unsigned(g_state.bp[0]&15u)));','const unsigned generators=s_gpu_vertex_mode?8u:std::min(8u,std::max(1u,unsigned(g_state.bp[0]&15u)));',1)
    # The snapshot can change between adjacent draws despite matching TEV state.
    a=s.index('static void DrawPrimitive(')
    pos=s.index('  if (!s_batch_open ||',a)
    boundary='''  const bool gpu=bool(cmd.native_gpu_snapshot);
  const auto old=s_gpu_vertex_snapshot;
  bool matrix_changed=gpu!=s_gpu_vertex_mode;
  if(gpu && old && old!=cmd.native_gpu_snapshot){const auto& q=*cmd.native_gpu_snapshot;
    matrix_changed=old->snap_pos_bank!=q.snap_pos_bank || old->snap_nrm_bank!=q.snap_nrm_bank || old->have_snap_mtx!=q.have_snap_mtx ||
      std::memcmp(old->snap_pos_mtx,q.snap_pos_mtx,sizeof(q.snap_pos_mtx)) || std::memcmp(old->snap_nrm_mtx,q.snap_nrm_mtx,sizeof(q.snap_nrm_mtx));}
  if(matrix_changed && s_batch_open){FlushBatch();s_batch_open=false;}
  s_gpu_vertex_mode=gpu;s_gpu_vertex_snapshot=cmd.native_gpu_snapshot;
'''
    s=s[:pos]+boundary+s[pos:]
    # All shader programs share the same UBO layout. CPU draws use mode zero.
    a=s.index('static void SetupDrawState(const GXCommand &cmd) {')
    helper='''// std140 rows; avoids exceeding GL 3.3 ordinary uniform limits.
struct NativeGpuXfBlock {float pos[64][4],normal[32][4],tex[64][4],post[64][4];int info[8][4],control[4];};
static void NativeUploadGpuXf(){
 static GLuint buffer=0;static NativeGpuXfBlock previous{};static bool have=false;
 NativeGpuXfBlock block{};
 block.control[0]=s_gpu_vertex_mode?(s_gpu_vertex_snapshot->snap_pos_bank.empty()?2:1):0;
 if(s_gpu_vertex_mode){const auto& q=*s_gpu_vertex_snapshot;
   if(block.control[0]==1){std::memcpy(block.pos,q.snap_pos_bank.data(),1024);for(unsigned row=0;row<32;++row)std::memcpy(block.normal[row],q.snap_nrm_bank.data()+row*3,12);}
   else {std::memcpy(block.pos,q.snap_pos_mtx,48);for(unsigned row=0;row<3;++row)std::memcpy(block.normal[row],q.snap_nrm_mtx+row*3,12);}
   std::memcpy(block.tex,g_state.posMatrices,1024);std::memcpy(block.post,g_state.postMatrices,1024);
   for(unsigned t=0;t<8;++t){block.info[t][0]=int(g_state.xf[0x40+t]);block.info[t][1]=int(g_state.xf[0x50+t]);}
   block.control[1]=g_state.xf[0x12]&1;block.control[2]=std::min(8u,std::max(1u,unsigned(g_state.bp[0]&15u)));
 }
 if(!buffer){glGenBuffers(1,&buffer);glBindBufferBase(GL_UNIFORM_BUFFER,1,buffer);}
 if(!have || std::memcmp(&previous,&block,sizeof(block))){glBindBuffer(GL_UNIFORM_BUFFER,buffer);glBufferData(GL_UNIFORM_BUFFER,sizeof(block),&block,GL_STREAM_DRAW);previous=block;have=true;native_trace::mark("upload.xf_bytes",sizeof(block));}
}
'''
    s=s[:a]+helper+s[a:]
    a=s.index('static void SetupDrawState(const GXCommand &cmd) {');b=s.index('\n',a)+1
    s=s[:b]+'  NativeUploadGpuXf();\n'+s[b:]
    marker='    s_shader_cache[hash] = sh;'
    s=s.replace(marker,'    const GLuint xfblock=glGetUniformBlockIndex(sh.id,"NativeGpuXF");\n    if(xfblock!=GL_INVALID_INDEX)glUniformBlockBinding(sh.id,xfblock,1);\n'+marker,1)
    return s
edit('src/hle/gx/renderer_gl.cpp',renderer)
glsl='''
layout(std140) uniform NativeGpuXF {
 vec4 gxPos[64];vec4 gxNormal[32];vec4 gxTex[64];vec4 gxPost[64];ivec4 gxInfo[8];ivec4 gxControl;
};
float gxRow(vec4 row,vec4 v){return ((row.x*v.x+row.y*v.y)+row.z*v.z)+row.w*v.w;}
vec3 gxPosition(vec3 p,int index){int b=gxControl.x==2?0:index;
 return vec3(gxRow(gxPos[b],vec4(p,1)),gxRow(gxPos[b+1],vec4(p,1)),gxRow(gxPos[b+2],vec4(p,1)));}
vec3 gxNrm(vec3 n,int index){int b=gxControl.x==2?0:(index&31);
 return vec3(gxRow(gxNormal[b],vec4(n,0)),gxRow(gxNormal[b+1],vec4(n,0)),gxRow(gxNormal[b+2],vec4(n,0)));}
vec3 gxTexgen(int t,vec3 p,vec3 n,vec3 inputs[8]){
 int info=gxInfo[t].x;int source=(info>>7)&31;vec4 inputValue=vec4(0,0,1,1);
 if(source==0)inputValue.xyz=p;else if(source==1)inputValue.xyz=n;
 else if(source>=5 && source<=12)inputValue.xy=inputs[source-5].xy;
 if((info&4)==0)inputValue.z=1;
 int index=int(inputs[t].z);int b=index;
 vec3 result=vec3(inputValue.xy,(info&2)!=0?inputValue.z:1);
 if(index!=60 && b+3<=64){result.x=gxRow(gxTex[b],inputValue);result.y=gxRow(gxTex[b+1],inputValue);if((info&2)!=0)result.z=gxRow(gxTex[b+2],inputValue);}
 if(gxControl.y!=0){int post=gxInfo[t].y;vec3 value=result;
   if((post&256)!=0){float len=sqrt((value.x*value.x+value.y*value.y)+value.z*value.z);if(len>0)value/=len;}
   int start=post&63;for(int row=0;row<3;++row){vec4 m=gxPost[(start+row)&63];result[row]=((m.w+m.x*value.x)+m.y*value.y)+m.z*value.z;}
 }
 return result;
}
'''
def shader(s):
    if 'NATIVE_GPU_SHADER_V1' in s:return s
    anchor='    vs << "void main() {\\n";'
    insert='    // NATIVE_GPU_SHADER_V1\n    vs << R"('+glsl+')";\n'
    s=s.replace(anchor,insert+anchor,1)
    s=s.replace('    vs << "    gl_Position = mvp * vec4(vertexPosition, 1.0);\\n";','''    vs << "    vec3 viewPosition=vertexPosition,viewNormal=vertexNormal;\\n";
    vs << "    if(gxControl.x!=0){viewPosition=gxPosition(vertexPosition,int(vertexTexCoord.x));if(vertexTexCoord.y!=0)viewNormal=gxNrm(vertexNormal,int(vertexTexCoord.x));}\\n";
    vs << "    gl_Position = mvp * vec4(viewPosition, 1.0);\\n";''',1)
    s=s.replace('dolphin_chan(0, vertexPosition, vertexNormal, vertexColor)','dolphin_chan(0, viewPosition, viewNormal, vertexColor)').replace('dolphin_chan(1, vertexPosition, vertexNormal, vertexColor1)','dolphin_chan(1, viewPosition, viewNormal, vertexColor1)')
    anchor='    for (unsigned i=0;i<8;++i) {\n        const unsigned type='
    s=s.replace(anchor,'    vs << "    vec3 gxInputs[8]=vec3[8](generatedTex0,generatedTex1,generatedTex2,generatedTex3,generatedTex4,generatedTex5,generatedTex6,generatedTex7);\\n";\n'+anchor,1)
    s=s.replace('else vs << "    vTex" << i << " = generatedTex" << i << ";\\n";','else vs << "    vTex" << i << " = gxControl.x!=0 ? (" << i << "<gxControl.z ? gxTexgen(" << i << ",vertexPosition,vertexNormal,gxInputs):vec3(0)) : generatedTex" << i << ";\\n";',1)
    return s
edit('src/hle/gx/tev_shader_gen.cpp',shader)
print('Snapshot GPU transforms/texgen staged; CPU diagnostics/lines/invalid-bank fallback retained.')
