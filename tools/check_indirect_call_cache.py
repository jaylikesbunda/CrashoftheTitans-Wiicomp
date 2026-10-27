"""Differential equivalence of native_call_indirect and native_call_indirect_cached.

Extracts both bodies from build/crash-generated/main_output.cpp, compiles them side by side against
deterministic host stubs and drives them over the same inputs. The two must leave identical guest
state (pc, inst_count) and call the same number of process_pending_callbacks / native_wpad_dispatch /
interpret_step helpers; find_dispatch_target itself is the only thing the cache may skip. A dedicated
repeat loop then shows the cache really does skip lookups for a stable target (host-only counter).

usage: check_indirect_call_cache.py [--source PATH] [--keep]
"""
from pathlib import Path
from port_paths import default_storage
import argparse, re, subprocess

PORT = Path(__file__).resolve().parents[1]
MARK_BEGIN = '// NATIVE_INDIRECT_SITE_CACHE_V1 begin\n'
MARK_END = '// NATIVE_INDIRECT_SITE_CACHE_V1 end\n'


def grab_legacy(text):
    a = text.index('void native_call_indirect(CPUContext& ctx) {')
    b = text.index('\n}\n}', a) + 2  # keep the function's own closing brace, drop the namespace close
    return text[a:b]


def grab_block(text):
    a = text.index(MARK_BEGIN)
    inner = text[a + len(MARK_BEGIN):text.index(MARK_END, a)].rstrip('\n')
    prefix = 'namespace nwii::runtime {'
    assert inner.startswith(prefix), inner[:80]
    inner = inner[len(prefix):].rstrip()
    assert inner.endswith('}')
    return inner[:-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, default=PORT / 'build' / 'crash-generated' / 'main_output.cpp')
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--work',type=Path,default=default_storage()/'indirect-cache-tests')
    ap.add_argument('--header',type=Path,help='Production cpu_context.h for the real poll-credit primitive')
    args = ap.parse_args()
    text = args.source.read_text(errors='surrogateescape')
    text=re.sub(r'/\* NATIVE_V13_RUNTIME_V1 [A-Za-z0-9+/=]+ \*/\n|/\* END_NATIVE_V13_RUNTIME_V1 \*/\n?', '',text)
    if MARK_BEGIN not in text:
        raise SystemExit('native_call_indirect_cached is absent; run tools/fix_indirect_call_cache.py first')
    legacy = grab_legacy(text).replace('native_call_indirect(', 'Legacy(')
    block = grab_block(text).replace('native_call_indirect_cached', 'Candidate')
    fast_header=(PORT/'runtime-overlays/native_guest_fast.h').read_text(encoding='utf-8')
    inline_fast=''
    if 'struct NativeIndirectSite' not in block:
        structure=re.search(r'struct NativeIndirectSite \{[^\n]+',fast_header)[0]
        block=structure+'\n'+block
        inline_fast=fast_header[fast_header.index('__attribute__((always_inline)) inline void native_call_indirect_fast('):].rsplit('#endif',1)[0]
        inline_fast=inline_fast.replace('native_call_indirect_cached','Candidate').replace('native_guest_fast::scripted','scripted')
        block+='\ninline bool scripted=false;\n'+inline_fast
    assert 'struct NativeIndirectSite' in block and 'g_indirect_sites[' in block

    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    code = r'''#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <atomic>
namespace nwii::runtime {
struct CPUContext{bool is_running=true;uint32_t pc=0,lr=0;uint64_t inst_count=0;};
using NativeGuestFnPtr=void(*)(CPUContext&);
// Credit=1 exercises the exact original callback boundary, also used for IPC/DI delays.
int32_t g_native_poll_credit=1;int32_t& credit=g_native_poll_credit;
namespace native_idle {std::atomic<uint32_t> attention{0};}
auto& attention=native_idle::attention;
bool native_poll_due(){return --credit<=0||attention;}
unsigned calls=0,polls=0,dispatches=0,interprets=0;bool callback=false,wpad_enabled=true;
void process_pending_callbacks(CPUContext& c){++polls;if(callback)c.pc=0xFFFFFFFC;}
bool native_wpad_dispatch(CPUContext& c,uint32_t target){++calls;if(target!=0x803EC524 || !wpad_enabled)return false;c.pc=c.lr;return true;}
void leaf(CPUContext& c){c.inst_count+=7;c.pc=c.lr;}
struct D{void(*func)(CPUContext&);bool hle;};
D find_dispatch_target(uint32_t target){++dispatches;return target&1?D{nullptr,false}:D{leaf,(target&2)!=0};}
void interpret_step(CPUContext& c){++interprets;c.pc+=4;}
''' + legacy + '\n' + block + r'''
}
int main(){using namespace nwii::runtime;unsigned checks=0,failed=0;
uint32_t inputs[]={0,0xFFFFFFFC,0x80001234,0xC0001234,0xD0001234,0x1234,0x803EC524,0x803ED101,0x803EC523,0x803EEF10};
for(unsigned trial=0;trial<400000;++trial){CPUContext a{trial%31!=0,inputs[trial%10],0x80001500,trial},b=a;
 callback=trial%17==0;wpad_enabled=trial%7!=0;
 polls=dispatches=interprets=calls=0;Legacy(a);auto p0=polls,w0=calls,i0=interprets;
 polls=dispatches=interprets=calls=0;credit=1;Candidate(b,0);++checks;
 if(a.pc!=b.pc||a.inst_count!=b.inst_count||p0!=polls||w0!=calls||i0!=interprets){if(failed<8)printf("INDIRECT_CACHE_FAIL %u pc %08X/%08X ic %llu/%llu polls %u/%u wpad %u/%u interp %u/%u\n",trial,a.pc,b.pc,(unsigned long long)a.inst_count,(unsigned long long)b.inst_count,p0,polls,w0,calls,i0,interprets);++failed;}}
// Stable-target loop: the cache must collapse the resolvers without changing guest state.
uint64_t ld=0,cd=0;uint32_t pa=0,pb=0;uint64_t ia=0,ib=0;
for(unsigned i=0;i<100000;++i){CPUContext a{true,0x80001236,0x80001500,i},b=a;callback=false;wpad_enabled=true;
 polls=dispatches=interprets=calls=0;Legacy(a);ld+=dispatches;
 polls=dispatches=interprets=calls=0;credit=1;Candidate(b,0);cd+=dispatches;pa=a.pc;pb=b.pc;ia=a.inst_count;ib=b.inst_count;
 if(a.pc!=b.pc||a.inst_count!=b.inst_count)++failed;}
printf("INDIRECT_CACHE %u/%u dispatches legacy=%llu cached=%llu state %08X/%08X %llu/%llu\n",checks-failed,checks,(unsigned long long)ld,(unsigned long long)cd,pa,pb,(unsigned long long)ia,(unsigned long long)ib);
if(failed)return 1;if(!(cd<ld/10)){printf("INDIRECT_CACHE_NO_WIN\n");return 2;}
// Pending attention must interrupt immediately even with a full credit window.
CPUContext event{true,0x80001234,0x80001500,0};callback=true;credit=64;attention=true;polls=0;
Candidate(event,0);if(event.pc!=0xFFFFFFFC||polls!=1)return 3;
if(TEST_CREDIT_MODE){
  event={true,0x80001234,0x80001500,0};callback=false;credit=64;attention=false;polls=0;
  Candidate(event,0);if(event.pc!=event.lr||polls!=0||credit!=63)return 4;
  credit=1;Candidate(event,0);if(polls!=1)return 5;
}
}
'''
    credit_mode='INDIRECT_CREDIT' in text
    if inline_fast:
        # Exercise exactly the production inline fast path, including due polls,
        # HLE/normalization fallback, cache hits, callbacks and stopped contexts.
        code=code.replace('Candidate(b,0);','native_call_indirect_fast(b,0);')
        code=code.replace('Candidate(event,0);','native_call_indirect_fast(event,0);')
        code=code.replace('callback=trial%17==0;wpad_enabled=trial%7!=0;',
                          'callback=trial%17==0;wpad_enabled=trial%7!=0;scripted=trial%3==0;')
    code=code.replace('TEST_CREDIT_MODE','true' if credit_mode else 'false')
    if credit_mode:
        assert args.header,'Pass --header to test the production native_poll_due primitive'
        body=re.search(r'__attribute__\(\(always_inline\)\) inline bool native_poll_due\(\) \{.*?\n\}',args.header.read_text(encoding='utf-8'),re.S)
        assert body,'Production poll-credit primitive missing'
        code=code.replace('bool native_poll_due(){return --credit<=0||attention;}',body[0])
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
    (work / 'indirect-cache-tests.json').write_text(__import__('json').dumps(runs, indent=2) + '\n')
    raise SystemExit(r.returncode)


if __name__ == '__main__':
    main()
