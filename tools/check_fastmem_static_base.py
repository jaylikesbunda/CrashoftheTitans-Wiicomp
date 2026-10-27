"""NATIVE_FASTMEM_STATIC_BASE_V1 check (no game launch): prove the fixed-base fast path is real.

Three independent facts, all measured on this machine:
  1. PLACEMENT: with -DNWII_STATIC_RAM_BASE the MMU commits MEM1 at 0x20000000 and MEM2 at 0x30000000
     (VirtualAlloc at the fixed address succeeds), so mem1.data()/mem2.data() are compile-time constants.
  2. EQUIVALENCE: the same deterministic accessor workload compiled twice -- once with the fixed base and
     once with the dynamic NativeWatchedRam base -- observes exactly the same read values, the same
     watch/gather/0x0C behaviour and the same final MEM1/MEM2 image (identical FNV hash). Only the host
     address of RAM differs.
  3. DIRECT ADDRESSING: objdump shows the static accessor as a single disp32-addressed load
     (`movbe 0x20000000(%rax),%eax`) with no base-pointer load and no page_bias table, whereas the
     dynamic accessor loads the base pointer from the MMU first (`mov (%rcx),%rdx; movbe (%rdx,%rax,1),%eax`).

The .inc//test files and evidence go to a scratch build directory (never the repo). --include may point at
any staged nWiiRuntime/include; if that header predates tools/fix_mmu_flat_ram.py the flat-ram transform is
applied to a private copy so the check always measures the intended final state.
"""
from pathlib import Path
from port_paths import default_storage
import argparse, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, tempfile

PORT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument('--include', type=Path, default=PORT / 'build/nwii-full-source/nWiiRuntime/include')
ap.add_argument('--work', type=Path, default=default_storage()/'fastmem-static-tests')
ap.add_argument('--overlay', type=Path, default=PORT / 'runtime-overlays/static_ram_base.cpp')
args = ap.parse_args()

WORK = args.work.resolve()
INC = args.include.resolve()
WORK.mkdir(parents=True, exist_ok=True)
LOCAL_INC = WORK / 'include' / 'runtime'
LOCAL_INC.mkdir(parents=True, exist_ok=True)


def find_gxx():
    exe = shutil.which('g++')
    if exe:
        return exe
    guess = Path.home() / 'AppData/Local/Microsoft/WinGet/Packages/BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe/mingw64/bin/g++.exe'
    return str(guess) if guess.exists() else 'g++'


def find_objdump():
    exe = shutil.which('objdump')
    if exe:
        return exe
    guess = Path.home() / 'AppData/Local/Microsoft/WinGet/Packages/BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe/mingw64/bin/objdump.exe'
    return str(guess) if guess.exists() else 'objdump'


def stage_header():
    """Copy cpu_context.h into a private include dir and apply the flat-RAM transform if needed."""
    src = INC / 'runtime/cpu_context.h'
    text = src.read_text()
    if 'NATIVE_MMU_FLAT_RAM_V1' not in text:
        spec = importlib.util.spec_from_file_location('fix_mmu_flat_ram', PORT / 'tools/fix_mmu_flat_ram.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        text = mod.apply(text)
    (LOCAL_INC / 'cpu_context.h').write_text(text)
    return 'NATIVE_MMU_FLAT_RAM_V1' in text and 'NativeFixedRam' in text


STUBS = r'''
namespace nwii::runtime {
bool g_trace_calls=false; uint32_t g_watch_addr=0;
uint64_t g_observations=0;
void trace_call(uint32_t,CPUContext&){} void interpret_step(CPUContext&){}
void watch_hit(uint32_t,uint32_t,int){ ++g_observations; }
void GX_WGPIPE_Write8(uint8_t){ ++g_observations; } void GX_WGPIPE_Write16(uint16_t){ ++g_observations; }
void GX_WGPIPE_Write32(uint32_t){ ++g_observations; } void GX_WGPIPE_WriteF32(float){ ++g_observations; }
void GX_WGPIPE_WriteF64(double){ ++g_observations; }
uint16_t HW_Reg_Read16(uint32_t){return 0;} uint32_t HW_Reg_Read32(uint32_t){return 0;}
void HW_Reg_Write16(uint32_t,uint16_t){} void HW_Reg_Write32(uint32_t,uint32_t){}
}
'''

FIXTURE = r'''#include "runtime/cpu_context.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <random>
using namespace nwii::runtime;
extern "C" void run_game(CPUContext&){}
__attribute__((noinline)) uint32_t ProbeRead32(MMU& m, uint32_t a){ return m.read32(a); }
__attribute__((noinline)) void ProbeWrite32(MMU& m, uint32_t a, uint32_t v){ m.write32(a,v); }
static uint32_t pick(std::mt19937& r){
  switch(r()%8){
    case 0: case 1: return r()%0x01800000u;                          // MEM1
    case 2: return 0x10000000u + r()%0x04000000u;                    // MEM2
    case 3: return r()%0x10000u;                                     // page 0 (incl. 0x0C)
    case 4: return 0x01800000u + r()%(0x0C000000u-0x01800000u);      // hole
    case 5: return 0x0D010000u + r()%(0x10000000u-0x0D010000u);      // hole
    case 6: { static const uint32_t edges[]={0x017FFFF0,0x01800000,0x0000FFF0,0x00010000,0x13FFFFF0,0x14000000,0x0FFFFFF0,0x10000000,0x0000000C,0x00000008,0x0001FFFC};
              return edges[r()%12] + (r()%24) - 8; }
    default: return r()%0x40000000u;
  }
}
int main(){
  g_watch_addr=0;
  auto ctx=std::make_unique<CPUContext>(); MMU& m=ctx->mmu;
  std::printf("FASTMEM_PLACEMENT mem1=%llx mem2=%llx\n",
      (unsigned long long)(uintptr_t)m.mem1.data(),(unsigned long long)(uintptr_t)m.mem2.data());
  std::mt19937 rng(20261013);
  for(size_t i=0;i+4<=m.mem1.size();i+=4)*reinterpret_cast<uint32_t*>(m.mem1.data()+i)=rng();
  for(size_t i=0;i+4<=m.mem2.size();i+=4)*reinterpret_cast<uint32_t*>(m.mem2.data()+i)=rng();
  uint64_t hash=1469598103934665603ull;
  auto add=[&](uint64_t v){ hash=(hash^v)*1099511628211ull; };
  unsigned long reads=0,writes=0;
  for(unsigned n=0;n<600000;++n){
    const uint32_t base=pick(rng);
    const uint32_t mirror=((rng()&3)==0)?(rng()&1?0x80000000u:0xC0000000u):0u;
    const uint32_t addr=base|mirror;
    const uint64_t v=(uint64_t(rng())<<32)|rng();
    const unsigned op=rng()%7;
    if(op==0){ add(m.read8(addr)); ++reads; }
    else if(op==1){ add(m.read16(addr)); ++reads; }
    else if(op==2){ add(m.read32(addr)); ++reads; }
    else if(op==3){ m.write8(addr,uint8_t(v)); ++writes; }
    else if(op==4){ m.write16(addr,uint16_t(v)); ++writes; }
    else if(op==5){ m.write32(addr,uint32_t(v)); ++writes; }
    else { uint32_t u=uint32_t(v); float f; std::memcpy(&f,&u,4); m.write_f32(addr,f); ++writes; }
  }
  // page 0 / 0x0C special, the 0x0C008000 write-gather mirror, and the write watch
  add(m.read8(0x0Cu)); add(m.read32(0x0Cu)); m.write32(0x0Cu,0xdeadbeefu); add(m.read32(0x0Cu));
  add(m.read32(0xCC008000u)); m.write32(0xCC008000u,0x12345678u); add(g_observations);
  g_watch_addr=0x80123450u; m.write32(0x80123450u,1u); add(g_observations);
  m.write8(0x80123451u,2u); add(g_observations);            // same page, different offset
  m.write32(0x80100000u,3u); add(g_observations);           // different page
  g_watch_addr=0;
  for(size_t i=0;i<m.mem1.size();++i)add(m.mem1.data()[i]);
  for(size_t i=0;i<m.mem2.size();++i)add(m.mem2.data()[i]);
  std::printf("FASTMEM_STATE hash=%llu reads=%lu writes=%lu observations=%llu\n",
      (unsigned long long)hash,reads,writes,(unsigned long long)g_observations);
  return 0;
}
'''

GXX, OBJDUMP = find_gxx(), find_objdump()
flat_ok = stage_header()
(WORK / 'fixture.cpp').write_text('#include "runtime/cpu_context.h"\n' + STUBS + FIXTURE.split('\n',1)[1])

common = [GXX, '-std=c++20', '-O2', '-fwrapv', '-fno-strict-aliasing', '-ffp-contract=off',
          '-march=x86-64-v3', '-DNWII_WATCH_PAGE_MARKING', '-I', str(WORK / 'include'), '-I', str(INC)]
variant_src = [str(WORK / 'fixture.cpp')]
if flat_ok:
    variant_src.append(str(args.overlay.resolve()))

log = {}
outputs = {}
for name, define in (('static', ['-DNWII_STATIC_RAM_BASE']), ('dynamic', [])):
    exe = WORK / (name + '.exe')
    cmd = common + define + variant_src + ['-o', str(exe)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    log[name + '-compile'] = dict(cmd=cmd, rc=r.returncode, out=(r.stdout + r.stderr)[-4000:])
    if r.returncode:
        print(log[name + '-compile']['out'])
        (WORK / 'fastmem-static-base.json').write_text(json.dumps(log, indent=2) + '\n')
        print('FASTMEM_STATIC_BASE FAIL compile ' + name)
        raise SystemExit(1)
    x = subprocess.run([str(exe)], capture_output=True, text=True, timeout=600)
    log[name + '-run'] = dict(rc=x.returncode, out=x.stdout + x.stderr)
    outputs[name] = x.stdout

(WORK / 'fastmem-static-base.json').write_text(json.dumps(log, indent=2) + '\n')

state = {n: re.search(r'FASTMEM_STATE ([^\n]+)', o) for n, o in outputs.items()}
placement = {n: re.search(r'FASTMEM_PLACEMENT mem1=(\S+) mem2=(\S+)', o) for n, o in outputs.items()}
fail = 0
if not flat_ok:
    print('FASTMEM_STATIC_BASE FAIL: header has no NativeFixedRam block'); fail += 1
if not placement['static'] or placement['static'].group(1) != '20000000' or placement['static'].group(2) != '30000000':
    print('FASTMEM_STATIC_BASE FAIL placement static:', outputs['static'].strip()); fail += 1
if placement['dynamic'] and placement['dynamic'].group(1) == '20000000':
    print('FASTMEM_STATIC_BASE FAIL: dynamic build unexpectedly at the fixed base'); fail += 1
if not state['static'] or not state['dynamic'] or state['static'].group(1) != state['dynamic'].group(1):
    print('FASTMEM_STATIC_BASE FAIL equivalence:')
    print(' static ', state['static'].group(0) if state['static'] else None)
    print(' dynamic', state['dynamic'].group(0) if state['dynamic'] else None); fail += 1
if state['static'] and 'observations=0' in state['static'].group(0):
    print('FASTMEM_STATIC_BASE FAIL: watch/gather observer never fired'); fail += 1

# objdump evidence
def body(exe, sym):
    d = subprocess.run([OBJDUMP, '-d', '--no-show-raw-insn', str(exe)], capture_output=True, text=True, timeout=300).stdout
    lines = d.splitlines(); out = []; on = False
    for ln in lines:
        if re.search(r'<[^>]*' + sym + r'[^>]*>:', ln):
            on = True; continue
        if on:
            if re.search(r'<[^>]+>:', ln):
                break
            if ln.strip():
                out.append(ln.split('\t', 1)[-1].strip())
    return '\n'.join(out)

asm = {}
for n in ('static', 'dynamic'):
    asm[n] = body(WORK / (n + '.exe'), 'ProbeRead32')
log['objdump-Read32'] = asm
(WORK / 'fastmem-static-base.json').write_text(json.dumps(log, indent=2) + '\n')
obj_fail = 0
if '0x20000000(' not in asm['static'] or '0x30000000(' not in asm['static']:
    print('FASTMEM_STATIC_BASE FAIL: static accessor is not direct-addressed\n' + asm['static']); obj_fail += 1
if re.search(r'\(%r[a-z0-9]+\),%r', asm['dynamic']) is None:
    print('FASTMEM_STATIC_BASE FAIL: dynamic accessor has no base-pointer load\n' + asm['dynamic']); obj_fail += 1
if '(%rcx)' in asm['static']:
    print('FASTMEM_STATIC_BASE FAIL: static accessor still dereferences the MMU\n' + asm['static']); obj_fail += 1
fail += obj_fail

print('--- static ProbeRead32 ---\n' + asm['static'])
print('--- dynamic ProbeRead32 ---\n' + asm['dynamic'])
print('FASTMEM_STATIC_BASE placement_static=%s/%s equivalence_hash=%s direct_addressing=%s %s' % (
    placement['static'].group(1) if placement['static'] else '?', placement['static'].group(2) if placement['static'] else '?',
    state['static'].group(1) if state['static'] else '?', 'FAIL' if obj_fail else 'ok',
    'PASS' if not fail else 'FAIL'))
raise SystemExit(1 if fail else 0)
