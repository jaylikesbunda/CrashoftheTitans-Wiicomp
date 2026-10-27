// NATIVE_INDIRECT_V1: indirect coordinates are accumulated in texel units.
// NATIVE_IND_DEBUG_V1: overlay key F8 cycles a debug view that only affects shaders with indirect stages (see tools/fix_ind_debug.py):
//   1 = no indirect warp (offset forced to 0)   2 = indirect-using stages contribute nothing   3 = only indirect-using stages (plain stages white)
//   4 = indirect map read without filtering (texelFetch)
static bool NativeStageUsesIndirect(const GXState& state,unsigned stage) {
  const unsigned config=state.bp[0x10+stage];
  const unsigned selected=config&3, available=(state.bp[0]>>16)&7;
  return selected<available && (((config>>9)&3) || ((config>>7)&3));
}
static void NativeIndirectDebugOverride(std::stringstream& output,const GXState& state,unsigned stage) {
  const int mode=native_hud::ind_debug.load();
  if(!mode || ((state.bp[0]>>16)&7)==0) return;
  const bool uses=NativeStageUsesIndirect(state,stage);
  if(mode==2 && uses) output << "    texColor=vec4(0.0);\n";
  if(mode==3 && !uses) output << "    texColor=vec4(1.0);\n";
}
static void NativeWriteIndirect(std::stringstream& output,const GXState& state,unsigned stage) {
  const auto& tev=state.tevStages[stage];
  const unsigned config=state.bp[0x10+stage];
  const unsigned selected=config&3;
  const unsigned available=(state.bp[0]>>16)&7;
  const unsigned matrix=(config>>9)&3, kind=(config>>11)&3;
  const unsigned format=(config>>2)&3, shifts[]={0,3,4,5};
  const unsigned bump=(config>>7)&3;
  output << "    vec2 tevUV=vTex" << unsigned(tev.texCoord) << ".xy/vTex" << unsigned(tev.texCoord) << ".z;\n";
  output << "    vec2 tevDimensions=vec2(textureSize(uTex" << (tev.texMap==0xff?0:unsigned(tev.texMap)) << ",0));\n";
  output << "    vec2 tevBase=tevUV*tevDimensions;vec2 indOffset=vec2(0.0);\n";
  if(selected<available && (matrix || bump)) {
    const unsigned order=(state.bp[0x27]>>(selected*6))&63;
    const unsigned scales=(state.bp[0x25+selected/2]>>((selected%2)*8))&255;
    output << "    vec2 indUV=vTex" << (order>>3) << ".xy/vTex" << (order>>3) << ".z;\n";
    output << "    indUV/=exp2(vec2(" << (scales&15) << "," << (scales>>4) << "));\n";
    if(native_hud::ind_debug.load()==4) output << "    vec3 indRaw=floor(texelFetch(uTex" << (order&7) << ",ivec2(indUV*vec2(textureSize(uTex" << (order&7) << ",0))),0).abg*255.0+0.5);\n";
    else output << "    vec3 indRaw=floor(texture(uTex" << (order&7) << ",indUV).abg*255.0+0.5);\n";
    output << "    vec3 indValue=floor(indRaw/" << (1<<shifts[format]) << ".0);\n";
    if(bump) {
      const char* axis[]={"x","y","z"};
      output << "    indAlpha=";
      if(format==0) output << "floor(indRaw." << axis[bump-1] << "/8.0)*8.0/255.0;\n";
      else output << "mod(indRaw." << axis[bump-1] << "," << (1<<shifts[format]) << ".0)*" << (1<<(8-shifts[format])) << ".0/255.0;\n";
    }
    const int bias=format==0?-128:1;
    for(unsigned axis=0;axis<3;++axis) if(config&(1<<(4+axis)))
      output << "    indValue[" << axis << "]+=" << bias << ".0;\n";
    if(matrix) {
      const unsigned base=6+(matrix-1)*3;
      const unsigned exponent=((state.bp[base]>>22)&3)|(((state.bp[base+1]>>22)&3)<<2)|(((state.bp[base+2]>>22)&3)<<4);
      if(kind==0) {
        auto coefficient=[](unsigned value){const int bits=value&2047;return bits>=1024?bits-2048:bits;};
        for(unsigned row=0;row<2;++row) {
          output << "    indOffset[" << row << "]=dot(vec3(";
          for(unsigned col=0;col<3;++col) {
            if(col) output << ",";
            output << coefficient(state.bp[base+col]>>(row*11)) << ".0";
          }
          output << "),indValue)/1024.0;\n";
        }
      } else if(kind==1 || kind==2) output << "    indOffset=tevBase*indValue." << (kind==1?"x":"y") << "/256.0;\n";
      output << "    indOffset*=exp2(" << int(exponent)-17 << ".0);\n";
      output << "    indOffset=floor(indOffset*128.0)/128.0;\n";
    }
  }
  for(unsigned axis=0;axis<2;++axis) {
    const unsigned wrap=(config>>(13+axis*3))&7;
    if(wrap>=6) output << "    tevBase[" << axis << "]=0.0;\n";
    else if(wrap) output << "    tevBase[" << axis << "]=mod(tevBase[" << axis << "]," << (512>>wrap) << ".0);\n";
  }
  if(native_hud::ind_debug.load()==1) output << "    indOffset=vec2(0.0);\n";
  output << "    indPrevious=" << ((config&(1<<20))?"indPrevious+":"") << "tevBase+indOffset;\n";
  output << "    tevUV=indPrevious/tevDimensions;\n";
  if(tev.colorChan==5 || tev.colorChan==6)
    output << "    rasColor=vec4(indAlpha" << (tev.colorChan==6?"*(255.0/248.0)":"") << ");\n";
}
