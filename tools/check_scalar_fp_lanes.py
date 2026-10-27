"""Actual emitted scalar-single lane writes, finite exactly representable constants and aliases.

Oracle: Dolphin's Interpreter_FloatingPoint.cpp Fill(result) for single operations/frsp.
This does not certify FPSCR, estimate tables, or rounding edge cases.
"""
from pathlib import Path
from port_paths import default_storage
import json,subprocess,re,struct
PORT=Path(__file__).resolve().parents[1];BASE=default_storage()
SRC=PORT/'build/nwii-full-source';BUILD=BASE/'toolchain';WORK=BASE/'scalar-fp-lanes';WORK.mkdir(exist_ok=True)
def run(cmd):
    r=subprocess.run(list(map(str,cmd)),capture_output=True,text=True)
    if r.returncode:raise SystemExit((r.stdout+r.stderr)[-4000:])
    return r.stdout
forms=[('fadds',59,21,3,.75,.25,3.75),('fsubs',59,20,3,.75,.25,2.25),('fmuls',59,25,3,.75,.25,.75),('fdivs',59,18,3,.75,.25,4),('fsqrts',59,22,3,9,.25,3),('fres',59,24,3,4,.25,.25),('fmadds',59,29,3,.75,.25,1.5),('fmsubs',59,28,3,.75,.25,0.),('fnmadds',59,31,3,.75,.25,-1.5),('fnmsubs',59,30,3,.75,.25,-0.),('frsp',63,12,3,1.25,.25,1.25)]
cases=[(name,op,xo,a,b,c,e,d) for name,op,xo,a,b,c,e in forms for d in (1,2,3,4)]
emit=WORK/'emit.exe'
run(['g++','-std=c++20','-O0','-static','-I',SRC/'nWiiRecomp/include','-I',SRC/'nWiiAnalyzer/include','-I',SRC/'nWiiRuntime/include',PORT/'tools/nwii_emit.cpp',BUILD/'nWiiRecomp/libnwiirecomp_lib.a',BUILD/'nWiiAnalyzer/libnwiianalyzer.a',BUILD/'nWiiRuntime/libnwiiruntime.a','-o',emit])
inputs=[]
for i,(name,op,xo,a,b,c,e,d) in enumerate(cases):
    w=(op<<26)|(d<<21)|(3<<11)|(xo<<1)
    if op==59:w|=(2<<16)|(4<<6)
    inputs.append(f'{0x81110000+i*16:x} {w:08x} 4e800020')
(WORK/'input.txt').write_text(chr(10).join(inputs)+chr(10));run([emit,WORK/'input.txt',WORK/'generated.cpp'])
parts=(WORK/'generated.cpp').read_text().split('void func_')[1:]
body=[];expected=[]
def bits(x):return struct.unpack('>Q',struct.pack('>d',x))[0]
for i,((name,op,xo,a,b,c,e,d),part) in enumerate(zip(cases,parts)):
    expression=next(x.strip() for x in part.splitlines() if x.strip().endswith('// '+name))
    body.append(f'ctx.fpr[2]={float(a).hex()};ctx.fpr[3]={float(b).hex()};ctx.fpr[4]={float(c).hex()};ctx.ps1[{d}]=42.5;'+expression)
    body.append(f'std::cout<<std::bit_cast<uint64_t>(ctx.fpr[{d}])<<" "<<std::bit_cast<uint64_t>(ctx.ps1[{d}])<<std::endl;')
    expected.append(bits(e))
cpp='#include <array>\n#include <bit>\n#include <cmath>\n#include <cstdint>\n#include <iostream>\nstruct Context{std::array<double,32> fpr{},ps1{};};int main(){Context ctx;\n'+chr(10).join(body)+'\n}'
(WORK/'test.cpp').write_text(cpp);run(['g++','-std=c++20','-O1','-static',WORK/'test.cpp','-o',WORK/'test.exe'])
rows=[tuple(map(int,x.split())) for x in run([WORK/'test.exe']).splitlines()]
checks=[{'form':cases[i][0],'destination':cases[i][-1],'ps0_pass':v[0]==expected[i],'ps1_pass':v[1]==expected[i]} for i,v in enumerate(rows)]
result={'source':'https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/Core/PowerPC/Interpreter/Interpreter_FloatingPoint.cpp','assertions':len(checks)*2,'passed':sum(x['ps0_pass']+x['ps1_pass'] for x in checks),'checks':checks,'scope':'finite constants and lane replication with register aliases; no FPSCR/rounding/estimation-table certification'}
(PORT/'reports/scalar-fp-lane-tests.json').write_text(json.dumps(result,indent=2)+'\n');print('Scalar FP lane assertions:',result['passed'],'/',result['assertions'])
