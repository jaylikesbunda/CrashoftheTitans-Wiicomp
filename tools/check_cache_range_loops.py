"""Original vs collapsed SDK cache-range loops over random address/length pairs (no game launch)."""
from pathlib import Path
from port_paths import default_storage
import re,subprocess,json,random
p=Path(__file__).resolve().parents[1];w=default_storage()/'cache-range-tests';w.mkdir(exist_ok=True)
targets=['803B50F4','803B5120','803B5150','803B5180','803B51D8','803B5204','803B525C']
orig=(default_storage()/'bounded-block-before/output_74.cpp').read_text(errors='surrogateescape')
cur=(p/'build/crash-generated/output_74.cpp').read_text(errors='surrogateescape')
def func(text,addr,name):
    a=re.search(r'void func_'+addr+r'\((?:CPUContext&|NWII_CTX_PARAM) ctx\) \{',text).start();z=text.find('\nvoid func_',a+1);z=len(text) if z<0 else z
    body=text[a:z];body=re.sub(r'#include[^\n]*','',body)
    return body.replace(f'func_{addr}',name).replace('\nloc_','\nloc_')
code='''#include "runtime/cpu_context.h"
using namespace nwii::runtime;
namespace nwii::runtime {bool g_trace_calls=false;void trace_call(uint32_t,CPUContext&){}void interpret_step(CPUContext&){__builtin_trap();}
bool process_pending_callbacks(CPUContext&){return false;}bool handle_syscall(CPUContext&){return false;}int32_t g_native_poll_credit=1;
uint32_t g_watch_addr=0;void watch_hit(uint32_t,uint32_t,int){}}
#include "'''+(p/'runtime-overlays/bounded-blocks/output_74.h').as_posix()+'''"
'''
for addr in targets:
    code+=func(orig,addr,'orig_'+addr)+'\n'+func(cur,addr,'new_'+addr)+'\n'
code+='''#include <iostream>
#include <random>
using F=void(*)(CPUContext&);
struct Pair{const char* name;F a,b;};
int main(){Pair pairs[]={'''+','.join(f'{{"{a}",orig_{a},new_{a}}}' for a in targets)+'''};
std::mt19937 rng(7);unsigned checks=0;
for(auto& pr:pairs)for(unsigned t=0;t<20000;++t){
  static CPUContext x,y;uint32_t c0=rng(),r3=rng(),r4=t<64?t:(t%3==0?rng()%100000:rng()%5000),r5=rng();
  for(auto* c:{&x,&y}){c->gpr[3]=r3;c->gpr[4]=r4;c->gpr[5]=r5;c->lr=0x80123450;c->pc=strtoul(pr.name,nullptr,16);c->inst_count=1000;c->ctr=c0;c->cr[0].so=t&1;}
  pr.a(x);pr.b(y);
  bool same=x.gpr==y.gpr&&x.ctr==y.ctr&&x.pc==y.pc&&x.lr==y.lr&&x.inst_count==y.inst_count&&x.cr[0].lt==y.cr[0].lt&&x.cr[0].gt==y.cr[0].gt&&x.cr[0].eq==y.cr[0].eq&&x.cr[0].so==y.cr[0].so;
  if(!same){std::cout<<"MISMATCH "<<pr.name<<" r3="<<std::hex<<r3<<std::dec<<" r4="<<r4<<" ctr "<<x.ctr<<"/"<<y.ctr<<" r3 "<<x.gpr[3]<<"/"<<y.gpr[3]<<" ic "<<x.inst_count<<"/"<<y.inst_count<<std::endl;return 1;}
  ++checks;}
std::cout<<"CACHE_RANGE "<<checks<<"/"<<checks<<" states equal"<<std::endl;}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code,errors='surrogateescape')
cmd=['g++','-std=c++20','-O2','-fwrapv','-fno-strict-aliasing','-static','-I',str(p/'build/nwii-full-source/nWiiRuntime/include'),str(cpp),'-o',str(exe)]
r=subprocess.run(cmd,capture_output=True,text=True);print(r.stdout+r.stderr[-1500:]);assert r.returncode==0
r=subprocess.run([str(exe)],capture_output=True,text=True);print(r.stdout[-400:]);assert r.returncode==0
(p/'reports/cache-range-tests.json').write_text(json.dumps(dict(output=r.stdout),indent=2)+'\n')
