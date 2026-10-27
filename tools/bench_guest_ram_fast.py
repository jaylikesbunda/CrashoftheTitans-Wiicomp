"""Direct ordinary-RAM bank reads versus the current dependent page-bias load."""
from pathlib import Path
from port_paths import default_storage
import ast,subprocess,json,argparse
p=Path(__file__).resolve().parents[1];base=default_storage();w=base/'guest-direct-ram';w.mkdir(exist_ok=True)
ap=argparse.ArgumentParser()
ap.add_argument('--include',type=Path,default=p/'build/nwii-full-source/nWiiRuntime/include',help='staged nWiiRuntime/include to benchmark against')
ap.add_argument('--static-base',action='store_true',help='compile the candidate with -DNWII_STATIC_RAM_BASE (fixed-base fast path)')
ap.add_argument('--report',type=Path,default=p/'reports/guest-direct-ram-probe.json')
args=ap.parse_args()
stubs=next(ast.literal_eval(n.value) for n in ast.parse((p/'tools/check_runtime_repairs.py').read_text()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='stubs' for t in n.targets))
stubs=stubs.replace('uint16_t HW_Reg_Read16(uint32_t){std::abort();}','uint16_t HW_Reg_Read16(uint32_t a){return uint16_t(a^0x1973);}')
stubs=stubs.replace('uint32_t HW_Reg_Read32(uint32_t){std::abort();}','uint32_t HW_Reg_Read32(uint32_t a){return a^0x51973482;}')
code='#include "runtime/cpu_context.h"\n#include <memory>\n#include <random>\n#include <chrono>\n#include <vector>\n#include <algorithm>\n#include <cstdio>\n'+stubs+r'''
using namespace nwii::runtime;
__attribute__((always_inline)) inline uint32_t Direct(MMU& memory,uint32_t address){
 const uint32_t physical=address&0x3fffffffu;const uint8_t* source;
 if(__builtin_expect(physical<MMU::kMem1Limit && physical!=12,1))source=memory.mem1.data()+physical;
 else if(physical-0x10000000u<MMU::kMem2Limit)source=memory.mem2.data()+physical-0x10000000u;
 else return memory.native_read32_slow(address);
 uint32_t value;memcpy(&value,source,4);return __builtin_bswap32(value);
}
template<bool direct> __attribute__((always_inline)) inline uint32_t Read(MMU& memory,uint32_t address){if constexpr(direct)return Direct(memory,address);else return memory.read32(address);}
template<bool direct> __attribute__((always_inline)) inline uint64_t Walk(MMU& memory,uint32_t address,unsigned count){uint64_t h=0;for(unsigned i=0;i<count;++i){address=Read<direct>(memory,address);h+=address;}return h;}
__attribute__((noipa)) uint64_t Baseline(MMU& m,uint32_t a,unsigned n){return Walk<false>(m,a,n);}
__attribute__((noipa)) uint64_t Candidate(MMU& m,uint32_t a,unsigned n){return Walk<true>(m,a,n);}
__attribute__((noipa,target("movbe"))) uint64_t BaselineMovbe(MMU& m,uint32_t a,unsigned n){return Walk<false>(m,a,n);}
__attribute__((noipa,target("movbe"))) uint64_t CandidateMovbe(MMU& m,uint32_t a,unsigned n){return Walk<true>(m,a,n);}
int main(){auto cp=std::make_unique<CPUContext>();auto& m=cp->mmu;std::mt19937 q(347);
 for(unsigned i=0;i<65536;++i){m.mem1[i]=uint8_t(q());m.mem2[i]=uint8_t(q());}unsigned checks=0;
 const uint32_t addresses[]={0,1,8,9,11,12,13,15,0xfffe,0x10000,0x17ffffd,0x17fffff,0x1800000,0x1800007,0x10000000,0x13fffffd,0x13ffffff,0x14000000,0x0c008000,0x0d000000};
 for(uint32_t prefix:{0u,0x80000000u,0xc0000000u})for(uint32_t a:addresses){if(m.read32(a|prefix)!=Direct(m,a|prefix)){printf("DIRECT_RAM_FAIL %x\n",a|prefix);return 1;}++checks;}
 for(unsigned i=0;i<60000;++i){uint32_t a=(i%2?0x80000000:0x90000000)+(q()%65532);if(m.read32(a)!=Direct(m,a))return 2;++checks;}
 printf("DIRECT_RAM_STATES %u/%u\n",checks,checks);
 using Fn=uint64_t(*)(MMU&,uint32_t,unsigned);Fn functions[]={Baseline,Candidate,BaselineMovbe,CandidateMovbe};
 const unsigned nodes=16384;std::vector<unsigned> order(nodes);for(unsigned i=0;i<nodes;++i)order[i]=i;std::shuffle(order.begin(),order.end(),q);
 for(unsigned kind=0;kind<3;++kind){uint32_t base=kind==0?0x80010000:kind==1?0x80000100:0x90010000;
  unsigned length=kind==1?8192:nodes;
  for(unsigned i=0;i<length;++i)m.write32(base+order[i]*4,base+order[(i+1)%length]*4);
  uint32_t start=base+order[0]*4;
  for(unsigned variant=1;variant<4;++variant){std::vector<double> old,next,ratios;uint64_t digest[2]{};
   for(unsigned trial=0;trial<16;++trial){double ns[2]{};for(unsigned first=0;first<2;++first){unsigned which=(trial+first)%2;auto tick=std::chrono::steady_clock::now();digest[which]=functions[which?variant:0](m,start,300000);auto stop=std::chrono::steady_clock::now();ns[which]=std::chrono::duration<double,std::nano>(stop-tick).count()/300000;}
    if(digest[0]!=digest[1])return 3;if(trial>=2){old.push_back(ns[0]);next.push_back(ns[1]);ratios.push_back(ns[1]/ns[0]);}
   }for(auto* v:{&old,&next,&ratios})std::sort(v->begin(),v->end());printf("DIRECT_RAM_BENCH kind=%u variant=%u old_ns=%.3f next_ns=%.3f paired_ratio=%.4f\n",kind,variant,old[old.size()/2],next[next.size()/2],ratios[ratios.size()/2]);
  }
 }
}
'''
cpp=w/('fixture-static.cpp' if args.static_base else 'fixture.cpp');exe=w/('fixture-static.exe' if args.static_base else 'fixture.exe');cpp.write_text(code);runs=[]
compile_cmd=['g++','-std=c++20','-O2','-g0','-fno-strict-aliasing','-static','-I',str(args.include)]
if args.static_base:compile_cmd.append('-DNWII_STATIC_RAM_BASE')
compile_cmd.append(str(cpp))
if args.static_base:compile_cmd.append(str(p/'runtime-overlays/static_ram_base.cpp'))
compile_cmd+=[ '-o',str(exe)]
for cmd in (compile_cmd,[str(exe)]):
 x=subprocess.run(cmd,capture_output=True,text=True,timeout=300);runs.append(dict(command=cmd,exit_code=x.returncode,output=x.stdout+x.stderr));print((x.stdout+x.stderr)[-3500:],flush=True)
 if x.returncode:break
args.report.write_text(json.dumps(dict(runs=runs,game_launched=False,production_enabled=False,static_base=args.static_base,include=str(args.include)),indent=2)+'\n');raise SystemExit(x.returncode)
