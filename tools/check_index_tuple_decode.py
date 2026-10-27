"""Production parser differential including reuse, fallback and per-draw mutation."""
from pathlib import Path
from port_paths import default_storage
import json,os,re,subprocess
p=Path(__file__).resolve().parents[1];b=default_storage();w=b/'index-tuple-tests';w.mkdir(exist_ok=True)
old=(b/'trace-bottleneck-tests/decoder.cpp').read_text()
prefix=old[:old.index('int main()')]
prefix=prefix.replace('word(c.vertices.size());for(const auto&v:c.vertices){','word(c.NativeVertexCount());for(size_t i=0;i<c.NativeVertexCount();++i){const auto&v=c.NativeVertex(i);')
main=r'''
int main(){auto ctx=std::make_unique<CPUContext>();g_ctx_ptr=ctx.get();g_mmu=&ctx->mmu;
uint32_t rng=0x73138723;auto random=[&](){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;};
for(unsigned i=0;i<262144;++i)ctx->mmu.write8(0x80010000+i,uint8_t(random()));
uint64_t reused_draws=0,saved_vertices=0;
for(unsigned trial=0;trial<12000;++trial){GXCommand c{};c.type=GXCommandType::DrawPrimitive;c.prim_type=uint8_t(0x80+(trial%4)*8);if(c.prim_type==0x88)c.prim_type=0x90;
c.raw=std::make_shared<DrawRaw>();auto&r=*c.raw;r.vat={};r.count=uint16_t(1+random()%256);r.defPosMtxIdx=random()%64;
for(unsigned t=0;t<8;++t)r.defTexMtxIdx[t]=random()%64;
auto v=unsigned(trial);r.vat.posMask=VtxAttrMask(2+(v&1));r.vat.nrmMask=VtxAttrMask(2+((v>>1)&1));r.vat.texMask[0]=VtxAttrMask(2+((v>>2)&1));
r.vat.posType=r.vat.nrmType=r.vat.texType[0]=VtxAttrType::F32;r.vat.posElements=r.vat.texElements[0]=1;
r.vat.posMatIdx=(trial%3==0);r.vat.texMatIdx[0]=(trial%5==0);
for(unsigned j=0;j<16;++j){r.arrayBase[j]=trial%13?0x80010000+j*4096:0x70000000;r.arrayStride[j]=12;}
for(unsigned j=0;j<2;++j){r.vat.clrMask[j]=VtxAttrMask(trial%4);r.vat.clrType[j]=VtxAttrType::F32;}
r.have_snap_mtx=trial%3==0;for(unsigned j=0;j<12;++j)r.snap_pos_mtx[j]=float(int(random()%31)-15)/16;
for(unsigned j=0;j<9;++j)r.snap_nrm_mtx[j]=float(int(random()%31)-15)/16;
if(trial%3==1){r.snap_pos_bank.resize(256);r.snap_nrm_bank.resize(96);for(auto&f:r.snap_pos_bank)f=float(int(random()%31)-15)/16;for(auto&f:r.snap_nrm_bank)f=float(int(random()%31)-15)/16;}
auto append_index=[&](unsigned mask,uint32_t idx){if(mask==3)r.bytes.push_back(uint8_t(idx>>8));r.bytes.push_back(uint8_t(idx));};
const unsigned sharing=trial%4==0?1:trial%4==1?2:trial%4==2?3:8;
for(unsigned i=0;i<r.count;++i){const unsigned k=i/sharing;
if(r.vat.posMatIdx)r.bytes.push_back(uint8_t(k%64));if(r.vat.texMatIdx[0])r.bytes.push_back(uint8_t(k%64));
append_index(unsigned(r.vat.posMask),k);append_index(unsigned(r.vat.nrmMask),k);
for(unsigned j=0;j<2;++j){auto mask=unsigned(r.vat.clrMask[j]);if(mask==1)for(unsigned x=0;x<3;++x)r.bytes.push_back(uint8_t(k*7+j*3+x));else if(mask)append_index(mask,k);}
append_index(unsigned(r.vat.texMask[0]),k);}
if(trial%17==0 && !r.bytes.empty())r.bytes.pop_back();
// Each draw sees freshly changed source RAM; nothing is reused across draws.
ctx->mmu.write32(0x80010000,random());g_mmu=trial%19?&ctx->mmu:nullptr;
FifoParser::DecodeDraw(c);digest(c);if(!c.native_vertex_indices.empty()){++reused_draws;saved_vertices+=c.NativeVertexCount()-c.NativeStoredVertexCount();}}
std::cout<<"DIFFERENTIAL hash="<<hash<<" vertices="<<checks<<'\n';
std::cout<<"REUSE draws="<<reused_draws<<" vertices_avoided="<<saved_vertices<<'\n';g_mmu=&ctx->mmu;
for(unsigned sharing:{1u,2u,3u,8u}) {
GXCommand c{};c.type=GXCommandType::DrawPrimitive;c.prim_type=0x90;c.raw=std::make_shared<DrawRaw>();auto&r=*c.raw;r.vat={};r.count=32766;
r.vat.posMask=r.vat.nrmMask=r.vat.texMask[0]=VtxAttrMask::Index16;r.vat.posType=r.vat.nrmType=r.vat.texType[0]=VtxAttrType::F32;r.vat.posElements=r.vat.texElements[0]=1;
for(unsigned j=0;j<16;++j){r.arrayBase[j]=0x80010000;r.arrayStride[j]=12;}
r.have_snap_mtx=true;for(unsigned j=0;j<12;++j)r.snap_pos_mtx[j]=(j%5==0)?1.f:0.f;for(unsigned j=0;j<9;++j)r.snap_nrm_mtx[j]=(j%4==0)?1.f:0.f;
for(unsigned i=0;i<r.count;++i)for(unsigned a=0;a<3;++a){unsigned k=i/sharing;r.bytes.push_back(k>>8);r.bytes.push_back(k);}
auto raw=c.raw;auto start=std::chrono::steady_clock::now();for(unsigned i=0;i<40;++i){c.raw=raw;c.vertices.clear();c.native_vertex_indices.clear();FifoParser::DecodeDraw(c);if(c.NativeVertexCount()!=r.count)return 2;}
std::cout<<"DECODE_BENCH sharing="<<sharing<<" us="<<std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-start).count()<<" unique="<<c.NativeStoredVertexCount()<<'\n';}
}
'''
(w/'decoder.cpp').write_text(prefix+main)
rt=p/'build/nwii-full-source/nWiiRuntime';parser=(rt/'src/hle/gx/fifo_parser.cpp').read_text();a=parser.index('VertexData GXCommand::NativeDecodedVertex(');z=parser.index('\n}\n',a)+3
(w/'baseline_getter.cpp').write_text('#include "runtime/gx/fifo_parser.h"\n#include <algorithm>\n#include <cstring>\nnamespace nwii::runtime::gx {\n'+parser[a:z]+'\n}\n')
cm=p/'build/nwii-full-source/CMakeLists.txt';s=cm.read_text();marker='# Index tuple reuse production fixtures'
if marker not in s:
    s+='\n'+marker+'\n'
    for name,extra in [('tuple_before',' "'+(p/'runtime-overlays/index_tuple_parser_baseline.cpp').as_posix()+'"'),('tuple_after','')]:
        s+=f'add_executable(crash_{name} "{(w/"decoder.cpp").as_posix()}"{extra})\ntarget_link_libraries(crash_{name} PRIVATE nwiiruntime)\ntarget_compile_options(crash_{name} PRIVATE -O2 -fwrapv -fno-strict-aliasing -ffp-contract=off)\n'
    cm.write_text(s)
old=f'add_executable(crash_tuple_before "{(w/"decoder.cpp").as_posix()}" "{(p/"runtime-overlays/index_tuple_parser_baseline.cpp").as_posix()}")'
new=old[:-1]+f' "{(w/"baseline_getter.cpp").as_posix()}")'
if new not in s:
 assert s.count(old)==1;s=s.replace(old,new,1);cm.write_text(s)
rows=[];env=os.environ.copy()
for k in list(env):
    if k.startswith('NWII_'):env.pop(k)
env['NWII_NO_DIAG']='1'
env['NWII_NO_GPU_VERTEX']='1'
def run(cmd):
    r=subprocess.run(list(map(str,cmd)),capture_output=True,text=True,env=env);rows.append(dict(command=list(map(str,cmd)),exit_code=r.returncode,output=r.stdout+r.stderr));(p/'reports/index-tuple-decode-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print(rows[-1]['output'][-2600:],flush=True)
    if r.returncode:raise SystemExit(r.returncode)
    return r.stdout
run(['cmake','--build',b/'toolchain','--target','crash_tuple_before','crash_tuple_after','-j','6'])
before=run([b/'toolchain/crash_tuple_before.exe']);after=run([b/'toolchain/crash_tuple_after.exe'])
assert re.search(r'DIFFERENTIAL.*',before)[0]==re.search(r'DIFFERENTIAL.*',after)[0]
assert int(re.search(r'REUSE draws=(\d+)',after)[1])>100
env['NWII_NO_INDEX_REUSE']='1';fallback=run([b/'toolchain/crash_tuple_after.exe'])
assert re.search(r'DIFFERENTIAL.*',before)[0]==re.search(r'DIFFERENTIAL.*',fallback)[0]
print('Complete expanded semantics agree; disable switch restores expanded storage.')
