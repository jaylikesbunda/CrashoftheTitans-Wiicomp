"""Differential real-runtime fixture for the experimental region poll contract.

The inspected IOS source retains its legacy entry as oracle. Test-only producer
hooks inject events at matching original observation positions. Architecture is
deliberately stale during Prepare, then published before Finish. No game launch.
All build artifacts and result records go to an external --work directory.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import guest_region_poll as P

HARNESS=r'''
#include "runtime/cpu_context.h"
#include "native_region_poll.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <vector>
using namespace nwii::runtime;
namespace nwii::runtime {MMU* g_mmu=nullptr;CPUContext* g_ctx_ptr=nullptr;}
extern "C" void run_game(CPUContext& c){c.pc=c.lr;}
extern "C" void RegionPollTestInputs(unsigned,unsigned,unsigned);
extern "C" uint64_t RegionPollTestPhases();
extern "C" uint64_t RegionPollTestGlobals();
static uint64_t hash(uint64_t h,uint64_t v){return (h^v)*1099511628211ull;}
int main(int argc,char** argv){
 const bool candidate=argc>1&&argv[1][0]=='c';
 auto c=std::make_unique<CPUContext>();g_mmu=&c->mmu;g_ctx_ptr=c.get();
 uint32_t seed=0x72656769;auto next=[&](){seed=seed*1664525u+1013904223u;return seed;};
 uint64_t tickets[4]{};
 for(unsigned i=0;i<8192;++i){
  uint32_t regs[32];double fpr[32],ps1[32];
  for(unsigned n=0;n<32;++n){regs[n]=next();fpr[n]=double(int32_t(next()));ps1[n]=double(int32_t(next()));}
  regs[1]=0x80100000;regs[13]=0x80590000;
  const uint32_t pc=0x80300000u+4*(i&255),lr=0x80200000u+4*(i&31);
  const uint64_t count=(i%101==0)?20000000ull*(i+1):uint64_t(i)*31+1;
  c->is_running=true;c->msr=0x2000u|((i%5==0)?0u:0x8000u);
  c->in_callback=(i%7==0);c->dispatch_saved_ctx=0;c->vblank_pending=false;
  c->pending_cb_count.store(i%113==0?1:0);c->dec_irq_pending=false;
  c->dec_value=0x7fffffffu;c->dec_written_tb=0;
  RegionPollTestInputs(i%29==0?2:0,i%31==0,i%37==0);
  auto publish=[&](){
   for(unsigned n=0;n<32;++n){c->gpr[n]=regs[n];c->fpr[n]=fpr[n];c->ps1[n]=ps1[n];}
   for(unsigned n=0;n<8;++n){c->cr[n].lt=(i>>n)&1;c->cr[n].gt=(i>>(n+1))&1;c->cr[n].eq=(i>>(n+2))&1;c->cr[n].so=(i>>(n+3))&1;}
   c->pc=pc;c->lr=lr;c->inst_count=count;c->ctr=0x12345678;c->xer=0x20000000;
  };
  bool result=false;
  if(candidate){
   for(unsigned n=0;n<32;++n){c->gpr[n]=0xdead0000u+n;c->fpr[n]=-123.5;c->ps1[n]=456.25;}
   c->pc=0;c->lr=0xffffffffu;c->inst_count=0;c->ctr=0;c->xer=0;
   const auto ticket=native_region_poll::Prepare(*c,pc,count);++tickets[unsigned(ticket)];
   // Completing without publication must leave stale architecture untouched.
   if(ticket==native_region_poll::Ticket::Complete){
    if(c->pc!=0||c->lr!=0xffffffffu||c->inst_count!=0)return 20;
    for(unsigned n=0;n<32;++n)if(c->gpr[n]!=0xdead0000u+n||c->fpr[n]!=-123.5||c->ps1[n]!=456.25)return 21;
   }
   publish();result=native_region_poll::Finish(*c,ticket);
  }else{publish();result=process_pending_callbacks(*c);}
  uint64_t h=1469598103934665603ull;
  for(unsigned n=0;n<32;++n){uint64_t f,p;std::memcpy(&f,&c->fpr[n],8);std::memcpy(&p,&c->ps1[n],8);h=hash(hash(hash(h,c->gpr[n]),f),p);}
  for(unsigned n=0;n<8;++n)h=hash(h,c->cr[n].lt|(c->cr[n].gt<<1)|(c->cr[n].eq<<2)|(c->cr[n].so<<3));
  h=hash(hash(hash(hash(hash(hash(h,c->pc),c->lr),c->inst_count),c->ctr),c->xer),c->msr);
  h=hash(hash(hash(hash(h,c->pending_cb_count.load()),c->vblank_pending.load()),c->in_callback),result);
  h=hash(hash(h,RegionPollTestPhases()),RegionPollTestGlobals());
  std::printf("ROW %u %llu\n",i,(unsigned long long)h);
 }
 std::printf("TICKETS %llu %llu %llu %llu\n",(unsigned long long)tickets[0],(unsigned long long)tickets[1],(unsigned long long)tickets[2],(unsigned long long)tickets[3]);
}
'''

TEST_HOOKS=r'''
static bool region_poll_test_early=false,region_poll_test_late=false;
static inline void region_poll_test_before_flags(CPUContext& c){if(region_poll_test_early)c.pending_cb_count.store(1);}
static inline void region_poll_test_after_wpad(CPUContext& c){if(region_poll_test_late)c.pending_cb_count.store(1);}
'''
TEST_ACCESS=r'''
extern "C" void RegionPollTestInputs(unsigned delay,unsigned early,unsigned late){
 g_ipc_interrupt_delay=int(delay);hw::g_di_interrupt_delay=0;
 hw::pi_intsr.store(0);hw::pi_intmr.store(0);g_trace_after_dec=0;
 native_idle::attention.store(1);region_poll_test_early=early;region_poll_test_late=late;
}
extern "C" uint64_t RegionPollTestPhases(){return (uint64_t(native_region_wpad_phase)<<32)|native_region_clock_phase;}
extern "C" uint64_t RegionPollTestGlobals(){
 uint64_t h=1469598103934665603ull;
 const uint32_t values[]={uint32_t(g_native_poll_credit),uint32_t(g_ipc_interrupt_delay),
  uint32_t(hw::g_di_interrupt_delay),native_idle::attention.load(),hw::pi_intsr.load(),hw::pi_intmr.load()};
 for(auto value:values)h=(h^value)*1099511628211ull;return h;
}
'''


def build_and_check(base,source_path,work):
    work=work.resolve();port=Path(__file__).resolve().parents[1]
    if work==port or port in work.parents:raise ValueError('Output must be outside repo')
    work.mkdir(parents=True,exist_ok=True)
    source=source_path.read_text();patched=P.transform(source)
    # Expose test controls in this scratch copy only. The test producer runs at
    # both versions' matching flag-observation and non-due-WPAD positions.
    anchor='static const bool native_region_wpad_no_throttle='
    at=patched.index(anchor);patched=patched[:at]+TEST_HOOKS+patched[at:]
    patched=P.edit(patched,
        '  if (!native_region_wpad_no_throttle && (++native_region_wpad_phase & 127u)!=0) return;',
        '  if (!native_region_wpad_no_throttle && (++native_region_wpad_phase & 127u)!=0) {region_poll_test_after_wpad(ctx);return;}')
    patched=P.edit(patched,
        '\n  const bool diagnostics=native_poll_launch_poll_diagnostics; // NATIVE_POLL_GUARD_OUTLINE_V1: pre-initialized\n',
        '\n  const bool diagnostics=native_poll_launch_poll_diagnostics; // NATIVE_POLL_GUARD_OUTLINE_V1: pre-initialized\n  region_poll_test_before_flags(ctx);\n')
    patched=P.edit(patched,'  ++native_region_clock_phase; // due=false, established without side effects',
        '  ++native_region_clock_phase; // due=false, established without side effects\n  region_poll_test_before_flags(control);')
    patched=P.edit(patched,'  ++native_region_wpad_phase; // original non-due native_wpad_poll returns here',
        '  ++native_region_wpad_phase; // original non-due native_wpad_poll returns here\n  region_poll_test_after_wpad(control);')
    (work/'ios.cpp').write_text(patched+TEST_ACCESS)
    (work/'native_region_poll.h').write_bytes((port/'runtime-overlays/native_region_poll.h').read_bytes())
    (work/'fixture.cpp').write_text(HARNESS)
    cxx=shutil.which('g++')
    if not cxx:raise ValueError('g++ required')
    toolchain=base/'storage/toolchain'
    commands=subprocess.run(['ninja','-t','commands'],cwd=toolchain,capture_output=True,text=True,check=True).stdout.splitlines()
    original=next(s for s in commands if '/ios.cpp' in s.replace('\\','/') and ' -c ' in s)
    argv=shlex.split(original);argv[0]=cxx;argv[argv.index('-c')+1]=str(work/'ios.cpp')
    for flag in ('-o','-MT','-MF'):argv[argv.index(flag)+1]=str(work/('ios.cpp.obj.d' if flag=='-MF' else 'ios.cpp.obj'))
    def run(command,label,env=None):
        result=subprocess.run([str(v) for v in command],cwd=toolchain,capture_output=True,text=True,env=env,timeout=240)
        (work/(label+'.log')).write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(label+' failed: '+(result.stdout+result.stderr)[-2200:])
        print(label,'ok',flush=True);return result.stdout
    run(argv,'compile-ios')
    libs=[toolchain/'nWiiRuntime/libnwiiruntime.a',toolchain/'_deps/sdl2-build/libSDL2.a',toolchain/'_deps/glad-build/libglad.a']
    common=[cxx,'-std=c++20','-O2','-g0','-static','-DNWII_WATCH_PAGE_MARKING','-I',base/'native-port/build/nwii-full-source/nWiiRuntime/include','-I',work]
    link=common+[work/'fixture.cpp',work/'ios.cpp.obj',*libs,*['-l'+s for s in 'm kernel32 user32 gdi32 winmm imm32 ole32 oleaut32 version uuid advapi32 setupapi shell32 dinput8 ws2_32 winspool comdlg32'.split()],'-o',work/'fixture.exe']
    run(link,'link')
    env=os.environ.copy()
    for key in list(env):
        if key.startswith('NWII_'):env.pop(key)
    cases=[]
    for name,extra in [('ordinary',{}),('no-throttle',{'NWII_NO_POLL_THROTTLE':'1'}),('fp-diagnostic',{'NWII_FP_CONTEXT_PROBE':'1'})]:
        case_env={**env,**extra}
        reference=run([work/'fixture.exe','legacy'],name+'-legacy',case_env)
        candidate=run([work/'fixture.exe','candidate'],name+'-candidate',case_env)
        a=[line for line in reference.splitlines() if line.startswith('ROW ')]
        b=[line for line in candidate.splitlines() if line.startswith('ROW ')]
        mismatch=next((i for i,(x,y) in enumerate(zip(a,b)) if x!=y),None)
        ok=len(a)==len(b)==8192 and mismatch is None
        tickets=next(line for line in candidate.splitlines() if line.startswith('TICKETS '))
        cases.append(dict(name=name,rows=len(a),passed=ok,first_mismatch=mismatch,tickets=tickets))
        print(cases[-1],flush=True)
        if not ok:raise RuntimeError('Differential poll contract mismatch: '+name+' at '+str(mismatch))
    result=dict(source_sha256=hashlib.sha256(source.encode()).hexdigest(),cases=cases,game_launched=False,
        limitations=['Matches the retained legacy entry in a source-bound transformed copy; no live-game speedup measured.',
          'The fixture injects callback-count races with an empty queue; actual guest callback and longjmp integration remain untested.'])
    (work/'results.json').write_text(json.dumps(result,indent=2))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base',type=Path,required=True);ap.add_argument('--source',type=Path,required=True);ap.add_argument('--work',type=Path,required=True)
    args=ap.parse_args();build_and_check(args.base,args.source,args.work)
