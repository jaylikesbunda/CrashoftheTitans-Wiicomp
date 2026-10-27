"""Offline differential for poll-site continuations (tools/guest_poll_continuations.py). No game, no staging.

For each selected real generated function this builds three variants of the same guest code:
  old   the generated function as hot-split receives it (all interior entries, unpromoted)
  base  guest_entry_specialize output with the current LR-only continuation rule
  new   guest_entry_specialize output with NWII_BUILD_POLL_CONTINUATIONS=1
The rule is selected per variant with NWII_BUILD_POLL_CONTINUATIONS (guest_entry_specialize's hook).

Each trial runs a small dispatcher: enter at a PC, and while the function returns with ctx.pc on one of its
own interior entries (an interrupted poll, or a callee that did not return normally and is then resumed at
its LR), enter again at that PC. Stubbed polls deliver an "interrupt" at a trial-chosen poll count; stubbed
callees return normally, or are interrupted and resumed at LR. All architectural state, the touched RAM,
the callee trace and the poll count must match bit for bit, under five MXCSR modes.

The same functions are compiled with the production hot-unit flags to report static code size per variant.
"""
from pathlib import Path
import argparse, json, os, re, shutil, subprocess, sys, types
from port_paths import winlibs_bin
import promote_regs as P

PORT = Path(__file__).resolve().parents[1]
BUILD = Path.home() / '.codex/native-port-builds/clean-room-guest-batch-20261019-2'
def hooked_specializer():
    """guest_entry_specialize with its NATIVE_POLL_CONTINUATIONS_V1 hook; NWII_BUILD_POLL_CONTINUATIONS picks the rule."""
    import guest_entry_specialize
    assert 'NATIVE_POLL_CONTINUATIONS_V1' in Path(guest_entry_specialize.__file__).read_text(), 'hook missing'
    return guest_entry_specialize


def generated_function(generated, address):
    for f in sorted(generated.glob('output_*.cpp')):
        t = f.read_text(errors='surrogateescape')
        m = re.search(r'^(?:NWII_GUEST_ABI )?void func_' + address + r'\(NWII_CTX_PARAM ctx\) \{$', t, re.M)
        if m:
            end = t.find('\n}\n', m.start()) + 2
            s = t[m.start():end].replace('NWII_GUEST_ABI void', 'void', 1)
            # The hot-split input predates the census switch counter.
            return re.sub(r'\n\s*native_perf_census::GuestSwitch\([^\n]*', '', s, count=1)
    raise SystemExit('missing func_' + address)


def variants(source, spec):
    name = P.parse(source)[0]
    out = {}
    for tag, flag in (('base', '0'), ('new', '1')):
        os.environ['NWII_BUILD_POLL_CONTINUATIONS'] = flag
        text, stats = spec.specialize(source, None, continuation_allowlist={name})
        assert text is not None and stats.get('entry_specialized'), (name, tag, stats)
        out[tag] = (text, stats)
    os.environ.pop('NWII_BUILD_POLL_CONTINUATIONS', None)
    return out


PRELUDE = r'''
#include "runtime/native_guest_batch.h"
#include "runtime/native_loop_span.h"
#include "runtime/native_fastmem.h"
#define NWII_V13_INLINE_INDIRECT 1
#include "runtime/native_guest_fast.h"
#include "runtime/native_perf_census.h"
#include "runtime/cpu_context.h"
#include "runtime/native_guest_entry.h"
#include "runtime/native_linked_region.h"
#include <bit>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <iostream>
#include <memory>
#include <random>
#include <vector>
#include <windows.h>
#include <immintrin.h>
using namespace nwii::runtime;
static inline int32_t fx_cvt(double v){return _mm_cvttsd_si32(_mm_set_sd(v));}
namespace native_trace {struct Scope{explicit Scope(const char*){}};}
namespace nwii::runtime {
CPUContext* g_ctx_ptr=nullptr;MMU* g_mmu=nullptr;
bool g_trace_calls=false;uint32_t g_watch_addr=0;int32_t g_native_poll_credit=1;bool g_native_fifo_ok=false;
void native_static_ram_commit(uintptr_t base,size_t bytes){
 // Every run constructs a fresh CPUContext; the fixed banks are committed once.
 // Write-watched, so every run restores and compares exactly the pages any variant wrote, in both banks.
 static uintptr_t done[2]{};for(auto d:done)if(d==base)return;(done[0]?done[1]:done[0])=base;
 if(!VirtualAlloc(reinterpret_cast<void*>(base),bytes,MEM_RESERVE|MEM_COMMIT|MEM_WRITE_WATCH,PAGE_READWRITE))std::abort();}
struct FxBank{uintptr_t base;size_t bytes;};
const FxBank fx_banks[2]={{0x20000000u,24u*1024u*1024u+8u},{0x30000000u,64u*1024u*1024u+8u}};
std::vector<uintptr_t> fx_dirty(const FxBank& b){
 std::vector<void*> pages(b.bytes/4096+2);ULONG_PTR count=pages.size();ULONG gran=0;
 if(GetWriteWatch(0,reinterpret_cast<void*>(b.base),b.bytes,pages.data(),&count,&gran))std::abort();
 std::vector<uintptr_t> out;for(ULONG_PTR i=0;i<count;++i)out.push_back(reinterpret_cast<uintptr_t>(pages[i]));return out;}
void trace_call(uint32_t,CPUContext&){}
void interpret_step(CPUContext& c){c.pc=0xDEAD0000u;}
void note_null_call(uint32_t,uint32_t){}
std::vector<uint64_t> fx_trace;
// FIFO, MMIO and watch effects are recorded in order, so the comparison sees them too.
void GX_WGPIPE_Write8(uint8_t v){fx_trace.push_back(0x1100000000000000ull|v);}
void GX_WGPIPE_Write16(uint16_t v){fx_trace.push_back(0x1200000000000000ull|v);}
void GX_WGPIPE_Write32(uint32_t v){fx_trace.push_back(0x1400000000000000ull|v);}
void GX_WGPIPE_WriteF32(float v){fx_trace.push_back(0x1500000000000000ull|std::bit_cast<uint32_t>(v));}
void GX_WGPIPE_WriteF64(double v){fx_trace.push_back(0x1800000000000000ull);fx_trace.push_back(std::bit_cast<uint64_t>(v));}
uint32_t HW_Reg_Read32(uint32_t a){fx_trace.push_back(0x2400000000000000ull|a);return a^0x01234567u;}
uint16_t HW_Reg_Read16(uint32_t a){fx_trace.push_back(0x2200000000000000ull|a);return uint16_t(a^0x1258u);}
void HW_Reg_Write32(uint32_t a,uint32_t v){fx_trace.push_back((0x3400000000000000ull|a)^(uint64_t(v)<<20));}
void HW_Reg_Write16(uint32_t a,uint16_t v){fx_trace.push_back((0x3200000000000000ull|a)^(uint64_t(v)<<20));}
void watch_hit(uint32_t a,uint32_t v,int n){fx_trace.push_back((0x4000000000000000ull|a)^(uint64_t(v)<<16)^n);}
unsigned fx_polls=0,fx_deliver=0,fx_cap=48;uint32_t fx_seed=0;
bool process_pending_callbacks(CPUContext& c){
 ++fx_polls;g_native_poll_credit=1;
 if(fx_polls>=fx_cap || (fx_deliver && fx_polls%fx_deliver==0)){fx_trace.push_back(0x5000000000000000ull|c.pc);c.srr0=c.pc;return true;}
 return false;}
// Callee stub: deterministic register effects; every third call per trial is "interrupted" and is
// later resumed by the harness at LR, exercising the call-return continuations too.
unsigned fx_calls=0;
void fx_callee(CPUContext& c,uint32_t self){
 ++fx_calls;fx_trace.push_back((uint64_t(self)<<32)|c.lr);fx_trace.push_back((uint64_t(c.gpr[3])<<32)|c.gpr[4]);
 c.gpr[3]=c.gpr[3]*2654435761u+self;c.gpr[4]^=c.gpr[3]>>7;c.gpr[0]=self;c.gpr[12]=c.lr;
 c.fpr[1]=c.fpr[1]*0.5+1.0;c.cr[0].eq=(c.gpr[3]&1)!=0;c.cr[0].lt=(c.gpr[3]&2)!=0;c.cr[0].gt=!c.cr[0].eq&&!c.cr[0].lt;
 c.ctr=c.gpr[3]&7;c.inst_count+=17;
 c.pc=((fx_calls+fx_seed)%3==0)?0x80000500u:c.lr;}
}
'''

HARNESS = r'''
static std::vector<uint8_t> fx_image;
// invalid: the run raised MXCSR.IE (an invalid FP operation, i.e. it created a NaN from non-NaN inputs).
struct Snap{std::vector<uint8_t> state,ram;std::vector<uint64_t> trace;unsigned polls,entries;bool invalid=false;
 bool operator==(const Snap& o)const{return state==o.state&&ram==o.ram&&trace==o.trace&&polls==o.polls&&entries==o.entries;}};
static Snap snap(CPUContext& c,unsigned entries){
 Snap s;auto save=[&](const auto& v){const auto* p=reinterpret_cast<const uint8_t*>(&v);s.state.insert(s.state.end(),p,p+sizeof(v));};
 save(c.gpr);save(c.fpr);save(c.ps1);save(c.cr);save(c.ctr);save(c.xer);save(c.lr);save(c.pc);save(c.inst_count);save(c.msr);save(c.gqr);save(c.srr0);
 for(const auto& bank:fx_banks)for(uintptr_t page:fx_dirty(bank)){
  const auto* a=reinterpret_cast<const uint8_t*>(&page);s.ram.insert(s.ram.end(),a,a+sizeof(page));
  const size_t n=std::min<size_t>(4096,bank.base+bank.bytes-page),off=page-bank.base;
  // Only pages whose bytes differ from the reset baseline: a written-but-unchanged page is not a difference.
  const auto* bytes=reinterpret_cast<const uint8_t*>(page);bool same=true;
  for(size_t i=0;i<n&&same;++i)same=bytes[i]==((bank.base==fx_banks[0].base&&off+i<fx_image.size())?fx_image[off+i]:0);
  if(same){s.ram.resize(s.ram.size()-sizeof(page));continue;}
  s.ram.insert(s.ram.end(),bytes,bytes+n);}
 s.trace=fx_trace;s.polls=fx_polls;s.entries=entries;return s;}
static void reset(CPUContext& c,unsigned trial,uint32_t pc){
 std::mt19937 r(trial*7919+pc);
 for(unsigned i=0;i<32;++i)c.gpr[i]=(r()&1)?0x80100000u+(r()%0x00E00000u&~3u):r()%64;
 c.gpr[1]=0x80400000u+(r()%0x1000u&~7u);c.gpr[2]=0x80600000u;c.gpr[13]=0x80700000u;
 for(auto&v:c.fpr)v=double(int(r()%4096)-2048)/64.;for(auto&v:c.ps1)v=double(int(r()%4096)-2048)/64.;
 for(auto&f:c.cr){f.lt=r()&1;f.gt=r()&1;f.eq=r()&1;f.so=r()&1;}
 c.xer=r()&0xE000007Fu;c.ctr=1+r()%9;c.lr=0x81234560u;c.pc=pc;c.inst_count=r();c.msr=(trial%23==7)?0:0x2000;c.srr0=0;
 c.gqr.fill(0);
 // MEM1 starts from a fixed sparse image, MEM2 from zero; only pages written since the last reset are restored.

 auto& image=fx_image;
 // Words are zero, small finite floats or MEM1 pointers: no NaN/Inf/subnormal inputs. Two-NaN arithmetic
 // returns an operand chosen by GCC's operand order (commuted freely), which differs between equivalent
 // compiles of the same text and is not what this check is about.
 if(image.empty()){image.resize(0x01800000);std::mt19937 q(99);const uint8_t tops[]={0x3f,0x40,0xbf,0xc0,0x80,0x80,0x81};
  for(size_t w=0;w+4<=image.size();w+=4)if(q()%7==0){image[w]=tops[q()%7];image[w+1]=uint8_t(image[w]>=0x80&&image[w]<0x82?q()%0x80:q());image[w+2]=uint8_t(q());image[w+3]=uint8_t(q()&~3u);}
  std::memcpy(c.mmu.mem1.data(),image.data(),image.size());}
 else for(int k=0;k<2;++k)for(uintptr_t page:fx_dirty(fx_banks[k])){
  const size_t off=page-fx_banks[k].base,n=std::min<size_t>(4096,fx_banks[k].bytes-off);
  if(k==0)std::memcpy(reinterpret_cast<void*>(page),image.data()+std::min(off,image.size()),std::min(n,off<image.size()?image.size()-off:0));
  if(k==0&&off+n>image.size())std::memset(reinterpret_cast<void*>(page+(off<image.size()?image.size()-off:0)),0,off+n-std::max(off,image.size()));
  if(k==1)std::memset(reinterpret_cast<void*>(page),0,n);}
 for(const auto& bank:fx_banks)ResetWriteWatch(reinterpret_cast<void*>(bank.base),bank.bytes);
 // Every fifth trial watches a word inside the stack frame, so watched-page and watch-hit paths run too.
 g_watch_addr=(trial%5==3)?((c.gpr[1]+16u)&~3u):0u;c.mmu.native_init_page_bias();g_ctx_ptr=&c;g_mmu=&c.mmu;
 fx_trace.clear();fx_polls=0;fx_calls=0;fx_seed=trial;if(const char* e=std::getenv("FX_POLL_CAP"))fx_cap=unsigned(std::atoi(e));fx_deliver=trial%4==0?0:1+trial%13;g_native_poll_credit=1;
}
static void statediff(const Snap& a,const Snap& b){unsigned k=0;
 for(size_t i=0;i<a.state.size()&&k<6;++i)if(a.state[i]!=b.state[i]){std::printf("  state+%zu: %02x vs %02x\n",i,a.state[i],b.state[i]);++k;}}
static void ramdiff(const Snap& a,const Snap& b){statediff(a,b);unsigned k=0;
 for(size_t i=0;i<a.ram.size()&&k<8;++i)if(a.ram[i]!=b.ram[i]){std::printf("  ram 0x%08zx: %02x vs %02x\n",0x80000000+i,a.ram[i],b.ram[i]);++k;}}
// Equality that treats any two NaNs as equal: PowerPC returns frA's NaN, x86 the first source operand's, and GCC
// commutes a+b / a*b freely, so equivalent compiles of one text may differ only in NaN sign/payload.
static uint32_t fx_be32(const uint8_t* p){return (uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3];}
static bool fx_nan32(uint32_t w){return (w&0x7f800000u)==0x7f800000u && (w&0x007fffffu);}
static bool fx_nan64hi(uint32_t w){return (w&0x7ff00000u)==0x7ff00000u;}
static bool fx_ram_eq_nan(const std::vector<uint8_t>& a,const std::vector<uint8_t>& b){
 if(a.size()!=b.size())return false;
 for(size_t i=0;i+4<=a.size();i+=4){const uint32_t x=fx_be32(&a[i]),y=fx_be32(&b[i]);if(x==y)continue;
  if(fx_nan32(x)&&fx_nan32(y))continue;
  if(fx_nan64hi(x)&&fx_nan64hi(y))continue;
  if(i>=4&&fx_nan64hi(fx_be32(&a[i-4]))&&fx_nan64hi(fx_be32(&b[i-4])))continue;
  return false;}
 for(size_t i=a.size()&~size_t(3);i<a.size();++i)if(a[i]!=b[i])return false;return true;}
static bool fx_state_eq_nan(const std::vector<uint8_t>& a,const std::vector<uint8_t>& b){
 if(a.size()!=b.size())return false;
 // gpr[32] (128 bytes) then fpr[32] and ps1[32] doubles (512 bytes), then the rest exactly.
 for(size_t i=0;i<a.size();){
  if(i>=128&&i<640&&((i-128)%8)==0){double x,y;std::memcpy(&x,&a[i],8);std::memcpy(&y,&b[i],8);
   if(!(std::memcmp(&x,&y,8)==0||(std::isnan(x)&&std::isnan(y))))return false;i+=8;continue;}
  if(a[i]!=b[i])return false;++i;}
 return true;}
static bool fx_eq_nan(const Snap& a,const Snap& b){return fx_state_eq_nan(a.state,b.state)&&fx_ram_eq_nan(a.ram,b.ram)&&a.trace==b.trace&&a.polls==b.polls&&a.entries==b.entries;}
using Fn=void(*)(CPUContext&);
static bool interior(uint32_t pc,const uint32_t* pcs,unsigned n){for(unsigned i=0;i<n;++i)if(pcs[i]==pc)return true;return false;}
static Snap run(Fn f,CPUContext&,unsigned trial,uint32_t pc,const uint32_t* pcs,unsigned n){
 // A fresh context per run: no field a previous run touched can leak into the next.
 auto fresh=std::make_unique<CPUContext>();CPUContext& c=*fresh;
 reset(c,trial,pc);unsigned entries=0;
 _mm_setcsr(_mm_getcsr()&~0x3Fu);
 for(;;){++entries;f(c);
  static const unsigned cap=std::getenv("FX_MAX_ENTRIES")?unsigned(std::atoi(std::getenv("FX_MAX_ENTRIES"))):64u;
  if(entries>=cap)break;
  if(c.pc==0x80000500u)c.pc=c.lr;
  if(!interior(c.pc,pcs,n))break;}
 const bool invalid=(_mm_getcsr()&1u)!=0;Snap s=snap(c,entries);s.invalid=invalid;return s;}
'''


FP_TO_INT = re.compile(r'\(int32_t\)\s*(ctx\.fpr\[\d+\]|ctx\.ps1\[\d+\]|\b[fp]\d+\b)')


def namespaced(tag, text, address):
    # Harness-only, applied identically to every variant: the generator spells fctiwz as `(int32_t)<double>`,
    # which is undefined for NaN/out-of-range values that random states produce, so each variant may compile it
    # differently. fx_cvt is the defined x86 conversion (cvttsd2si) the production code gets for in-range values.
    text = FP_TO_INT.sub(r'fx_cvt(\1)', text)
    return 'namespace v_' + tag + ' {\n' + text + '\n}\n'


def build_source(funcs):
    callees = set()
    parts = []
    for address, d in funcs.items():
        for text in (d['old'], d['base'], d['new']):
            callees |= set(re.findall(r'\b(func_[0-9A-F]{8})\(ctx\)', text))
    own = {'func_' + a for a in funcs}
    stub = ''.join('void %s(CPUContext& c){fx_callee(c,0x%s);}\n' % (n, n[5:]) for n in sorted(callees - own))
    body = PRELUDE.replace('CPUContext* g_ctx_ptr', 'CPUContext* g_ctx_ptr') + stub
    for address, d in funcs.items():
        for tag in ('old', 'base', 'new'):
            body += namespaced(tag + '_' + address, d[tag], address)
    return body


def driver(funcs, trials):
    lines = ['int main(){',
             ' unsigned long long checks=0,reentries=0,bad=0,nanonly=0,tainted=0,tainted_bw=0;const bool keep=std::getenv("FX_KEEP_GOING")!=nullptr;auto c=std::make_unique<CPUContext>();',
             # The four IEEE rounding modes. FTZ/DAZ (0x9fc0) is left out: the runtime never sets it, and under it
             # GCC's folding of float->double->float round trips differs between equivalent compiles of one text.
             ' const unsigned modes[]={0x1f80,0x3f80,0x5f80,0x7f80};']
    # FX_BENCH=<runs>: time the function call alone (state reset outside the timed region), base vs new,
    # same entry PCs and trials, interleaved to cancel drift. Reports the median of per-pass ratios.
    lines += [' if(const char* bench=std::getenv("FX_BENCH")){const unsigned passes=unsigned(std::atoi(bench));']
    for address, d in funcs.items():
        pcs = ','.join('0x%08Xu' % x for x in d['pcs'])
        lines += ['  {static const uint32_t pcs[]={%s};const unsigned n=%d;static const uint32_t starts[]={0x%su,%s};' % (pcs, len(d['pcs']), address, pcs),
                  '   std::vector<double> ratio;double tb=0,tn=0;',
                  '   for(unsigned pass=0;pass<passes;++pass){double sb=0,sn=0;',
                  '    for(unsigned t=0;t<6;++t)for(uint32_t pc:starts)for(int v=0;v<2;++v){',
                  '     auto fresh=std::make_unique<CPUContext>();reset(*fresh,t,pc);',
                  '     const auto a=std::chrono::steady_clock::now();',
                  '     if(v)v_new_{0}::func_{0}(*fresh);else v_base_{0}::func_{0}(*fresh);'.format(address),
                  '     const double us=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-a).count();',
                  '     (v?sn:sb)+=us;}',
                  '    ratio.push_back(sb/sn);tb+=sb;tn+=sn;}',
                  '   std::sort(ratio.begin(),ratio.end());',
                  '   std::printf("BENCH %s base %%.1f us new %%.1f us per pass, median speedup %%.3fx\\n",tb/passes,tn/passes,ratio[ratio.size()/2]);}' % address]
    lines += ['  return 0;}']
    for address, d in funcs.items():
        pcs = ','.join('0x%08Xu' % x for x in d['pcs'])
        lines += [' {static const uint32_t pcs[]={%s};const unsigned n=%d;' % (pcs, len(d['pcs'])),
                  '  static const uint32_t starts[]={0x%su,%s};' % (address, pcs),
                  '  for(unsigned mode:modes){_mm_setcsr(mode);for(unsigned t=0;t<%d;++t)for(uint32_t pc:starts){' % trials,
                  '   auto o=run(v_old_{0}::func_{0},*c,t,pc,pcs,n);auto b=std::getenv("FX_SELF")?run(v_old_{0}::func_{0},*c,t,pc,pcs,n):run(v_base_{0}::func_{0},*c,t,pc,pcs,n);'
                  'auto w=run(v_new_{0}::func_{0},*c,t,pc,pcs,n);'.format(address),
                  '   reentries+=w.entries-1;++checks;',
                  # A run that created a NaN from NaN-free inputs is only compared NaN-insensitively: x86 and GCC choose
                  # which NaN propagates by operand order, so equivalent compiles may legitimately diverge after it.
                  '   if(o.invalid||b.invalid||w.invalid){++tainted;if(!fx_eq_nan(b,w))++tainted_bw;continue;}',
                  '   if((!(o==w)||!(b==w))&&fx_eq_nan(o,w)&&fx_eq_nan(b,w)){++nanonly;continue;}',
                  '   if(!(o==w)||!(b==w)){std::printf("MISMATCH ' + address + ' trial=%u pc=%08x mode=%x state=%d/%d '
                  'ram=%d/%d trace=%d/%d polls=%u/%u/%u entries=%u/%u/%u\\n",t,pc,mode,o.state==w.state,b.state==w.state,'
                  'o.ram==w.ram,b.ram==w.ram,o.trace==w.trace,b.trace==w.trace,o.polls,b.polls,w.polls,o.entries,b.entries,w.entries);if(!keep)ramdiff(o,w);++bad;if(!keep)return 1;}',
                  '  }}',
                  '  _mm_setcsr(0x1f80);}']
    lines += [' if(bad){std::printf("MISMATCHES: %llu of %llu runs (+%llu NaN-payload-only; %llu NaN-creating runs, %llu of them base/new different)\\n",bad,checks,nanonly,tainted,tainted_bw);return 1;}',
              ' std::printf("ALL MATCH: %llu differential runs, %llu dispatcher re-entries; old/base/new identical'
              ' (%llu runs differ only in NaN sign/payload; %llu NaN-creating runs, %llu of them base/new different)\\n",checks,reentries,nanonly,tainted,tainted_bw);',
              ' return 0;', '}']
    return '\n'.join(lines) + '\n'


def instruction_counts(objdump, obj):
    per, cur = {}, None
    for line in subprocess.run([objdump, '-d', '--no-show-raw-insn', '-C', str(obj)], capture_output=True, text=True).stdout.splitlines():
        m = re.match(r'^[0-9a-f]+ <(.*)>:$', line)
        if m:
            cur = m[1]; per[cur] = 0
        elif cur and re.match(r'^\s+[0-9a-f]+:\t', line):
            per[cur] += 1
    return per


def sizes(gxx, common, a, funcs):
    """Static x86 instruction counts per body, production hot-unit level (-Os plus the bodies' own optimize attributes)."""
    objdump = str(winlibs_bin() / 'objdump.exe') if winlibs_bin() else shutil.which('objdump')
    result = {}
    for tag in ('base', 'new'):
        callees = set(re.findall(r'\b(func_[0-9A-F]{8})\(ctx\)', ''.join(d[tag] for d in funcs.values()))) - {'func_' + x for x in funcs}
        text = PRELUDE + ''.join('void %s(CPUContext& c);\n' % n for n in sorted(callees))
        text += ''.join(d[tag] + '\n' for d in funcs.values())
        src = a.work / ('size_' + tag + '.cpp'); src.write_text(text, errors='surrogateescape')
        obj = src.with_suffix('.o')
        r = subprocess.run([gxx, *common, '-Os', '-c', str(src), '-o', str(obj)], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-3000:]); raise SystemExit('size compile failed')
        per = instruction_counts(objdump, obj)
        for address in funcs:
            row = result.setdefault(address, {})
            for kind in ('resume', 'continuation', 'primary'):
                row[tag + '_' + kind] = sum(v for k, v in per.items() if ('native_guest_' + kind + '_' + address) in k)
            row[tag + '_total'] = sum(v for k, v in per.items() if address in k)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--generated', type=Path, default=BUILD / 'native-port/build/crash-generated')
    ap.add_argument('--include', type=Path, default=BUILD / 'storage/nwii-full-source/nWiiRuntime/include')
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--functions', default='803DB050,801B8B10,801B8E48,802F71E0')
    ap.add_argument('--trials', type=int, default=60)
    a = ap.parse_args()
    excluded = set(json.loads((PORT / 'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
    spec = hooked_specializer()
    a.work.mkdir(parents=True, exist_ok=True)
    funcs, report = {}, {}
    for address in a.functions.split(','):
        assert address not in excluded, 'effect-excluded address: ' + address
        source = generated_function(a.generated, address)
        v = variants(source, spec)
        funcs[address] = {'old': source, 'base': v['base'][0], 'new': v['new'][0],
                          'pcs': [int(x, 16) for x, _ in P.parse(source)[3]]}
        report[address] = {'interior_entries': len(P.parse(source)[3]),
                           'base_continuations': v['base'][1].get('fast_continuations'),
                           'new_continuations': v['new'][1].get('fast_continuations')}
    gxx = str(winlibs_bin() / 'g++.exe') if winlibs_bin() else shutil.which('g++')
    common = ['-std=c++20', '-march=x86-64-v3', '-mfma', '-fwrapv', '-fno-strict-aliasing', '-ffp-contract=off',
              '-DNWII_STATIC_RAM_BASE', '-DNWII_WATCH_PAGE_MARKING', '-DNWII_BUILT_WITH_FMA=1',
              '-I', str(a.include), '-I', str(a.generated), '-w']
    cpp = a.work / 'poll_continuations.cpp'
    cpp.write_text(build_source(funcs) + HARNESS + driver(funcs, a.trials), errors='surrogateescape')
    exe = a.work / 'poll_continuations.exe'
    r = subprocess.run([gxx, *common, os.environ.get('NWII_CHECK_OPT', '-Os'), '-static', str(cpp), '-o', str(exe)], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-6000:]); raise SystemExit('compile failed')
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=3600)
    out = (r.stdout.strip() or r.stderr.strip()[-400:]).splitlines()
    report['differential'] = out[-1] if out else 'no output (exit %d)' % r.returncode
    ok = r.returncode == 0 and report['differential'].startswith('ALL MATCH')
    report['size'] = sizes(gxx, common, a, funcs)
    print(json.dumps(report, indent=1))
    (a.work / 'report.json').write_text(json.dumps(report, indent=1))
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
