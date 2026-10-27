static void EmitPrimitive(const GXCommand &cmd) {
  // NATIVE_VERTEX_REUSE_V1 / NATIVE_INDEXED_BATCH_V1
  const auto &v=cmd.vertices;
  const size_t n=v.size();
  const uint32_t base=uint32_t(s_batch.size());
  if (cmd.prim_type>=0xA8 && cmd.prim_type<=0xB8) {
    NativeEmitLinesPoints(cmd);
    return;
  }
  size_t count=0;
  switch(cmd.prim_type) {
  case 0x80: count=n/4*4; break;
  case 0x90: count=n; break;
  case 0x98: case 0xA0: count=n>=3?n:0; break;
  default: return;
  }
  for(size_t i=0;i<count;++i) EmitVertex(v[i]);
  switch(cmd.prim_type) {
  case 0x80:
    for(uint32_t i=0;i<count;i+=4) {
      s_indices.push_back(base+i);s_indices.push_back(base+i+1);s_indices.push_back(base+i+2);
      s_indices.push_back(base+i+2);s_indices.push_back(base+i+3);s_indices.push_back(base+i);
    }
    break;
  case 0x90:
    break;
  case 0x98:
    for(uint32_t i=2;i<count;++i) {
      s_indices.push_back(base+i-((i&1)?1:2));
      s_indices.push_back(base+i-((i&1)?2:1));
      s_indices.push_back(base+i);
    }
    break;
  case 0xA0:
    for(uint32_t i=2;i<count;++i) {
      s_indices.push_back(base);s_indices.push_back(base+i-1);s_indices.push_back(base+i);
    }
    break;
  }
}
