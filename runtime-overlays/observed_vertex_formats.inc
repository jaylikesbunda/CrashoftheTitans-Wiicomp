// Formats observed in the user-running waterfall: F32 XYZ, optional byte/float
// normals, optional indexed/direct colour, and F32/S16/U16 ST. Matrix-index and
// secondary-attribute layouts retain the qualified generic decoder.
static unsigned NativeObservedFormat(const DrawRaw& r){
 static const bool enabled=!std::getenv("NWII_NO_FORMAT_LOADERS");if(!enabled)return 0;
 const auto& v=r.vat;unsigned mask=unsigned(v.posMask);
 if(mask<1 || mask>3 || v.posType!=VtxAttrType::F32 || !v.posElements || v.posMatIdx)return 0;
 for(unsigned t=0;t<8;++t)if(v.texMatIdx[t] || (t && v.texMask[t]!=VtxAttrMask::None))return 0;
 if(unsigned(v.texMask[0])!=mask || !v.texElements[0])return 0;
 const unsigned uv=unsigned(v.texType[0]);if(uv<2 || uv>4)return 0;
 if(v.clrMask[1]!=VtxAttrMask::None || (v.clrMask[0]!=VtxAttrMask::None && unsigned(v.clrMask[0])!=mask))return 0;
 unsigned normal=0;
 if(v.nrmMask!=VtxAttrMask::None){
  if(unsigned(v.nrmMask)!=mask || v.nrmElements)return 0;
  normal=unsigned(v.nrmType);if(normal!=1 && normal!=4)return 0;
 }
 return 1+((mask-1)*9+(uv-2)*3+(normal==0?0:normal==1?1:2))*2+(v.clrMask[0]!=VtxAttrMask::None?1:0);
}
template<int Mask,int UV,int Normal,bool Colour> static bool NativeObservedDecode(GXCommand& c,NativePackedBuffer& out,unsigned inputs,unsigned words,const float* defaults){
 const DrawRaw& r=*c.raw;const std::vector<uint8_t>& bytes=r.bytes;const auto& v=r.vat;
 if(unsigned(r.defPosMtxIdx)*4+12>256 || 3u*(r.defPosMtxIdx&31)+9>96 || g_skindbg)return false;
 const NativeVectorPlan pos(v.posMask,VtxAttrType::F32,0,r.arrayBase[0],r.arrayStride[0],3);
 const NativeVectorPlan tex(v.texMask[0],v.texType[0],v.texShift[0],r.arrayBase[4],r.arrayStride[4],2);
 const NativeVectorPlan nrm(v.nrmMask,v.nrmType,NativeNormalFracBits(v.nrmType),r.arrayBase[1],r.arrayStride[1],3);
 out.resize(size_t(r.count)*words);float* storage=out.data();size_t offset=0;
 for(unsigned i=0;i<r.count;++i){float* p=storage+size_t(i)*words;std::memcpy(p,defaults,words*4);
  if(!NativeVectorPlan::Read<Mask,4,3>(pos,bytes,offset,bytes.size(),p))return false;
  p[7]=float(r.defPosMtxIdx);p[8]=Normal?1.f:0.f;
  if constexpr(Normal!=0)if(!NativeVectorPlan::Read<Mask,Normal,3>(nrm,bytes,offset,bytes.size(),p+9))return false;
  if constexpr(Colour)if(!ReadColorAttr(bytes,offset,bytes.size(),VtxAttrMask(Mask),v.clrType[0],r.arrayBase[2],r.arrayStride[2],p+3))return false;
  if(!NativeVectorPlan::Read<Mask,UV,2>(tex,bytes,offset,bytes.size(),p+16))return false;
 }
 return true;
}
template<int Mask,int UV,int Normal> static bool NativeObservedColour(GXCommand& c,NativePackedBuffer& out,unsigned inputs,unsigned words,const float* defaults){
 return c.raw->vat.clrMask[0]==VtxAttrMask::None?NativeObservedDecode<Mask,UV,Normal,false>(c,out,inputs,words,defaults):NativeObservedDecode<Mask,UV,Normal,true>(c,out,inputs,words,defaults);
}
static bool NativeObservedDispatch(unsigned key,GXCommand& c,NativePackedBuffer& out,unsigned inputs,unsigned words,const float* defaults){
 if(native_hud::on.load(std::memory_order_relaxed))native_geometry::format_draws.fetch_add(1,std::memory_order_relaxed);
 switch((key-1)/2){
 case 0:return NativeObservedColour<1,2,0>(c,out,inputs,words,defaults);
 case 1:return NativeObservedColour<1,2,1>(c,out,inputs,words,defaults);
 case 2:return NativeObservedColour<1,2,4>(c,out,inputs,words,defaults);
 case 3:return NativeObservedColour<1,3,0>(c,out,inputs,words,defaults);
 case 4:return NativeObservedColour<1,3,1>(c,out,inputs,words,defaults);
 case 5:return NativeObservedColour<1,3,4>(c,out,inputs,words,defaults);
 case 6:return NativeObservedColour<1,4,0>(c,out,inputs,words,defaults);
 case 7:return NativeObservedColour<1,4,1>(c,out,inputs,words,defaults);
 case 8:return NativeObservedColour<1,4,4>(c,out,inputs,words,defaults);
 case 9:return NativeObservedColour<2,2,0>(c,out,inputs,words,defaults);
 case 10:return NativeObservedColour<2,2,1>(c,out,inputs,words,defaults);
 case 11:return NativeObservedColour<2,2,4>(c,out,inputs,words,defaults);
 case 12:return NativeObservedColour<2,3,0>(c,out,inputs,words,defaults);
 case 13:return NativeObservedColour<2,3,1>(c,out,inputs,words,defaults);
 case 14:return NativeObservedColour<2,3,4>(c,out,inputs,words,defaults);
 case 15:return NativeObservedColour<2,4,0>(c,out,inputs,words,defaults);
 case 16:return NativeObservedColour<2,4,1>(c,out,inputs,words,defaults);
 case 17:return NativeObservedColour<2,4,4>(c,out,inputs,words,defaults);
 case 18:return NativeObservedColour<3,2,0>(c,out,inputs,words,defaults);
 case 19:return NativeObservedColour<3,2,1>(c,out,inputs,words,defaults);
 case 20:return NativeObservedColour<3,2,4>(c,out,inputs,words,defaults);
 case 21:return NativeObservedColour<3,3,0>(c,out,inputs,words,defaults);
 case 22:return NativeObservedColour<3,3,1>(c,out,inputs,words,defaults);
 case 23:return NativeObservedColour<3,3,4>(c,out,inputs,words,defaults);
 case 24:return NativeObservedColour<3,4,0>(c,out,inputs,words,defaults);
 case 25:return NativeObservedColour<3,4,1>(c,out,inputs,words,defaults);
 case 26:return NativeObservedColour<3,4,4>(c,out,inputs,words,defaults);
 }
 return false;
}
