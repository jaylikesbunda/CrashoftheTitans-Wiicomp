"""Compact GPU decoding and contiguous uploads, retaining the established fallback."""
from pathlib import Path
from packed_decode_codegen import generate
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
header=rt/'include/runtime/native_packed_buffer.h';value=(p/'runtime-overlays/native_packed_buffer.h').read_text()
if not header.exists() or header.read_text()!=value:header.write_text(value)
h=rt/'include/runtime/gx_command.h';s=h.read_text()
if 'NATIVE_PACKED_COMMAND_V1' not in s:
 s=s.replace('#include <memory>','#include <memory>\n#include "runtime/native_packed_buffer.h"',1)
 old='''    size_t NativeVertexCount() const {return native_vertex_indices.empty()?vertices.size():native_vertex_indices.size();}
    const VertexData& NativeVertex(size_t i) const {return vertices[native_vertex_indices.empty()?i:native_vertex_indices[i]];}'''
 new='''    // NATIVE_PACKED_COMMAND_V1: only one representation owns decoded storage.
    NativePackedBuffer native_packed;
    uint8_t native_packed_inputs=8,native_packed_stride=0;
    size_t NativeStoredVertexCount() const {return native_packed.empty()?vertices.size():native_packed.size()/native_packed_stride;}
    size_t NativeVertexCount() const {return native_vertex_indices.empty()?NativeStoredVertexCount():native_vertex_indices.size();}
    VertexData NativeDecodedVertex(size_t i) const;
    VertexData NativeVertex(size_t i) const {return NativeDecodedVertex(native_vertex_indices.empty()?i:native_vertex_indices[i]);}'''
 assert s.count(old)==1;s=s.replace(old,new,1);h.write_text(s)
f=rt/'src/hle/gx/fifo_parser.cpp';s=f.read_text()
start='// NATIVE_PACKED_DECODE_V1: same attribute reads, final GPU float layout.'
end='// NATIVE_PACKED_DECODE_END'
if start in s:
 a=s.index(start);b=s.index(end,a)+len(end);s=s[:a]+s[b:]
generated=generate(s)
dispatch='''
static bool NativeTryPackedDecode(GXCommand& c){
 static const bool enabled=!std::getenv("NWII_NO_PACKED_DECODE") && !std::getenv("NWII_NO_FUSED_BATCH") && !std::getenv("NWII_DRAW_ARCHIVE") && !std::getenv("NWII_EFB_PROBE") && !std::getenv("NWII_STATE_CENSUS");
 if(!enabled || !c.raw || !c.vertices.empty() || !c.native_packed.empty() || !NativeGpuVertexEnabled())return false;
 if(c.prim_type!=0x80 && c.prim_type!=0x90 && c.prim_type!=0x98 && c.prim_type!=0xa0)return false;
 const auto& r=*c.raw;const auto& v=r.vat;
 if(!r.have_snap_mtx && r.snap_pos_bank.size()!=256)return false;
 if(r.snap_pos_bank.size()==256 && r.snap_nrm_bank.size()!=96)return false;
 if(uint64_t(VertexSize(v))*r.count>r.bytes.size())return false;
 const unsigned inputs=NativePackedStorageInputs(r,c.native_packed_inputs),words=16+3*inputs;
 // Wide layouts did not consistently win in the combined probe. Keep them on
 // the established route until a real workload justifies extending coverage.
 static const bool wide=std::getenv("NWII_PACKED_WIDE")!=nullptr;
 if(inputs>2 && !wide)return false;
 float defaults[40];NativePackedDefaults(r,defaults,inputs);
 NativePackedBuffer output;std::vector<uint32_t> indices;
 auto indexed=[](VtxAttrMask m){return m==VtxAttrMask::Index8 || m==VtxAttrMask::Index16;};
 bool fast=indexed(v.posMask)&&indexed(v.nrmMask)&&indexed(v.texMask[0])&&v.posType==VtxAttrType::F32&&v.posElements&&v.nrmType==VtxAttrType::F32&&!v.nrmElements&&v.texType[0]==VtxAttrType::F32&&v.texElements[0];
 for(unsigned t=1;t<8;++t)fast=fast && v.texMask[t]==VtxAttrMask::None;
 bool ok=false;
 if(fast){unsigned key=(v.posMask==VtxAttrMask::Index16?4:0)|(v.nrmMask==VtxAttrMask::Index16?2:0)|(v.texMask[0]==VtxAttrMask::Index16?1:0);
 switch(key){
 case 0:ok=NativePackedIndexed<2,2,2>(c,output,indices,inputs,words,defaults);break;
 case 1:ok=NativePackedIndexed<2,2,3>(c,output,indices,inputs,words,defaults);break;
 case 2:ok=NativePackedIndexed<2,3,2>(c,output,indices,inputs,words,defaults);break;
 case 3:ok=NativePackedIndexed<2,3,3>(c,output,indices,inputs,words,defaults);break;
 case 4:ok=NativePackedIndexed<3,2,2>(c,output,indices,inputs,words,defaults);break;
 case 5:ok=NativePackedIndexed<3,2,3>(c,output,indices,inputs,words,defaults);break;
 case 6:ok=NativePackedIndexed<3,3,2>(c,output,indices,inputs,words,defaults);break;
 case 7:ok=NativePackedIndexed<3,3,3>(c,output,indices,inputs,words,defaults);break;}}
 else {
 bool direct=v.posMask==VtxAttrMask::Direct && v.posType==VtxAttrType::F32 && v.posElements && v.nrmMask==VtxAttrMask::Direct && v.nrmType==VtxAttrType::F32 && !v.nrmElements && v.texMask[0]==VtxAttrMask::Direct && v.texType[0]==VtxAttrType::F32 && v.texElements[0];
 for(unsigned t=1;t<8;++t)direct=direct && v.texMask[t]==VtxAttrMask::None;
 ok=direct?NativePackedGeneric<true>(c,output,indices,inputs,words,defaults):NativePackedGeneric<false>(c,output,indices,inputs,words,defaults);
 }
 if(!ok)return false;
 c.native_packed=std::move(output);c.native_packed_stride=uint8_t(words);
 c.native_vertex_indices=std::move(indices);c.native_gpu_snapshot=c.raw;c.raw.reset();
 native_trace::mark("decode.packed_vertices",c.NativeStoredVertexCount(),words*sizeof(float));
 return true;
}
// Tuple-reused draws retain the qualified dedup decoder. Packing its unique
// vertices on the decode worker removes scalar conversion from the render
// owner without replacing the faster high-reuse decoder.
static void NativePrepackDecodedGpu(GXCommand& c){
 static const bool enabled=!std::getenv("NWII_NO_PACKED_DECODE") && !std::getenv("NWII_NO_FUSED_BATCH") && !std::getenv("NWII_DRAW_ARCHIVE") && !std::getenv("NWII_EFB_PROBE") && !std::getenv("NWII_STATE_CENSUS");
 if(!enabled || !c.native_gpu_snapshot || c.vertices.empty() || !c.native_packed.empty())return;
 const unsigned inputs=NativePackedStorageInputs(*c.native_gpu_snapshot,c.native_packed_inputs);
 if(inputs>2 && !std::getenv("NWII_PACKED_WIDE"))return;
 const unsigned words=16+3*inputs;c.native_packed.resize(c.vertices.size()*words);c.native_packed_stride=uint8_t(words);
 for(size_t i=0;i<c.vertices.size();++i){const auto& v=c.vertices[i];float* p=c.native_packed.data()+i*words;
  std::memcpy(p,v.pos,12);if(v.has_color)std::memcpy(p+3,v.color,16);else std::fill(p+3,p+7,1.f);
  p[7]=float(v.posMtxIdx);p[8]=v.has_norm?1.f:0.f;if(v.has_norm)std::memcpy(p+9,v.norm,12);else std::fill(p+9,p+12,0.f);
  std::memcpy(p+12,v.color1,16);for(unsigned t=0;t<inputs;++t){p[16+3*t]=v.tex[t][0];p[17+3*t]=v.tex[t][1];p[18+3*t]=float(v.texMtxIdx[t]);}
 }
 std::vector<VertexData>().swap(c.vertices);native_trace::mark("decode.prepacked_vertices",c.NativeStoredVertexCount(),words*sizeof(float));
}
VertexData GXCommand::NativeDecodedVertex(size_t i) const {
 if(native_packed.empty())return vertices[i];
 const float* p=native_packed.data()+i*native_packed_stride;VertexData v;
 std::memcpy(v.pos,p,12);std::memcpy(v.color,p+3,16);v.posMtxIdx=uint8_t(p[7]);v.has_norm=p[8]!=0;
 std::memcpy(v.norm,p+9,12);std::memcpy(v.color1,p+12,16);v.has_pos=true;
 const auto& r=*native_gpu_snapshot;v.has_color=r.vat.clrMask[0]!=VtxAttrMask::None;v.has_color1=r.vat.clrMask[1]!=VtxAttrMask::None;
 if(!v.has_color)std::fill(v.color,v.color+4,0.f); // GPU white default is not the raw vertex's absent-color storage
 for(unsigned t=0;t<8;++t){v.texMtxIdx[t]=r.defTexMtxIdx[t];v.has_tex[t]=r.vat.texMask[t]!=VtxAttrMask::None;
  if(16+3*t<native_packed_stride){v.tex[t][0]=p[16+3*t];v.tex[t][1]=p[17+3*t];v.texMtxIdx[t]=uint8_t(p[18+3*t]);}}
 std::memcpy(v.object_pos,v.pos,12);std::memcpy(v.object_norm,v.norm,12);return v;
}
// NATIVE_PACKED_DECODE_END
'''
a=s.index('void FifoParser::DecodeDraw(GXCommand& c)');s=s[:a]+generated+dispatch+'\n'+s[a:]
old='    native_trace::Scope trace_decode("gx.decode");\n if(!c.raw){return;}'
new='    native_trace::Scope trace_decode("gx.decode");\n if(NativeTryPackedDecode(c))return;\n if(!c.raw){return;}'
if new not in s and 'if(NativeTryPackedDecode(c))' not in s:assert s.count(old)==1;s=s.replace(old,new,1)
hook=' native_hud::work_decoded(c.vertices.size(),0);'
if hook in s:s=s.replace(hook,' NativePrepackDecodedGpu(c);\n native_hud::work_decoded(c.NativeStoredVertexCount(),c.native_packed.empty()?0:c.NativeStoredVertexCount());',1)
elif ' NativePrepackDecodedGpu(c);' not in s:
 old='  else {native_trace::mark("decode.gpu_vertices",c.vertices.size());}\n }\n}'
 new=old[:-2]+' NativePrepackDecodedGpu(c);\n}'
 assert s.count(old)==1;s=s.replace(old,new,1)
if s!=f.read_text():f.write_text(s)

f=rt/'src/hle/gx/renderer_gl.cpp';s=f.read_text()
def part(old,new):
 global s
 if new in s:return
 assert s.count(old)==1,(old[:100],s.count(old));s=s.replace(old,new,1)
part('struct NativeGpuSpan {const VertexData* vertices; size_t count;};',
 'struct NativeGpuSpan {const VertexData* vertices; size_t count; const GXCommand* packed=nullptr;}; // NATIVE_PACKED_SPAN_V1')
part('NativeWriteConvertedVertex(span.vertices[i],s_batch.emplace_back_uninitialized());',
 'NativeWriteConvertedVertex(span.packed?span.packed->NativeDecodedVertex(i):span.vertices[i],s_batch.emplace_back_uninitialized());')
part('    const size_t emitted=reused?v.size():count;\n    s_gpu_spans.push_back({v.data(),emitted});s_gpu_span_vertices+=emitted;',
 '    const size_t emitted=reused?cmd.NativeStoredVertexCount():count;\n    s_gpu_spans.push_back({v.data(),emitted,cmd.native_packed.empty()?nullptr:&cmd});s_gpu_span_vertices+=emitted;')
part('    if(reused)for(const auto& vertex:v)EmitVertex(vertex);\n    else for(size_t i=0;i<count;++i)EmitVertex(v[i]);',
 '    if(!cmd.native_packed.empty()){for(size_t i=0;i<(reused?cmd.NativeStoredVertexCount():count);++i)EmitVertex(cmd.NativeDecodedVertex(i));}\n    else if(reused)for(const auto& vertex:v)EmitVertex(vertex);\n    else for(size_t i=0;i<count;++i)EmitVertex(v[i]);')
old='    for(const auto& span:s_gpu_spans)for(size_t i=0;i<span.count;++i,cursor+=stride)NativePackGpuVertex(span.vertices[i],reinterpret_cast<float*>(cursor),generators);'
new='''    for(const auto& span:s_gpu_spans){
      if(span.packed){const auto& c=*span.packed;const size_t source_stride=c.native_packed_stride*sizeof(float);
        if(source_stride==stride){std::memcpy(cursor,c.native_packed.data(),span.count*stride);cursor+=span.count*stride;}
        else for(size_t i=0;i<span.count;++i,cursor+=stride)std::memcpy(cursor,c.native_packed.data()+i*c.native_packed_stride,stride);
      }else for(size_t i=0;i<span.count;++i,cursor+=stride)NativePackGpuVertex(span.vertices[i],reinterpret_cast<float*>(cursor),generators);
    }'''
part(old,new)
# Diagnostic summaries keep access to the original decoded semantics.
part('for (const auto &vtx : cmd.vertices) { ++total;',
 'for (size_t vi=0;vi<cmd.NativeStoredVertexCount();++vi) { const auto vtx=cmd.NativeDecodedVertex(vi); ++total;')
part('      const VertexData& v = sp.vertices[i];',
 '      const auto v = sp.packed?sp.packed->NativeDecodedVertex(i):sp.vertices[i];')
# Capture the shader input requirements by replaying only the relevant state
# into locals. The real render state and side effects are untouched.
layout='''// NATIVE_PACKED_LAYOUT_REPLAY_V1: same GEN/XF input projection as NativeUploadBatch.
void NativePreparePackedLayouts(std::vector<GXCommand>& commands){
 uint32_t bp[256];std::memcpy(bp,s_native_bp_shadow,sizeof(bp));uint32_t mask=s_native_bp_mask;
 uint32_t info[8];std::memcpy(info,g_state.xf+0x40,sizeof(info));
 for(auto& c:commands){
  if(c.type==GXCommandType::BPRegister){const unsigned reg=c.reg;const uint32_t resolved=((bp[reg]&~mask)|(c.val&mask))&0xffffff;
   bp[reg]=resolved;mask=reg==0xfe?resolved:0xffffff;
  }else if(c.type==GXCommandType::XFRegister){for(size_t i=0;i<c.payload.size();++i){unsigned reg=c.reg+unsigned(i);if(reg>=0x1040 && reg<0x1048)std::memcpy(info+reg-0x1040,&c.payload[i],4);}}
  else if(c.type==GXCommandType::DrawPrimitive){unsigned active=std::min(8u,std::max(1u,bp[0]&15u)),required=active;
   for(unsigned t=0;t<active;++t){unsigned type=(info[t]>>4)&7u,source=(info[t]>>7)&31u;if(type!=2 && type!=3 && source>=5 && source<=12)required=std::max(required,source-4u);}
   c.native_packed_inputs=uint8_t(required);
  }
 }
}
'''
if layout not in s:
 anchor='static void NativeRestoreBpSnapshot() {';a=s.index(anchor);s=s[:a]+layout+s[a:]
if s!=f.read_text():f.write_text(s)
f=rt/'src/hle/gx.cpp';s=f.read_text()
old='    // Decode all parsed draws before executing their FIFO commands.'
new='''    // NATIVE_PACKED_LAYOUT_PREPARE_V1: layout captured on the render owner before helpers decode.
    extern void NativePreparePackedLayoutsBridge(std::vector<nwii::runtime::gx::GXCommand>&);
    NativePreparePackedLayoutsBridge(frame);
'''+old
if new not in s:assert s.count(old)==1;s=s.replace(old,new,1);f.write_text(s)
f=rt/'src/hle/gx/renderer_gl.cpp';s=f.read_text()
bridge='''
void NativePreparePackedLayoutsBridge(std::vector<nwii::runtime::gx::GXCommand>& commands){nwii::runtime::gx::NativePreparePackedLayouts(commands);}
'''
if bridge not in s:f.write_text(s+bridge)
print('Compact GPU decode, layout replay and contiguous upload staged; NWII_NO_PACKED_DECODE restores the old route.')
