"""Build/run production runtime regressions without launching CrashNative."""
from pathlib import Path
from port_paths import default_storage
import argparse,json,subprocess,os
P=Path(__file__).resolve().parents[1];B=default_storage()/'toolchain'
a=argparse.ArgumentParser();a.add_argument('--name',required=True);a.add_argument('--traced',action='store_true');v=a.parse_args()
p=P/'build/nwii-full-source/CMakeLists.txt';s=p.read_text();marker='# Native deterministic wakeup fixture'
if marker not in s:
    s+='\n'+marker+'\nadd_executable(crash_deterministic_wakeup_tests "'+(P/'tools/deterministic_wakeup_tests.cpp').as_posix()+'")\ntarget_link_libraries(crash_deterministic_wakeup_tests PRIVATE nwiiruntime)\n';p.write_text(s)
env=os.environ.copy()
for name in ('NWII_WALLTB','NWII_SAMPLE','NWII_WPAD_SCRIPT','NWII_WPAD_LIVE','NWII_FAKE_PAD'):env.pop(name,None)
env['NWII_NO_DIAG']='1'
env.pop('NWII_TRACED_WAKEUP_FIXTURE',None)
env.pop('NWII_FRAME_TRACE',None)
if v.traced:
    env['NWII_TRACED_WAKEUP_FIXTURE']='1'
    env['NWII_FRAME_TRACE']=str(B/'traced-wakeup-fixture')
results=[]
for cmd in [['cmake','--build',str(B),'--target','crash_deterministic_wakeup_tests','-j','6'],[str(B/'crash_deterministic_wakeup_tests.exe')]]:
    p=subprocess.run(cmd,capture_output=True,text=True,env=env);print(p.returncode,p.stdout+p.stderr,flush=True)
    results.append(dict(command=cmd,exit_code=p.returncode,output=p.stdout+p.stderr))
    (P/'reports'/('deterministic-wakeups-'+v.name+'.json')).write_text(json.dumps(results,indent=2)+'\n')
    if p.returncode:raise SystemExit(p.returncode)
