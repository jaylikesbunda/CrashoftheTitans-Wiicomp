"""Derive compact GPU decoders from the actual qualified readers and loop bodies.

The CPU transforms and diagnostic paths remain in the original decoders. The
compact attempt commits only complete valid GPU draws; otherwise it discards its
own output and the established decoder runs from the original immutable bytes.
"""
from pathlib import Path

def generate(source):
 helpers=r'''
// NATIVE_PACKED_DECODE_V1: same attribute reads, final GPU float layout.
struct NativePackedProxy {
 float *pos,*norm,*color,*color1,*tex[8];
 uint8_t posMtxIdx=0,texMtxIdx[8]={};
 bool has_pos=false,has_norm=false,has_color=false,has_color1=false,has_tex[8]={},pre_xf=false;
 NativePackedProxy(float* out,unsigned inputs):pos(out),norm(out+9),color(out+3),color1(out+12){
  for(unsigned t=0;t<8;++t)tex[t]=t<inputs?out+16+3*t:nullptr;
 }
 void finish(float* out,unsigned inputs){out[7]=float(posMtxIdx);out[8]=has_norm?1.f:0.f;for(unsigned t=0;t<inputs;++t)out[18+t*3]=float(texMtxIdx[t]);}
 bool valid()const{return has_pos && !pre_xf && unsigned(posMtxIdx)*4+12<=256 && 3u*(posMtxIdx&31)+9<=96;}
};
static unsigned NativePackedStorageInputs(const DrawRaw& r,unsigned required){
 unsigned inputs=std::clamp(required,1u,8u);
 for(unsigned t=0;t<8;++t)if(r.vat.texMask[t]!=VtxAttrMask::None || r.vat.texMatIdx[t])inputs=std::max(inputs,t+1);
 return inputs;
}
static void NativePackedDefaults(const DrawRaw& r,float* defaults,unsigned inputs){
 std::fill(defaults,defaults+16+3*inputs,0.f);
 for(unsigned i=3;i<7;++i)defaults[i]=1.f;
 for(unsigned i=12;i<16;++i)defaults[i]=1.f;
 for(unsigned t=0;t<inputs;++t)defaults[18+3*t]=float(r.defTexMtxIdx[t]);
}
'''
 def decoder(indexed):
  start=source.index('template<int P,int N,int T> static void NativeIndexedDecodeDraw' if indexed else 'template<bool Direct> static void NativeDecodeDraw')
  begin=source.index('    const DrawRaw& r',start)
  transform=source.index('        // Transform by the per-draw matrix snapshot',begin)
  body=source[begin:transform]
  # Crop loop after all declared input reads and stream-size checks. GPU
  # candidate validation is identical, performed before publishing any output.
  body=body.replace('    native_trace::mark("decode.vertices",r.count,r.bytes.size());\n','')
  body=body.replace('    c.vertices.reserve(r.count);','    output.resize(size_t(r.count)*words);')
  body=body.replace('    if(reuse)c.vertices.reserve(r.count/2+1);else c.vertices.resize(r.count);','    output.resize(size_t(r.count)*words);')
  body=body.replace('c.native_vertex_indices','indices')
  if indexed:
   anchor='    std::vector<uint32_t> slots,first;'
   assert body.count(anchor)==1
   body=body.replace(anchor,'    static const bool experimental_reuse=std::getenv("NWII_PACKED_REUSE")!=nullptr;\n    if(reuse && inputs>2 && !experimental_reuse)return false;\n'+anchor,1)
  body=body.replace('        c.vertices.emplace_back();\n        VertexData& vtx=c.vertices.back();',
    '        float* destination=output.data()+size_t(i)*words;\n        std::memcpy(destination,defaults,words*sizeof(float));\n        NativePackedProxy vtx(destination,inputs);')
  body=body.replace('          c.vertices.emplace_back();','')
  body=body.replace('        VertexData& vtx=c.vertices[reuse?unique-1:i];',
    '        float* destination=output.data()+size_t(reuse?unique-1:i)*words;\n        std::memcpy(destination,defaults,words*sizeof(float));\n        NativePackedProxy vtx(destination,inputs);')
  body=body.replace('{c.vertices.pop_back();return;}','{return false;}').replace('{c.vertices.resize(i);return;}','{return false;}')
  body=body.replace('        for(unsigned j=0;j<3;++j) {vtx.object_pos[j]=vtx.pos[j];vtx.object_norm[j]=vtx.has_norm?vtx.norm[j]:0;}\n','')
  body=body.replace('        NativeValidateDecodedGpuVertex(c,vtx);','')
  body=body.replace('        if(!reuse)NativeValidateDecodedGpuVertex(c,vtx);','')
  assert 'c.vertices' not in body and 'return;' not in body and 'object_pos' not in body
  # Obtain mutable storage once. Completed retained buffers use copy-on-write;
  # checking unique ownership per vertex would defeat the specialized loop.
  body=body.replace('    output.resize(size_t(r.count)*words);','    output.resize(size_t(r.count)*words);\n    float* storage=output.data();',1)
  body=body.replace('output.data()+size_t(', 'storage+size_t(')
  name='template<int P,int N,int T> static bool NativePackedIndexed' if indexed else 'template<bool Direct> static bool NativePackedGeneric'
  tail='\n        if(!vtx.valid())return false;\n        vtx.finish(destination,inputs);\n    }\n'
  tail+=('    output.resize(size_t(reuse?unique:r.count)*words);\n' if indexed else '')+'    return true;\n}\n'
  return name+'(GXCommand& c,NativePackedBuffer& output,std::vector<uint32_t>& indices,unsigned inputs,unsigned words,const float* defaults){\n'+body+tail
 return helpers+decoder(False)+decoder(True)

if __name__=='__main__':
 import argparse
 ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('out',type=Path);args=ap.parse_args();args.out.write_text(generate(args.source.read_text()))
