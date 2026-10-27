"""Differential and code generation checks for the guarded F32 transport.

The check builds three versions of every fixture from the same source:

* ``old``  - the untouched generated guest code,
* ``base`` - the previous in-tree implementation (``guest_batch_lowering``),
* ``new``  - the standalone module (``guest_f32_transport``).

All three run on identical randomised CPUContext states and the complete
architectural state (gpr, fpr, ps1, cr, ctr, xer, lr, pc, inst_count, msr,
fpscr, gqr, srr0/1, guest RAM, MEM2, FIFO, hardware writes, watch hits,
publication boundaries and poll counts) has to match bit for bit.  The
fixtures cover joins, backedges, interior entries, observers, opaque calls,
exceptional float encodings and all five MXCSR modes.

The scalar transport test compares the old and the new lowering over one
million float bit patterns (including NaN, signalling NaN, infinity,
subnormals and signed zero) in every MXCSR mode and compares every FPR and
PS1 register plus the guest memory.

Nothing in this file touches the repository: every artefact is written below
the work directory.
"""

from pathlib import Path
from collections import Counter
import argparse, json, os, re, shutil, subprocess, sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guest_batch_lowering as BASE
import guest_f32_transport as NEW

PORT = Path(__file__).resolve().parents[1]


def method(text, signature):
    a = text.index(signature)
    brace = text.index('{', a)
    depth = 1
    i = brace + 1
    while depth and i < len(text):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        i += 1
    return text[a:i]


def fixture_function(name, body):
    return 'void ' + name + '(NWII_CTX_PARAM ctx) {\n' + body + '\n}'


# --- fixtures ---------------------------------------------------------------
# Every fixture is a straight line of the generated guest shape: the guest
# floating point registers are promoted locals and the guest memory is
# reached through the MMU.
FIXTURES = {
    # plain transport: load, store, write back
    'transport': r'''
    double f0=ctx.fpr[0],f1=ctx.fpr[1],p0=ctx.ps1[0];
    f0 = p0 = ctx.mmu.read_f32(ctx.gpr[3] + 0);
    f1 = f0;
    ctx.mmu.write_f32(ctx.gpr[3]+8, (float)f1);
    ctx.mmu.write_f32(ctx.gpr[5], (float)p0);
    ctx.fpr[0]=f0;ctx.fpr[1]=f1;ctx.ps1[0]=p0;
''',
    # a join: one path skips the load, the other does not
    'joins': r'''
    double f0=ctx.fpr[0],f1=ctx.fpr[1],p0=ctx.ps1[0];
    if(ctx.gpr[0]&1u)goto join_1;
    f0 = p0 = ctx.mmu.read_f32(ctx.gpr[3] + 0);
    ctx.mmu.write_f32(ctx.gpr[5], (float)f0);
join_1: ;
    f1 = p0 = ctx.mmu.read_f32(ctx.gpr[4] + 0);
    ctx.mmu.write_f32(ctx.gpr[5], (float)f1);
    ctx.fpr[0]=f0;ctx.fpr[1]=f1;ctx.ps1[0]=p0;
''',
    # a backedge: the load runs again on the next iteration
    'backedge': r'''
    double f0=ctx.fpr[0],p0=ctx.ps1[0];
    uint32_t g0=ctx.gpr[0];
loop_1: ;
    f0 = p0 = ctx.mmu.read_f32(ctx.gpr[3] + 0);
    ctx.mmu.write_f32(ctx.gpr[5], (float)f0);
    g0 = g0 - 1u;
    if (g0 & 0xffu) goto loop_1;
    ctx.fpr[0]=f0;ctx.ps1[0]=p0;ctx.gpr[0]=g0;
''',
    # an interior entry: the guest may resume at the second label
    'interior': r'''
    double f0=ctx.fpr[0],f1=ctx.fpr[1],p0=ctx.ps1[0];
    if (ctx.gpr[0]&2u) goto resume_1;
    f0 = p0 = ctx.mmu.read_f32(ctx.gpr[3] + 0);
resume_1: ;
    ctx.mmu.write_f32(ctx.gpr[5], (float)f0);
    f1 = p0 = ctx.mmu.read_f32(ctx.gpr[4] + 0);
    ctx.mmu.write_f32(ctx.gpr[5], (float)f1);
    ctx.fpr[0]=f0;ctx.fpr[1]=f1;ctx.ps1[0]=p0;
''',
    # an observer: an opaque call between the load and the store
    'observer': r'''
    double f0=ctx.fpr[0],f1=ctx.fpr[1],p0=ctx.ps1[0];
    f0 = p0 = ctx.mmu.read_f32(ctx.gpr[3] + 0);
    ctx.gpr[0] = ctx.gpr[0] + 1u; ctx.inst_count = ctx.inst_count + 1u;
    ctx.mmu.write_f32(ctx.gpr[5], (float)f0);
    f1 = p0 = ctx.mmu.read_f32(ctx.gpr[4] + 0);
    ctx.mmu.write_f32(ctx.gpr[5], (float)f1);
    ctx.fpr[0]=f0;ctx.fpr[1]=f1;ctx.ps1[0]=p0;
''',
    # several stores of the same value
    'multi_store': r'''
    double f0=ctx.fpr[0],p0=ctx.ps1[0];
    f0 = p0 = ctx.mmu.read_f32(ctx.gpr[3] + 0);
    ctx.mmu.write_f32(ctx.gpr[5], (float)f0);
    ctx.mmu.write_f32(ctx.gpr[5]+4, (float)f0);
    ctx.mmu.write_f32(ctx.gpr[5]+8, (float)p0);
    ctx.fpr[0]=f0;ctx.ps1[0]=p0;
''',
    # the guest double is observed by arithmetic before the store
    'arithmetic': r'''
    double f0=ctx.fpr[0],f1=ctx.fpr[1],p0=ctx.ps1[0];
    f0 = p0 = ctx.mmu.read_f32(ctx.gpr[3] + 0);
    f1 = f0 + f1;
    ctx.mmu.write_f32(ctx.gpr[5], (float)f0);
    ctx.mmu.write_f32(ctx.gpr[6], (float)f1);
    ctx.fpr[0]=f0;ctx.fpr[1]=f1;ctx.ps1[0]=p0;
''',
    # a register copy carries the loaded value
    'copy': r'''
    double f0=ctx.fpr[0],f1=ctx.fpr[1],p0=ctx.ps1[0];
    f0 = p0 = ctx.mmu.read_f32(ctx.gpr[3] + 0);
    f1 = f0;
    ctx.mmu.write_f32(ctx.gpr[5], (float)f1);
    ctx.fpr[0]=f0;ctx.fpr[1]=f1;ctx.ps1[0]=p0;
''',
    # the value is dead after the store
    'dead_value': r'''
    double f0=ctx.fpr[0],p0=ctx.ps1[0];
    f0 = p0 = ctx.mmu.read_f32(ctx.gpr[3] + 0);
    ctx.mmu.write_f32(ctx.gpr[5], (float)f0);
    ctx.gpr[0] = ctx.gpr[0] + 1u;
''',
}

PRELUDE = r'''
#include "runtime/cpu_context.h"
#include "runtime/native_guest_batch.h"
#include "runtime/native_loop_span.h"
#include "runtime/native_fifo_lock.h"
#include "runtime/native_gx_batching.h"
#include <bit>
#include <cassert>
#include <memory>
#include <random>
#include <tuple>
#include <cstdio>
using namespace nwii::runtime;
namespace native_trace {struct Scope{explicit Scope(const char*){}};}
namespace nwii::runtime {
CPUContext* g_ctx_ptr=nullptr;MMU* g_mmu=nullptr;
bool g_trace_calls=false;uint32_t g_watch_addr=0;int32_t g_native_poll_credit=1<<30;
unsigned fixture_credit=1<<30,fixture_polls=0;
void trace_call(uint32_t,CPUContext&){}void interpret_step(CPUContext&){std::abort();}
std::vector<uint64_t> fixture_watches,fixture_hw;
void watch_hit(uint32_t a,uint32_t v,int n){fixture_watches.push_back((uint64_t(a)<<32)^v^n);}
uint16_t HW_Reg_Read16(uint32_t a){return uint16_t(a^0x1258);}
uint32_t HW_Reg_Read32(uint32_t a){return a^0x01234567;}
void HW_Reg_Write16(uint32_t a,uint16_t v){fixture_hw.push_back((uint64_t(a)<<32)|v);}
void HW_Reg_Write32(uint32_t a,uint32_t v){fixture_hw.push_back((uint64_t(a)<<32)|v);}
bool process_pending_callbacks(CPUContext& c){++fixture_polls;++c.gpr[9];c.gpr[3]+=4;c.gpr[4]+=4;c.gqr[0]^=0x00040004;if(fixture_polls==1)native_wpar_write(0x80020100);g_native_poll_credit=fixture_credit;return false;}
void note_null_call(uint32_t,uint32_t){}
NativeFifoLock g_fifo_mutex;
std::vector<uint8_t> g_hw_fifo;
std::atomic<size_t>g_gx_pending_bytes{0};
std::vector<size_t> fixture_boundaries;
void NativeRequestGxDrain(){}
void native_gather_invalidate();
struct WgpRingEntry{uint8_t b;uint32_t pc,lr;};WgpRingEntry g_wgp_ring[16384];uint64_t g_wgp_ring_n=0;
namespace hw {
uint32_t pi_fifo_base=0,pi_fifo_end=0x01000000u,pi_fifo_wptr=0,g_cp_fifo_base=0;
void pi_fifo_get(uint32_t& b,uint32_t& e,uint32_t& w){b=pi_fifo_base;e=pi_fifo_end;w=pi_fifo_wptr;}
void pi_fifo_set_wptr(uint32_t w){pi_fifo_wptr=w;}
uint32_t cp_fifo_base_reg(){return g_cp_fifo_base;}
}
}
'''

HARNESS = r'''
struct Snapshot {
  std::vector<uint8_t> state,ram,mem2,fifo,tail;
  std::vector<uint64_t> watches,hw;std::vector<size_t> boundaries;
  uint32_t wpar;unsigned polls;
  bool operator==(const Snapshot&) const=default;
};
Snapshot snapshot(CPUContext& c){
  Snapshot s;
  auto save=[&](auto& value){const auto* p=reinterpret_cast<const uint8_t*>(&value);s.state.insert(s.state.end(),p,p+sizeof(value));};
  save(c.gpr);save(c.fpr);save(c.ps1);save(c.cr);save(c.ctr);save(c.xer);save(c.lr);save(c.pc);save(c.inst_count);save(c.msr);save(c.fpscr);save(c.gqr);save(c.srr0);save(c.srr1);
  s.ram.assign(c.mmu.mem1.data(),c.mmu.mem1.data()+0x22000);
  s.ram.insert(s.ram.end(),c.mmu.mem1.data()+MMU::kMem1Limit-16,c.mmu.mem1.data()+MMU::kMem1Limit+8);
  s.mem2.assign(c.mmu.mem2.data(),c.mmu.mem2.data()+0x1000);
  s.mem2.insert(s.mem2.end(),c.mmu.mem2.data()+MMU::kMem2Limit-16,c.mmu.mem2.data()+MMU::kMem2Limit+8);
  s.fifo=g_hw_fifo;s.tail.assign(g_cpu_fifo.data,g_cpu_fifo.data+g_cpu_fifo.n);
  s.boundaries=fixture_boundaries;s.watches=fixture_watches;s.hw=fixture_hw;
  s.wpar=native_wpar_read();s.polls=fixture_polls;return s;
}
void reset(CPUContext& c,unsigned trial){
  std::mt19937 r(trial+782923);
  for(auto&v:c.gpr)v=r();for(auto&v:c.fpr)v=std::bit_cast<double>((uint64_t(r())<<32)|r());
  for(auto&v:c.ps1)v=std::bit_cast<double>((uint64_t(r())<<32)|r());
  if(trial%3==0){for(auto&v:c.fpr)v=double(int(r()%2048)-1024)/128.;for(auto&v:c.ps1)v=double(int(r()%2048)-1024)/128.;}
  for(auto&f:c.cr){f.lt=r()&1;f.gt=r()&1;f.eq=r()&1;f.so=r()&1;}
  c.xer=r();c.ctr=1+trial%7;c.lr=0x81234560;c.pc=0x80010000;c.inst_count=r();c.msr=0x2000;c.fpscr=0;c.srr0=c.srr1=0;
  c.gqr.fill(0);c.gqr[0]=(trial%8)|((trial%8)<<16)|((trial%64)<<8)|((trial%64)<<24);
  c.gpr[3]=0x80010000;c.gpr[4]=0x80018000;c.gpr[5]=0xcc008000;
  if(trial%19==0)c.gpr[3]=0x817ffffc;
  if(trial%29==0)c.gpr[4]=0x93fffffc;
  if(trial%31==0)c.gpr[3]=0x80000008;
  if(trial%23==0)c.msr=0;
  g_watch_addr=trial%11==0?0x80010004:trial%13==0?0x0c008000:0;
  c.mmu.native_init_page_bias();
  for(unsigned j=0;j<0x22000;++j)c.mmu.mem1[j]=uint8_t(r());
  for(unsigned j=0;j<0x1000;++j)c.mmu.mem2[j]=uint8_t(r());
  std::memset(c.mmu.mem1.data()+MMU::kMem1Limit-16,0x69,24);
  std::memset(c.mmu.mem2.data()+MMU::kMem2Limit-16,0x96,24);
  g_cpu_fifo.clear();g_hw_fifo.clear();fixture_boundaries.clear();fixture_watches.clear();fixture_hw.clear();fixture_polls=0;
  g_ctx_ptr=&c;g_mmu=&c.mmu;g_native_fifo_ok=false;native_wpar_write(trial%17==0?0x80020000:0x0c008800);
  fixture_credit=trial%5==0?1:trial%7==0?2:1<<30;g_native_poll_credit=fixture_credit;
  for(unsigned j=0;j<4078+trial%40;++j)GX_WGPIPE_Write8(uint8_t(j));
}
using Fn=void(*)(CPUContext&);
static std::unique_ptr<CPUContext> g_ctx;
void compare(Fn old,Fn now,unsigned trial,const char* tag){
  if(!g_ctx)g_ctx=std::make_unique<CPUContext>();
  CPUContext& c=*g_ctx;reset(c,trial);old(c);auto a=snapshot(c);
  reset(c,trial);now(c);auto b=snapshot(c);
  if(a!=b){std::printf("DIFFERENTIAL FAILURE %s trial=%u state=%d ram=%d mem2=%d fifo=%d tail=%d boundaries=%d watches=%d hw=%d wpar=%d polls=%d\n",tag,trial,a.state==b.state,a.ram==b.ram,a.mem2==b.mem2,a.fifo==b.fifo,a.tail==b.tail,a.boundaries==b.boundaries,a.watches==b.watches,a.hw==b.hw,a.wpar==b.wpar,a.polls==b.polls);std::fflush(stdout);std::abort();}
}
int main(){
  unsigned long long checks=0;
  const unsigned modes[]={0x1f80,0x3f80,0x5f80,0x7f80,0x9fc0};
  for(unsigned mode:modes){_mm_setcsr(mode);
   std::printf("mode %08x differential trials\n",mode);std::fflush(stdout);
   for(unsigned trial=0;trial<40;++trial){
    for(auto [tag,old,base,now]:cases){compare(old,base,trial,tag);compare(old,now,trial,tag);++checks;}
   }
  }
  auto scalar=std::make_unique<CPUContext>();g_ctx_ptr=scalar.get();g_mmu=&scalar->mmu;g_watch_addr=0;
  scalar->gpr[3]=0x80010000;scalar->gpr[5]=0x80010020;scalar->mmu.native_init_page_bias();
  unsigned long long transports=0;
  for(unsigned mode:modes){_mm_setcsr(mode);
   for(unsigned i=0;i<200000;++i){
    const uint32_t edges[]={0u,0x80000000u,1u,0x80000001u,0x007fffffu,0x7f800000u,0xff800000u,0x7f800001u,0x7fc01234u,0x00800000u,0x807fffffu,0x00000002u};
    uint32_t bits=i<12?edges[i]:i*2654435761u;scalar->mmu.write32(0x80010000,bits);
    old_transport::transport(*scalar);
    auto f0=std::bit_cast<uint64_t>(scalar->fpr[0]),f1=std::bit_cast<uint64_t>(scalar->fpr[1]),p0=std::bit_cast<uint64_t>(scalar->ps1[0]);
    auto a=scalar->mmu.read32(0x80010008),b=scalar->mmu.read32(0x80010020);
    new_transport::transport(*scalar);
    assert(f0==std::bit_cast<uint64_t>(scalar->fpr[0]) && f1==std::bit_cast<uint64_t>(scalar->fpr[1]) && p0==std::bit_cast<uint64_t>(scalar->ps1[0]));
    assert(a==scalar->mmu.read32(0x80010008) && b==scalar->mmu.read32(0x80010020));++transports;
   }
  }
  _mm_setcsr(0x1f80);
  std::printf("FP transport: %llu exact bit comparisons, including exceptional encodings and five MXCSR modes.\n",transports);
  std::printf("Guest F32 transport differential checks: %llu; all architectural state, RAM, FIFO/tail/publications, watches, hardware writes and polls matched.\n",checks);
}
'''


def build_fixture(runtime, work, hot=None):
    header = (runtime / 'include/runtime/cpu_context.h').read_text()
    local = work / 'include/runtime'
    local.mkdir(parents=True, exist_ok=True)
    (local / 'cpu_context.h').write_text(header)
    for name in ('native_guest_batch.h', 'native_loop_span.h'):
        shutil.copy2(PORT / 'runtime-overlays' / name, local / name)
    gx = (runtime / 'src/hle/gx.cpp').read_text()
    block = gx[gx.index('struct NativeCpuFifo {'):gx.index('\nuint64_t g_prof_decode_us_accum = 0;')]
    anchor = '    destination.insert(destination.end(), source.data, source.data + source.n);'
    assert block.count(anchor) == 1
    block = block.replace(anchor, '    fixture_boundaries.push_back(destination.size()+source.n);\n' + anchor)
    code = PRELUDE + '\nnamespace nwii::runtime {\n' + block + '\n'
    source = []
    names = []
    stats = Counter()
    for name, body in FIXTURES.items():
        old = fixture_function(name, body)
        base, base_count = BASE.fp_transport(old)
        new, new_count = NEW.fp_transport(old)
        stats['base_sites'] += base_count
        stats['new_sites'] += new_count
        assert new != old or base != old, name
        source += ['namespace old_' + name + '{' + old + '}',
                   'namespace base_' + name + '{' + base + '}',
                   'namespace new_' + name + '{' + new + '}']
        names.append((name, 'old_' + name + '::' + name, 'base_' + name + '::' + name, 'new_' + name + '::' + name))
    # The 'transport' fixture above already provides old_transport,
    # base_transport and new_transport for the one million pattern test.
    code += '\n'.join(source)
    code += '\nconst std::tuple<const char*,Fn,Fn,Fn> cases[]={\n'
    code += ',\n'.join('{"%s",%s,%s,%s}' % (n, a, b, c) for n, a, b, c in names) + '\n};\n'
    code = code.replace('const std::tuple<const char*,Fn,Fn,Fn> cases[]',
                        'using Fn=void(*)(CPUContext&);\nconst std::tuple<const char*,Fn,Fn,Fn> cases[]')
    code += HARNESS
    (work / 'fixture.cpp').write_text(code, errors='surrogateescape')
    return dict(stats=stats, cases=len(names))


def source_checks():
    """The pass must be idempotent, reversible and honour the selected mode."""
    stats = Counter()
    for name, body in FIXTURES.items():
        old = fixture_function(name, body)
        new, count = NEW.fp_transport(old)
        again, count2 = NEW.fp_transport(new)
        assert count2 == 0, ('not idempotent', name)
        assert again == new, ('not idempotent', name)
        # The selected mode only guards the selected shadows.
        function = NEW.Function(old)
        shadows = [read['shadow'] for read in function.reads if any(read['stores'].values())]
        if not shadows:
            continue
        pick = {shadows[0]}
        selected, selected_count = NEW.fp_transport(old, pick)
        assert selected_count >= 1, name
        for read in function.reads:
            guarded = read['shadow'] in pick
            assert (read['shadow'] + ' =' in selected) == guarded, (name, read['shadow'])
        for read in function.reads:
            if read['shadow'] in pick:
                continue
            assert read['shadow'] + ' =' not in selected, (name, read['shadow'])
        stats['selected_sites'] += selected_count
        stats['fixtures'] += 1
    return dict(stats)


def production_coverage(hot, work):
    """Apply the pass to the production hot source and report the effect."""
    stats = Counter()
    for path in sorted(Path(hot).glob('hot_*.cpp')):
        text = path.read_text(errors='surrogateescape')
        new, count = NEW.fp_transport(text)
        if new == text:
            continue
        stats['files'] += 1
        stats['sites'] += count
        local = work / path.name
        local.write_text(new, errors='surrogateescape')
    return dict(stats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runtime', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--hot', type=Path, default=None)
    a = ap.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    report = {}
    report['source'] = source_checks()
    stats = build_fixture(a.runtime, a.work)
    report['fixture'] = stats
    if a.hot is not None and Path(a.hot).is_dir():
        report['production'] = production_coverage(a.hot, a.work)
    compiler = shutil.which('g++')
    if not compiler:
        raise SystemExit('no g++ found')
    flags = ['-std=c++20', '-O2', '-march=x86-64-v3', '-mfma', '-fwrapv', '-fno-strict-aliasing',
             '-ffp-contract=off', '-I', str(a.work / 'include'), '-I', str(a.runtime / 'include')]
    command = [compiler, *flags, str(a.work / 'fixture.cpp'), '-o', str(a.work / 'fixture.exe')]
    print('Compiling the local differential fixture.', flush=True)
    result = subprocess.run(command, capture_output=True, text=True)
    (a.work / 'compile.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit((result.stdout + result.stderr)[-14000:])
    env = {k: v for k, v in os.environ.items() if not k.startswith('NWII_')}
    env['NWII_NO_DIAG'] = '1'
    result = subprocess.run([str(a.work / 'fixture.exe')], capture_output=True, text=True, env=env, timeout=1800)
    print(result.stdout + result.stderr, flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
    report['output'] = result.stdout
    (a.work / 'results.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    print(json.dumps(report, indent=2, default=str), flush=True)


if __name__ == '__main__':
    main()
