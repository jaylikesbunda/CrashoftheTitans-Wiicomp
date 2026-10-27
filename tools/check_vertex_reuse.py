"""Byte-exact production primitive expansion comparison and CPU benchmark."""
from pathlib import Path
from port_paths import default_storage
import subprocess, json, argparse
ap=argparse.ArgumentParser();ap.add_argument('--baseline',choices=('original','reuse','inplace','tuple'),default='original');ap.add_argument('--no-benchmark',action='store_true');args=ap.parse_args()
p=Path(__file__).resolve().parents[1]
stage=p/'build/nwii-full-source/nWiiRuntime'
s=(stage/'src/hle/gx/renderer_gl.cpp').read_text()
parser=(stage/'src/hle/gx/fifo_parser.cpp').read_text();x=parser.index('VertexData GXCommand::NativeDecodedVertex(');z=parser.index('\n}\n',x)+3
getter='namespace nwii::runtime::gx {\n'+parser[x:z]+'\n}\n'
a=s.index('static void NativeTexGen('); b=s.index('static BatchVertex ConvertVertex(',a)
texgen=s[a:b]
a=b; b=s.index('\nstatic bool s_batch_open',a)
c=s.index('static void EmitPrimitive(const GXCommand &cmd) {'); d=s.index('\n}',c)+2
optimized=s[a:b]+'\n'+s[c:d]
baseline=(p/'runtime-overlays/vertex_reuse_baseline.inc').read_text()
if args.baseline=='reuse':baseline=s[a:b]+'\n'+(p/'runtime-overlays/indexed_batch_baseline.inc').read_text()
if args.baseline=='inplace':baseline=(p/'runtime-overlays/vertex_inplace_baseline.inc').read_text()+'\n'+s[c:d]
if args.baseline=='tuple':
    previous=(p/'runtime-overlays/index_tuple_renderer_baseline.cpp').read_text()
    x=previous.index('static void EmitPrimitive(const GXCommand &cmd) {');z=previous.index('\n}',x)+2
    baseline=s[a:b]+'\n'+previous[x:z]
indexed='NATIVE_INDEXED_BATCH_V1' in optimized
lines=s[s.index('static void NativeQuadBoth('):c]
optimized=optimized.replace('static void EmitPrimitive(const GXCommand &cmd) {', lines+'\nstatic void EmitPrimitive(const GXCommand &cmd) {',1)
baseline=baseline.replace('static void EmitPrimitive(const GXCommand &cmd) {', lines+'\nstatic void EmitPrimitive(const GXCommand &cmd) {',1)
a=s.index('struct BatchVertex {'); b=s.index('\n};',a)+3
struct=s[a:b]
work=default_storage()/'vertex-reuse-tests'
work.mkdir(exist_ok=True)
prefix='''#include "runtime/gx_command.h"
#include "runtime/gx/fifo_parser.h"
#include "runtime/gx_state.h"
#include "runtime/native_frame_trace.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <iostream>
#include <chrono>
using namespace nwii::runtime::gx;
namespace nwii::runtime::gx { GXState g_state; }
static bool s_gpu_vertex_mode=false;
static const int kNativeEfbHeight=480;
static bool CurrentIsPerspective() { return g_state.projection[6]==0; }
'''+getter+struct+'''
struct TestBatch : std::vector<BatchVertex> { BatchVertex& emplace_back_uninitialized(){return emplace_back();} }; // NativeBatchBuffer stand-in
'''+'\n'+texgen
def wrap(name,body):
    return ('\nnamespace '+name+' {\nstatic TestBatch s_batch;\nstatic std::vector<uint32_t> s_indices;\n'
            'struct NativeGpuSpan{const VertexData* vertices;size_t count;const GXCommand* packed=nullptr;};\nstatic std::vector<NativeGpuSpan> s_gpu_spans;static size_t s_gpu_span_vertices=0;\n'
            'static size_t NativeBatchSize(){return s_batch.size();}\nstatic bool NativeFusedBatchEnabled(){return false;} // fusion is proved byte-exact by check_fused_batch.py\n'
            +body+'\n}\n')
body=r'''
int main() {
 unsigned cases=0,failed=0;
 for(unsigned scenario=0;scenario<64;++scenario) {
  g_state.bp[0]=scenario%16; g_state.xf[0x12]=scenario&1;
  for(unsigned j=0;j<256;++j) {g_state.posMatrices[j]=(int(j%13)-6)*.125f;g_state.postMatrices[j]=(int(j%7)-3)*.25f;}
  for(unsigned j=0;j<96;++j)g_state.normalMatrices[j]=(int(j%11)-5)*.125f;
  for(unsigned t=0;t<8;++t) {g_state.xf[0x40+t]=((scenario%10)<<7)|(scenario&6);g_state.xf[0x50+t]=(scenario*7+t*5)%64|((scenario&8)?256:0);}
  g_state.projection[0]=g_state.projection[2]=1;g_state.projection[6]=scenario&1;g_state.bp[0x22]=scenario*257;
  for(unsigned prim : {0x80u,0x90u,0x98u,0xA0u,0xA8u,0xB0u,0xB8u,0x00u}) for(unsigned n=0;n<70;++n) {
   GXCommand cmd{};cmd.prim_type=prim;cmd.vertices.resize(n);
   for(unsigned i=0;i<n;++i) {
    auto&v=cmd.vertices[i];v.has_pos=scenario&1;v.has_norm=scenario&2;v.pre_xf=scenario&4;v.has_color=scenario&8;v.posMtxIdx=(i+scenario)%64;
    for(unsigned j=0;j<3;++j){v.pos[j]=(int(i%17)-8)*.125f+j;v.norm[j]=(int(i%7)-3)*.25f+j;v.object_pos[j]=i*.0625f+j;v.object_norm[j]=i*.03125f-j;}
    for(unsigned j=0;j<4;++j){v.color[j]=(i+j)%9*.125f;v.color1[j]=(i+j)%5*.25f;}
    for(unsigned t=0;t<8;++t){v.texMtxIdx[t]=(scenario&16)?60:t*3;v.tex[t][0]=i*.125f+t;v.tex[t][1]=i*.25f-t;}
   }
   Baseline::s_batch.assign(3,BatchVertex{});Optimized::s_batch=Baseline::s_batch;
   Optimized::s_indices.clear();
   if(prim==0x80 || prim==0x98 || prim==0xA0) Optimized::s_indices={0,1,2};
   // Two draws in one batch catch incorrect per-draw index bases.
   Baseline::EmitPrimitive(cmd);Optimized::EmitPrimitive(cmd);
   Baseline::EmitPrimitive(cmd);Optimized::EmitPrimitive(cmd);++cases;
   auto expanded=ExpandOptimized();
   if(Baseline::s_batch.size()!=expanded.size() || std::memcmp(Baseline::s_batch.data(),expanded.data(),Baseline::s_batch.size()*sizeof(BatchVertex)))++failed;
  }
 }
 std::cout<<"BYTE_EQUIVALENCE "<<cases-failed<<"/"<<cases<<std::endl;
 for(unsigned active : {1u,8u}) for(unsigned prim : {0x80u,0x90u,0x98u,0xA0u}) {
  GXCommand cmd{};cmd.prim_type=prim;cmd.vertices.resize(1024);g_state.bp[0]=active;g_state.xf[0x12]=0;
  for(unsigned t=0;t<8;++t)g_state.xf[0x40+t]=(5+t)<<7;
  for(auto&v:cmd.vertices){v.has_pos=v.has_norm=true;for(unsigned t=0;t<8;++t)v.texMtxIdx[t]=t*3;}
  for(unsigned repeat=0;repeat<3;++repeat)for(unsigned mode=0;mode<2;++mode){
   auto&batch=mode?Optimized::s_batch:Baseline::s_batch;batch.reserve(3072);volatile double checksum=0;
   for(auto&v:cmd.vertices)v.tex[0][0]=0;
   auto start=std::chrono::steady_clock::now();
   for(unsigned i=0;i<500;++i){cmd.vertices[0].tex[0][0]=float(i&31);batch.clear();Optimized::s_indices.clear();if(mode)Optimized::EmitPrimitive(cmd);else Baseline::EmitPrimitive(cmd);checksum=checksum+batch.front().u;}
   auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-start).count();
   std::cout<<"BENCH prim="<<prim<<" active="<<active<<" repeat="<<repeat<<" mode="<<mode<<" ns="<<ns<<" checksum="<<checksum<<" bytes="<<batch.size()*sizeof(BatchVertex)+(mode?Optimized::s_indices.size()*4:0)<<std::endl;
  }
 }
 return failed?1:0;
}
'''
cpp=work/'test.cpp';exe=work/'test.exe'
if args.baseline in ('inplace','tuple'):
    body=body.replace('   // Two draws in one batch catch incorrect per-draw index bases.',
                      '   Baseline::s_indices=Optimized::s_indices;\n   // Two draws in one batch catch incorrect per-draw index bases.')
    body=body.replace('   auto expanded=ExpandOptimized();',
                      '   auto expanded=ExpandOptimized();auto original=ExpandBaseline();')
    body=body.replace('Baseline::s_batch.size()!=expanded.size() || std::memcmp(Baseline::s_batch.data(),expanded.data(),Baseline::s_batch.size()*sizeof(BatchVertex))',
                      'original.size()!=expanded.size() || std::memcmp(original.data(),expanded.data(),original.size()*sizeof(BatchVertex))')
    body=body.replace('batch.clear();Optimized::s_indices.clear();','batch.clear();Baseline::s_indices.clear();Optimized::s_indices.clear();')
if args.no_benchmark:body=body[:body.index(' for(unsigned active :')]+ '\n return failed?1:0;\n}\n'
if args.baseline=='original':
    extra='''unsigned mixed=0;for(unsigned first:{0x80u,0x90u,0x98u,0xa0u})for(unsigned second:{0x80u,0x90u,0x98u,0xa0u}){
 GXCommand command{};command.vertices.resize(6);for(auto& vertex:command.vertices){vertex.has_pos=true;vertex.pre_xf=true;vertex.pos[0]=.25f;}
 Baseline::s_batch.clear();Optimized::s_batch.clear();Optimized::s_indices.clear();command.prim_type=first;Baseline::EmitPrimitive(command);Optimized::EmitPrimitive(command);command.prim_type=second;Baseline::EmitPrimitive(command);Optimized::EmitPrimitive(command);
 auto expanded=ExpandOptimized();bool ok=expanded.size()==Baseline::s_batch.size() && std::memcmp(expanded.data(),Baseline::s_batch.data(),expanded.size()*sizeof(BatchVertex))==0;if(!ok)++failed;else ++mixed;
}std::cout<<"MIXED_TOPOLOGY "<<mixed<<"/16"<<std::endl;
'''
    body=body.replace(' return failed?1:0;',extra+' return failed?1:0;',1)
expand='''static std::vector<BatchVertex> ExpandOptimized() {
 if(Optimized::s_indices.empty())return Optimized::s_batch;
 std::vector<BatchVertex> result;
 for(auto i:Optimized::s_indices) { if(i>=Optimized::s_batch.size())std::abort();result.push_back(Optimized::s_batch[i]); }
 return result;
}
''' if indexed else 'static std::vector<BatchVertex> ExpandOptimized() { return Optimized::s_batch; }\n'
if args.baseline in ('inplace','tuple'):expand+=expand.replace('ExpandOptimized','ExpandBaseline').replace('Optimized::','Baseline::')
if args.baseline=='tuple':
    helper='''static GXCommand Reused(GXCommand& cmd) {
 GXCommand unique=cmd;
 if(cmd.prim_type<0x80 || cmd.prim_type>0xA0)return unique;
 unique.vertices.clear();unique.native_vertex_indices.clear();
 const auto original=cmd.vertices;
 for(size_t i=0;i<original.size();++i) {
   const size_t k=i/3;cmd.vertices[i]=original[k];
   if(i%3==0)unique.vertices.push_back(original[k]);
   unique.native_vertex_indices.push_back(uint32_t(k));
 }
 return unique;
}
'''
    body=body.replace('   Baseline::s_batch.assign(3,BatchVertex{});','   auto unique=Reused(cmd);\n   Baseline::s_batch.assign(3,BatchVertex{});')
    body=body.replace('Optimized::EmitPrimitive(cmd)','Optimized::EmitPrimitive(unique)')
    # Benchmark commands are created once outside the timed primitive loops.
    body=body.replace('  for(unsigned repeat=', '  auto unique=Reused(cmd);\n  for(unsigned repeat=')
    body=body.replace('cmd.vertices[0].tex[0][0]=float(i&31);','cmd.vertices[0].tex[0][0]=float(i&31);unique.vertices[0].tex[0][0]=float(i&31);')
    body=body.replace('Optimized::EmitPrimitive(unique);++cases;', 'Optimized::EmitPrimitive(unique);\n   Baseline::EmitPrimitive(cmd);Optimized::EmitPrimitive(cmd);++cases;')
    expand+=helper
cpp.write_text(prefix+wrap('Baseline',baseline)+wrap('Optimized',optimized)+expand+body)
results=[]
for cmd in (['g++','-std=c++20','-O2','-I',str(stage/'include'),str(cpp),'-o',str(exe)],[str(exe)]):
    r=subprocess.run(cmd,capture_output=True,text=True)
    print(r.returncode,r.stdout+r.stderr,flush=True)
    results.append(dict(command=cmd,exit_code=r.returncode,output=r.stdout+r.stderr))
    name='index-tuple-vertex-tests.json' if args.baseline=='tuple' else 'vertex-inplace-tests.json' if args.baseline=='inplace' else 'indexed-vs-reuse-tests.json' if args.baseline=='reuse' else 'indexed-batch-tests.json' if indexed else 'vertex-reuse-tests.json'
    if args.baseline=='inplace' and args.no_benchmark:name='vertex-inplace-verification.json'
    (p/'reports'/name).write_text(json.dumps(results,indent=2)+'\n')
    if r.returncode:raise SystemExit(r.returncode)
