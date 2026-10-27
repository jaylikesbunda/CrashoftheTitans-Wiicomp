// Conservative set of every sampler referenced by GenerateTEVShader, including
// textureSize on disabled direct stages and indirect maps used by water effects.
static unsigned NativeUsedTextureMask(const GXState& state) {
  unsigned mask=0;
  const unsigned count=state.numTevStages?state.numTevStages:1;
  for(unsigned i=0;i<count && i<16;++i){
    const unsigned map=state.tevStages[i].texMap;
    mask |= 1u << (map==0xff?0:(map&7));
    const unsigned config=state.bp[0x10+i], selected=config&3;
    if(selected<((state.bp[0]>>16)&7) && (((config>>9)&3) || ((config>>7)&3)))
      mask |= 1u << ((state.bp[0x27]>>(selected*6))&7);
  }
  // A logical sampler whose GX address is unset retains its cached alias.
  // NativeApplyShaderSamplerAliases permits only its own unit or initial unit 0.
  for(unsigned unit=1;unit<8;++unit)
    if((mask&(1u<<unit)) && state.texStages[unit].base_addr==0)mask|=1u;
  return mask;
}
