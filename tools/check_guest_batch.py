"""Local differential/code-generation checks; never stages inputs or launches a game."""
from pathlib import Path
from collections import Counter
import argparse,hashlib,json,os,re,shutil,subprocess
import fix_guest_batch as B
import guest_batch_lowering as L
import guest_typed_leaves as T
import guest_loop_guards as G
from port_paths import winlibs_bin
PORT=Path(__file__).resolve().parents[1]

def method(text,signature):
    a=text.index(signature);brace=text.index('{',a)
    return text[a:B.scope_end(text,brace)]

def fixture_function(name,body):
    return 'void '+name+'(NWII_CTX_PARAM ctx) {\n'+body+'\n}'

def refusal_checks():
    # Incoming non-loc labels are real joins too (notably lazy-FPU exits).
    value=fixture_function('sample','''
    double f0=ctx.fpr[0];
    if(ctx.gpr[0])goto exit_1;
    f0 = ctx.mmu.read_f32(ctx.gpr[3]);
    ctx.mmu.write_f32(ctx.gpr[5], (float)f0);
exit_1: ;
    ctx.fpr[0]=f0;
''')
    new,n=L.fp_transport(value);assert n==1
    assert new.index('f0 = double(native_f32_1);')<new.index('exit_1: ;')
    # An observer or unknown/full-word XER use ends carry/format knowledge.
    update='xer = (xer & ~(1 << 29)) | (((res >> 32) & 1) << 29);'
    assert L.carry_regions(update+'\nctx.xer=xer;\n'+update)[1]==0
    assert L.carry_regions(update+'\nxer = 0;\n'+update)[1]==0
    unsafe='xer = (xer & ~(1 << 29)) | ((((res & 1)+2) << 29));'
    assert L.carry_regions(unsafe+'\n'+unsafe)[1]==0
    psq='{ auto pq_ = ctx.psq_load_pair(g1 + 0, 0, 0); f0=pq_.f;p0=pq_.p; }'
    for observer in ('ctx.gqr[0]=4;','unknown(ctx);','incoming: ;\nif(g0)goto incoming;'):
        assert L.psq_regions(psq+'\n'+observer+'\n'+psq)[1]==0
    bad=fixture_function('func_F0000000','++ctx.inst_count;\nunknown(ctx);\nctx.pc=ctx.lr;return;')
    assert T.qualify(bad,'F0000000') is None
    bad=fixture_function('func_F0000000','loc_F0000000: ;\n++ctx.inst_count;\ngoto loc_F0000000;')
    assert T.qualify(bad,'F0000000') is None
    # A partial-lane merge must never load an unused, uninitialized lane.
    merges=fixture_function('sample','''
    { f0 = f1; p0 = f2; }
    { f3 = f0; p3 = p0; }
    { f4 = f3; p4 = p3; }
    { f5 = f4; p5 = p4; }
''')
    new,n=L.packed_pairs(merges);assert n==4
    fast=new.split('}else{',1)[0]
    assert 'PackedInput(p1,' not in fast and 'PackedInput(p2,' not in fast and 'PackedInput(f0,' not in fast

def record_loop(words,streams):
    names=['g3','g4'][:streams]
    rows=['    uint32_t g3=ctx.gpr[3],g4=ctx.gpr[4],g5=ctx.gpr[5],ctrv=ctx.ctr;',
          '    uint64_t ic=ctx.inst_count;',
          '    double '+','.join('f'+str(i)+'=ctx.fpr['+str(i)+']' for i in range(words))+';',
          '    if(!ctrv)return;','loc_80010000: ;']
    for i in range(words):
        root=names[i%streams];offset=(i//streams)*4
        rows+=['    if (__builtin_expect(ctx.require_fpu(0x80010000),0)) return;',
               '    f'+str(i)+' = ctx.mmu.read_f32('+root+' + '+str(offset)+');']
    for i in range(words):
        rows+=['    ctx.mmu.write_f32(g5 + 0, (float)f'+str(i)+');','    ++ic;']
    for root in names:rows+=['    '+root+' = '+root+' + 32;']
    spill='ctx.gpr[3]=g3;ctx.gpr[4]=g4;ctx.gpr[5]=g5;ctx.ctr=ctrv;ctx.inst_count=ic;'+''.join('ctx.fpr['+str(i)+']=f'+str(i)+';' for i in range(words))
    reload='g3=ctx.gpr[3];g4=ctx.gpr[4];g5=ctx.gpr[5];ctrv=ctx.ctr;ic=ctx.inst_count;'+''.join('f'+str(i)+'=ctx.fpr['+str(i)+'];' for i in range(words))
    rows+=['    ctrv--;','    if (nwii::runtime::native_poll_due()) {',spill,'    process_pending_callbacks(ctx);',reload,'    }',
           '    if (ctrv != 0) goto loc_80010000;',spill,'    ctx.pc=ctx.lr;']
    return fixture_function('record_'+str(words)+'_'+str(streams),'\n'.join(rows))

PRELUDE=r'''
#include "runtime/cpu_context.h"
#include "runtime/native_guest_batch.h"
#include "runtime/native_loop_span.h"
#include "runtime/native_fifo_lock.h"
#include "runtime/native_gx_batching.h"
#include <bit>
#include <cassert>
#include <memory>
#include <random>
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

HARNESS=r'''
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
 g_ctx_ptr=&c;g_mmu=&c.mmu;g_native_fifo_ok=false;native_wpar_write(trial%17==0?0x80020000:0x0c008000);
 fixture_credit=trial%5==0?1:trial%7==0?2:1<<30;g_native_poll_credit=fixture_credit;
 for(unsigned j=0;j<4078+trial%40;++j)GX_WGPIPE_Write8(uint8_t(j));
}
using Fn=void(*)(CPUContext&);
void compare(Fn old,Fn now,unsigned trial){
 auto c=std::make_unique<CPUContext>();reset(*c,trial);old(*c);auto a=snapshot(*c);
 reset(*c,trial);now(*c);auto b=snapshot(*c);
 if(a!=b){std::printf("DIFFERENTIAL FAILURE trial=%u state=%d ram=%d mem2=%d fifo=%d tail=%d boundaries=%d watches=%d hw=%d wpar=%d polls=%d old=%p now=%p\n",trial,a.state==b.state,a.ram==b.ram,a.mem2==b.mem2,a.fifo==b.fifo,a.tail==b.tail,a.boundaries==b.boundaries,a.watches==b.watches,a.hw==b.hw,a.wpar==b.wpar,a.polls==b.polls,(void*)old,(void*)now);std::fflush(stdout);std::abort();}
}
int main(){
 check_bp();
 unsigned long long checks=0;
 const unsigned modes[]={0x1f80,0x3f80,0x5f80,0x7f80,0x9fc0};
 for(unsigned mode:modes){_mm_setcsr(mode);
  for(unsigned trial=0;trial<80;++trial){
   for(auto [old,now]:cases){compare(old,now,trial);++checks;}
  }
 }
 auto scalar=std::make_unique<CPUContext>();g_ctx_ptr=scalar.get();g_mmu=&scalar->mmu;g_watch_addr=0;
 scalar->gpr[3]=0x80010000;scalar->gpr[5]=0x80010020;scalar->mmu.native_init_page_bias();
 unsigned long long transports=0;
 for(unsigned mode:modes){_mm_setcsr(mode);
  for(unsigned i=0;i<200000;++i){
   const uint32_t edges[]={0u,0x80000000u,1u,0x80000001u,0x007fffffu,0x7f800000u,0xff800000u,0x7f800001u,0x7fc01234u};
   uint32_t bits=i<9?edges[i]:i*2654435761u;scalar->mmu.write32(0x80010000,bits);
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
 std::printf("Guest batch differential checks: %llu; all architectural state, RAM, FIFO/tail/publications, watches, hardware writes and polls matched.\n",checks);
}
'''

def build_fixture(runtime,generated,hot,work):
    header=(runtime/'include/runtime/cpu_context.h').read_text();patched=B.cpu_source(header)
    assert B.restore(patched)==header and B.cpu_source(patched)==patched
    local=work/'include/runtime';local.mkdir(parents=True,exist_ok=True)
    (local/'cpu_context.h').write_text(patched)
    for name in ('native_guest_batch.h','native_loop_span.h'):
        shutil.copy2(PORT/'runtime-overlays'/name,local/name)
    gx=(runtime/'src/hle/gx.cpp').read_text()
    block=gx[gx.index('struct NativeCpuFifo {'):gx.index('\nuint64_t g_prof_decode_us_accum = 0;')]
    # Record publication boundaries without replacing the production append logic.
    anchor='    destination.insert(destination.end(), source.data, source.data + source.n);'
    assert block.count(anchor)==1
    block=block.replace(anchor,'    fixture_boundaries.push_back(destination.size()+source.n);\n'+anchor)
    code=PRELUDE+'\nnamespace nwii::runtime {\n'+block+'\n'
    # gx.cpp closes its namespace at the end of the extracted block.
    source=[];names=[];stats=Counter()
    transport=fixture_function('transport',r'''
    double f0=ctx.fpr[0],f1=ctx.fpr[1],p0=ctx.ps1[0];
    f0 = p0 = ctx.mmu.read_f32(ctx.gpr[3] + 0);
    f1 = f0;
    ctx.mmu.write_f32(ctx.gpr[3]+8, (float)f1);
    ctx.mmu.write_f32(ctx.gpr[5], (float)p0);
    ctx.fpr[0]=f0;ctx.fpr[1]=f1;ctx.ps1[0]=p0;
''')
    joins=fixture_function('joins',r'''
    double f0=ctx.fpr[0],f1=ctx.fpr[1];
    if(ctx.gpr[0]&1u)goto exit_1;
    f0 = ctx.mmu.read_f32(ctx.gpr[3] + 0);
    ctx.mmu.write_f32(ctx.gpr[5], (float)f0);
exit_1: ;
    f1 = ctx.mmu.read_f32(ctx.gpr[4] + 0);
    ctx.mmu.write_f32(ctx.gpr[5], (float)f1);
    ctx.fpr[0]=f0;ctx.fpr[1]=f1;
''')
    carry=fixture_function('carry',r'''
    uint32_t xer=ctx.xer,g0=ctx.gpr[0],g1=ctx.gpr[1],g2=ctx.gpr[2];
    {uint64_t res=uint64_t(g0)+g1;g0=uint32_t(res);xer = (xer & ~(1 << 29)) | (((res >> 32) & 1) << 29);}
    {uint64_t res=uint64_t(g0)+g2+((xer >> 29) & 1);g2=uint32_t(res);xer = (xer & ~(1 << 29)) | (((res >> 32) & 1) << 29);}
    {uint64_t res=uint64_t(g0)+g2+((xer >> 29) & 1);g1=uint32_t(res);xer = (xer & ~(1 << 29)) | (((res >> 32) & 1) << 29);}
    ctx.xer=xer;ctx.gpr[0]=g0;ctx.gpr[1]=g1;ctx.gpr[2]=g2;
''')
    paired=fixture_function('paired',r'''
    double f0=ctx.fpr[0],f1=ctx.fpr[1],f2=ctx.fpr[2],f3=ctx.fpr[3],p0=ctx.ps1[0],p1=ctx.ps1[1],p2=ctx.ps1[2],p3=ctx.ps1[3];
    { f0 = f0 + f1; p0 = p0 + p1; }
    { f1 = f0 - f1; p1 = p0 - p1; }
    { f2 = f1 * f2; p2 = p1 * p2; }
    { f3 = f2 + f3; p3 = p2 + p3; }
    { f0 = f3 * f0; p0 = p3 * p0; }
    ctx.fpr[0]=f0;ctx.fpr[1]=f1;ctx.fpr[2]=f2;ctx.fpr[3]=f3;
    ctx.ps1[0]=p0;ctx.ps1[1]=p1;ctx.ps1[2]=p2;ctx.ps1[3]=p3;
''')
    merges=fixture_function('merges',r'''
    double f0=ctx.fpr[0],f1=ctx.fpr[1],f2=ctx.fpr[2],p0=ctx.ps1[0],p1=ctx.ps1[1],p2=ctx.ps1[2];
    { f0 = f1; p0 = p1; }
    { f1 = p0; p1 = f1; }
    { f2 = f1; p2 = f2; }
    { f0 = p2; p0 = p0; }
    { f2 = f0 + f2; p2 = p0 + p2; }
    ctx.fpr[0]=f0;ctx.fpr[1]=f1;ctx.fpr[2]=f2;ctx.ps1[0]=p0;ctx.ps1[1]=p1;ctx.ps1[2]=p2;
''')
    conditions=fixture_function('conditions',r'''
    uint32_t g0=ctx.gpr[0],g1=ctx.gpr[1];bool c0lt=ctx.cr[0].lt,c0eq=ctx.cr[0].eq,c0gt=ctx.cr[0].gt;
    c0lt = (int32_t(g0)<0); c0eq = (g0==0); c0gt = (int32_t(g0)>0);
    g0 += uint32_t(c0eq);
    c0lt = (int32_t(g1)<0); c0eq = (g1==0); c0gt = (int32_t(g1)>0);
    ctx.cr[0].lt=c0lt;ctx.cr[0].eq=c0eq;ctx.cr[0].gt=c0gt;ctx.gpr[0]=g0;
''')
    psq=fixture_function('psq',r'''
    double f0,p0,f1,p1;
    { auto pq_ = ctx.psq_load_pair(ctx.gpr[3] + 0, 0, 0); f0 = pq_.f; p0 = pq_.p; }
    { auto pq_ = ctx.psq_load_pair(ctx.gpr[4] + 0, 1, 0); f1 = pq_.f; p1 = pq_.p; }
    { ctx.psq_store_val(f0, p0, ctx.gpr[5], 0, 0); }
    { ctx.psq_store_val(f1, p1, ctx.gpr[3] + 16, 1, 0); }
    ctx.fpr[0]=f0;ctx.ps1[0]=p0;ctx.fpr[1]=f1;ctx.ps1[1]=p1;
''')
    for name,old in [('transport',transport),('joins',joins),('carry',carry),('paired',paired),('merges',merges),('conditions',conditions),('psq',psq)]:
        new,counts=L.lower(old);stats.update(counts);assert new!=old,name
        source+=['namespace old_'+name+'{'+old+'}', 'namespace new_'+name+'{'+new+'}']
        names.append((name,'old_'+name+'::'+name,'new_'+name+'::'+name))
    for words in range(2,9):
        for streams in (1,2):
            old=record_loop(words,streams);new,n=G.apply(old,clone=True,fuse=True,records=True)
            assert n==1,(words,streams)
            name='record_'+str(words)+'_'+str(streams)
            source+=['namespace old_'+name+'{'+old+'}', 'namespace new_'+name+'{'+new+'}']
            names.append((name,'old_'+name+'::'+name,'new_'+name+'::'+name))
    excluded=set(json.loads((PORT/'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
    leaves=T.collect(sorted(generated.glob('output_*.cpp')),excluded)
    assert leaves,'No actual typed leaves qualified'
    # Compile and compare the actual qualifying leaf bodies, including all outputs.
    calls=Counter()
    for path in hot.glob('hot_*.cpp'):calls.update(re.findall(r'\bfunc_([0-9A-F]{8})\(ctx\)',path.read_text(errors='surrogateescape')))
    selected=sorted(leaves,key=lambda address:(-calls[address],address))[:64]
    for address in selected:
        leaf=leaves[address]
        original=leaf.source
        call='ctx.pc = 0x'+address+'; func_'+address+'(ctx);'
        new,helper,count=T.apply(call,{address:leaf});assert count==1
        name='leaf_'+address
        source+=['namespace old_'+name+'{'+original+'\n'+fixture_function(name,call)+'}',
                 'namespace new_'+name+'{'+helper+'\n'+fixture_function(name,new)+'}']
        names.append((name,'old_'+name+'::'+name,'new_'+name+'::'+name))
    import promote_regs as P
    address=selected[0];leaf=leaves[address]
    caller=fixture_function('func_F0000000','''
    ctx.gpr[0] = ctx.gpr[0] + 1;
    ctx.gpr[7] = ctx.gpr[7] + 1;
    ++ctx.inst_count;
    ctx.lr = 0x80010010;
    ctx.pc = 0x'''+address+'''; func_'''+address+'''(ctx);
    if (ctx.pc != 0x80010010) return;
    ctx.gpr[8] = ctx.gpr[7] + ctx.gpr[3];
    ctx.pc = ctx.lr; return;
''')
    lifted,helper,count=T.apply(caller,{address:leaf});assert count==1
    old,old_stats=P.promote(caller);new,new_stats=P.promote(lifted)
    assert old and new,(old_stats,new_stats)
    assert new_stats['spill_bits']+new_stats['reload_bits']<old_stats['spill_bits']+old_stats['reload_bits'],(old_stats,new_stats)
    source+=['namespace old_typed_caller{'+leaf.source+'\n'+old+'}', 'namespace new_typed_caller{'+helper+'\n'+new+'}']
    names.append(('typed_caller','old_typed_caller::func_F0000000','new_typed_caller::func_F0000000'))
    stats['typed_caller_sync_bits_before']=old_stats['spill_bits']+old_stats['reload_bits']
    stats['typed_caller_sync_bits_after']=new_stats['spill_bits']+new_stats['reload_bits']
    raw_renderer=(runtime/'src/hle/gx/renderer_gl.cpp').read_text();renderer=B.renderer_source(raw_renderer)
    assert B.restore(renderer)==raw_renderer and B.renderer_source(renderer)==renderer
    bp=method(renderer,'static bool NativeRedundantBp(uint8_t kind,')
    resolve=method(renderer,'static uint32_t NativeBpRegisterValue(')
    code+='\n#include "runtime/native_batch_boundaries.h"\nnamespace bp_fixture {\n'
    code+='enum class GXCommandType:uint8_t{BPRegister=1,CPRegister=2};\nuint32_t s_native_bp_shadow[256]{},s_native_bp_mask=0xffffff;\n'
    code+='namespace native_state_epochs{inline bool ShaderBp(unsigned){return false;}inline void Invalidate(){}}\n'+bp+'\n'+resolve+'\n}\n'
    code+=r'''
void check_bp(){using namespace bp_fixture;std::mt19937 r(90231);
 for(unsigned reg:{0x0fu,0xe8u})for(unsigned i=0;i<10000;++i){
  s_native_bp_shadow[reg]=r()&0xffffff;s_native_bp_mask=r()&0xffffff;
  uint32_t value=(s_native_bp_shadow[reg]&s_native_bp_mask)|(r()&~s_native_bp_mask);
  assert(NativeRedundantBp(uint8_t(GXCommandType::BPRegister),reg,value));
  auto expected=s_native_bp_shadow[reg];assert(NativeBpRegisterValue(reg,value)==expected && s_native_bp_mask==0xffffff);
  assert(!NativeRedundantBp(uint8_t(GXCommandType::BPRegister),reg,expected^1u));
 }
 for(unsigned reg:{0x45u,0x47u,0x48u,0x52u,0x65u})assert(!NativeRedundantBp(uint8_t(GXCommandType::BPRegister),reg,0));
 assert(!NativeRedundantBp(uint8_t(GXCommandType::CPRegister),0xe8,0));
 std::puts("BP: 20000 masked duplicates retained mask consumption; changed values and mandatory events retain boundaries.");
}
'''
    code+='\n'.join(source)+'\nconst std::pair<Fn,Fn> cases[]={\n'+',\n'.join('{'+a+','+b+'}' for _,a,b in names)+'\n};\n'
    code=code.replace('const std::pair<Fn,Fn> cases[]','using Fn=void(*)(CPUContext&);\nconst std::pair<Fn,Fn> cases[]')
    code+=HARNESS
    (work/'fixture.cpp').write_text(code,errors='surrogateescape')
    return dict(stats=stats,typed_leaves=len(leaves),fixture_cases=len(names))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--runtime',type=Path,required=True);ap.add_argument('--generated',type=Path,required=True);ap.add_argument('--hot',type=Path,required=True);ap.add_argument('--work',type=Path,required=True);a=ap.parse_args()
    a.work.mkdir(parents=True,exist_ok=True)
    refusal_checks()
    # Inputs are read-only. All generated test files and compilation outputs live here.
    assert not any(a.work.resolve()==p.resolve() or p.resolve() in a.work.resolve().parents for p in (a.runtime,a.generated,a.hot))
    stats=build_fixture(a.runtime,a.generated,a.hot,a.work)
    compiler=str(winlibs_bin()/'g++.exe') if winlibs_bin() else shutil.which('g++')
    flags=['-std=c++20','-O2','-march=x86-64-v3','-mfma','-fwrapv','-fno-strict-aliasing','-ffp-contract=off','-static','-I',str(a.work/'include'),'-I',str(a.runtime/'include')]
    command=[compiler,*flags,str(a.work/'fixture.cpp'),'-o',str(a.work/'fixture.exe')]
    print('Compiling local differential fixture.',flush=True)
    result=subprocess.run(command,capture_output=True,text=True)
    (a.work/'compile.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise SystemExit((result.stdout+result.stderr)[-14000:])
    env={k:v for k,v in os.environ.items() if not k.startswith('NWII_')};env['NWII_NO_DIAG']='1';env['NWII_WATCH']='0c008000'
    result=subprocess.run([str(a.work/'fixture.exe')],capture_output=True,text=True,env=env,timeout=120)
    print(result.stdout+result.stderr,flush=True)
    if result.returncode:raise SystemExit(result.returncode)
    stats['output']=result.stdout;counts=Counter()
    excluded=set(json.loads((PORT/'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
    checked=[]
    for path in sorted(a.hot.glob('hot_*.cpp')):
        raw=path.read_text(errors='surrogateescape');new,n=B.guest_source(raw,excluded);counts.update(n)
        assert B.restore(new)==raw and B.guest_source(new,excluded)[0]==new,path
        if new!=raw:
            local=a.work/path.name;local.write_text(new,errors='surrogateescape')
            stamp=a.work/(path.stem+'-syntax.sha256')
            key=hashlib.sha256(new.encode('utf-8','surrogateescape')+(a.work/'include/runtime/cpu_context.h').read_bytes()+(a.work/'include/runtime/native_guest_batch.h').read_bytes()+(a.work/'include/runtime/native_loop_span.h').read_bytes()+str(flags).encode()).hexdigest()
            if not stamp.exists() or stamp.read_text()!=key:
                result=subprocess.run([compiler,*flags,'-fsyntax-only','-I',str(a.generated),str(local)],capture_output=True,text=True)
                (a.work/(path.stem+'-syntax.log')).write_text(result.stdout+result.stderr)
                if result.returncode:raise SystemExit((result.stdout+result.stderr)[-12000:])
                stamp.write_text(key)
            checked.append(path.name)
    stats['source_sites']=dict(counts)
    stats['syntax_checked_hot_units']=checked
    (a.work/'results.json').write_text(json.dumps(stats,indent=2)+'\n')
    print(json.dumps(stats,indent=2),flush=True)

if __name__=='__main__':main()
