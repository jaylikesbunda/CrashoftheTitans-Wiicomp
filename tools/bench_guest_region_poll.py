"""Price the tested poll contract with eight live guest GPRs (not game CPI).

Requires the external real-runtime fixture built by check_guest_region_poll.py.
Both variants use identical arithmetic, compiler flags and original slow paths.
The API variant publishes/reloads only when its ticket requires it. The baseline
publishes/reloads on every poll, matching the existing opaque-context contract.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess

SOURCE=r'''
#include "runtime/cpu_context.h"
#include "native_region_poll.h"
#include <memory>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <x86intrin.h>
#include <windows.h>
using namespace nwii::runtime;
namespace nwii::runtime {MMU* g_mmu=nullptr;CPUContext* g_ctx_ptr=nullptr;}
extern "C" void run_game(CPUContext& c){c.pc=c.lr;}
extern "C" void RegionPollTestInputs(unsigned,unsigned,unsigned);
extern "C" uint64_t RegionPollTestPhases();

BENCHMARK_FUNCTIONS

int main(int argc,char** argv){
 if(argc!=2)return 1;
 auto c=std::make_unique<CPUContext>();g_mmu=&c->mmu;g_ctx_ptr=c.get();
 c->pc=0x80300000;c->lr=0x80200000;c->msr=0xa032;c->in_callback=false;
 c->dispatch_saved_ctx=0;c->pending_cb_count=0;c->vblank_pending=false;
 c->dec_irq_pending=false;c->dec_value=0x7fffffff;c->dec_written_tb=0;
 c->inst_count=1;for(unsigned i=0;i<32;++i)c->gpr[i]=i+1;
 RegionPollTestInputs(0,0,0);
 auto target=argv[1][0]=='l'?Legacy:Region;
 target(*c,10000);
 ULONG64 before=0,after=0;QueryThreadCycleTime(GetCurrentThread(),&before);
 unsigned aux;_mm_lfence();auto t0=__rdtscp(&aux);_mm_lfence();
 const auto checksum=target(*c,1000000);
 _mm_lfence();auto t1=__rdtscp(&aux);_mm_lfence();QueryThreadCycleTime(GetCurrentThread(),&after);
 std::printf("BENCH %llu %llu %llu %llu\n",(unsigned long long)(after-before),(unsigned long long)(t1-t0),(unsigned long long)checksum,(unsigned long long)RegionPollTestPhases());
}
'''

LOOP=r'''
__attribute__((noinline)) static uint64_t NAME(CPUContext& ctx,unsigned count){
 uint32_t a=ctx.gpr[3],b=ctx.gpr[4],c=ctx.gpr[5],d=ctx.gpr[6];
 uint32_t e=ctx.gpr[7],f=ctx.gpr[8],g=ctx.gpr[9],h=ctx.gpr[10];
 uint64_t ic=ctx.inst_count;
 for(unsigned i=0;i<count;++i){
  a+=b;c^=d;e+=f;g^=h;b+=e;d+=g;f^=a;h+=c;ic+=37;
  POLL
 }
 PUBLISH
 return (uint64_t(a)<<32)^b^c^d^e^f^g^h^ic;
}
'''
PUBLISH='ctx.gpr[3]=a;ctx.gpr[4]=b;ctx.gpr[5]=c;ctx.gpr[6]=d;ctx.gpr[7]=e;ctx.gpr[8]=f;ctx.gpr[9]=g;ctx.gpr[10]=h;ctx.inst_count=ic;ctx.pc=0x80300000;ctx.lr=0x80200000;'
RELOAD='a=ctx.gpr[3];b=ctx.gpr[4];c=ctx.gpr[5];d=ctx.gpr[6];e=ctx.gpr[7];f=ctx.gpr[8];g=ctx.gpr[9];h=ctx.gpr[10];ic=ctx.inst_count;'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base',type=Path,required=True);ap.add_argument('--fixture',type=Path,required=True);ap.add_argument('--work',type=Path,required=True)
    args=ap.parse_args();base=args.base.resolve();work=args.work.resolve();port=Path(__file__).resolve().parents[1]
    if work==port or port in work.parents:raise ValueError('Output must be outside repo')
    work.mkdir(parents=True,exist_ok=True)
    legacy=LOOP.replace('NAME','Legacy').replace('POLL',PUBLISH+'process_pending_callbacks(ctx);'+RELOAD).replace('PUBLISH',PUBLISH)
    region=LOOP.replace('NAME','Region').replace('POLL','auto ticket=native_region_poll::Prepare(ctx,0x80300000,ic);if(ticket!=native_region_poll::Ticket::Complete){'+PUBLISH+'native_region_poll::Finish(ctx,ticket);'+RELOAD+'}').replace('PUBLISH',PUBLISH)
    cpp=SOURCE.replace('BENCHMARK_FUNCTIONS',legacy+'\n'+region)
    (work/'bench.cpp').write_text(cpp)
    cxx=shutil.which('g++');tc=base/'storage/toolchain'
    libs=[tc/'nWiiRuntime/libnwiiruntime.a',tc/'_deps/sdl2-build/libSDL2.a',tc/'_deps/glad-build/libglad.a']
    command=[cxx,'-std=c++20','-O2','-g0','-fwrapv','-fno-strict-aliasing','-ffp-contract=off','-march=x86-64-v3','-static','-DNWII_WATCH_PAGE_MARKING','-I',base/'native-port/build/nwii-full-source/nWiiRuntime/include','-I',args.fixture,
        work/'bench.cpp',args.fixture/'ios.cpp.obj',*libs,*['-l'+s for s in 'm kernel32 user32 gdi32 winmm imm32 ole32 oleaut32 version uuid advapi32 setupapi shell32 dinput8 ws2_32 winspool comdlg32'.split()],'-o',work/'bench.exe']
    result=subprocess.run([str(v) for v in command],capture_output=True,text=True)
    (work/'compile.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError((result.stdout+result.stderr)[-2400:])
    env={k:v for k,v in os.environ.items() if not k.startswith('NWII_')}
    rows=[]
    for repeat in range(7):
        for mode in (('legacy','region') if repeat%2==0 else ('region','legacy')):
            result=subprocess.run([str(work/'bench.exe'),mode],env=env,capture_output=True,text=True,timeout=60)
            (work/f'{repeat}-{mode}.log').write_text(result.stdout+result.stderr)
            if result.returncode:raise RuntimeError('Benchmark failed')
            line=next(line for line in result.stdout.splitlines() if line.startswith('BENCH '))
            cycles,tsc,checksum,phases=map(int,line.split()[1:])
            row=dict(repeat=repeat,mode=mode,cycles_per_iteration=cycles/1000000,tsc_per_iteration=tsc/1000000,checksum=checksum,phases=phases)
            rows.append(row);print(row,flush=True)
    if len({(row['checksum'],row['phases']) for row in rows})!=1:raise RuntimeError('Variant state or phase mismatch')
    medians={mode:statistics.median(r['cycles_per_iteration'] for r in rows if r['mode']==mode) for mode in ('legacy','region')}
    result=dict(rows=rows,median_cycles_per_iteration=medians,ratio=medians['region']/medians['legacy'],
        executable_sha256=hashlib.file_digest((work/'bench.exe').open('rb'),'sha256').hexdigest(),
        fixture_object_sha256=hashlib.file_digest((args.fixture/'ios.cpp.obj').open('rb'),'sha256').hexdigest(),
        source_sha256=hashlib.sha256(cpp.encode()).hexdigest(),compile_command=[str(v) for v in command],
        limitations=['Eight-live-GPR synthetic mechanism benchmark, not guest instructions/frame or measured game performance.',
          'Uses the real runtime fixture object with producer-injection hooks inactive. Actual guest callback/longjmp integration remains untested.'])
    (work/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))


if __name__=='__main__':main()
