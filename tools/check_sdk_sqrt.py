"""Qualify sqrt shortcuts against actual translated code, without a game run."""
from pathlib import Path
import ast,json,re,subprocess
import guest_baselines
from port_paths import default_storage
p=Path(__file__).resolve().parents[1]
work=default_storage()/'sdk-sqrt-tests'
work.mkdir(exist_ok=True)
s=(p/'build/crash-generated/output_73.cpp').read_text()
baseline=guest_baselines.path('sdk_sqrt_baseline.cpp')
if 'NATIVE_SQRT_LOCAL_LOOPS_V1' in s:s=baseline.read_text()
a=s.index('void func_803A35F0(CPUContext& ctx) {');b=s.find('\nvoid func_',a+1)
original=s[a:b if b>=0 else len(s)]
tree=ast.parse((p/'tools/check_runtime_repairs.py').read_text())
stubs=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='stubs' for t in n.targets))
# Prototype: scalar register locals within one pure-integer loop iteration.
# Every original poll and interior entry stays available; no skipped instruction
# counts, altered math, or assumed ABI-clobber behavior.
helpers=[];candidate=original
for start,end in [('803A36F4','803A3724'),('803A3730','803A379C')]:
    a=original.index('loc_'+start+': ;');b=original.index('loc_'+end+': ;')
    body=original[a:b];assert 'process_pending_callbacks' not in body and 'mmu.' not in body
    regs=sorted(set(map(int,re.findall(r'ctx.gpr\[(\d+)\]',body))))
    before=''.join(f'uint32_t r{i}=ctx.gpr[{i}];' for i in regs)
    after=''.join(f'ctx.gpr[{i}]=r{i};' for i in regs)
    body=re.sub(r'ctx.gpr\[(\d+)\]',lambda m:'r'+m[1],body).replace('ctx.cr[0]','cr').replace('ctx.inst_count','count')
    body=body.replace('loc_','local_')
    helpers.append('static inline void loop_'+start+'(CPUContext& ctx){'+before+'auto cr=ctx.cr[0];auto count=ctx.inst_count;\n'+body+'\n'+after+'ctx.cr[0]=cr;ctx.inst_count=count; }\n')
    anchor='loc_'+start+': ;'
    candidate=candidate.replace(anchor,anchor+'\n loop_'+start+'(ctx);goto loc_'+end+';',1)
candidate=candidate.replace('func_803A35F0','candidate_sqrt')
main=r'''
static int budget=0,hits=0;static uint64_t pollhash=0;static bool qualify=true;
namespace nwii::runtime {
bool process_pending_callbacks(CPUContext& c){++hits;if(qualify){pollhash=pollhash*31+c.pc+c.inst_count+c.xer;for(auto r:c.gpr)pollhash=pollhash*31+r;for(auto cr:c.cr)pollhash=pollhash*31+cr.lt+2*cr.gt+4*cr.eq+8*cr.so;}return budget&&hits%budget==0;}
}
int main(){CPUContext c; c.msr=0x2000;c.gpr[2]=0x80020000;c.gpr[13]=0x80024000;
c.mmu.write_f64(c.gpr[2]-14984,1.0);c.mmu.write32(0x805935d0,0x7fc00000);
auto reset=[&](uint64_t bits){for(int i=0;i<32;++i){c.gpr[i]=0x10000000+i;c.fpr[i]=i+.25;c.ps1[i]=i+.75;}
c.gpr[1]=0x80018000;c.gpr[2]=0x80020000;c.gpr[13]=0x80024000;c.pc=0x803A35F0;c.lr=0x81234560;
c.fpr[1]=std::bit_cast<double>(bits);c.inst_count=17;c.xer=0xa0000000;hits=0;pollhash=0;
for(auto&cr:c.cr){cr.lt=true;cr.gt=false;cr.eq=true;cr.so=true;}for(int i=0;i<48;++i)c.mmu.write8(0x80017fe0+i,0xab);c.mmu.write32(c.gpr[13]-10816,123);};
auto digest=[&](){uint64_t h=1469598103934665603ull;auto add=[&](uint64_t v){h=(h^v)*1099511628211ull;};
for(auto r:c.gpr)add(r);for(auto r:c.fpr)add(std::bit_cast<uint64_t>(r));for(auto r:c.ps1)add(std::bit_cast<uint64_t>(r));
for(auto cr:c.cr){add(cr.lt);add(cr.gt);add(cr.eq);add(cr.so);}add(c.xer);add(c.pc);add(c.lr);add(c.inst_count);add(c.msr);add(c.fpscr);add(hits);add(pollhash);
for(int i=0;i<48;++i)add(c.mmu.read8(0x80017fe0+i));add(c.mmu.read32(c.gpr[13]-10816));return h;};
std::vector<uint64_t> inputs={0,0x8000000000000000ull,1,2,0xfffffffffffffull,0x10000000000000ull,0x3ff0000000000000ull,0x4000000000000000ull,0x7fefffffffffffffull,0x7ff0000000000000ull,0xfff0000000000000ull,0xbff0000000000000ull,0x7ff8000000000001ull};
uint64_t rng=12345;for(int i=0;i<20000;++i){rng^=rng<<13;rng^=rng>>7;rng^=rng<<17;inputs.push_back(rng);}
unsigned failed=0,total=0,math_bad=0,math_total=0,hle_state_diff=0;
for(int suspension:{0,7,19}){budget=suspension;for(auto bits:inputs){reset(bits);int guard=0;do{func_803A35F0(c);}while(c.pc!=c.lr&&++guard<10000);
auto ref=digest();auto result=std::bit_cast<uint64_t>(c.fpr[1]);if(!suspension && bits<0x7ff0000000000000ull){++math_total;if(result!=std::bit_cast<uint64_t>(std::sqrt(std::bit_cast<double>(bits))))++math_bad;}
reset(bits);guard=0;do{candidate_sqrt(c);}while(c.pc!=c.lr&&++guard<10000);++total;if(digest()!=ref){if(failed++<5)std::cout<<"MISMATCH "<<std::hex<<bits<<std::dec<<" suspension "<<suspension<<std::endl;}
if(!suspension && bits==0x4000000000000000ull){reset(bits);c.fpr[1]=std::sqrt(c.fpr[1]);c.pc=c.lr;if(digest()!=ref)++hle_state_diff;}
}}
std::cout<<"STATES "<<total-failed<<"/"<<total<<" MATH "<<math_total-math_bad<<"/"<<math_total<<" NAIVE_HLE_STATE_DIFFERENCES "<<hle_state_diff<<std::endl;
budget=0;qualify=false;for(int mode:{0,1,2}){volatile uint64_t checksum=0;auto begin=std::chrono::steady_clock::now();
for(int i=0;i<100000;++i){c.fpr[1]=double(i)+.25;c.gpr[1]=0x80018000;c.pc=0x803A35F0;if(mode==0)func_803A35F0(c);else if(mode==1)candidate_sqrt(c);else c.fpr[1]=std::sqrt(c.fpr[1]);checksum=checksum^std::bit_cast<uint64_t>(c.fpr[1]);}
std::cout<<"TIMING "<<mode<<" US "<<std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-begin).count()<<" CHECKSUM "<<checksum<<std::endl;}
return failed?1:0;}
'''
cpp=work/'test.cpp';exe=work/'test.exe'
cpp.write_text('#include "runtime/cpu_context.h"\n#include <bit>\nusing nwii::runtime::CPUContext;\n'+stubs+original+'\n'+''.join(helpers)+candidate+main)
rows=[]
for name,cmd in [('compile',['g++','-std=c++20','-O2','-fwrapv','-fno-strict-aliasing','-ffp-contract=off','-static','-I',str(p/'build/nwii-full-source/nWiiRuntime/include'),str(cpp),'-o',str(exe)]),('execute',[str(exe)])]:
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=180);rows.append(dict(stage=name,exit_code=r.returncode,output=r.stdout+r.stderr));print(rows[-1],flush=True)
    (p/'data/sdk-sqrt-tests.json').write_text(json.dumps(rows,indent=2))
    if r.returncode:raise SystemExit(r.returncode)
(work/'qualified_helpers.inc').write_text(''.join(helpers))
guest_baselines.path('sdk_sqrt_local_loops.inc').write_text(''.join(helpers))
print('Record this helper hash in tools/fix_sdk_sqrt_loops.py HELPERS_SHA256:',guest_baselines.sha256(''.join(helpers)))
