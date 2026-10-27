"""Fall-through tail calls vs the original dispatcher round trips, on random state (no game launch).

Takes every generated function that carries NATIVE_FALLTHROUGH_TAIL_V1 plus the functions it falls into (closure), keeping only chains
that call nothing else. 'old' is the same text with the tail-call lines removed, driven by a dispatcher loop that looks up ctx.pc after
each function returns; 'new' is called once from the same entry and then driven by the same loop. Every function in the closure is
tried as an entry point (a __savegpr_N call enters at a different link each time). Registers, FPRs, memory, pc, lr, ctr, xer and the
instruction count must be identical.
"""
from pathlib import Path
import re
p=Path(__file__).resolve().parents[1];gen=p/'build/crash-generated';work=p/'build/gap-emit/fixture';work.mkdir(parents=True,exist_ok=True)
funcs={};order=[]
for f in sorted(gen.glob('output_*.cpp'))+[gen/'main_output.cpp']:
    if f.name=='output_74.cpp':continue
    s=f.read_text(errors='ignore')
    for m in re.finditer(r'^void (func_[0-9A-F]{8})\((?:CPUContext&|NWII_CTX_PARAM) ctx\) \{',s,re.M):
        z=s.find('\nvoid func_',m.end());funcs[m[1]]=s[m.start():z if z>0 else len(s)]
tail=re.compile(r'    (func_[0-9A-F]{8})\(ctx\); // NATIVE_FALLTHROUGH_TAIL_V1\n')
chain={}
for k,v in funcs.items():
    t=tail.findall(v)
    others=[x for x in re.findall(r'func_[0-9A-F]{8}',v) if x!=k and x not in t]
    if not others and 'dispatch' not in v and 'find_func_bound' not in v and 'native_poll_due' not in v and 'native_block' not in v:chain[k]=v
roots=[k for k,v in chain.items() if tail.search(v)]
keep=set()
def close(k):
    if k in keep or k not in chain:return
    keep.add(k)
    for t in tail.findall(chain[k]):close(t)
for k in roots:close(k)
while True:
    n=len(keep);keep={k for k in keep if all(t in keep for t in tail.findall(chain[k]))}
    if len(keep)==n:break
keep=sorted(keep)
body_old={k:tail.sub('',chain[k]) for k in keep}
def wrap(ns,bodies,rename):
    out='namespace '+ns+' {\n'+''.join(f'void {k}(CPUContext&);\n' for k in keep)
    for k in keep:
        b=bodies[k]
        out+=b+'\n'
    return out+'}\n'
code='''#include "runtime/cpu_context.h"
#include "runtime/config.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <bit>
#include <cmath>
#include <random>
#include <map>
#include <memory>
using namespace nwii::runtime;
namespace nwii::runtime {MMU* g_mmu=nullptr;CPUContext* g_ctx_ptr=nullptr;}
extern "C" void run_game(CPUContext&){abort();}
'''
# New functions call each other by plain name, so each version gets its own namespace.
code+=wrap('nw',{k:chain[k] for k in keep},False)+wrap('ow',body_old,False)
code+='struct Entry{uint32_t addr;void(*nw_fn)(CPUContext&);void(*ow_fn)(CPUContext&);};\nstatic const Entry entries[]={\n'+''.join(f'{{0x{k[5:]},nw::{k},ow::{k}}},\n' for k in keep)+'};\n'
code+=r'''
static const Entry* find(uint32_t pc){for(const auto& e:entries)if(e.addr==pc)return &e;return nullptr;}
template<bool NEW> static unsigned drive(CPUContext& c,const Entry* first){
  unsigned steps=0;const Entry* e=first;
  while(e&&steps<400){(NEW?e->nw_fn:e->ow_fn)(c);++steps;if(NEW&&steps==1&&c.pc==0){break;}e=find(c.pc);}
  return steps;}
int main(){
  auto A=std::make_unique<CPUContext>(),B=std::make_unique<CPUContext>();std::mt19937 rng(777);unsigned entries_tested=0,bad=0,trials=0;
  for(const auto& e:entries){
    bool entry_bad=false;++entries_tested;
    for(unsigned t=0;t<40;++t){
      uint32_t seed=rng();CPUContext* cs[2]={A.get(),B.get()};
      for(auto* c:cs){std::mt19937 q(seed);
        for(int i=0;i<32;++i)c->gpr[i]=0x80100000u+((q()&0x3FFu)<<2);
        for(int i=0;i<32;++i){c->fpr[i]=double(int32_t(q()))/4096.0;c->ps1[i]=double(int32_t(q()))/4096.0;}
        c->lr=0x80000F00u;c->ctr=q()&~3u;c->xer=q()&0xE000007Fu;c->msr=0xA032u|0x2000u;c->fpscr=0;c->gqr.fill(0);c->in_callback=false;c->inst_count=0;
        for(uint32_t i=0;i<2048;++i)c->mmu.write32(0x80100000u+4*i,q());
        c->pc=e.addr;}
      g_mmu=&A->mmu;drive<false>(*A,&e);g_mmu=&B->mmu;drive<true>(*B,&e);++trials;
      bool same=A->pc==B->pc&&A->lr==B->lr&&A->ctr==B->ctr&&A->xer==B->xer&&A->inst_count==B->inst_count;
      for(int i=0;i<32;++i){same=same&&A->gpr[i]==B->gpr[i];uint64_t x,y;memcpy(&x,&A->fpr[i],8);memcpy(&y,&B->fpr[i],8);same=same&&x==y;memcpy(&x,&A->ps1[i],8);memcpy(&y,&B->ps1[i],8);same=same&&x==y;}
      for(uint32_t i=0;i<2048&&same;++i)same=A->mmu.read32(0x80100000u+4*i)==B->mmu.read32(0x80100000u+4*i);
      if(!same){entry_bad=true;if(!bad)printf("first mismatch entry=%08X seed=%u pcA=%08X pcB=%08X instA=%llu instB=%llu\n",e.addr,seed,A->pc,B->pc,(unsigned long long)A->inst_count,(unsigned long long)B->inst_count);}
    }
    bad+=entry_bad;
  }
  printf("FALLTHROUGH_TAIL entries=%u mismatching=%u trials=%u\n",entries_tested,bad,trials);
  return bad?1:0;
}
'''
(work/'tail_test.cpp').write_text(code,errors='surrogateescape')
cm=p/'build/nwii-full-source/CMakeLists.txt';t=cm.read_text();marker='# NATIVE_TAIL_TEST_V1'
if marker not in t:
    t+='\n'+marker+'\nadd_executable(crash_tail_tests "'+(work/'tail_test.cpp').as_posix()+'")\ntarget_link_libraries(crash_tail_tests PRIVATE nwiiruntime)\ntarget_include_directories(crash_tail_tests PRIVATE "'+gen.as_posix()+'")\ntarget_compile_options(crash_tail_tests PRIVATE -O2 -fwrapv -fno-strict-aliasing -ffp-contract=off)\n'
    cm.write_text(t)
print('tail fixture written:',len(keep),'chain functions,',len(roots),'with tail calls')
