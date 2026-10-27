"""Build and run actual idle/wakeup checks, including bounded CPU-use comparison."""
from pathlib import Path
from port_paths import default_storage
import json,os,subprocess
p=Path(__file__).resolve().parents[1];b=default_storage()/'toolchain'
cm=p/'build/nwii-full-source/CMakeLists.txt';s=cm.read_text();marker='# Guest idle production fixture'
if marker not in s:
    s+='\n'+marker+'\nadd_executable(crash_guest_idle_tests "'+(p/'tools/guest_idle_wait_tests.cpp').as_posix()+'")\ntarget_link_libraries(crash_guest_idle_tests PRIVATE nwiiruntime)\ntarget_compile_options(crash_guest_idle_tests PRIVATE -O2 -fwrapv -fno-strict-aliasing -ffp-contract=off)\n'
    cm.write_text(s)
env=os.environ.copy()
for k in list(env):
    if k.startswith('NWII_'):env.pop(k)
env['NWII_WALLTB']='1';env['NWII_NO_DIAG']='1';rows=[]
def run(cmd):
    r=subprocess.run(list(map(str,cmd)),capture_output=True,text=True,env=env)
    rows.append(dict(command=list(map(str,cmd)),idle_enabled='NWII_NO_IDLE_WAIT' not in env,exit_code=r.returncode,output=r.stdout+r.stderr))
    (p/'reports/guest-idle-tests.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(rows[-1]['output'][-2600:],flush=True)
    if r.returncode:raise SystemExit(r.returncode)
run(['cmake','--build',b,'--target','crash_guest_idle_tests','-j','6'])
env['NWII_NO_IDLE_WAIT']='1';run([b/'crash_guest_idle_tests.exe'])
env.pop('NWII_NO_IDLE_WAIT');run([b/'crash_guest_idle_tests.exe'])
