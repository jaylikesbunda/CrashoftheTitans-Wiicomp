// NATIVE_FOG_V1: BP float fields and six documented fog curves.
static void NativeWriteFog(std::stringstream& output, const GXState& state) {
  const unsigned control=state.bp[0xf1];
  const unsigned type=(control>>21)&7;
  if(type==0) return;
  if(NativeShaderDataUniformsEnabled()) {
    output << "    float fogA=uintBitsToFloat(uNativeFogData.x);\n";
    output << "    float fogC=uintBitsToFloat(uNativeFogData.y);\n";
  } else {
    output << "    float fogA=uintBitsToFloat(" << ((state.bp[0xee]&0xfffff)<<12) << "u);\n";
    output << "    float fogC=uintBitsToFloat(" << ((control&0xfffff)<<12) << "u);\n";
  }
  // GL projection preserves the guest's 0..1 depth interval.
  output << "    uint fogZ=uint(clamp(gl_FragCoord.z,0.0,1.0)*16777215.0);\n";
  if(control&(1<<20)) output << "    float fogEye=fogA*float(fogZ)/16777216.0;\n";
  else {
    if(NativeShaderDataUniformsEnabled())output << "    float fogDenom=float(uNativeFogData.z)-float(fogZ>>uNativeFogData.w);\n";
    else output << "    float fogDenom=float(" << state.bp[0xef] << "u)-float(fogZ>>" << (state.bp[0xf0]&31) << "u);\n";
    output << "    float fogEye=fogA==0.0?0.0:fogA*16777216.0/fogDenom;\n";
  }
  output << "    float fogFactor=clamp(fogEye-fogC,0.0,1.0);\n";
  if(type==4) output << "    fogFactor=1.0-exp2(-8.0*fogFactor);\n";
  if(type==5) output << "    fogFactor=1.0-exp2(-8.0*fogFactor*fogFactor);\n";
  if(type==6) output << "    fogFactor=exp2(-8.0*(1.0-fogFactor));\n";
  if(type==7) output << "    fogFactor=exp2(-8.0*(1.0-fogFactor)*(1.0-fogFactor));\n";
  const unsigned color=state.bp[0xf2];
  if(NativeShaderDataUniformsEnabled())output << "    vec3 fogColor=uNativeFogColor/255.0;\n";
  else output << "    vec3 fogColor=vec3(" << ((color>>16)&255) << "," << ((color>>8)&255) << "," << (color&255) << ")/255.0;\n";
  // Fog interpolates 8-bit RGB; alpha remains the TEV result.
  output << "    float fogWeight=floor(fogFactor*256.0+0.5);\n";
  output << "    FragColor.rgb=floor((floor(FragColor.rgb*255.0+0.5)*(256.0-fogWeight)+floor(fogColor*255.0+0.5)*fogWeight)/256.0)/255.0;\n";
}
