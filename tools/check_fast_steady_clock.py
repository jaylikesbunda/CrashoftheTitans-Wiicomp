"""Bracket original/wrapped clock readings, compare quantization, and time calls offline."""
from pathlib import Path
from port_paths import default_storage
import subprocess,json,os
p=Path(__file__).resolve().parents[1];w=default_storage()/'fast-clock-tests';w.mkdir(exist_ok=True)
s=r'''
#include <chrono>
#include <cstdio>
#include <cassert>
#include <thread>
#include <vector>
extern "C" std::chrono::steady_clock::time_point __real__ZNSt6chrono3_V212steady_clock3nowEv();
int main(){using C=std::chrono::steady_clock;unsigned checks=0;
for(unsigned i=0;i<100000;++i){auto before=__real__ZNSt6chrono3_V212steady_clock3nowEv();auto value=C::now();auto after=__real__ZNSt6chrono3_V212steady_clock3nowEv();assert(before-std::chrono::nanoseconds(1000)<=value&&value<=after+std::chrono::nanoseconds(1000));++checks;}
auto epoch=__real__ZNSt6chrono3_V212steady_clock3nowEv();for(unsigned i=0;i<100000;++i){auto before=__real__ZNSt6chrono3_V212steady_clock3nowEv();auto value=C::now();auto after=__real__ZNSt6chrono3_V212steady_clock3nowEv();
auto us=[](auto t){return std::chrono::duration_cast<std::chrono::microseconds>(t).count();};assert(us(before-epoch)-1<=us(value-epoch)&&us(value-epoch)<=us(after-epoch)+1);++checks;}
std::vector<std::thread> ts;for(unsigned i=0;i<8;++i)ts.emplace_back([](){auto previous=C::now();for(unsigned j=0;j<100000;++j){auto next=C::now();assert(next>=previous);previous=next;}});for(auto& t:ts)t.join();
long long worst=0;unsigned drift_samples=0;auto drift_start=__real__ZNSt6chrono3_V212steady_clock3nowEv();
while(__real__ZNSt6chrono3_V212steady_clock3nowEv()-drift_start<std::chrono::seconds(4)){auto before=__real__ZNSt6chrono3_V212steady_clock3nowEv();auto value=C::now();auto after=__real__ZNSt6chrono3_V212steady_clock3nowEv();
long long error=value<before?(before-value).count():(value>after?(value-after).count():0);if(error>worst)worst=error;++drift_samples;auto pause=__real__ZNSt6chrono3_V212steady_clock3nowEv();while(__real__ZNSt6chrono3_V212steady_clock3nowEv()-pause<std::chrono::microseconds(150)){}}
assert(worst<=1000);
std::printf("CLOCK_BRACKETS %u/%u; concurrent800000 monotonic reads pass; drift_samples=%u worst_outside_bracket_ns=%lld\n",checks,checks,drift_samples,worst);
for(unsigned repeat=0;repeat<3;++repeat){volatile long long sum=0;auto a=__real__ZNSt6chrono3_V212steady_clock3nowEv();for(unsigned i=0;i<1000000;++i)sum=sum^__real__ZNSt6chrono3_V212steady_clock3nowEv().time_since_epoch().count();auto mid=__real__ZNSt6chrono3_V212steady_clock3nowEv();for(unsigned i=0;i<1000000;++i)sum=sum^C::now().time_since_epoch().count();auto end=__real__ZNSt6chrono3_V212steady_clock3nowEv();
std::printf("CLOCK_BENCH before_us=%lld after_us=%lld checksum=%lld\n",(long long)std::chrono::duration_cast<std::chrono::microseconds>(mid-a).count(),(long long)std::chrono::duration_cast<std::chrono::microseconds>(end-mid).count(),(long long)sum);}
}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(s);rows=[]
commands=[(['g++','-std=c++20','-O2','-static',str(cpp),str(p/'runtime-overlays/steady_clock_fast.cpp'),'-Wl,--wrap,_ZNSt6chrono3_V212steady_clock3nowEv','-o',str(exe)],None),([str(exe)],False),([str(exe)],True)]
for cmd,fallback in commands:
    env=os.environ.copy();env.pop('NWII_NO_FAST_CLOCK',None)
    if fallback:env['NWII_NO_FAST_CLOCK']='1'
    r=subprocess.run(cmd,capture_output=True,text=True,env=env);rows.append(dict(command=cmd,fallback=fallback,exit_code=r.returncode,output=r.stdout+r.stderr));(p/'reports/fast-clock-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print(rows[-1]['output'],flush=True)
    if r.returncode:raise SystemExit(r.returncode)
