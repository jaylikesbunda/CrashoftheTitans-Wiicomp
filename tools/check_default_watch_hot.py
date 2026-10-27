"""Same production hot routine, old/new MMU headers, actual default watch."""
from pathlib import Path
from port_paths import default_storage
import json,re,subprocess,statistics
p=Path(__file__).resolve().parents[1];root=default_storage();w=root/'default-watch-hot-tests';w.mkdir(exist_ok=True)
source=(root/'bounded-hot-tests/test.cpp').read_text()
# That qualified fixture compares original and bounded routines; use the same
# current production routine in both modes to isolate the header change.
a=source.index('void candidate(CPUContext& ctx) {');i=source.index('{',a)+1;depth=1
while depth:depth+=(source[i]=='{')-(source[i]=='}');i+=1
candidate=source[a:i];a=source.index('void reference_803DB050(CPUContext& ctx) {');j=source.index('{',a)+1;depth=1
while depth:depth+=(source[j]=='{')-(source[j]=='}');j+=1
source=source[:a]+candidate.replace('void candidate(', 'void reference_803DB050(',1)+source[j:]
source=source.replace('uint32_t g_watch_addr=0;','uint32_t g_watch_addr=0x80597790u;')
source=source.replace('unsigned checks=0;for(unsigned suspension:', 'uint64_t state_digest=0;unsigned checks=0;for(unsigned suspension:')
source=source.replace('auto reference=digest();reset(seed);','auto reference=digest();state_digest=(state_digest^reference)*1099511628211ull;reset(seed);')
source=source.replace('std::cout<<"HOT_STATE_POLL "', 'std::cout<<"DEFAULT_WATCH_DIGEST "<<state_digest<<"\\n";std::cout<<"HOT_STATE_POLL "')
# The support headers include the MMU guard selected by include-path precedence.
cpp=w/'test.cpp';cpp.write_text(source);rows=[];outputs={}
for version in ('before','after'):
 exe=w/(version+'.exe');cmd=['g++','-std=c++20','-O2','-fwrapv','-fno-strict-aliasing','-ffp-contract=off','-static']
 if version=='before':cmd+=['-I',str(root/'watch-span-tests/before')]
 cmd+=['-I',str(p/'build/nwii-full-source/nWiiRuntime/include'),str(cpp),'-o',str(exe)]
 for command in (cmd,[str(exe)]):
  r=subprocess.run(command,capture_output=True,text=True,timeout=240);rows.append(dict(version=version,command=command,exit_code=r.returncode,output=r.stdout+r.stderr));(p/'reports/default-watch-hot-tests.json').write_text(json.dumps(rows,indent=2)+'\n');print((r.stdout+r.stderr)[-2000:],flush=True)
  if r.returncode:raise SystemExit(r.returncode)
 outputs[version]=r.stdout
# Both internal state/callback comparisons pass. Whole routine throughput is a
# component measurement, not an estimate of its current captured time share.
assert re.search('DEFAULT_WATCH_DIGEST.*',outputs['before'])[0]==re.search('DEFAULT_WATCH_DIGEST.*',outputs['after'])[0],'Cross-header state mismatch'
times=[statistics.median(map(int,re.findall(r'HOT_TIME mode=\d us=(\d+)',outputs[k]))) for k in ('before','after')]
report=dict(watch='0x80597790',address='803DB050',before_us=times[0],after_us=times[1],speedup=times[0]/times[1],game_launched=False)
(p/'reports/default-watch-hot-speedup.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
