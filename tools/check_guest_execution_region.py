"""Differential check of the actual selected translator statements and CFG.

The small deterministic runtime model isolates transformation semantics: all
architectural fields, ordered memory, FP guards, observer changes and unwind
exits. This is not a game benchmark or a substitute for the real poll fixture.
"""
from pathlib import Path
import argparse
import json
import re
import shutil
import subprocess
import guest_execution_region as E

MODEL=r'''
#include <array>
#include <bit>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <stdexcept>
#include <vector>
namespace nwii::runtime {
struct MMU {
 static constexpr uint32_t kMem1Limit=0x01800000,kMem2Limit=0x04000000;
 std::map<uint32_t,uint8_t> bytes;
 uint8_t read8(uint32_t a){auto i=bytes.find(a);return i==bytes.end()?0:i->second;}
 uint16_t read16(uint32_t a){return uint16_t(read8(a))<<8|read8(a+1);}
 uint32_t read32(uint32_t a){return uint32_t(read16(a))<<16|read16(a+2);}
 uint64_t read64(uint32_t a){return uint64_t(read32(a))<<32|read32(a+4);}
 float read_f32(uint32_t a){return std::bit_cast<float>(read32(a));}
 double read_f64(uint32_t a){return std::bit_cast<double>(read64(a));}
 void write8(uint32_t a,uint8_t v){bytes[a]=v;}
 void write16(uint32_t a,uint16_t v){write8(a,v>>8);write8(a+1,v);}
 void write32(uint32_t a,uint32_t v){write16(a,v>>16);write16(a+2,v);}
 void write64(uint32_t a,uint64_t v){write32(a,v>>32);write32(a+4,v);}
 void write_f32(uint32_t a,float v){write32(a,std::bit_cast<uint32_t>(v));}
 void write_f64(uint32_t a,double v){write64(a,std::bit_cast<uint64_t>(v));}
};
struct CR {bool lt,gt,eq,so;};
struct CPUContext {
 std::array<uint32_t,32> gpr{};std::array<double,32> fpr{},ps1{};
 std::array<CR,8> cr{};uint32_t ctr=0,lr=0,pc=0,xer=0,msr=0,srr0=0,srr1=0;
 uint64_t inst_count=0;bool is_running=true;MMU mmu;
 bool require_fpu(uint32_t fault){if(msr&0x2000)return false;srr0=fault;srr1=msr&0x87C0FFFF;msr=(msr&~1u)|((msr>>16)&1u);msr&=~0x04EF36u;pc=0x800;return true;}
 struct Pair{double f,p;};
 Pair psq_load_pair(uint32_t a,int w,int){return {mmu.read_f32(a),w?1.:double(mmu.read_f32(a+4))};}
 void psq_store_val(double f,double p,uint32_t a,int w,int){mmu.write_f32(a,float(f));if(!w)mmu.write_f32(a+4,float(p));}
};
static bool g_trace_calls=false;
static void trace_call(uint32_t,CPUContext&){}
static void note_null_call(uint32_t,uint32_t){}
static void interpret_step(CPUContext& c){throw std::runtime_error("Unexpected interpreter");}
static unsigned phase=0,mode=0;
static bool observe(CPUContext& c){
 uint64_t h=1469598103934665603ull;auto mix=[&](uint64_t v){h=(h^v)*1099511628211ull;};
 for(auto v:c.gpr)mix(v);for(auto v:c.fpr)mix(std::bit_cast<uint64_t>(v));for(auto v:c.ps1)mix(std::bit_cast<uint64_t>(v));
 for(auto v:c.cr)mix(v.lt|(v.gt<<1)|(v.eq<<2)|(v.so<<3));
 mix(c.inst_count);mix(c.pc);mix(c.lr);mix(c.ctr);mix(c.xer);mix(c.msr);
 c.gpr[9]^=uint32_t(h)^uint32_t(h>>32);
 c.fpr[7]+=double(int32_t(c.gpr[9]&255));c.ps1[7]-=1.;
 if(mode==2 && phase%20==0)c.msr&=~0x2000u;
 if(mode==3 && phase%12==0)throw unsigned(1);
 if(mode==4 && phase%28==0){c.pc=0x81234568;return true;}
 return false;
}
static bool process_pending_callbacks(CPUContext& c){return (++phase%4)==0?observe(c):false;}
static bool native_watch_span_may_hit(uint32_t a,uint32_t){return mode==1&&(a&16);}
void native_call_indirect_cached(CPUContext&,uint32_t);
void native_region_dispatch_prepared(CPUContext&,uint32_t);
}
using namespace nwii::runtime;
#define NWII_CTX_PARAM CPUContext&
#define NWII_TRACE_CALL(a,c) ((void)0)
namespace native_guest_entry {static bool Enabled(){return true;}static void Record(bool){}}
namespace native_perf_census {static void GuestEntry(uint32_t,int){}static void GuestSwitch(uint32_t,int){}}
namespace native_region_poll {
enum class Ticket:uint32_t {Complete,Restart,Slow,SlowAfterWpad};
static Ticket Prepare(CPUContext&,uint32_t,uint64_t){return (++phase%4)==0?Ticket::Slow:Ticket::Complete;}
static bool Finish(CPUContext& c,Ticket t){return t==Ticket::Complete?false:observe(c);}
}
static const bool execution_region_scripted=false;
static unsigned fixture_entries[8]{};
static void ExecutionRegionObserve(CPUContext& c){asm volatile(""::"g"(&c):"memory");}
static bool ExecutionRegionOrdinary(uint32_t a,uint32_t b){return !native_watch_span_may_hit(a,b);}
DECLARATIONS
ORIGINAL_FUNCTIONS
namespace nwii::runtime {
void native_region_dispatch_prepared(CPUContext& c,uint32_t){
 if(c.pc==0||c.pc==0xFFFFFFFC)return;
 if((c.pc&0xF0000000)==0xC0000000)c.pc=(c.pc&0x0FFFFFFF)|0x80000000;
 else if((c.pc&0xF0000000)==0xD0000000)c.pc=(c.pc&0x0FFFFFFF)|0x90000000;
 else if(c.pc<0x80000000)c.pc|=0x80000000;
 ++c.inst_count;
 switch(c.pc){DISPATCH_CASES default:c.gpr[3]^=c.gpr[4]+17;c.pc=c.lr;break;}
}
void native_call_indirect_cached(CPUContext& c,uint32_t site){
 if(!c.is_running||!c.pc)return;process_pending_callbacks(c);native_region_dispatch_prepared(c,site);
}
}
REGION_FUNCTION
static uint64_t hash(uint64_t h,uint64_t v){return (h^v)*1099511628211ull;}
static std::vector<uint64_t> snapshot(const CPUContext& c){
 std::vector<uint64_t> out;
 for(auto v:c.gpr)out.push_back(v);for(auto v:c.fpr)out.push_back(std::bit_cast<uint64_t>(v));for(auto v:c.ps1)out.push_back(std::bit_cast<uint64_t>(v));
 for(auto v:c.cr)out.push_back(v.lt|(v.gt<<1)|(v.eq<<2)|(v.so<<3));
 for(auto v:{uint64_t(c.pc),uint64_t(c.lr),uint64_t(c.ctr),uint64_t(c.xer),uint64_t(c.msr),uint64_t(c.srr0),uint64_t(c.srr1),c.inst_count,uint64_t(phase)})out.push_back(v);
 uint64_t h=0;for(auto [a,v]:c.mmu.bytes)h=hash(hash(h,a),v);out.push_back(h);return out;
}
int main(){
 uint32_t seed=0x72656769;auto next=[&](){seed=seed*1664525+1013904223;return seed;};
 unsigned rows=0;
 for(mode=0;mode<5;++mode)for(unsigned i=0;i<400;++i){
  CPUContext original;
  for(auto& v:original.gpr)v=next();for(auto& v:original.fpr)v=double(int32_t(next()))/4096.;for(auto& v:original.ps1)v=double(int32_t(next()))/8192.;
  for(auto& v:original.cr)v={bool(next()&1),bool(next()&1),bool(next()&1),bool(next()&1)};
  original.gpr[1]=0x80180000;original.gpr[13]=0x80590000;original.gpr[3]=0x81000000;original.gpr[4]=next()&1;
  original.pc=0x803061E0;original.lr=0x80123458;original.msr=i%19==0?0x8032:0xa032;original.xer=next();original.inst_count=uint64_t(next())+1;
  auto& m=original.mmu;
  m.write32(original.gpr[13]-11036,0x81001000);m.write32(original.gpr[13]-29920,i&1?0x8100000c:0x81004000);
  m.write32(0x81000000,0x81002000);m.write32(0x81001000,0x81002000);
  m.write32(0x81000000+84,0x81003000);m.write32(0x81001000+84,0x81003000);
  m.write32(0x81000000+64,0x81004000);m.write32(0x81004000+24,i%7);
  m.write8(0x81000000+92,next());m.write8(0x81003004,next()&1);
  for(unsigned a=0;a<172;a+=4)m.write_f32(0x81003000+a,float(int32_t(next())%1000)/13.);
  m.write8(0x81003004,next()&1);
  auto alias=[&](uint32_t pc){return i%17==0?(pc|0x40000000u):i%19==0?(pc&0x7fffffffu):pc;};
  const uint32_t setter=alias(0x802924D8);
  m.write32(0x81002000+324,setter);m.write32(0x81002000+328,alias(0x802924E4));m.write32(0x81002000+336,alias(0x80292510));
  const uint32_t targets[]={0x802CC108,0x802CC1F0,0x802CB758,0x80292510,0x802924E4,0x81230000};
  m.write32(0x81002000+332,alias(targets[i%6]));m.write32(0x81002000+140,alias(targets[(i/6)%6]));
  m.write32(0x81002000+52,0x81230000);m.write32(0x81002000+56,0x81230000);m.write32(0x81002000+60,0x81230000);
  auto candidate=original;phase=i%16;try{func_803061E0(original);}catch(unsigned){}auto before=snapshot(original);
  phase=i%16;try{ExecutionRegion(candidate);}catch(unsigned){}auto after=snapshot(candidate);
  if(before!=after){
   std::cerr<<"Mismatch mode "<<mode<<" row "<<i<<"\n";
   for(unsigned n=0;n<before.size();++n)if(before[n]!=after[n])std::cerr<<n<<" "<<std::hex<<before[n]<<" "<<after[n]<<std::dec<<"\n";
   return 1;
  }++rows;
 }
 for(auto n:fixture_entries){if(!n){std::cerr<<"Missing admitted-owner coverage\n";return 2;}std::cout<<n<<" ";}
 std::cout<<"\nPASS "<<rows<<" complete-state/memory comparisons\n";
}
'''


def fixture(found,region):
    for index in range(8):
        target=f'loc_{0x5e000000+index*4:08X}: ;'
        if region.count(target)!=1:raise ValueError('Missing internal-entry test hook')
        region=region.replace(target,target+f'\n    ++fixture_entries[{index}];')
    combined='\n'.join(found.values())
    calls=set(re.findall(r'\b(func_[0-9A-F]{8}|native_guest_resume_[0-9A-F]{8})\(ctx\)',combined))
    names={'func_'+a for a in found}
    declarations='\n'.join('void '+name+'(CPUContext&);' for name in sorted(names))
    for name in sorted(calls-names):
        declarations+='\nvoid '+name+'(CPUContext& c){c.gpr[3]^=c.gpr[4]+17;c.pc=c.lr;}'
    return MODEL.replace('DECLARATIONS',declarations).replace('ORIGINAL_FUNCTIONS',combined).replace(
        'DISPATCH_CASES','\n'.join(f'case 0x{a}:func_{a}(c);break;' for a in found)).replace('REGION_FUNCTION',region)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base',type=Path,required=True);ap.add_argument('--corrected',type=Path,required=True);ap.add_argument('--work',type=Path,required=True)
    args=ap.parse_args();work=args.work.resolve()
    if work==E.PORT or E.PORT in work.parents:raise ValueError('Artifacts must be outside repo')
    work.mkdir(parents=True,exist_ok=True)
    found,_=E.sources(args.base,args.corrected);region,_,_=E.generate(found)
    cpp=work/'fixture.cpp';cpp.write_text(fixture(found,region));compiler=shutil.which('g++')
    for option in ('-Os','-O2'):
        exe=work/('fixture'+option+'.exe')
        result=subprocess.run([compiler,'-std=c++20',option,'-fwrapv','-fno-strict-aliasing','-ffp-contract=off',str(cpp),'-o',str(exe)],capture_output=True,text=True)
        (work/('compile'+option+'.log')).write_text(result.stdout+result.stderr)
        if result.returncode:raise ValueError(result.stderr[-5000:])
        result=subprocess.run([exe],capture_output=True,text=True,timeout=90)
        (work/('result'+option+'.log')).write_text(result.stdout+result.stderr)
        print(option,result.stdout+result.stderr,flush=True)
        if result.returncode:raise ValueError('Execution-region differential mismatch')
