"""Cache resolved per-unit sampler parameters; keep every binding and lookup."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';s=f.read_text()
if 'NATIVE_SAMPLER_MODE_CACHE_V1' not in s:
 baseline=p/'runtime-overlays/sampler-mode-before.cpp'
 if not baseline.exists():baseline.write_text(s)
 old='''  if(!samplers[unit])glGenSamplers(1,&samplers[unit]);
  auto wrap='''
 new='''  if(!samplers[unit])glGenSamplers(1,&samplers[unit]);
  // NATIVE_SAMPLER_MODE_CACHE_V1: only these bits affect current parameters.
  static unsigned previous[8]={};static bool have[8]={};
  static const bool cache=std::getenv("NWII_NO_SAMPLER_MODE_CACHE")==nullptr;
  const unsigned resolved=mode&0x9fu;
  if(!cache || !have[unit] || previous[unit]!=resolved){
  auto wrap='''
 assert s.count(old)==1;s=s.replace(old,new,1)
 old='''  glSamplerParameteri(samplers[unit],GL_TEXTURE_MIN_FILTER,(mode&128)?GL_LINEAR:GL_NEAREST);
  glBindSampler(unit,samplers[unit]);'''
 new='''  glSamplerParameteri(samplers[unit],GL_TEXTURE_MIN_FILTER,(mode&128)?GL_LINEAR:GL_NEAREST);
  previous[unit]=resolved;have[unit]=true;
  }
  glBindSampler(unit,samplers[unit]);'''
 assert s.count(old)==1;s=s.replace(old,new,1);f.write_text(s)
print('Sampler modes compare used bits; all bindings, texture lookups and palette/source checks retained.')
