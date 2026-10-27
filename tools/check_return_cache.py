"""Differential equivalence of the return helper against the inlined-dispatch reference.

native_return_cached is run_game's one-iteration body for ctx.pc with the pc==0 and
pc==0xFFFFFFFC cases deferred whole to run_game.  For every other pc it must behave exactly like
native_call_indirect (the already-accepted inlined iteration): same process_pending_callbacks count,
same native_wpad_dispatch count, same interpret_step count, same pc and inst_count.  For pc==0 and
pc==0xFFFFFFFC it must leave ctx completely untouched and call nothing, so the following run_game
iteration reproduces the loop's idle-sleep / backup-restore branch.

usage: check_return_cache.py [--source PATH]
"""
from pathlib import Path
from port_paths import default_storage
import argparse, json, re, subprocess

PORT = Path(__file__).resolve().parents[1]
RET_BEGIN = '// NATIVE_RETURN_SITE_CACHE_V1 begin\n'
RET_END = '// NATIVE_RETURN_SITE_CACHE_V1 end\n'


def grab_function(text, signature):
    a = text.index(signature)
    b = text.index('\n}\n}', a) + 2  # keep the function's own closing brace, drop the namespace close
    return text[a:b]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, default=PORT / 'build' / 'crash-generated' / 'main_output.cpp')
    args = ap.parse_args()
    text = args.source.read_text(errors='surrogateescape')
    if RET_BEGIN not in text:
        raise SystemExit('native_return_cached is absent; run tools/fix_indirect_call_cache.py first')
    legacy = grab_function(text, 'void native_call_indirect(CPUContext& ctx) {').replace('native_call_indirect(', 'Legacy(')
    a = text.index(RET_BEGIN)
    block = text[a + len(RET_BEGIN):text.index(RET_END, a)].rstrip('\n')
    prefix = 'namespace nwii::runtime {'
    assert block.startswith(prefix)
    block = block[len(prefix):].rstrip()
    assert block.endswith('}')
    block = block[:-1].replace('native_return_cached', 'Candidate')

    work = default_storage()/'return-cache-tests'
    work.mkdir(parents=True, exist_ok=True)
    code = r'''#include <cstdint>
#include <cstdlib>
#include <cstdio>
namespace nwii::runtime {
struct CPUContext{bool is_running=true;uint32_t pc=0,lr=0;uint64_t inst_count=0;};
unsigned calls=0,polls=0,dispatches=0,interprets=0;bool callback=false,wpad_enabled=true;
void process_pending_callbacks(CPUContext& c){++polls;if(callback)c.pc=0xFFFFFFFC;}
bool native_wpad_dispatch(CPUContext& c,uint32_t target){++calls;if(target!=0x803EC524 || !wpad_enabled)return false;c.pc=c.lr;return true;}
void leaf(CPUContext& c){c.inst_count+=7;c.pc=c.lr;}
struct D{void(*func)(CPUContext&);bool hle;};
D find_dispatch_target(uint32_t target){++dispatches;return target&1?D{nullptr,false}:D{leaf,(target&2)!=0};}
void interpret_step(CPUContext& c){++interprets;c.pc+=4;}
''' + legacy + '\n' + block + r'''
}
int main(){using namespace nwii::runtime;unsigned checks=0,failed=0,deferred=0;
uint32_t inputs[]={0,0xFFFFFFFC,0x80001234,0xC0001234,0xD0001234,0x1234,0x803EC524,0x803ED101,0x803EC523,0x803EEF10,0x80001236};
for(unsigned trial=0;trial<400000;++trial){CPUContext a{trial%31!=0,inputs[trial%11],0x80001500,trial},b=a;
 callback=trial%17==0;wpad_enabled=trial%7!=0;uint32_t pc0=a.pc;uint64_t ic0=a.inst_count;
 polls=dispatches=interprets=calls=0;Legacy(a);auto p0=polls,w0=calls,i0=interprets;
 polls=dispatches=interprets=calls=0;Candidate(b);++checks;
 if(pc0==0 || pc0==0xFFFFFFFC){
   // must be a no-op so run_game's own loop branch handles it
   if(b.pc!=pc0||b.inst_count!=ic0||polls||calls||interprets){if(failed<8)printf("RET_DEFER_FAIL %u pc %08X\n",trial,pc0);++failed;}
   ++deferred;
 } else if(a.pc!=b.pc||a.inst_count!=b.inst_count||p0!=polls||w0!=calls||i0!=interprets){
   if(failed<8)printf("RET_CACHE_FAIL %u pc %08X/%08X ic %llu/%llu polls %u/%u wpad %u/%u interp %u/%u\n",trial,a.pc,b.pc,(unsigned long long)a.inst_count,(unsigned long long)b.inst_count,p0,polls,w0,calls,i0,interprets);++failed;}}
// Stable return target: the cache must collapse the resolver and keep state identical.
uint64_t ld=0,cd=0;
for(unsigned i=0;i<100000;++i){CPUContext a{true,0x80001236,0x80001500,i},b=a;callback=false;wpad_enabled=true;
 polls=dispatches=interprets=calls=0;Legacy(a);ld+=dispatches;
 polls=dispatches=interprets=calls=0;Candidate(b);cd+=dispatches;
 if(a.pc!=b.pc||a.inst_count!=b.inst_count)++failed;}
printf("RETURN_CACHE %u/%u deferred=%u dispatches legacy=%llu cached=%llu\n",checks-failed,checks,deferred,(unsigned long long)ld,(unsigned long long)cd);
if(failed)return 1;if(!(cd<ld/10)){printf("RETURN_CACHE_NO_WIN\n");return 2;}}
'''
    cpp = work / 'fixture.cpp'
    exe = work / 'fixture.exe'
    cpp.write_text(code)
    runs = []
    for cmd in (['g++', '-std=c++20', '-O2', '-static', str(cpp), '-o', str(exe)], [str(exe)]):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        runs.append(dict(command=cmd, exit_code=r.returncode, output=r.stdout + r.stderr))
        print(r.stdout + r.stderr, flush=True)
        if r.returncode:
            break
    (PORT / 'reports/return-cache-tests.json').write_text(json.dumps(runs, indent=2) + '\n')
    raise SystemExit(r.returncode)


if __name__ == '__main__':
    main()
