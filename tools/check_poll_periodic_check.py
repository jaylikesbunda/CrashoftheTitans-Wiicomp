"""Differential test of the poll heartbeat countdown against the original modulo.

The staged predicate must be exactly `(inst_count % 20000000) != 0` for every monotonic
poll sequence: the fast path is taken on the same counts, and the slow-path heartbeat
fires on the same counts. The fixture extracts the staged helper text, renames its file
scope state so a sequence can be restarted, and compares it against the literal modulo
over a dense range, over skip patterns that jump past several periods at once, and over
randomised monotonic sequences.
"""
from pathlib import Path
from port_paths import default_storage
import json
import re
import subprocess

PORT = Path(__file__).resolve().parents[1]
WORK = default_storage()/'poll-periodic-tests'
WORK.mkdir(parents=True, exist_ok=True)
PERIOD = 20000000

source = (PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp').read_text()
if 'NATIVE_POLL_PERIODIC_COUNTDOWN_V1' not in source:
    raise SystemExit('staged ios.cpp does not carry NATIVE_POLL_PERIODIC_COUNTDOWN_V1')
a = source.index('static uint64_t native_poll_heartbeat_next')
b = source.index('\n}\n', a) + 3
helper = source[a:b]
assert 'native_poll_not_periodic' in helper, helper[:200]
helper = helper.replace('native_poll_heartbeat_next', 'staged_next')
helper = helper.replace('static __attribute__((always_inline)) inline bool', 'bool')

main = r'''
static unsigned checks=0,failed=0;
static void check(bool ok,uint64_t count,unsigned seq){++checks;if(!ok){if(++failed<20)std::printf("FAIL seq=%u count=%llu\n",seq,(unsigned long long)count);}}
// Reference: the literal predicate the countdown replaces.
static bool reference(uint64_t count){return (count % 20000000ull)!=0;}
static void run(uint64_t start,uint64_t step,uint64_t steps,unsigned seq){
  staged_next=0ull;
  uint64_t count=start;
  for(uint64_t i=0;i<steps;++i){
    check(staged::native_poll_not_periodic(count)==reference(count),count,seq);
    count+=step;
  }
}
int main(){
  // Dense: every count from 0 to two periods, then every count around each multiple.
  run(0,1,40000001,0);
  for(uint64_t base=0;base<400000000ull;base+=20000000ull)run(base?base-8:0,1,base?17:25,1);
  // Steps that skip whole periods, including landing exactly on a multiple.
  for(uint64_t step:{2ull,3ull,7ull,997ull,19999999ull,20000000ull,20000001ull,39999999ull,40000000ull})
    run(1,step,200000,2);
  // Randomised monotonic sequences with occasional large jumps.
  uint64_t rng=88172645463325252ull;
  auto next=[&](){rng^=rng<<13;rng^=rng>>7;rng^=rng<<17;return rng;};
  for(unsigned seq=0;seq<64;++seq){
    staged_next=0ull;uint64_t count=0;
    for(unsigned i=0;i<20000;++i){
      check(staged::native_poll_not_periodic(count)==reference(count),count,3);
      count+=(next()%3u)?1u:(next()%20000000ull);
    }
  }
  // Extremes: near 2^63, and the largest count the guest can reach in practice.
  staged_next=0ull;
  for(uint64_t count:{0ull,1ull,19999999ull,20000000ull,20000001ull,39999999ull,40000000ull,
                      0x7fffffffffffffffull-1ull,0x7fffffffffffffffull})
    check(staged::native_poll_not_periodic(count)==reference(count),count,4);
  std::printf("POLL_PERIODIC_STATE checks=%u failed=%u\n",checks,failed);
  return failed?1:0;
}
'''
cpp = WORK / 'test.cpp'
exe = WORK / 'test.exe'
cpp.write_text('#include <cstdio>\n#include <cstdint>\n#include <initializer_list>\nnamespace staged {\n' + helper + '\n}\nusing staged::staged_next;\n' + main)
rows = []
for cmd in (['g++', '-std=c++20', '-O2', '-static', str(cpp), '-o', str(exe)], [str(exe)]):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    rows.append(dict(command=cmd, exit_code=r.returncode, output=r.stdout + r.stderr))
    (PORT / 'reports/poll-periodic-tests.json').write_text(json.dumps(rows, indent=2) + '\n')
    print((r.stdout + r.stderr)[-2000:], flush=True)
    if r.returncode:
        raise SystemExit(r.returncode)
assert re.search(r'POLL_PERIODIC_STATE checks=\d+ failed=0', r.stdout), r.stdout[-400:]
print('Poll heartbeat countdown matches the original modulo predicate on every tested sequence.')
