"""Exercise actual cached display-list expansion and repeated GPU decoding."""
from pathlib import Path
from port_paths import default_storage
import json,subprocess,os,argparse
ap=argparse.ArgumentParser();ap.add_argument('--allow-failure',action='store_true');args=ap.parse_args()
p=Path(__file__).resolve().parents[1];base=default_storage();w=base/'dl-gpu-ownership-tests';w.mkdir(exist_ok=True)
rt=p/'build/nwii-full-source/nWiiRuntime'
code='''#include "'''+(rt/'src/hle/gx/fifo_parser.cpp').as_posix()+'''"
#include <memory>
namespace nwii::runtime {MMU* g_mmu=nullptr;CPUContext* g_ctx_ptr=nullptr;}
extern "C" void run_game(nwii::runtime::CPUContext&){std::abort();}
int main(){using namespace nwii::runtime;using namespace nwii::runtime::gx;
 auto ctx=std::make_unique<CPUContext>();g_mmu=&ctx->mmu;g_ctx_ptr=ctx.get();
 unsigned checks=0,failed=0,draws=0;
 auto expect=[&](bool ok,const char* reason){++checks;if(!ok){if(failed++<12)std::cout<<"FAIL "<<reason<<'\\n';}};
 for(unsigned bank=0;bank<2;++bank)for(unsigned prim:{0x80u,0x90u,0x98u,0xa0u})for(unsigned trial=0;trial<8;++trial){
   g_state={};auto& vat=g_state.vat[0];vat.posMask=VtxAttrMask::Direct;vat.posType=VtxAttrType::F32;vat.posElements=1;vat.posMatIdx=bank;
   std::fill(std::begin(g_parse_posmtx),std::end(g_parse_posmtx),0.f);std::fill(std::begin(g_parse_nrmmtx),std::end(g_parse_nrmmtx),0.f);
   g_parse_posmtx[0]=g_parse_posmtx[5]=g_parse_posmtx[10]=1;
   g_parse_nrmmtx[0]=g_parse_nrmmtx[4]=g_parse_nrmmtx[8]=1;
   std::vector<uint8_t> dl{uint8_t(prim),0,4};
   for(unsigned i=0;i<4;++i){if(bank)dl.push_back(0);for(float v:{2.f+float(i),3.f,4.f}){uint32_t bits;std::memcpy(&bits,&v,4);for(int shift:{24,16,8,0})dl.push_back(uint8_t(bits>>shift));}}
   uint32_t address=0x80040000+bank*0x10000+prim*256+trial*128;
   for(unsigned i=0;i<dl.size();++i)ctx->mmu.write8(address+i,dl[i]);
   for(unsigned replay=0;replay<12;++replay){
     g_parse_posmtx[3]=float(replay);
     std::vector<GXCommand> commands;ExpandDisplayList(address,dl.size(),commands,0);
     for(auto& c:commands)if(c.type==GXCommandType::DrawPrimitive){++draws;auto retained=c.raw;const auto original=retained->bytes;
       expect(original.size()==4*(12+bank),"cache retained encoded bytes before decode");
       if(bank)expect(c.raw->snap_pos_bank[3]==float(replay),"cached full matrix bank refreshed");
       else expect(c.raw->snap_pos_mtx[3]==float(replay),"cached single matrix refreshed");
       FifoParser::DecodeDraw(c);expect(retained->bytes==original,"decode must not mutate shared DrawRaw bytes");
       expect(c.NativeVertexCount()==4,"logical vertices preserved");
       expect(c.NativeVertex(0).pos[0]==(c.native_gpu_snapshot?2.f:2.f+float(replay)),"cached vertex remains nonzero");
     }
   }
 }
 std::cout<<"DL_OWNERSHIP "<<checks-failed<<"/"<<checks<<" draws="<<draws<<'\\n';return failed?1:0;
}
'''
(w/'fixture.cpp').write_text(code)
cm=p/'build/nwii-full-source/CMakeLists.txt';s=cm.read_text();marker='# NATIVE_DL_GPU_OWNERSHIP_TEST_V1'
if marker not in s:
    s+='\n'+marker+f'\nadd_executable(crash_dl_gpu_ownership "{(w/"fixture.cpp").as_posix()}")\ntarget_link_libraries(crash_dl_gpu_ownership PRIVATE nwiiruntime)\ntarget_compile_options(crash_dl_gpu_ownership PRIVATE -O2 -fwrapv -fno-strict-aliasing -ffp-contract=off)\n';cm.write_text(s)
rows=[]
for cmd in (['cmake','--build',str(base/'toolchain'),'--target','crash_dl_gpu_ownership','-j','6'],[str(base/'toolchain/crash_dl_gpu_ownership.exe')]):
    env={k:v for k,v in os.environ.items() if not k.startswith('NWII_')};env['NWII_NO_DIAG']='1'
    r=subprocess.run(cmd,capture_output=True,text=True,env=env,timeout=180);rows.append(dict(command=cmd,exit_code=r.returncode,output=r.stdout+r.stderr))
    name='dl-gpu-ownership-negative.json' if args.allow_failure else 'dl-gpu-ownership-tests.json'
    (p/'reports'/name).write_text(json.dumps(rows,indent=2)+'\n');print((r.stdout+r.stderr)[-2500:],flush=True)
    if r.returncode:raise SystemExit(0 if args.allow_failure and len(rows)==2 else r.returncode)
