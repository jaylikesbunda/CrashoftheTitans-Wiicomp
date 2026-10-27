"""Actual headers with production default watch: hits, aliases, widths and throughput."""
from pathlib import Path
from port_paths import default_storage
import ast,json,re,shutil,statistics,subprocess
p=Path(__file__).resolve().parents[1];w=default_storage()/'watch-span-tests';w.mkdir(exist_ok=True)
inc=w/'before/runtime';inc.mkdir(parents=True,exist_ok=True);shutil.copyfile(p/'runtime-overlays/watch-span-before.h',inc/'cpu_context.h')
tree=ast.parse((p/'tools/check_inline_gather_mmio.py').read_text());fixture=next(ast.literal_eval(n.args[0]) for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='write_text');prefix=fixture[:fixture.index('int main()')]
code=prefix+r'''
int main(){MMU m;uint64_t hash=1469598103934665603ull;auto add=[&](uint64_t v){hash=(hash^v)*1099511628211ull;};uint32_t seed=127;unsigned checks=0;
const uint32_t watches[]={0,0x80597790,0x00597790,0xc0597790,0x90597790,0xcc008004,0xcc004000,0x817ffffc,0x80000000,0x8000000c,0x3ffffffc};
for(unsigned trial=0;trial<440000;++trial){seed=seed*1664525u+1013904223u;g_watch_addr=watches[trial%std::size(watches)];uint32_t a;
switch((trial/11)%8){case 0:a=0x80000000u+(seed&0x3ffff);break;case 1:a=0x90000000u+(seed&0x3ffff);break;case 2:a=(g_watch_addr&0x3fffffffu)+uint32_t(int((trial/88)%25)-12);break;case 3:a=0x80000000u+((g_watch_addr&0x3fffffffu)+uint32_t(int((trial/88)%25)-12));break;case 4:a=0xcc008000+(seed&31);break;case 5:a=0x817ffff8+(seed&15);break;case 6:a=0x93fffff8+(seed&15);break;default:a=seed;}
uint64_t bits=uint64_t(seed)<<32|(seed^0x7ff01234u);
switch((trial/11)%6){case 0:m.write8(a,bits);break;case 1:m.write16(a,bits);break;case 2:m.write32(a,bits);break;case 3:m.write64(a,bits);break;case 4:m.write_f32(a,std::bit_cast<float>(uint32_t(bits)));break;default:m.write_f64(a,std::bit_cast<double>(bits));}
add(m.read64(a));add(observations);++checks;}
for(auto v:m.mem1)add(v);for(auto v:m.mem2)add(v);std::cout<<"WATCH_SPAN_STATE checks="<<checks<<" hash="<<hash<<" observer="<<observations<<'\n';
g_watch_addr=0x80597790;timing=true;
for(unsigned mode=0;mode<3;++mode)for(unsigned repeat=0;repeat<5;++repeat){uint64_t sum=0;uint32_t rng=123;auto start=std::chrono::steady_clock::now();
for(unsigned i=0;i<5000000;++i){rng=rng*1664525+1013904223;uint32_t a=(mode==0?0x80000000u:0x90000000u)+(rng&0x3ffff);
if(mode==2){m.write32(0xcc008000,rng);sum+=fifo_count;}else{m.write32(a,rng);sum+=m.read32((mode==0?0x80000000u:0x90000000u)+((rng>>3)&0x3ffff));}}
std::cout<<"WATCH_SPAN_TIME mode="<<mode<<" us="<<std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-start).count()<<" sum="<<sum<<'\n';}}
'''
cpp=w/'test.cpp';cpp.write_text(code);rows=[];outputs={}
for version in ('before','after'):
 exe=w/(version+'.exe');cmd=['g++','-std=c++20','-O2','-fwrapv','-fno-strict-aliasing','-ffp-contract=off','-static']
 if version=='before':cmd+=['-I',str(w/'before')]
 cmd+=['-I',str(p/'build/nwii-full-source/nWiiRuntime/include'),str(cpp),'-o',str(exe)]
 for command in (cmd,[str(exe)]):
  r=subprocess.run(command,capture_output=True,text=True,timeout=180);rows.append(dict(version=version,command=command,exit_code=r.returncode,output=r.stdout+r.stderr));(p/'reports/watch-span-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print((r.stdout+r.stderr)[-2100:],flush=True)
  if r.returncode:raise SystemExit(r.returncode)
 outputs[version]=r.stdout
assert re.search('WATCH_SPAN_STATE.*',outputs['before'])[0]==re.search('WATCH_SPAN_STATE.*',outputs['after'])[0]
results=[]
for mode in range(3):
 times=[statistics.median(map(int,re.findall('WATCH_SPAN_TIME mode='+str(mode)+r' us=(\d+)',outputs[k]))) for k in ('before','after')]
 results.append(dict(mode=['MEM1','MEM2','gather_wrapper'][mode],watch='0x80597790',before_us=times[0],after_us=times[1],speedup=times[0]/times[1]))
(p/'reports/watch-span-speedups.json').write_text(json.dumps(results,indent=2)+'\n');print(results)
