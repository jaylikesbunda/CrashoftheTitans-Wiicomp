"""Actual publish/drain handoffs, leftovers and early-drain notification witnesses."""
from pathlib import Path
from port_paths import default_storage
import json,os,re,statistics,subprocess
p=Path(__file__).resolve().parents[1];w=default_storage()/'fifo-transfer-tests';w.mkdir(exist_ok=True)
prefix=r'''
#include <vector>
#include <mutex>
#include <atomic>
#include <chrono>
#include <iostream>
#include <cstdlib>
namespace native_trace {struct Scope{Scope(const char*){}};}
std::vector<uint8_t> g_cpu_fifo,g_hw_fifo,s_parse_buf;
std::mutex g_fifo_mutex;std::atomic<size_t> g_gx_pending_bytes{0};std::atomic<bool> g_gx_drain_request{false};
struct Notify{unsigned count=0;void notify_one(){++count;}}g_gx_drain_cv;
// Notification witness only; production locking is tested by check_drain_protocol.
void NativeRequestGxDrain(){if(!g_gx_drain_request.exchange(true,std::memory_order_acq_rel))g_gx_drain_cv.notify_one();}
'''
main=r'''
int main(){uint64_t hash=1469598103934665603ull;auto add=[&](uint64_t v){hash=(hash^v)*1099511628211ull;};uint32_t rng=123;unsigned checks=0;
for(unsigned trial=0;trial<20000;++trial){rng=rng*1664525u+1013904223u;const size_t n=rng%5000;for(unsigned i=0;i<n;++i)g_cpu_fifo.push_back(uint8_t(i^rng));native_gx_publish();
if(trial%3==0){drain();const size_t consumed=s_parse_buf.size()*(trial%5)/4;for(size_t i=0;i<consumed;++i)add(s_parse_buf[i]);s_parse_buf.erase(s_parse_buf.begin(),s_parse_buf.begin()+consumed);}
add(g_cpu_fifo.size());add(g_hw_fifo.size());add(g_gx_pending_bytes.load());add(g_gx_drain_request.load());add(g_gx_drain_cv.count);++checks;if(trial%7==0)g_gx_drain_request=false;}
native_gx_publish();drain();for(auto v:s_parse_buf)add(v);std::cout<<"FIFO_TRANSFER_STATE hash="<<hash<<" checks="<<checks<<'\n';
for(unsigned size:{128u,4096u,65536u})for(unsigned repeat=0;repeat<5;++repeat){std::vector<uint8_t> packet(size);for(unsigned i=0;i<size;++i)packet[i]=i;g_cpu_fifo.clear();g_hw_fifo.clear();s_parse_buf.clear();uint64_t sum=0;auto start=std::chrono::steady_clock::now();
for(unsigned i=0;i<10000;++i){g_cpu_fifo.assign(packet.begin(),packet.end());native_gx_publish();drain();sum+=s_parse_buf.front()+s_parse_buf.back();s_parse_buf.clear();}
std::cout<<"FIFO_TRANSFER_TIME bytes="<<size<<" us="<<std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-start).count()<<" hash="<<sum<<'\n';}}
'''
rows=[];outputs={}
for version,path in [('before',p/'runtime-overlays/fifo-transfer-before.cpp'),('after',p/'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp')]:
    s=path.read_text();a=s.index('void native_gx_publish() {');b=s.index('\nstatic inline void wgp_push(',a);publish=s[a:b]
    a=s.index('void ProcessGXFifo() {');a=s.index('    {\n        std::lock_guard',a);b=s.index('    if (s_parse_buf.empty()) return;',a);drain='void drain(){\n'+s[a:b]+'}\n'
    helper=''
    if version=='after':a=s.index('// NATIVE_FIFO_OWNERSHIP_TRANSFER_V1');b=s.index('void ProcessGXFifo()',a);helper=s[a:b];helper=helper[:helper.index('// NATIVE_PARALLEL_DECODE_V1')] if '// NATIVE_PARALLEL_DECODE_V1' in helper else helper
    cpp=w/(version+'.cpp');exe=w/(version+'.exe');cpp.write_text(prefix+helper+publish+drain+main)
    cmd=['g++','-std=c++20','-O2','-static',str(cpp),'-o',str(exe)]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=120);rows.append(dict(version=version,command=cmd,exit_code=r.returncode,output=r.stdout+r.stderr));print(r.stdout+r.stderr,flush=True)
    if r.returncode:raise SystemExit(r.returncode)
    for early in (False,True):
        env={k:v for k,v in os.environ.items() if not k.startswith('NWII_')}
        if early:env['NWII_GX_EARLY_DRAIN']='1'
        elif version=='after':env['NWII_NO_GX_EARLY_DRAIN']='1' # staged default is early drain on
        r=subprocess.run([str(exe)],capture_output=True,text=True,timeout=120,env=env);rows.append(dict(version=version,early_drain=early,exit_code=r.returncode,output=r.stdout+r.stderr));(p/'reports/fifo-transfer-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print((r.stdout+r.stderr)[-2400:],flush=True)
        if r.returncode:raise SystemExit(r.returncode)
        outputs[(version,early)]=r.stdout
for early in (False,True):assert re.search('FIFO_TRANSFER_STATE.*',outputs[('before',early)])[0]==re.search('FIFO_TRANSFER_STATE.*',outputs[('after',early)])[0]
results=[]
for size in (128,4096,65536):
    times=[statistics.median(map(int,re.findall('FIFO_TRANSFER_TIME bytes='+str(size)+r' us=(\d+)',outputs[(k,False)]))) for k in ('before','after')]
    results.append(dict(bytes=size,before_us=times[0],after_us=times[1],speedup=times[0]/times[1]))
(p/'reports/fifo-transfer-speedups.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
