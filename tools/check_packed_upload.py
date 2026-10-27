"""Execute the actual mixed span-copy loop against legacy GPU packed bytes."""
from pathlib import Path
from port_paths import default_storage
import subprocess,json
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime';w=default_storage()/'packed-upload-tests';w.mkdir(exist_ok=True)
s=(rt/'src/hle/gx/renderer_gl.cpp').read_text();a=s.index('static inline void NativePackGpuVertex(');b=s.index('\n}\n',a)+3;packer=s[a:b]
a=s.index('    for(const auto& span:s_gpu_spans){');b=s.index('    s_upload_first=',a);loop=s[a:b]
code='''#include "runtime/gx_command.h"
#include <cstring>
#include <random>
#include <iostream>
using namespace nwii::runtime::gx;
struct NativeGpuSpan{const VertexData* vertices;size_t count;const GXCommand* packed=nullptr;};
'''+packer+r'''
int main(){std::mt19937 rng(3415);unsigned checks=0;
for(unsigned destination_inputs=1;destination_inputs<=8;++destination_inputs)for(unsigned source_inputs=1;source_inputs<=8;++source_inputs){
 std::vector<VertexData> vertices(257);
 for(auto& v:vertices){v.has_norm=rng()&1;v.has_color=rng()&1;v.posMtxIdx=uint8_t(rng());for(auto& x:v.texMtxIdx)x=uint8_t(rng());
  for(auto& f:v.pos){uint32_t bits=rng();memcpy(&f,&bits,4);}for(auto& f:v.norm){uint32_t bits=rng();memcpy(&f,&bits,4);}
  for(auto& f:v.color){uint32_t bits=rng();memcpy(&f,&bits,4);}for(auto& f:v.color1){uint32_t bits=rng();memcpy(&f,&bits,4);}
  for(auto& t:v.tex)for(auto& f:t){uint32_t bits=rng();memcpy(&f,&bits,4);}}
 GXCommand c{};c.native_packed_stride=uint8_t(16+source_inputs*3);c.native_packed.resize(vertices.size()*c.native_packed_stride);
 for(size_t i=0;i<vertices.size();++i)NativePackGpuVertex(vertices[i],c.native_packed.data()+i*c.native_packed_stride,source_inputs);
 size_t stride=64+12*destination_inputs;unsigned generators=destination_inputs;
 std::vector<NativeGpuSpan> s_gpu_spans{{vertices.data(),vertices.size(),nullptr},{nullptr,vertices.size(),&c},{vertices.data(),3,nullptr},{nullptr,17,&c}};
 size_t count=vertices.size()*2+20;std::vector<uint8_t> expected(count*stride),actual(count*stride+32,0xcd);uint8_t* cursor=expected.data();
 for(const auto& span:s_gpu_spans)for(size_t i=0;i<span.count;++i,cursor+=stride){NativePackGpuVertex(vertices[i],reinterpret_cast<float*>(cursor),generators);
   if(span.packed && source_inputs<destination_inputs)std::memset(cursor+64+12*source_inputs,0,12*(destination_inputs-source_inputs));}
 cursor=actual.data();
'''+loop+r'''
 if(memcmp(expected.data(),actual.data(),expected.size()))return 1;
 for(size_t i=expected.size();i<actual.size();++i)if(actual[i]!=0xcd)return 2;
 checks+=unsigned(count);
 // Command copies own their buffers; moving leaves the source reusable.
 GXCommand copied=c;GXCommand moved=std::move(copied);if(moved.native_packed.size()!=c.native_packed.size() || !copied.native_packed.empty())return 3;
 copied.native_packed.resize(100);for(size_t i=0;i<100;++i)copied.native_packed.data()[i]=float(i);
 if(memcmp(moved.native_packed.data(),c.native_packed.data(),c.native_packed.size()*4))return 4;
}
std::cout<<"PACKED_UPLOAD_BYTES "<<checks<<'/'<<checks<<'\n';}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code);rows=[]
for cmd in (['g++','-std=c++20','-O2','-static','-I',str(rt/'include'),str(cpp),'-o',str(exe)],[str(exe)]):
 r=subprocess.run(cmd,capture_output=True,text=True,timeout=120);rows.append(dict(command=cmd,exit_code=r.returncode,output=r.stdout+r.stderr));print((r.stdout+r.stderr)[-1800:])
 if r.returncode:break
(p/'reports/packed-upload-tests.json').write_text(json.dumps(rows,indent=2)+'\n');raise SystemExit(r.returncode)
