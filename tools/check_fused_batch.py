"""NativePackGpuVertex (fused) vs the old GPU-mode conversion + repack, byte for byte (no GL, no game launch).

The old path: NativeWriteConvertedVertex(vtx, BatchVertex) in GPU vertex mode, then NativeUploadBatch copied the first
offsetof(texgen)+12*generators bytes of each BatchVertex. The new path writes those bytes directly from the VertexData. Both are
taken verbatim from the staged renderer_gl.cpp. Random vertices cover every has_* combination, special float values (NaN, +-0, +-inf)
and every generator count 1..8.
"""
from pathlib import Path
from port_paths import default_storage
import os,re,subprocess,sys
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
w=default_storage()/'fused-batch-tests';w.mkdir(exist_ok=True)
s=(rt/'src/hle/gx/renderer_gl.cpp').read_text()
assert 'NATIVE_FUSED_BATCH_V1' in s,'fused batch stage not installed'
struct=s[s.index('struct BatchVertex {'):s.index('};',s.index('struct BatchVertex {'))+2]
pack=s[s.index('static inline void NativePackGpuVertex('):]
pack=pack[:pack.index('\n}\n')+3]
a=s.index('static void NativeWriteConvertedVertex(const VertexData &vtx, BatchVertex& bv) {')
gpu=s[a:s.index('    return;\n  }\n',a)+len('    return;\n  }\n')]+'}\n'   # the s_gpu_vertex_mode branch only
code='''#include "runtime/gx_command.h"
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
using namespace nwii::runtime::gx;
'''+struct+'''
static bool s_gpu_vertex_mode=true;
'''+gpu+pack+r'''
static float pick(std::mt19937& r){
  switch(r()%12){case 0:return 0.f;case 1:return -0.f;case 2:return 1.f/0.f;case 3:return -1.f/0.f;case 4:{uint32_t b=0x7fc00000u|(r()&0x3fffffu);float f;memcpy(&f,&b,4);return f;}
    default:{uint32_t b=r();float f;memcpy(&f,&b,4);return f;}}}
int main(){
  std::mt19937 r(2026);unsigned bad=0,total=0;
  for(unsigned i=0;i<2000000;++i){
    VertexData v;
    for(auto& x:v.pos)x=pick(r);for(auto& x:v.norm)x=pick(r);for(auto& x:v.color)x=pick(r);for(auto& x:v.color1)x=pick(r);
    for(auto& row:v.tex)for(auto& x:row)x=pick(r);
    v.has_pos=r()&1;v.has_norm=r()&1;v.pre_xf=r()&1;v.has_color=r()&1;v.has_color1=r()&1;for(auto& b:v.has_tex)b=r()&1;
    v.posMtxIdx=uint8_t(r());for(auto& m:v.texMtxIdx)m=uint8_t(r());
    const unsigned generators=1+r()%8;const size_t stride=offsetof(BatchVertex,texgen)+generators*12;
    BatchVertex old;memset(&old,0xAB,sizeof old);NativeWriteConvertedVertex(v,old);
    alignas(16) uint8_t packed[sizeof(BatchVertex)+16];memset(packed,0xCD,sizeof packed);
    NativePackGpuVertex(v,reinterpret_cast<float*>(packed),generators);
    ++total;if(memcmp(&old,packed,stride)!=0){if(!bad)printf("MISMATCH at %u generators=%u\n",i,generators);++bad;}
    if(packed[stride]!=0xCD){printf("OVERRUN at %u\n",i);++bad;}   // nothing may be written past the packed record
  }
  printf("FUSED_BATCH vertices=%u mismatching=%u\n",total,bad);
  return bad?1:0;
}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code)
r=subprocess.run(['g++','-std=c++20','-O2','-I',str(rt/'include'),str(cpp),'-o',str(exe)],capture_output=True,text=True)
if r.returncode:print(r.stderr[-1500:]);sys.exit(r.returncode)
x=subprocess.run([str(exe)],capture_output=True,text=True);print(x.stdout.strip())
sys.exit(x.returncode)
