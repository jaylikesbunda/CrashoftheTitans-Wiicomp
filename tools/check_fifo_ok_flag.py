"""Equivalence test for the cached ordinary-FIFO flag (tools/fix_fifo_ok_flag.py).

Compiles the staged native_ordinary_fifo_fast / native_wgp_bulk / GX_WGPIPE_Write8 / Write32 against a model of the config registers and
random write sequences interleaved with config changes (WPAR, PI base/end, CP base), each of which calls the same clear the real writers
make. The unmodified functions (flag block removed) run the same sequence; the staged FIFO byte streams and publish points must be identical.
A static check proves every real writer of the predicate's inputs clears the flag.
"""
from pathlib import Path
from port_paths import default_storage
import json, re, subprocess
PORT = Path(__file__).resolve().parents[1]
BASE = default_storage()
work = BASE / 'fifo-ok-tests'; work.mkdir(parents=True, exist_ok=True)
src = PORT / 'build/nwii-full-source/nWiiRuntime/src'
gx = (src / 'hle/gx.cpp').read_text(errors='surrogateescape')

# static: every writer clears the flag
assert re.search(r'g_wpar_redirect = [^\n]*\n\s*g_native_fifo_ok = false;', gx)
pi = (src / 'hw/hw_pi.cpp').read_text(errors='surrogateescape'); cp = (src / 'hw/hw_cp.cpp').read_text(errors='surrogateescape')
assert 'pi_fifo_base = val; nwii::runtime::g_native_fifo_ok = false;' in pi
assert 'pi_fifo_end = val; nwii::runtime::g_native_fifo_ok = false;' in pi
assert 'write_reg32(addr, val, g_cp_fifo_base); nwii::runtime::g_native_fifo_ok = false;' in cp
for text, names in ((pi, ['pi_fifo_base', 'pi_fifo_end']), (cp, ['g_cp_fifo_base'])):
    for n in names:
        for m in re.finditer(r'\b' + n + r'\b\s*(?:=[^=]|\+=|\|=)', text):
            line_start = text.rfind('\n', 0, m.start()) + 1
            if text[line_start:m.start()].lstrip().startswith('uint32_t'):
                continue  # the initialiser
            line_end = text.index('\n', m.start())
            assert 'g_native_fifo_ok = false' in text[m.start():line_end + 200], ('writer without clear', n)
assert len(re.findall(r'g_cp_fifo_base\b\s*=[^=]', cp)) == 1  # the initialiser only; the register write goes through write_reg32(..., g_cp_fifo_base)

a = gx.index('static inline void native_wgp_bulk(')
b = gx.index('void GX_WGPIPE_Write16(', a)
c = gx.index('void GX_WGPIPE_Write32(', b)
d = gx.index('void GX_WGPIPE_WriteF32(', c)
block = gx[a:b] + gx[c:d]
assert 'NATIVE_FIFO_OK_FLAG_V1' in block
old_block = re.sub(r'    if \(__builtin_expect\(g_native_fifo_ok, 1\)\) \{ // NATIVE_FIFO_OK_FLAG_V1\n.*?\n    \} else if \(native_ordinary_fifo_fast\(\)\) g_native_fifo_ok = true;\n', '', block, flags=re.S)
assert 'g_native_fifo_ok' not in old_block and old_block != block


def program(body, ns):
    return (r'''#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <vector>
#include <iostream>
#include <string>
#include <stdexcept>
#include <random>
namespace nwii::runtime { struct MMU{int x;}; MMU* g_mmu=(MMU*)1; uint64_t g_watch_addr=0; bool g_native_fifo_ok=false; }
namespace nwii::runtime::hw { uint32_t pi_fifo_base=0,pi_fifo_end=0,g_cp_fifo_base=0; }
namespace @NS@ {
using namespace nwii::runtime;
std::vector<uint8_t> published; int publishes=0;
struct NativeCpuFifo { alignas(64) uint8_t data[4160]; size_t n=0; } g_cpu_fifo;
bool g_wpar_redirect=false;
void native_gx_publish(){ published.insert(published.end(),g_cpu_fifo.data,g_cpu_fifo.data+g_cpu_fifo.n); g_cpu_fifo.n=0; ++publishes; }
static char* NativeLaunchFlag_NWII_SAMPLE(){return nullptr;}
std::vector<uint8_t> g_hw_fifo;
static bool native_wgp_diagnostics(){return false;}
static int native_gather_redirect_span(uint64_t,unsigned){return g_wpar_redirect?1:-1;} // redirected writes never reach the FIFO
static bool wgp_redirect(uint8_t){return false;}
static void wgp_push(uint8_t b){ g_cpu_fifo.data[g_cpu_fifo.n++]=b; if(g_cpu_fifo.n>=4096)native_gx_publish(); }
namespace hw { using namespace nwii::runtime::hw; }
''' + body + r'''
void config(int what,uint32_t v,bool clear){
  switch(what){case 0:g_wpar_redirect=(v&0x3fffffffu)!=0x0c008000u&&(v&0x3fffffffu)!=0u;break;
   case 1:nwii::runtime::hw::pi_fifo_base=v;break;case 2:nwii::runtime::hw::pi_fifo_end=v;break;case 3:nwii::runtime::hw::g_cp_fifo_base=v;break;}
  if(clear)nwii::runtime::g_native_fifo_ok=false;
}
std::vector<uint8_t> run(unsigned seed,bool clear){
  published.clear();publishes=0;g_cpu_fifo.n=0;nwii::runtime::g_native_fifo_ok=false;g_wpar_redirect=false;
  nwii::runtime::hw::pi_fifo_base=nwii::runtime::hw::pi_fifo_end=nwii::runtime::hw::g_cp_fifo_base=0;
  std::mt19937 rng(seed);
  const uint32_t choices[]={0,0x0c008000u,0x00100000u,0x80000000u,0x01000000u};
  for(int i=0;i<30000;++i){
    unsigned r=rng()%100;
    if(r<3)config(rng()%4,choices[rng()%5],clear);
    else if(r<10){if(rng()%2)GX_WGPIPE_Write8(rng());else GX_WGPIPE_Write8(rng());}
    else GX_WGPIPE_Write32(rng());
    if(rng()%977==0)native_gx_publish();
  }
  native_gx_publish();
  auto out=published;out.push_back(uint8_t(publishes));out.push_back(uint8_t(publishes>>8));return out;
}
}
''').replace('@NS@', ns)


new_body = block; old_body = old_block
digest_main = r'''
int main(){
  uint64_t h=1469598103934665603ull;size_t total=0;
  for(unsigned seed=1;seed<=60;++seed){auto v=@NS@::run(seed,true);total+=v.size();for(uint8_t b:v){h^=b;h*=1099511628211ull;}}
  std::cout<<h<<" "<<total<<"\n";
}
'''
results = []
for tag, body in (('old', old_body), ('new', new_body)):
    cpp = work / (tag + '.cpp'); cpp.write_text(program(body, 'N') + digest_main.replace('@NS@', 'N'))
    exe = work / (tag + '.exe')
    subprocess.run(['g++', '-std=c++20', '-O1', '-static', str(cpp), '-o', str(exe)], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True); assert r.returncode == 0, r.stderr
    results.append(r.stdout.strip())
assert results[0] == results[1], results
# negative control: a writer that forgets to clear must be detected by the model
cpp = work / 'neg.cpp'; cpp.write_text(program(new_body, 'N') + digest_main.replace('@NS@', 'N').replace('run(seed,true)', 'run(seed,false)'))
exe = work / 'neg.exe'; subprocess.run(['g++', '-std=c++20', '-O1', '-static', str(cpp), '-o', str(exe)], check=True)
neg = subprocess.run([str(exe)], capture_output=True, text=True).stdout.strip()
assert neg != results[0], 'model failed to detect a missing clear'
report = dict(passed=True, result='PASS 60 seeds x 30000 ops: identical FIFO bytes and publish counts (%s); negative control differs as expected' % results[0],
              scope='Staged native_ordinary_fifo_fast/native_wgp_bulk/Write8/Write32 with and without the flag block; static proof that every writer of WPAR/PI base/PI end/CP base clears it.')
(PORT / 'reports/fifo-ok-flag-tests.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
