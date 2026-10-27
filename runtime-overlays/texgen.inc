// NATIVE_TEXGEN_V1: evaluate each regular generator before perspective interpolation.
static void NativeTexGen(const GXState& state, const VertexData& vertex, float out[8][3]) {
  for (unsigned t=0;t<8;++t) {
    const uint32_t info=state.xf[0x40+t];
    const unsigned source=(info>>7)&31;
    float input[4]={0,0,1,1};
    if(source==0) for(unsigned j=0;j<3;++j) input[j]=vertex.pre_xf?vertex.object_pos[j]:vertex.pos[j];
    else if(source==1) for(unsigned j=0;j<3;++j) input[j]=vertex.pre_xf?vertex.object_norm[j]:vertex.norm[j];
    else if(source>=5 && source<=12) {input[0]=vertex.tex[source-5][0];input[1]=vertex.tex[source-5][1];}
    if(!(info&4)) input[2]=1;
    const unsigned index=vertex.texMtxIdx[t];
    const unsigned base=index*4;
    out[t][0]=input[0];out[t][1]=input[1];out[t][2]=(info&2)?input[2]:1;
    if(index!=60 && base+12<=256) {
      const float* m=state.posMatrices+base;
      for(unsigned row=0;row<2+unsigned(bool(info&2));++row) {
        out[t][row]=0;
        for(unsigned j=0;j<4;++j) out[t][row]+=m[row*4+j]*input[j];
      }
    }
    if(state.xf[0x12]&1) {
      const uint32_t post=state.xf[0x50+t];
      float value[3]={out[t][0],out[t][1],out[t][2]};
      if(post&0x100) {
        const float length=std::sqrt(value[0]*value[0]+value[1]*value[1]+value[2]*value[2]);
        if(length>0) for(auto& component:value) component/=length;
      }
      const unsigned start=(post&63)*4;
      // Post matrix row addresses wrap within their 64-row bank.
      for(unsigned row=0;row<3;++row) {
        const unsigned offset=(start+row*4)&255;
        out[t][row]=state.postMatrices[offset+3];
        for(unsigned j=0;j<3;++j) out[t][row]+=state.postMatrices[offset+j]*value[j];
      }
    }
  }
}
