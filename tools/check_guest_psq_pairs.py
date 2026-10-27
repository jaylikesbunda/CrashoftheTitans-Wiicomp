"""Current paired-single helpers against the pre-change production bodies."""
from pathlib import Path
from port_paths import default_storage
import ast,subprocess,json,os
p=Path(__file__).resolve().parents[1];base=default_storage();w=base/'guest-psq-pair-tests';w.mkdir(exist_ok=True)
tree=ast.parse((p/'tools/probe_guest_psq_pairs.py').read_text());code=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='code' for t in n.targets))
a=code.index('__attribute__((noinline)) CPUContext::NativePsqPair LegacyLoad');b=code.index('std::vector<uint8_t> TakeFifo()',a)
replacement=(p/'reference/guest-regions/psq-pair-baseline.inc').read_text()+r'''
__attribute__((noinline)) CPUContext::NativePsqPair LegacyLoad(CPUContext& c,uint32_t a){return LegacyPsqLoad(c,a,0,0);}
__attribute__((noinline)) CPUContext::NativePsqPair PairLoad(CPUContext& c,uint32_t a){return c.psq_load_pair(a,0,0);}
__attribute__((noinline)) void LegacyStore(CPUContext& c,uint32_t a,double f,double q){LegacyPsqStore(c,f,q,a,0,0);}
__attribute__((noinline)) void PairStore(CPUContext& c,uint32_t a,double f,double q){c.psq_store_val(f,q,a,0,0);}
'''
code=code[:a]+replacement+code[b:]
# W=1 and every GQR index/type/scale keep their scalar semantics.
a=code.index('// Include split publication')
code=code[:a]+r'''for(unsigned trial=0;trial<20000;++trial){uint32_t type=trial%8,index=(trial/8)%8,one=(trial/64)%2,addr=0x80014000+trial%1024;c.gqr[index]=(type<<16)|((trial%64)<<24)|type|((trial%64)<<8);
for(unsigned j=0;j<8;++j)c.mmu.get_ptr(addr)[j]=uint8_t(rng());auto a=LegacyPsqLoad(c,addr,one,index),b=c.psq_load_pair(addr,one,index);
if(std::bit_cast<uint64_t>(a.f)!=std::bit_cast<uint64_t>(b.f)||std::bit_cast<uint64_t>(a.p)!=std::bit_cast<uint64_t>(b.p))return 5;++checks;
// Bounded finite store values avoid implementation-undefined integer casts.
const int shift=int(trial%64);const int signed_shift=(shift&32)?shift-64:shift;const double x=type==0?.125:std::ldexp(1.0,-signed_shift),y=type==0?-.25:std::ldexp(2.0,-signed_shift);LegacyPsqStore(c,x,y,addr,one,index);uint8_t before[8];memcpy(before,c.mmu.get_ptr(addr),8);c.psq_store_val(x,y,addr,one,index);if(memcmp(before,c.mmu.get_ptr(addr),8))return 6;++checks;}
'''+r'''// Watched stores must retain scalar watch handling; redirected gather must
// retain its original RAM destination, WPAR position and empty GPU stream.
for(unsigned trial=0;trial<128;++trial){
 const uint32_t addr=0x80018000+(trial%8);c.gqr[0]=0;g_watch_addr=addr+(trial%2)*4;
 LegacyStore(c,addr,.125,-.25);uint8_t expected[8];memcpy(expected,c.mmu.get_ptr(addr),8);
 memset(c.mmu.get_ptr(addr),0,8);PairStore(c,addr,.125,-.25);
 if(memcmp(expected,c.mmu.get_ptr(addr),8))return 7;++checks;
 g_watch_addr=0;const uint32_t dest=0x80019000+(trial%4)*32;
 native_wpar_write(dest);if(g_native_fifo_ok)return 8;
 LegacyStore(c,0xcc008000,.125,-.25);memcpy(expected,c.mmu.get_ptr(dest),8);
 const auto position=native_wpar_read();auto before=TakeFifo();
 memset(c.mmu.get_ptr(dest),0,8);native_wpar_write(dest);
 PairStore(c,0xcc008000,.125,-.25);auto after=TakeFifo();
 if(memcmp(expected,c.mmu.get_ptr(dest),8)||position!=native_wpar_read()||before!=after||!after.empty())return 9;
 ++checks;native_wpar_write(0x0c008000);
}
'''+code[a:]
cpp=w/'fixture.cpp';cpp.write_text(code);cm=p/'build/nwii-full-source/CMakeLists.txt';s=cm.read_text();marker='# NATIVE_GUEST_PSQ_PAIR_TEST_V1'
if marker not in s:cm.write_text(s+'\n'+marker+'\nadd_executable(crash_guest_psq_pairs "'+cpp.as_posix()+'")\ntarget_link_libraries(crash_guest_psq_pairs PRIVATE nwiiruntime)\ntarget_compile_options(crash_guest_psq_pairs PRIVATE -Os -mfma -fwrapv -fno-strict-aliasing -ffp-contract=off)\n')
runs=[]
for cmd in (['cmake','--build',str(base/'toolchain'),'--target','crash_guest_psq_pairs','-j','6'],[str(base/'toolchain/crash_guest_psq_pairs.exe')]):
 env={k:v for k,v in os.environ.items() if not k.startswith('NWII_')};env['NWII_NO_DIAG']='1';x=subprocess.run(cmd,capture_output=True,text=True,timeout=900,env=env);runs.append(dict(command=cmd,exit_code=x.returncode,output=x.stdout+x.stderr));print((x.stdout+x.stderr)[-3500:],flush=True)
 if x.returncode:break
(p/'reports/guest-psq-pair-tests.json').write_text(json.dumps(dict(runs=runs,game_launched=False),indent=2)+'\n');raise SystemExit(x.returncode)
