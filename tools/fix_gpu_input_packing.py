"""Upload only the GX inputs required by active GPU texture generators."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]
f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s=f.read_text()
if 'NATIVE_GPU_INPUT_PACKING_V1' not in s:
    baseline=p/'runtime-overlays/gpu-input-packing-before.cpp'
    if not baseline.exists():baseline.write_text(s)
    old='  const unsigned generators=s_gpu_vertex_mode?8u:std::min(8u,std::max(1u,unsigned(g_state.bp[0]&15u)));'
    new='''  // NATIVE_GPU_INPUT_PACKING_V1: active matrix indices plus referenced ST inputs.
  const unsigned active=std::min(8u,std::max(1u,unsigned(g_state.bp[0]&15u)));
  unsigned generators=active;
  if(s_gpu_vertex_mode){
    for(unsigned t=0;t<active;++t){
      const unsigned info=g_state.xf[0x40+t],type=(info>>4)&7u;
      if(type==2u || type==3u)continue; // color generators do not read ST inputs
      const unsigned source=(info>>7)&31u;
      if(source>=5u && source<=12u)generators=std::max(generators,source-4u);
    }
  }'''
    assert s.count(old)==1
    s=s.replace(old,new,1);f.write_text(s)
print('GPU input packing retains active matrix indices and every referenced texture source.')
