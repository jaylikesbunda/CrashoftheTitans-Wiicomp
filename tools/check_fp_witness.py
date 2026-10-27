"""Decisive emitted double fmadd/fmsub rounding witnesses with an exact Fraction oracle.

This is a bounded audit, not FPSCR/paired-single fidelity certification.
"""
from pathlib import Path
from port_paths import default_storage
from fractions import Fraction
import json, subprocess, struct
PORT=Path(__file__).resolve().parents[1]
BASE=default_storage()
SRC=PORT/'build/nwii-full-source';BUILD=BASE/'toolchain';WORK=BASE/'fp-witness';WORK.mkdir(exist_ok=True)
def run(cmd):
    r=subprocess.run(list(map(str,cmd)),capture_output=True,text=True)
    if r.returncode:raise SystemExit((r.stdout+r.stderr)[-4000:])
    return r.stdout
emit=WORK/'emit.exe'
run(['g++','-std=c++20','-O0','-static','-I',SRC/'nWiiRecomp/include','-I',SRC/'nWiiAnalyzer/include','-I',SRC/'nWiiRuntime/include',PORT/'tools/nwii_emit.cpp',BUILD/'nWiiRecomp/libnwiirecomp_lib.a',BUILD/'nWiiAnalyzer/libnwiianalyzer.a',BUILD/'nWiiRuntime/libnwiiruntime.a','-o',emit])
forms=[('fmadd',29),('fmsub',28),('fnmadd',31),('fnmsub',30)]
(WORK/'input.txt').write_text(''.join(f'{0x81001000+i*0x10:08x} {(63<<26)|(1<<21)|(2<<16)|(3<<11)|(4<<6)|(xo<<1):08x} 4e800020\n' for i,(_,xo) in enumerate(forms)))
run([emit,WORK/'input.txt',WORK/'generated.cpp'])
generated=(WORK/'generated.cpp').read_text()
# Extract emitted arithmetic only; call dispatch scaffolding is outside this arithmetic audit.
expressions={name:next(line.strip() for line in generated.splitlines() if line.endswith('// '+name)) for name,_ in forms}
a=1+2**-27;c=1-2**-27
def bits(x):return struct.unpack('>Q',struct.pack('>d',x))[0]
expected={}
for name,_ in forms:
    b=-1.0 if name in ('fmadd','fnmadd') else 1.0
    exact=Fraction(a)*Fraction(c)+(Fraction(b) if name in ('fmadd','fnmadd') else -Fraction(b))
    if name.startswith('fn'):exact=-exact
    expected[name]=bits(float(exact))
body='\n'.join('ctx.fpr[2]='+a.hex()+';ctx.fpr[4]='+c.hex()+';ctx.fpr[3]='+('-1.0' if name in ('fmadd','fnmadd') else '1.0')+';'+expr+'\nstd::cout<<"'+name+' "<<std::hex<<std::bit_cast<uint64_t>(ctx.fpr[1])<<"\\n";' for name,expr in expressions.items())
(WORK/'test.cpp').write_text('#include <array>\n#include <bit>\n#include <cmath>\n#include <cstdint>\n#include <iostream>\nstruct Context{std::array<double,32> fpr{};};\nint main(){Context ctx;\n'+body+'\n}\n')
run(['g++','-std=c++20','-O1','-ffp-contract=off','-static',WORK/'test.cpp','-o',WORK/'test.exe'])
actual={n:int(v,16) for n,v in (line.split() for line in run([WORK/'test.exe']).splitlines())}
result={'oracle':'Fraction exact product/addition, rounded once to binary64; finite cancellation witnesses only',
        'compiler':'O1, ffp-contract=off; exposes missing explicit fusion independent of optional host contraction',
        'emitted':expressions,'checks':[{'form':n,'expected_hex':hex(expected[n]),'actual_hex':hex(actual[n]),'pass':expected[n]==actual[n]} for n,_ in forms]}
(PORT/'reports/fp-rounding-witness.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
