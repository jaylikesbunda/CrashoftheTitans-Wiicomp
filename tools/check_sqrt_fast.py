"""func_803A35F0 with the host-sqrt fast path vs the original generated function, on random operands (no game launch).

Positive normal doubles: result f1, pc, r1, lr, every callee-saved GPR/FPR and the memory above the caller's stack pointer must match
exactly; volatile scratch state is intentionally not compared. Everything else (zero, denormals, negatives, infinities, NaNs, and
MSR.FP clear) must match the original in ALL state, including volatile registers, instruction count and the dead stack frame.
"""
from pathlib import Path
import re
import guest_baselines
p=Path(__file__).resolve().parents[1];gen=p/'build/crash-generated';work=p/'build/gap-emit/fixture';work.mkdir(parents=True,exist_ok=True)
s=(gen/'output_73.cpp').read_text(errors='ignore')
a=s.index('void func_803A35F0(');z=s.find('\nvoid func_',a+1);new_fn=s[a:z if z>0 else len(s)]
assert 'NATIVE_SQRT_FAST_V1' in new_fn,'fast path not installed'
old_fn=guest_baselines.path('sdk_sqrt_baseline.cpp').read_text(errors='ignore')
old_fn=old_fn[old_fn.index('void func_803A35F0('):]
helpers=guest_baselines.path('sdk_sqrt_local_loops.inc').read_text()
def stubs(text):
    return ''.join(f'static void {n}(CPUContext&){{}}\n' for n in sorted(set(re.findall(r'\b(native_block_[0-9A-F_]+)\(ctx\)',text))))
code='''#include "runtime/cpu_context.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <bit>
#include <iostream>
#include <random>
#include <memory>
using namespace nwii::runtime;
namespace nwii::runtime {MMU* g_mmu=nullptr;CPUContext* g_ctx_ptr=nullptr;}
extern "C" void run_game(CPUContext&){abort();}
static bool native_blocks_on=false;
'''+stubs(new_fn+old_fn)
code+='namespace ow {\n'+old_fn.replace('CPUContext& ctx','CPUContext& ctx').replace('NWII_CTX_PARAM','CPUContext&')+'\n}\n'
code+='namespace nw {\n'+helpers+new_fn+'\n}\n'
code+=r'''
static uint64_t B(double d){uint64_t b;memcpy(&b,&d,8);return b;}
int main(){
  auto A=std::make_unique<CPUContext>(),C=std::make_unique<CPUContext>();std::mt19937_64 rng(2468);
  unsigned fast_cases=0,fast_bad=0,slow_cases=0,slow_bad=0;
  for(unsigned t=0;t<60000;++t){
    uint64_t bits;unsigned kind=t%10;bool fast=false;
    uint64_t r=rng();
    if(kind<6){bits=(r&0x800FFFFFFFFFFFFFull)&~(1ull<<63);uint64_t e=1+(rng()%2046);bits|=e<<52;fast=true;}        // positive normal
    else if(kind==6){bits=r&0x800FFFFFFFFFFFFFull;}                                                                   // zero or denormal (either sign)
    else if(kind==7){bits=(r&0x800FFFFFFFFFFFFFull)|(1ull<<63)|((1+(rng()%2046))<<52);}                              // negative normal
    else if(kind==8){bits=(r&0x800FFFFFFFFFFFFFull)|(0x7FFull<<52);}                                                 // inf / nan
    else {bits=(r&0x800FFFFFFFFFFFFFull)&~(1ull<<63);bits|=(1+(rng()%2046))<<52;fast=false;}                         // normal, FP disabled below
    const bool fp_on=(kind!=9);if(kind==9)fast=false;
    for(auto* c:{A.get(),C.get()}){
      std::mt19937 q(uint32_t(t)*7919u+13);
      for(int i=0;i<32;++i)c->gpr[i]=q();for(int i=0;i<32;++i){c->fpr[i]=double(int32_t(q()))/997.0;c->ps1[i]=double(int32_t(q()))/991.0;}
      c->gpr[1]=0x80100000u+0x8000u;c->gpr[2]=0x80105000u;c->gpr[13]=0x80106000u;c->lr=0x80000F00u;c->ctr=q();c->xer=q()&0xE000007Fu;
      c->msr=0x8032u|(fp_on?0x2000u:0u);c->fpscr=0;c->gqr.fill(0);c->in_callback=false;c->inst_count=0;
      memcpy(&c->fpr[1],&bits,8);
      for(uint32_t i=0;i<0x100;++i)c->mmu.write32(0x80100000u+0x7F00u+4*i,q());
      c->mmu.write32(0x80105000u-0x2A40u,0);
      c->pc=0x803A35F0u;
    }
    g_mmu=&A->mmu;ow::func_803A35F0(*A);g_mmu=&C->mmu;nw::func_803A35F0(*C);
    bool same=A->pc==C->pc&&A->lr==C->lr&&A->gpr[1]==C->gpr[1]&&B(A->fpr[1])==B(C->fpr[1]);
    for(int i=13;i<32;++i)same=same&&A->gpr[i]==C->gpr[i];
    for(int i=14;i<32;++i)same=same&&B(A->fpr[i])==B(C->fpr[i]);
    for(uint32_t i=0;i<0x100&&same;++i){uint32_t addr=0x80100000u+0x7F00u+4*i;if(addr>=0x80100000u+0x8000u)same=same&&A->mmu.read32(addr)==C->mmu.read32(addr);}
    if(!fast){ // original path ran: everything must be identical
      for(int i=0;i<32;++i)same=same&&A->gpr[i]==C->gpr[i]&&B(A->fpr[i])==B(C->fpr[i])&&B(A->ps1[i])==B(C->ps1[i]);
      for(int i=0;i<8;++i)same=same&&A->cr[i].lt==C->cr[i].lt&&A->cr[i].gt==C->cr[i].gt&&A->cr[i].eq==C->cr[i].eq;
      same=same&&A->ctr==C->ctr&&A->xer==C->xer&&A->inst_count==C->inst_count&&A->fpscr==C->fpscr;
      for(uint32_t i=0;i<0x100&&same;++i)same=same&&A->mmu.read32(0x80100000u+0x7F00u+4*i)==C->mmu.read32(0x80100000u+0x7F00u+4*i);
      same=same&&A->mmu.read32(0x80105000u-0x2A40u)==C->mmu.read32(0x80105000u-0x2A40u);
    }
    if(fast){++fast_cases;fast_bad+=!same;}else{++slow_cases;slow_bad+=!same;}
    if(!same&&fast_bad+slow_bad<2){printf("pc %08x/%08x lr %08x/%08x r1 %08x/%08x inst %llu/%llu msr %08x/%08x ctr %08x/%08x fpscr %08x/%08x\n",A->pc,C->pc,A->lr,C->lr,A->gpr[1],C->gpr[1],(unsigned long long)A->inst_count,(unsigned long long)C->inst_count,A->msr,C->msr,A->ctr,C->ctr,A->fpscr,C->fpscr);
      for(int i=0;i<32;++i)if(A->gpr[i]!=C->gpr[i])printf(" gpr%d %08x/%08x",i,A->gpr[i],C->gpr[i]);for(int i=0;i<32;++i)if(B(A->fpr[i])!=B(C->fpr[i]))printf(" fpr%d",i);for(int i=0;i<32;++i)if(B(A->ps1[i])!=B(C->ps1[i]))printf(" ps1_%d",i);printf("\n");}
    if(!same&&fast_bad+slow_bad<4)printf("MISMATCH t=%u kind=%u bits=%016llx A=%.17g C=%.17g\n",t,kind,(unsigned long long)bits,A->fpr[1],C->fpr[1]);
  }
  printf("SQRT_FAST fast=%u mismatching=%u original_path=%u mismatching=%u\n",fast_cases,fast_bad,slow_cases,slow_bad);
  return fast_bad+slow_bad?1:0;
}
'''
(work/'sqrt_test.cpp').write_text(code,errors='surrogateescape')
cm=p/'build/nwii-full-source/CMakeLists.txt';t=cm.read_text();marker='# NATIVE_SQRT_TEST_V1'
if marker not in t:
    t+='\n'+marker+'\nadd_executable(crash_sqrt_tests "'+(work/'sqrt_test.cpp').as_posix()+'")\ntarget_link_libraries(crash_sqrt_tests PRIVATE nwiiruntime)\ntarget_include_directories(crash_sqrt_tests PRIVATE "'+gen.as_posix()+'")\ntarget_compile_options(crash_sqrt_tests PRIVATE -O2 -fwrapv -fno-strict-aliasing -ffp-contract=off)\n'
    cm.write_text(t)
print('sqrt fixture written')
