"""Actual legacy GPU decode+pack vs compact output: byte checks and whole-path timing."""
from pathlib import Path
from port_paths import default_storage
import json,subprocess,os
from packed_decode_codegen import generate
p=Path(__file__).resolve().parents[1];base=default_storage();rt=p/'build/nwii-full-source/nWiiRuntime'
w=base/'format-loader-probe';w.mkdir(exist_ok=True)
parser=(rt/'src/hle/gx/fifo_parser.cpp').read_text();renderer=(rt/'src/hle/gx/renderer_gl.cpp').read_text()
a=renderer.index('static inline void NativePackGpuVertex(');b=renderer.index('\n}\n',a)+3;pack=renderer[a:b]
dispatch=r'''
static bool Attempt(GXCommand& c,NativePackedBuffer& output,std::vector<uint32_t>& indices,unsigned required=8){
 const auto& r=*c.raw;const auto& v=r.vat;
 if(!r.have_snap_mtx && r.snap_pos_bank.size()!=256)return false;
 if(r.snap_pos_bank.size()==256 && r.snap_nrm_bank.size()!=96)return false;
 if(uint64_t(VertexSize(v))*r.count>r.bytes.size())return false;
 unsigned inputs=NativePackedStorageInputs(r,required),words=16+3*inputs;float defaults[40];NativePackedDefaults(r,defaults,inputs);
 auto indexed=[](VtxAttrMask m){return m==VtxAttrMask::Index8 || m==VtxAttrMask::Index16;};
 bool fast=indexed(v.posMask)&&indexed(v.nrmMask)&&indexed(v.texMask[0])&&v.posType==VtxAttrType::F32&&v.posElements&&v.nrmType==VtxAttrType::F32&&!v.nrmElements&&v.texType[0]==VtxAttrType::F32&&v.texElements[0];
 for(unsigned t=1;t<8;++t)fast=fast && v.texMask[t]==VtxAttrMask::None;
 const unsigned observed=NativeObservedFormat(r);
 if(observed && !fast)return NativeObservedDispatch(observed,c,output,inputs,words,defaults);
 if(fast){unsigned key=(v.posMask==VtxAttrMask::Index16?4:0)|(v.nrmMask==VtxAttrMask::Index16?2:0)|(v.texMask[0]==VtxAttrMask::Index16?1:0);
 switch(key){
 case 0:return NativeFormatDispatch<2,2,2>(c,output,indices,inputs,words,defaults);
 case 1:return NativeFormatDispatch<2,2,3>(c,output,indices,inputs,words,defaults);
 case 2:return NativeFormatDispatch<2,3,2>(c,output,indices,inputs,words,defaults);
 case 3:return NativeFormatDispatch<2,3,3>(c,output,indices,inputs,words,defaults);
 case 4:return NativeFormatDispatch<3,2,2>(c,output,indices,inputs,words,defaults);
 case 5:return NativeFormatDispatch<3,2,3>(c,output,indices,inputs,words,defaults);
 case 6:return NativeFormatDispatch<3,3,2>(c,output,indices,inputs,words,defaults);
 case 7:return NativeFormatDispatch<3,3,3>(c,output,indices,inputs,words,defaults);}}
 bool direct=v.posMask==VtxAttrMask::Direct && v.posType==VtxAttrType::F32 && v.posElements && v.nrmMask==VtxAttrMask::Direct && v.nrmType==VtxAttrType::F32 && !v.nrmElements && v.texMask[0]==VtxAttrMask::Direct && v.texType[0]==VtxAttrType::F32 && v.texElements[0];
 for(unsigned t=1;t<8;++t)direct=direct && v.texMask[t]==VtxAttrMask::None;
 return direct?NativePackedGeneric<true>(c,output,indices,inputs,words,defaults):NativePackedGenericPosition(c,output,indices,inputs,words,defaults);
}
'''
generated='' if '// NATIVE_PACKED_DECODE_V1:' in parser else generate(parser)
code='#include <thread>\n#define SDL_MAIN_HANDLED\n#include <SDL.h>\n#include <glad/glad.h>\n#include "'+(rt/'src/hle/gx/fifo_parser.cpp').as_posix()+'"\n#include "'+(p/'runtime-overlays/native_packed_buffer.h').as_posix()+'"\n#include <random>\nnamespace nwii::runtime {MMU* g_mmu=nullptr;CPUContext* g_ctx_ptr=nullptr;}\nextern "C" void run_game(nwii::runtime::CPUContext&){std::abort();}\nnamespace nwii::runtime::gx {\n'+generated+pack+dispatch+'\n}\n'+r'''
using namespace nwii::runtime;using namespace nwii::runtime::gx;
int main(){auto ctx=std::make_unique<CPUContext>();g_mmu=&ctx->mmu;g_ctx_ptr=ctx.get();std::mt19937 rng(4345);
 for(unsigned i=0;i<1024*1024;++i)ctx->mmu.get_ptr(0x80010000)[i]=uint8_t(rng());
 unsigned checked=0,accepted=0,fallback=0,failed=0;
 for(unsigned trial=0;trial<18000;++trial){GXCommand c{};c.type=GXCommandType::DrawPrimitive;c.prim_type=0x90;c.raw=std::make_shared<DrawRaw>();auto& r=*c.raw;r.vat={};r.count=1+rng()%256;r.defPosMtxIdx=trial%11?0:255;
  for(unsigned t=0;t<8;++t)r.defTexMtxIdx[t]=uint8_t(rng());
  r.have_snap_mtx=true;for(unsigned j=0;j<12;++j)r.snap_pos_mtx[j]=j%5==0?1.f:0.f;for(unsigned j=0;j<9;++j)r.snap_nrm_mtx[j]=j%4==0?1.f:0.f;
  bool special=trial<9000;
  auto& v=r.vat;v.posMask=VtxAttrMask(1+trial%3);v.nrmMask=VtxAttrMask(1+(trial/3)%3);v.texMask[0]=VtxAttrMask(1+(trial/9)%3);
  v.posType=v.nrmType=v.texType[0]=VtxAttrType::F32;v.posElements=v.texElements[0]=1;
  if(!special){v.posMask=VtxAttrMask(trial%4);v.posType=VtxAttrType(trial%5);v.posElements=(trial/5)&1;v.nrmElements=(trial/7)&1;v.nrmType=VtxAttrType((trial/11)%5);v.nrmIndex3=(trial/13)&1;
   for(unsigned t=0;t<8;++t){v.texMask[t]=VtxAttrMask((trial+t)%4);v.texType[t]=VtxAttrType((trial/17+t)%5);v.texElements[t]=(trial+t)&1;}}
  v.posMatIdx=trial%7==0;for(unsigned t=0;t<8;++t)v.texMatIdx[t]=trial%31==t;
  for(unsigned j=0;j<2;++j){v.clrMask[j]=VtxAttrMask((trial+j)%4);v.clrType[j]=VtxAttrType((trial/4+j)%6);}
  for(unsigned j=0;j<16;++j){r.arrayBase[j]=0x80010000+j*4096;r.arrayStride[j]=32;}
  const size_t bytes=VertexSize(v);r.bytes.resize(size_t(r.count)*bytes);
  for(size_t j=0;j<r.bytes.size();++j)r.bytes[j]=uint8_t(rng());
  // Repeated byte records exercise tuple reuse with no mutation across the draw.
  if(trial%4==0)for(unsigned i=1;i<r.count;++i)std::memcpy(r.bytes.data()+size_t(i)*bytes,r.bytes.data()+size_t(i/4)*bytes,bytes);
  if(trial%19==0 && r.bytes.size())r.bytes.pop_back();
  if(trial%23==0)g_mmu=nullptr;else g_mmu=&ctx->mmu;
  unsigned inputs=NativePackedStorageInputs(r,1+trial%8),words=16+3*inputs;
  NativePackedBuffer out;std::vector<uint32_t> indices;bool ok=Attempt(c,out,indices,inputs);
  GXCommand old=c;FifoParser::DecodeDraw(old);
  if(!ok){++fallback;continue;}++accepted;
  if(!old.native_gpu_snapshot || indices!=old.native_vertex_indices || out.size()!=old.vertices.size()*words){++failed;continue;}
  for(size_t i=0;i<old.vertices.size();++i){float expected[40];NativePackGpuVertex(old.vertices[i],expected,inputs);++checked;
   if(std::memcmp(expected,out.data()+i*words,words*sizeof(float))){if(failed++<4)std::cout<<"MISMATCH trial="<<trial<<" vertex="<<i<<'\n';}}
 }
 std::cout<<"PACKED_BYTES checks="<<checked<<" accepted="<<accepted<<" fallback="<<fallback<<" failed="<<failed<<'\n';if(failed)return 1;
 g_mmu=&ctx->mmu;
 SDL_SetMainReady();if(SDL_Init(SDL_INIT_VIDEO))return 3;SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,SDL_GL_CONTEXT_PROFILE_CORE);
 auto window=SDL_CreateWindow("Mapped packing fixture",0,0,32,32,SDL_WINDOW_OPENGL|SDL_WINDOW_HIDDEN);auto context=SDL_GL_CreateContext(window);if(!context||!gladLoadGLLoader((GLADloadproc)SDL_GL_GetProcAddress))return 4;
 using Storage=void(APIENTRY*)(GLenum,GLsizeiptr,const void*,GLbitfield);auto storage=reinterpret_cast<Storage>(SDL_GL_GetProcAddress("glBufferStorage"));if(!storage)return 5;
 GLuint buffer;glGenBuffers(1,&buffer);glBindBuffer(GL_ARRAY_BUFFER,buffer);const unsigned mapflags=GL_MAP_WRITE_BIT|0x40|0x80;storage(GL_ARRAY_BUFFER,32u<<20,nullptr,mapflags);
 float* mapped=static_cast<float*>(glMapBufferRange(GL_ARRAY_BUFFER,0,32u<<20,mapflags));if(!mapped||glGetError())return 6;
 std::cout<<"PACKED_GPU "<<glGetString(GL_RENDERER)<<'\n';
 for(unsigned sharing:{1u,2u,3u,8u})for(unsigned inputs:{1u,2u,8u}){GXCommand c{};c.type=GXCommandType::DrawPrimitive;c.prim_type=0x90;c.raw=std::make_shared<DrawRaw>();auto& r=*c.raw;r.vat={};r.count=32766;
  r.vat.posMask=r.vat.nrmMask=r.vat.texMask[0]=VtxAttrMask::Index16;r.vat.posType=r.vat.nrmType=r.vat.texType[0]=VtxAttrType::F32;r.vat.posElements=r.vat.texElements[0]=1;
  for(unsigned j=0;j<16;++j){r.arrayBase[j]=0x80010000;r.arrayStride[j]=12;}r.have_snap_mtx=true;
  for(unsigned j=0;j<12;++j)r.snap_pos_mtx[j]=j%5==0?1.f:0.f;for(unsigned j=0;j<9;++j)r.snap_nrm_mtx[j]=j%4==0?1.f:0.f;
  for(unsigned i=0;i<r.count;++i)for(unsigned j=0;j<3;++j){unsigned k=i/sharing;r.bytes.push_back(k>>8);r.bytes.push_back(k);}
  auto raw=c.raw;NativePackedBuffer out;std::vector<uint32_t> indices;std::vector<float> upload;unsigned words=16+3*inputs;
  for(unsigned mode=0;mode<2;++mode){std::vector<double> timings;
   for(unsigned trial=0;trial<7;++trial){auto start=std::chrono::steady_clock::now();for(unsigned rep=0;rep<20;++rep){c.raw=raw;c.vertices.clear();c.native_vertex_indices.clear();c.native_gpu_snapshot.reset();
    if(mode==0){FifoParser::DecodeDraw(c);for(size_t i=0;i<c.vertices.size();++i)NativePackGpuVertex(c.vertices[i],mapped+i*words,inputs);}
    else {if(!Attempt(c,out,indices,inputs))return 2;std::memcpy(mapped,out.data(),out.size()*sizeof(float));}}
    timings.push_back(std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-start).count()/20);}
   std::sort(timings.begin(),timings.end());std::cout<<"PACKED_BENCH sharing="<<sharing<<" inputs="<<inputs<<" mode="<<mode<<" median_us="<<timings[3]<<'\n';
  }
 }
 if(glGetError()!=GL_NO_ERROR)return 7;SDL_GL_DeleteContext(context);SDL_DestroyWindow(window);SDL_Quit();
}
'''
code=code.replace(' g_mmu=&ctx->mmu;\n SDL_SetMainReady();', ' g_mmu=&ctx->mmu;\n'+(p/'runtime-overlays/retained_geometry_fixture.inc').read_text()+'\n SDL_SetMainReady();')
code=code.replace(' // Exercise production retention', (p/'runtime-overlays/decode_chunks_fixture.inc').read_text()+'\n // Exercise production retention')
code=code.replace(' // Exercise production retention', (p/'runtime-overlays/observed_formats_fixture.inc').read_text()+'\n // Exercise production retention')
if '// NATIVE_PACKED_DECODE_V1:' in parser:code=code.replace('#include "'+(p/'runtime-overlays/native_packed_buffer.h').as_posix()+'"\n','')
cpp=w/'fixture.cpp';cpp.write_text(code);cm=p/'build/nwii-full-source/CMakeLists.txt';s=cm.read_text();marker='# NATIVE_FORMAT_PROBE_V1'
if marker not in s:cm.write_text(s+'\n'+marker+'\nadd_executable(crash_format_probe "'+cpp.as_posix()+'")\ntarget_link_libraries(crash_format_probe PRIVATE nwiiruntime)\ntarget_compile_options(crash_format_probe PRIVATE -O2 -fwrapv -fno-strict-aliasing -ffp-contract=off)\n')
rows=[]
for cmd in (['cmake','--build',str(base/'toolchain'),'--target','crash_format_probe','-j','4'],[str(base/'toolchain/crash_format_probe.exe')]):
 env={k:v for k,v in os.environ.items() if not k.startswith('NWII_')};env['NWII_NO_DIAG']='1';env['NWII_PACKED_REUSE']='1'
 r=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=900);rows.append(dict(command=cmd,exit_code=r.returncode,output=r.stdout+r.stderr));print((r.stdout+r.stderr)[-5500:],flush=True)
 if r.returncode:break
(p/'reports/format-loader-probe.json').write_text(json.dumps(dict(game_launched=False,runs=rows),indent=2)+'\n');raise SystemExit(r.returncode)
