"""Recompiled gap functions vs the interpreter on random register/memory state (leaf gap functions only)."""
from pathlib import Path
import re,subprocess,json,sys
p=Path(__file__).resolve().parents[1];gen=p/'build/crash-generated';work=p/'build/gap-emit/fixture';work.mkdir(parents=True,exist_ok=True)
funcs={}
for f in sorted(gen.glob('output_gap_*.cpp')):
    s=f.read_text(errors='ignore')
    pre=s[:s.index('void func_')]
    for m in re.finditer(r'^void (func_[0-9A-F]{8})\((?:CPUContext&|NWII_CTX_PARAM) ctx\) \{',s,re.M):
        z=s.find('\nvoid func_',m.end());funcs[m[1]]=s[m.start():z if z>0 else len(s)]
leaf={k:v for k,v in funcs.items() if not [x for x in re.findall(r'func_[0-9A-F]{8}',v) if x!=k] and 'dispatch' not in v and 'find_func_bound' not in v and 'native_poll_due' not in v}  # loops need a bounded driver; straight-line leaves only
CR_LOGIC={33,129,193,225,257,289,417,449}  # crnor crandc crxor crnand crand creqv crorc cror: the interpreter's opcode-19 handler implements only bc/bclr/bcctr/mcrf
def uses_cr_logic(v):return any(int(w,16)>>26==19 and ((int(w,16)>>1)&0x3ff) in CR_LOGIC for _,w in re.findall(r'// 0x([0-9A-F]{8}): ([0-9A-F]{8})',v))
cr_logic_excluded=[k for k,v in leaf.items() if uses_cr_logic(v)]
leaf={k:v for k,v in leaf.items() if k not in cr_logic_excluded}
gaps=[l.split('\t') for l in (p/'build/gap-emit/gaps.tsv').read_text().splitlines()[1:]]
size={('func_'+a.upper()):int(b,16) for a,b,_,_ in gaps}
pre=re.sub(r'#include "functions.h"\n','',pre)
code=pre+'\nusing namespace nwii::runtime;\nnamespace nwii::runtime {MMU* g_mmu=nullptr;CPUContext* g_ctx_ptr=nullptr;}\nextern "C" void run_game(CPUContext&){abort();}\n'
code+='\n'.join(leaf.values())+'\n'
words=sorted({(int(a,16),int(w,16)) for v in leaf.values() for a,w in re.findall(r'// 0x([0-9A-F]{8}): ([0-9A-F]{8})',v)})
code+='static const uint32_t code_words[][2]={'+','.join('{0x%08X,0x%08X}'%(a,w) for a,w in words)+'};\n'
code+='#include <random>\n#include <algorithm>\n#include <iterator>\n#include <map>\n#include <memory>\nstruct Entry{uint32_t start,end;void(*fn)(CPUContext&);};\nstatic const Entry entries[]={\n'+''.join(f'{{0x{k[5:]},0x{int(k[5:],16)+size[k]:X},{k}}},\n' for k in sorted(leaf))+'};\n'
code+=r'''
static uint64_t bits(double d){uint64_t b;memcpy(&b,&d,8);return b;}
int main(int argc,char** argv){const int only_t=argc>2?atoi(argv[2]):-1;const uint32_t only=argc>1?uint32_t(strtoul(argv[1],nullptr,16)):0;
  auto A=std::make_unique<CPUContext>(),B=std::make_unique<CPUContext>();
  std::mt19937 rng(12345);std::map<std::string,unsigned> fields;unsigned dbg=0,ps1_known=0,tested=0,bad=0,trials_total=0;std::string first;
  for(const auto& e:entries){
    if(only&&e.start!=only)continue;
    bool function_bad=false;std::map<std::string,unsigned> mine;
    for(unsigned t=0;t<48;++t){
      uint32_t seed=rng();if(only_t>=0&&int(t)!=only_t)continue;std::mt19937 r(seed);
      CPUContext* ctxs[2]={A.get(),B.get()};
      for(auto* c:ctxs){
        std::mt19937 q(seed);
        for(int i=0;i<32;++i){uint32_t k=q()%3,v=q();c->gpr[i]=k==0?0x80100000u+(v&0xBFCu):k==1?(v&0xFFFFu):(0x80000000u|(v&0x017FFFFCu));}
        for(int i=0;i<32;++i){int32_t a=int32_t(q()),b=int32_t(q());c->fpr[i]=double(a)/4096.0;c->ps1[i]=double(b)/4096.0;}
        for(int i=0;i<8;++i){uint32_t v=q();c->cr[i].lt=v&1;c->cr[i].gt=v&2;c->cr[i].eq=v&4;c->cr[i].so=v&8;}
        c->ctr=q()&~3u;c->xer=q()&0xE000007Fu;c->lr=0x80000F00u;c->msr=0xA032u|0x2000u;c->fpscr=0;c->gqr.fill(0);c->in_callback=false;c->dispatch_saved_ctx=0;c->inst_count=0;
        for(uint32_t i=0;i<1024;++i)c->mmu.write32(0x80100000u+4*i,(q()&0x807FFFFFu)|0x3F800000u);
        for(uint32_t i=0;i<256;++i)c->mmu.write32(0x4*i+0x20,(q()&0x807FFFFFu)|0x3F800000u);
      }
      for(const auto* cw=std::lower_bound(std::begin(code_words),std::end(code_words),e.start,[](const uint32_t (&w)[2],uint32_t a){return w[0]<a;});cw!=std::end(code_words)&&(*cw)[0]<e.end;++cw){A->mmu.write32((*cw)[0],(*cw)[1]);B->mmu.write32((*cw)[0],(*cw)[1]);}
      const double i_f1=A->fpr[1];const uint32_t i_r2=A->gpr[2],i_r3=A->gpr[3];
      nwii::runtime::g_mmu=&A->mmu;A->pc=e.start;e.fn(*A);
      B->pc=e.start;nwii::runtime::g_mmu=&B->mmu;unsigned steps=0;
      while(B->pc!=0x80000F00u && B->pc>=e.start && B->pc<e.end && steps<4000){nwii::runtime::interpret_one(*B);++steps;}
      ++trials_total;
      if(only_t>=0&&only){for(int i=0;i<8;++i)printf("REG f%d A=%.9g B=%.9g\n",i,A->fpr[i],B->fpr[i]);printf("CR0 A=%d%d%d B=%d%d%d steps=%u\n",A->cr[0].lt,A->cr[0].gt,A->cr[0].eq,B->cr[0].lt,B->cr[0].gt,B->cr[0].eq,steps);}
      std::string why;
      auto cmp=[&](const char* name,bool same){if(!same){fields[name]++;mine[name]++;if(why.empty())why=name;}};
      for(int i=0;i<32;++i){if(A->gpr[i]!=B->gpr[i]){cmp("gpr",false);if(dbg<40&&mine["gpr"]==1){++dbg;printf("DBG %08X gpr%d A=%08X B=%08X\n",e.start,i,A->gpr[i],B->gpr[i]);}}if(bits(A->fpr[i])!=bits(B->fpr[i])){cmp("fpr",false);if(dbg<40&&mine["fpr"]==1){++dbg;printf("DBG t=%u %08X fpr%d A=%.17g B=%.17g f1=%.17g r2=%08X r3=%08X\n",t,e.start,i,A->fpr[i],B->fpr[i],i_f1,i_r2,i_r3);}}if(bits(A->ps1[i])!=bits(B->ps1[i]))++ps1_known;}
      for(int i=0;i<8;++i)cmp("cr",A->cr[i].lt==B->cr[i].lt&&A->cr[i].gt==B->cr[i].gt&&A->cr[i].eq==B->cr[i].eq);
      cmp("ctr",A->ctr==B->ctr);cmp("lr",A->lr==B->lr);cmp("xer",A->xer==B->xer);cmp("pc",(A->pc&~3u)==(B->pc&~3u));cmp("fpscr",A->fpscr==B->fpscr);
      uint64_t ha=1469598103934665603ull,hb=ha;
      for(uint32_t i=0;i<1024;++i){ha=(ha^A->mmu.read32(0x80100000u+4*i))*1099511628211ull;hb=(hb^B->mmu.read32(0x80100000u+4*i))*1099511628211ull;}
      for(uint32_t i=0;i<256;++i){ha=(ha^A->mmu.read32(4*i+0x20))*1099511628211ull;hb=(hb^B->mmu.read32(4*i+0x20))*1099511628211ull;}
      cmp("memory",ha==hb);
      if(!why.empty()){function_bad=true;if(first.empty()){char buf[160];snprintf(buf,sizeof buf,"%08X first mismatch field=%s seed=%u steps=%u pcA=%08X pcB=%08X lrA=%08X lrB=%08X",e.start,why.c_str(),seed,steps,A->pc,B->pc,A->lr,B->lr);first=buf;}}
    }
    if(function_bad){printf("BAD %08X",e.start);for(auto& m:mine)printf(" %s=%u",m.first.c_str(),m.second);printf("\n");}
    ++tested;bad+=function_bad;
  }
  printf("GAP_FUNCTIONS tested=%u mismatching=%u trials=%u\n",tested,bad,trials_total);
  for(auto& f:fields)printf("  field %s mismatches=%u\n",f.first.c_str(),f.second);
  printf("  known divergence, excluded: ps1 after single-precision ops (generator mirrors ps0 into ps1 as Dolphin does) %u; cr.so (generator does not copy XER.SO on compares)\n",ps1_known);
  if(!first.empty())printf("  %s\n",first.c_str());
  return bad?1:0;
}
'''
(work/'gap_test.cpp').write_text(code,errors='surrogateescape')
cm=p/'build/nwii-full-source/CMakeLists.txt';t=cm.read_text();marker='# NATIVE_GAP_TEST_V1'
if marker not in t:
    t+='\n'+marker+'\nadd_executable(crash_gap_tests "'+(work/'gap_test.cpp').as_posix()+'")\ntarget_link_libraries(crash_gap_tests PRIVATE nwiiruntime)\ntarget_include_directories(crash_gap_tests PRIVATE "'+gen.as_posix()+'")\ntarget_compile_options(crash_gap_tests PRIVATE -O1 -fwrapv -fno-strict-aliasing -ffp-contract=off)\n'
    cm.write_text(t)
print('gap fixture written:',len(leaf),'leaf gap functions;',len(cr_logic_excluded),'more use CR-logical ops the interpreter does not implement and are excluded')
