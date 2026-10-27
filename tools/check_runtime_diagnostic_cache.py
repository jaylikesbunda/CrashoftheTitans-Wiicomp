"""Measure actual write_dec code and verify guest state, flags set/unset."""
from pathlib import Path
from port_paths import default_storage
import json,os,re,subprocess
PORT=Path(__file__).resolve().parents[1]
work=default_storage()/'diagnostic-cache-tests'
work.mkdir(exist_ok=True)
header=(PORT/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h').read_text()
before=re.sub(r'(?:#include <cstdlib>\n)?// NATIVE_DIAGNOSTIC_CACHE_BEGIN\n.*?// NATIVE_DIAGNOSTIC_CACHE_END\n','',header,flags=re.S)
before=before.replace('NativeLaunchFlag_NWII_SAMPLE()','std::getenv("NWII_SAMPLE")')
body=r'''
#include "context.h"
#include <iostream>
int main() {
 nwii::runtime::CPUContext ctx;ctx.pc=0x80001234;ctx.lr=0x81234560;
 constexpr unsigned count=250000;
 auto start=std::chrono::steady_clock::now();
 for(unsigned i=0;i<count;++i) {
   ctx.inst_count=i;auto lo=ctx.read_timebase();ctx.write_dec(i);auto hi=ctx.read_timebase();
   if(ctx.dec_value!=i||ctx.dec_written_tb<lo||ctx.dec_written_tb>hi||!ctx.dec_irq_pending) return 1;
 }
 auto elapsed=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-start).count();
 ctx.write_dec(0xffffffff);if(ctx.dec_irq_pending||ctx.pc!=0x80001234||ctx.lr!=0x81234560||ctx.inst_count!=count-1)return 2;
 std::cout<<"PASS "<<count<<" checks us="<<elapsed<<"\n";return 0;
}
'''
results=[]
for version,source in [('before',before),('cached',header)]:
    folder=work/version;folder.mkdir(exist_ok=True)
    (folder/'context.h').write_text(source);(folder/'test.cpp').write_text(body)
    exe=folder/'test.exe'
    cmd=['g++','-std=c++20','-O1','-static','-I',str(PORT/'build/nwii-full-source/nWiiRuntime/include'),str(folder/'test.cpp'),'-o',str(exe)]
    p=subprocess.run(cmd,capture_output=True,text=True)
    if p.returncode:raise SystemExit(p.stdout+p.stderr)
    for sample in (False,True):
        for wall in (False,True):
            env=os.environ.copy()
            for name in ('NWII_SAMPLE','NWII_WALLTB'):env.pop(name,None)
            if sample:env['NWII_SAMPLE']='1'
            if wall:env['NWII_WALLTB']='1'
            p=subprocess.run([str(exe)],capture_output=True,text=True,env=env)
            result=dict(version=version,sample=sample,wall_timebase=wall,exit_code=p.returncode,output=p.stdout+p.stderr)
            results.append(result);print(version,sample,wall,p.returncode,p.stdout.splitlines()[-1] if p.stdout else p.stderr,flush=True)
            if p.returncode:raise SystemExit(p.returncode)
(PORT/'reports/runtime-diagnostic-cache-tests.json').write_text(json.dumps({'scope':'Actual header write_dec; flags fixed at launch. Per-operation decrementer/timebase/context checks. Timing is a microbenchmark, not scene FPS.','results':results},indent=2)+'\n')
