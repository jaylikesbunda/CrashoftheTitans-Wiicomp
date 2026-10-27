// NATIVE_TEXTURE_SAMPLERS_V1: sampling state belongs to the GX texture unit,
// not its cached image. Shared images can use different modes simultaneously.
// TexMode0 layout checked against Dolphin VideoCommon/BPMemory.h.
static void NativeApplyTextureSampler(unsigned unit, uint32_t mode) {
  static GLuint samplers[8]={};
  if(unit>=8)return;
  if(!samplers[unit])glGenSamplers(1,&samplers[unit]);
  auto wrap=[](unsigned value)->GLint {
    return value==1?GL_REPEAT:value==2?GL_MIRRORED_REPEAT:GL_CLAMP_TO_EDGE;
  };
  glSamplerParameteri(samplers[unit],GL_TEXTURE_WRAP_S,wrap(mode&3));
  glSamplerParameteri(samplers[unit],GL_TEXTURE_WRAP_T,wrap((mode>>2)&3));
  glSamplerParameteri(samplers[unit],GL_TEXTURE_MAG_FILTER,(mode&16)?GL_LINEAR:GL_NEAREST);
  // The cache currently uploads level zero only. Preserve the requested texel
  // filter without selecting an incomplete mip chain. Mip LOD remains open.
  glSamplerParameteri(samplers[unit],GL_TEXTURE_MIN_FILTER,(mode&128)?GL_LINEAR:GL_NEAREST);
  glBindSampler(unit,samplers[unit]);
}
