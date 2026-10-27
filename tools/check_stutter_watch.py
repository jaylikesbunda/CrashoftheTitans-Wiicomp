"""Deterministic timing/workload injection: attribution, bounded history and no baseline poisoning."""
from pathlib import Path
from port_paths import default_storage
import json,subprocess
p=Path(__file__).resolve().parents[1];w=default_storage()/'stutter-tests';w.mkdir(exist_ok=True)
code='#include "'+(p/'runtime-overlays/native_stutter.h').as_posix()+'"\n#include <iostream>\n'+r'''
int main(){using namespace native_stutter;unsigned checks=0,failed=0;auto check=[&](bool ok,const char* n){++checks;if(!ok){++failed;std::cout<<"FAIL "<<n<<'\n';}};
History h;uint64_t stamp=0;Work normal{100000,90000,7600000,400,500,2};Cost c[]={{"gx.vertex_pack",'R',5,5},{"gx.decode",'R',4,4},{"guest.idle_wait",'G',8,8}};
auto feed=[&](double ms,Work w){stamp+=uint64_t(ms*1000);return h.observe({stamp,ms,20,24,w},c,3);};
for(unsigned i=0;i<60;++i)check(!feed(33,normal),"steady frames do not report spikes");check(h.ready(),"baseline warm");
c[0].ms=16;Work heavy=normal;heavy.vertices*=2;heavy.upload_bytes*=2;heavy.shader_compiles=3;heavy.shader_binary_hits=2;heavy.shader_source_hits=7;
heavy.guest_wait={3,27,0,7100,420,170};heavy.field_wait={5,9,2,8000,2400,400};heavy.guest_context.stamp=stamp;heavy.guest_context.pc=0x803bbfa0;heavy.guest_context.count=1;heavy.guest_context.threads[0]={0x80020000,4,0x80340000,0x803bb008};
check(feed(65,heavy),"isolated spike");check(h.latest()->increases[0].name==c[0].name,"largest increase attributed");
check(h.latest()->usual.vertices==100000 && h.latest()->sample.work.vertices==200000,"workload comparison retained");
check(h.baseline()<34,"spike does not poison baseline");check(h.report().find("gx.vertex_pack")!=std::string::npos,"export includes scope");
auto spike=h.json();std::cout<<"SPIKE_JSON "<<spike<<'\n';
c[0].ms=5;for(unsigned i=0;i<40;++i)feed(33,normal);
h.reset();stamp=0;for(unsigned i=0;i<60;++i)feed(33,normal);for(unsigned i=0;i<14;++i)feed(40,normal);
check(h.latest() && h.latest()->sustained,"sustained 20 percent drop detected");
check(h.summary().find("FPS DROP")!=std::string::npos,"overlay describes sustained drop");
for(unsigned i=0;i<500;++i)feed(70,heavy);
auto bounded=h.json();std::cout<<"BOUNDED_JSON "<<bounded<<'\n';
h.reset();check(!h.ready() && !h.latest(),"F9 resets history and baseline");
for(unsigned i=0;i<60;++i)check(!feed(60,normal),"slow but steady scene learns its own baseline");
std::cout<<"STUTTER_WATCH "<<checks-failed<<'/'<<checks<<'\n';return failed?1:0;}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code);rows=[]
for cmd in (['g++','-std=c++20','-O2','-static',str(cpp),'-o',str(exe)],[str(exe)]):
 r=subprocess.run(cmd,capture_output=True,text=True,timeout=120);rows.append(dict(command=cmd,exit_code=r.returncode,output=r.stdout+r.stderr));print((r.stdout+r.stderr)[-2000:],flush=True)
 if r.returncode:break
if not r.returncode:
 for line in r.stdout.splitlines():
  if line.startswith('SPIKE_JSON '):
   s=json.loads(line[11:]);assert len(s['events'])==1 and s['events'][0]['increases'][0]['scope']=='gx.vertex_pack';assert s['events'][0]['shader_compiles']==3 and s['frames'][-1]['shader_binary_hits']==2 and s['frames'][-1]['shader_source_hits']==7
   assert s['events'][0]['guest_wait']['max_late_us']==170 and s['frames'][-1]['field_wait']['other']==2 and s['events'][0]['guest_context']['threads'][0]['saved_pc']==0x803bb008
  if line.startswith('BOUNDED_JSON '):s=json.loads(line[13:]);assert len(s['events'])==16 and len(s['frames'])==240
(p/'reports/stutter-watch-tests.json').write_text(json.dumps(dict(game_launched=False,runs=rows),indent=2)+'\n');raise SystemExit(r.returncode)
