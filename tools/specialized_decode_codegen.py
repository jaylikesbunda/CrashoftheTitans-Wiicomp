"""Specialize all attribute-presence branches of the common indexed F32 loader.

Derive the body from the qualified packed loader so tuple reuse, bounds checks,
colour conversion and malformed-stream rollback retain one authoritative form.
"""
def generate_specialized(source):
 from packed_decode_codegen import generate
 generated=generate(source)
 a=generated.index('template<int P,int N,int T> static bool NativePackedIndexed')
 body=generated[a:]
 body=body.replace('template<int P,int N,int T> static bool NativePackedIndexed',
  'template<int P,int N,int T,unsigned Format> static bool NativeFormatIndexed')
 old='''    std::array<NativeVectorPlan,8> tex;
    for(unsigned t=0;t<8;++t)tex[t]=NativeVectorPlan(r.vat.texMask[t],r.vat.texType[t],r.vat.texShift[t],r.arrayBase[4+t],r.arrayStride[4+t],r.vat.texElements[t]?2:1);'''
 assert old in body
 body=body.replace(old,'    const NativeVectorPlan tex0(r.vat.texMask[0],VtxAttrType::F32,0,r.arrayBase[4],r.arrayStride[4],2);')
 body=body.replace('if (r.vat.posMatIdx)', 'if constexpr(Format&1)')
 body=body.replace('if (r.vat.clrMask[0] != VtxAttrMask::None)', 'if constexpr(Format&4)')
 body=body.replace('if (r.vat.clrMask[1] != VtxAttrMask::None)', 'if constexpr(Format&8)')
 a=body.index('        for (int t = 0; t < 8; t++) {')
 b=body.index('        vtx.has_pos',a)
 body=body[:a]+'''        if constexpr(Format&2) {
            if(off+1>buf_size)return false;
            vtx.texMtxIdx[0]=buf[off++];
        }
'''+body[b:]
 a=body.index('        for (unsigned t=0;t<8;++t) if(r.vat.texMask[t]!=VtxAttrMask::None) {')
 b=body.index('        if (off > buf_size)',a)
 body=body[:a]+'''        if(!NativeVectorPlan::Read<T,4,2>(tex0,buf,off,buf_size,vtx.tex[0]))return false;
        vtx.has_tex[0]=true;
'''+body[b:]
 # The complete format dispatch establishes a single XYZ normal, never NBT.
 a=body.index('        const bool nrm_idx3 =')
 b=body.index('        if constexpr(Format&4)',a)
 body=body[:a]+'''        vtx.has_norm=true;
        if(!NativeVectorPlan::Read<N,4,3>(nrm,buf,off,buf_size,vtx.norm))return false;
'''+body[b:]
 dispatch='''
template<int P,int N,int T> static bool NativeFormatDispatch(GXCommand& c,NativePackedBuffer& out,std::vector<uint32_t>& indices,unsigned inputs,unsigned words,const float* defaults){
 const auto& v=c.raw->vat;
 for(unsigned t=1;t<8;++t)if(v.texMatIdx[t])return NativePackedIndexed<P,N,T>(c,out,indices,inputs,words,defaults);
 static const bool enabled=!std::getenv("NWII_NO_FORMAT_LOADERS");
 if(!enabled)return NativePackedIndexed<P,N,T>(c,out,indices,inputs,words,defaults);
 if(native_hud::on.load(std::memory_order_relaxed))native_geometry::format_draws.fetch_add(1,std::memory_order_relaxed);
 const unsigned format=unsigned(v.posMatIdx)|(unsigned(v.texMatIdx[0])<<1)|
  (v.clrMask[0]!=VtxAttrMask::None?4u:0u)|(v.clrMask[1]!=VtxAttrMask::None?8u:0u);
 switch(format){
'''
 dispatch+=''.join(f' case {i}:return NativeFormatIndexed<P,N,T,{i}>(c,out,indices,inputs,words,defaults);\n' for i in range(16))
 dispatch+=' }return false;\n}\n'
 return '\n// NATIVE_FORMAT_LOADERS_V1\n'+body+dispatch
