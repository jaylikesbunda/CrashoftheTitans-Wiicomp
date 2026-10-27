"""Compile synthetic ISA witnesses through the patched emitter and assert results."""
from pathlib import Path
from port_paths import default_storage
import argparse, hashlib, json, subprocess
PORT=Path(__file__).resolve().parents[1];ROOT=PORT.parent
ap=argparse.ArgumentParser();ap.add_argument('--storage',type=Path,default=default_storage());args=ap.parse_args()
base=args.storage.resolve();work=base/'repair-tests';work.mkdir(exist_ok=True)
src=PORT/'build/nwii-full-source';build=base/'toolchain';emitter=work/'emit.exe'
stages=[]
def run(name,cmd):
    p=subprocess.run([str(a) for a in cmd],capture_output=True,cwd=ROOT)
    text=(p.stdout+p.stderr).decode(errors='replace')
    stages.append({'stage':name,'exit_code':p.returncode,'output':text,'command':[str(a) for a in cmd]})
    (PORT/'reports/runtime-repair-tests.json').write_text(json.dumps(stages,indent=2))
    if p.returncode:raise SystemExit(text)
    return text
run('emitter-build',['g++','-std=c++20','-O0','-static','-I',src/'nWiiRecomp/include','-I',src/'nWiiAnalyzer/include','-I',src/'nWiiRuntime/include',PORT/'tools/nwii_emit.cpp',build/'nWiiRecomp/libnwiirecomp_lib.a',build/'nWiiAnalyzer/libnwiianalyzer.a',build/'nWiiRuntime/libnwiiruntime.a','-o',emitter])
fixtures=[];main='''int main(){CPUContext c;c.msr=0x2000;int failed=0,total=0;
auto check=[&](bool ok,const char*n){++total;if(!ok)++failed;std::cout<<(ok?"PASS ":"FAIL ")<<n<<std::endl;};
'''
for n,(xo,W) in enumerate((x,w) for x in (6,7,38,39) for w in (0,1)):
    addr=0x81002000+n*0x100;word=4<<26|1<<21|4<<16|5<<11|W<<10|1<<7|xo<<1
    fixtures.append(f'{addr:X} {word:08X} 4E800020')
    load=xo in (6,38);update=xo>=38
    main+=f'c.pc=0x{addr:X};c.lr=0x81234560;c.gpr[4]=0x80000100;c.gpr[5]=0x20;c.gqr[1]=0;c.fpr[1]=2.5;c.ps1[1]=9;'
    if load:main+='c.mmu.write_f32(0x80000120,3.5);c.mmu.write_f32(0x80000124,7);'
    else:main+='c.mmu.write32(0x80000124,0xDEADBEEF);'
    main+=f'func_{addr:X}(c);'
    cond=f'c.fpr[1]==3.5&&c.ps1[1]=={1 if W else 7}' if load else 'c.mmu.read_f32(0x80000120)==2.5&&'+('c.mmu.read32(0x80000124)==0xDEADBEEF' if W else 'c.mmu.read_f32(0x80000124)==9')
    main+=f'check({cond},"indexed_{xo}_W{W}_lanes");check(c.gpr[4]==0x{0x80000120 if update else 0x80000100:X}&&c.pc==c.lr,"indexed_{xo}_W{W}_state");\n'
store=0x81002300;load=0x81002100 # store W1 and load W1, both use GQR1
main+=f'''c.pc=0x{store:X};c.gpr[4]=0x80000100;c.gpr[5]=0x20;c.gqr[1]=(63u<<8)|4u;c.fpr[1]=6;c.ps1[1]=10;func_{store:X}(c);
check(c.mmu.read8(0x80000120)==3,"negative_scale_store");
c.pc=0x{load:X};c.gqr[1]=(63u<<24)|(4u<<16);func_{load:X}(c);check(c.fpr[1]==6&&c.ps1[1]==1,"negative_scale_load");
'''
# RA=0 is the zero base for non-update indexed addressing.
for n,xo in enumerate((7,6)):
    addr=0x81004000+n*0x100;word=4<<26|1<<21|5<<11|1<<10|xo<<1
    fixtures.append(f'{addr:X} {word:08X} 4E800020')
main+='''c.gpr[0]=0x11111111;c.gpr[5]=0x80000800;c.gqr[0]=0;c.fpr[1]=11.5;c.pc=0x81004000;func_81004000(c);
check(c.mmu.read_f32(0x80000800)==11.5,"indexed_zero_base_store");
c.fpr[1]=0;c.pc=0x81004100;func_81004100(c);check(c.fpr[1]==11.5&&c.ps1[1]==1,"indexed_zero_base_load");
'''
fixtures+=['81005000 90640000','81005004 90A40004','81005008 4E800020']
# Independent addic/addic. expectations: 32-bit operand plus sign-extended
# immediate, with carry out of bit 31 (not carry out of a signed 64-bit sum).
for op in (12,13):
    for imm in (-1,-32768,1,32767):
        addr=0x81006000+len(fixtures)*0x10
        word=op<<26|3<<21|4<<16|(imm&0xffff)
        fixtures.append(f'{addr:X} {word:08X} 4E800020')
        for value in (0,1,0x7fffffff,0x80000000,0xffffffff):
            total=value+(imm&0xffffffff);result=total&0xffffffff;carry=int(total>0xffffffff)
            main+=f'c.gpr[4]={value}u;c.xer=0x80000000u;c.pc=0x{addr:X};func_{addr:X}(c);'
            condition=f'c.gpr[3]=={result}u&&((c.xer>>29)&1)=={carry}&&((c.xer>>31)&1)==1'
            if op==13:
                condition+=f'&&c.cr[0].eq=={int(result==0)}&&c.cr[0].lt=={int(result>=0x80000000)}&&c.cr[0].gt=={int(0<result<0x80000000)}&&c.cr[0].so'
            main+=f'check({condition},"addic_{op}_{imm}_{value}");\n'
# The exact original comparison idiom must return false for equal CRCs.
fixtures+=['81008000 3003FFFF 7C601910 4E800020']
for value in (0,1,0xffffffff,0x80000000):
    main+=f'c.gpr[3]={value}u;c.pc=0x81008000;func_81008000(c);check(c.gpr[3]=={int(value!=0)},"crc_not_equal_{value}");\n'
main+='''c.gpr[3]=17;c.gpr[5]=42;c.gpr[4]=0x80001000;c.pc=0x81005000;c.lr=0x81234560;
func_81005000(c);check(c.pc==0x81005004&&c.mmu.read32(0x80001000)==17,"boundary_fallthrough_first");
func_81005004(c);check(c.pc==0x81005008&&c.mmu.read32(0x80001004)==42,"boundary_fallthrough_second");
func_81005008(c);check(c.pc==c.lr,"boundary_return");
std::cout<<"RESULT "<<total-failed<<"/"<<total<<" passed"<<std::endl;return failed?1:0;}
'''
inputs=work/'input.txt';outputs=work/'generated.cpp';inputs.write_text('\n'.join(fixtures)+'\n')
run('synthetic-generation',[emitter,inputs,outputs])
stubs='''namespace nwii::runtime {
bool g_trace_calls=false;uint32_t g_watch_addr=0;
void trace_call(uint32_t,CPUContext&){}void interpret_step(CPUContext&){std::abort();}
void watch_hit(uint32_t,uint32_t,int){std::abort();}
void GX_WGPIPE_Write8(uint8_t){std::abort();}void GX_WGPIPE_Write16(uint16_t){std::abort();}
void GX_WGPIPE_Write32(uint32_t){std::abort();}void GX_WGPIPE_WriteF32(float){std::abort();}void GX_WGPIPE_WriteF64(double){std::abort();}
uint16_t HW_Reg_Read16(uint32_t){std::abort();}uint32_t HW_Reg_Read32(uint32_t){std::abort();}
void HW_Reg_Write16(uint32_t,uint16_t){std::abort();}void HW_Reg_Write32(uint32_t,uint32_t){std::abort();}
}
'''
cpp=work/'test.cpp';cpp.write_text('#include "runtime/cpu_context.h"\nusing nwii::runtime::CPUContext;\n'+stubs+outputs.read_text()+main)
exe=work/'test.exe';run('synthetic-compile',['g++','-std=c++20','-O2','-static','-I',src/'nWiiRuntime/include',cpp,'-o',exe])
print(run('synthetic-execution',[exe]))
# Regeneration checks the source patches and incremental text edits agree.
fresh=base/'reemit-check';current=PORT/'build/crash-generated'
paths=list(current.glob('output_*.cpp'))+[current/'main_output.cpp']
same=all(hashlib.sha256(p.read_bytes()).digest()==hashlib.sha256((fresh/p.name).read_bytes()).digest() for p in paths)
result={'files_compared':len(paths),'byte_identical_regeneration':same}
(PORT/'reports/regeneration-check.json').write_text(json.dumps(result,indent=2));print(result)
if not same:raise SystemExit('Regeneration differs; investigate before relying on patches')
