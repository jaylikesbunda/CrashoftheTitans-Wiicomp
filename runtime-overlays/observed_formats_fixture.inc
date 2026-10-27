 // Exercise the exact observed families against the original full decoder.
 unsigned observed_checks=0,observed_draws=0,observed_hits=0;
 for(unsigned trial=0;trial<1800;++trial){
  GXCommand seed{};seed.type=GXCommandType::DrawPrimitive;seed.prim_type=0x90;seed.native_packed_inputs=1;
  seed.raw=std::make_shared<DrawRaw>();auto& r=*seed.raw;auto& v=r.vat;
  const unsigned mask=1+trial%3,uv=2+(trial/3)%3,normal=(trial/9)%2;
  r.count=48;r.have_snap_mtx=true;v.posMask=v.texMask[0]=VtxAttrMask(mask);v.posType=VtxAttrType::F32;v.posElements=v.texElements[0]=true;v.texType[0]=VtxAttrType(uv);v.texShift[0]=(trial/18)%16;
  v.nrmMask=normal?VtxAttrMask(mask):VtxAttrMask::None;v.nrmType=VtxAttrType::S8;
  v.clrMask[0]=(trial/36)%2?VtxAttrMask(mask):VtxAttrMask::None;v.clrType[0]=VtxAttrType((trial/72)%6);
  for(unsigned j=0;j<16;++j){r.arrayBase[j]=0x80010000+j*4096;r.arrayStride[j]=32;}
  auto attr=[&](unsigned m,unsigned bytes,unsigned index){if(m==1){for(unsigned b=0;b<bytes;++b)r.bytes.push_back(uint8_t(rng()));}else{if(m==3)r.bytes.push_back(uint8_t(index>>8));r.bytes.push_back(uint8_t(index));}};
  for(unsigned i=0;i<r.count;++i){unsigned index=trial%2?i*31:i/2;attr(mask,12,index);if(normal)attr(mask,3,index);if(v.clrMask[0]!=VtxAttrMask::None)attr(mask,ColorBytes(v.clrType[0]),index);attr(mask,uv==4?8:4,index);}
  for(unsigned round=0;round<3;++round){
   if(round==2){ctx->mmu.write32(r.arrayBase[0],rng());ctx->mmu.write32(r.arrayBase[2],rng());ctx->mmu.write32(r.arrayBase[4],rng());}
   GXCommand old=seed;old.native_packed_inputs=8;FifoParser::DecodeDraw(old);
   if(!old.native_packed.empty() || !old.native_gpu_snapshot)return 18;
   GXCommand actual=seed;if(!NativeTryPackedDecode(actual))return 19;
   const GXCommand& read=actual;
   if(read.NativeStoredVertexCount()!=old.vertices.size() || !read.native_vertex_indices.empty())return 20;
   for(size_t i=0;i<old.vertices.size();++i){float expected[40];NativePackGpuVertex(old.vertices[i],expected,1);
    if(std::memcmp(expected,read.native_packed.data()+19*i,76))return 21;++observed_checks;
   }
   if(round==1 && actual.native_geometry_retained)++observed_hits;++observed_draws;
  }
 }
 if(observed_hits<1000)return 22;
 std::cout<<"OBSERVED_FORMAT_BYTES checks="<<observed_checks<<" draws="<<observed_draws<<" retained_hits="<<observed_hits<<'\n';
