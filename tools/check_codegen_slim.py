"""Entry pruning + codegen slimming vs the original generated text, on random state (no game launch).

Every leaf generated function (no calls to other generated functions, no polling loops, no bounded blocks) is taken twice: from
build/slim-before (the chunks before fix_codegen_slim.py, i.e. after fix_prune_entry_cases.py) and from build/crash-generated (after).
Each pair is run from its entry on identical random registers, FPRs, CR, XER and memory. Everything must match exactly: GPR, FPR, PS1,
CR, CTR, LR, XER, FPSCR, pc, memory and the instruction count (count is exact at every function exit).
"""
from pathlib import Path
import re,sys,random
p=Path(__file__).resolve().parents[1];gen=p/'build/crash-generated';snap=p/'build/slim-before';work=p/'build/gap-emit/fixture';work.mkdir(parents=True,exist_ok=True)
FUNC=re.compile(r'^void (func_[0-9A-F]{8})\((?:CPUContext&|NWII_CTX_PARAM) ctx\) \{',re.M)
def functions(directory):
    out={}
    for f in sorted(directory.glob('output_*.cpp'))+[directory/'main_output.cpp']:
        if f.name=='output_74.cpp':continue
        s=f.read_text(errors='ignore')
        for m in FUNC.finditer(s):
            z=s.find('\nvoid func_',m.end());out[m[1]]=s[m.start():z if z>0 else len(s)]
    return out
old=functions(snap);new=functions(gen)
def leaf(k,v):
    if 'interpret' in v.replace('            default: nwii::runtime::interpret_step(ctx); return;',''):return False   # unsupported-opcode fallbacks run the interpreter
    return not [x for x in re.findall(r'func_[0-9A-F]{8}',v) if x!=k] and 'native_poll_due' not in v and 'native_block' not in v and 'find_func_bound' not in v and 'dispatch' not in v and 'trace_call' not in re.sub(r'    if \(nwii::runtime::g_trace_calls[^\n]*\n','',v).replace('NWII_TRACE_CALL','')
keep=sorted(k for k in old if k in new and leaf(k,old[k]) and leaf(k,new[k]))
changed=sum(1 for k in keep if old[k]!=new[k])
random.seed(99);sample=keep if len(sys.argv)<2 else random.sample(keep,min(int(sys.argv[1]),len(keep)))
code='''#include "runtime/cpu_context.h"
#include "runtime/config.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <bit>
#include <cmath>
#include <random>
#include <memory>
using namespace nwii::runtime;
namespace nwii::runtime {MMU* g_mmu=nullptr;CPUContext* g_ctx_ptr=nullptr;}
extern "C" void run_game(CPUContext&){abort();}
'''
def wrap(ns,bodies):
    return 'namespace '+ns+' {\n'+'\n'.join(bodies[k] for k in sample)+'\n}\n'
code+=wrap('ow',old)+wrap('nw',new)
code+='struct Entry{uint32_t addr;void(*nw_fn)(CPUContext&);void(*ow_fn)(CPUContext&);};\nstatic const Entry entries[]={\n'+''.join(f'{{0x{k[5:]},nw::{k},ow::{k}}},\n' for k in sample)+'};\n'
code+=r'''
static uint64_t B(double d){uint64_t b;memcpy(&b,&d,8);return b;}
int main(){
  auto A=std::make_unique<CPUContext>(),C=std::make_unique<CPUContext>();std::mt19937 rng(31337);unsigned tested=0,bad=0,trials=0;
  for(const auto& e:entries){
    bool entry_bad=false;++tested;
    for(unsigned t=0;t<10;++t){
      uint32_t seed=rng();
      for(auto* c:{A.get(),C.get()}){std::mt19937 q(seed);
        for(int i=0;i<32;++i){uint32_t k=q()%4,v=q();c->gpr[i]=k==0?0x80100000u+((v&0x7FFu)<<2):k==1?(v&0xFFFFu):k==2?(0x80100000u+((v&0x3FFu)<<2)):v;}
        for(int i=0;i<32;++i){c->fpr[i]=double(int32_t(q()))/4096.0;c->ps1[i]=double(int32_t(q()))/4096.0;}
        for(int i=0;i<8;++i){uint32_t v=q();c->cr[i].lt=v&1;c->cr[i].gt=v&2;c->cr[i].eq=v&4;c->cr[i].so=v&8;}
        c->ctr=q()&0xFFFu;c->xer=q()&0xE000007Fu;c->lr=0x80000F00u;c->msr=0x8032u|(((seed>>3)&1)?0x2000u:0u);c->fpscr=0;c->gqr.fill(0);c->in_callback=false;c->dispatch_saved_ctx=0;c->inst_count=0;
        for(uint32_t i=0;i<2048;++i)c->mmu.write32(0x80100000u+4*i,(q()&0x807FFFFFu)|0x3F800000u);
        c->pc=e.addr;}
      g_mmu=&A->mmu;e.ow_fn(*A);g_mmu=&C->mmu;e.nw_fn(*C);++trials;
      bool same=A->pc==C->pc&&A->srr0==C->srr0&&A->srr1==C->srr1&&A->msr==C->msr&&A->lr==C->lr&&A->ctr==C->ctr&&A->xer==C->xer&&A->fpscr==C->fpscr&&A->inst_count==C->inst_count;
      for(int i=0;i<32;++i)same=same&&A->gpr[i]==C->gpr[i]&&B(A->fpr[i])==B(C->fpr[i])&&B(A->ps1[i])==B(C->ps1[i]);
      for(int i=0;i<8;++i)same=same&&A->cr[i].lt==C->cr[i].lt&&A->cr[i].gt==C->cr[i].gt&&A->cr[i].eq==C->cr[i].eq&&A->cr[i].so==C->cr[i].so;
      for(uint32_t i=0;i<2048&&same;++i)same=A->mmu.read32(0x80100000u+4*i)==C->mmu.read32(0x80100000u+4*i);
      if(!same){entry_bad=true;if(!bad)printf("first mismatch entry=%08X seed=%u pcA=%08X pcB=%08X instA=%llu instB=%llu\n",e.addr,seed,A->pc,C->pc,(unsigned long long)A->inst_count,(unsigned long long)C->inst_count);}
    }
    bad+=entry_bad;
  }
  printf("CODEGEN_SLIM functions=%u mismatching=%u trials=%u\n",tested,bad,trials);
  return bad?1:0;
}
'''
(work/'slim_test.cpp').write_text(code,errors='surrogateescape')
cm=p/'build/nwii-full-source/CMakeLists.txt';t=cm.read_text();marker='# NATIVE_SLIM_TEST_V1'
if marker not in t:
    t+='\n'+marker+'\nadd_executable(crash_slim_tests "'+(work/'slim_test.cpp').as_posix()+'")\ntarget_link_libraries(crash_slim_tests PRIVATE nwiiruntime)\ntarget_include_directories(crash_slim_tests PRIVATE "'+gen.as_posix()+'")\ntarget_compile_options(crash_slim_tests PRIVATE -O1 -fwrapv -fno-strict-aliasing -ffp-contract=off)\n'
    cm.write_text(t)
print(f'slim fixture written: {len(sample)} leaf functions ({changed} of {len(keep)} leaf functions differ in text)')
