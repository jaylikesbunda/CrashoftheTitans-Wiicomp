"""Exercise actual staged PE/MMIO code with a fixture-only race barrier."""
from pathlib import Path
from port_paths import default_storage
import argparse, json, subprocess
p=argparse.ArgumentParser();p.add_argument('--label',required=True);args=p.parse_args()
port=Path(__file__).resolve().parents[1]
root=port/'build/nwii-full-source/nWiiRuntime'
work=default_storage()/'pe-interrupt-tests'
work.mkdir(exist_ok=True)
source=(root/'src/hw/hw_pe.cpp').read_text()
needle='        clear_pi_interrupt(0x400);'
assert source.count(needle)==1
source=source.replace(needle,'        { fixture_before_finish_clear(); clear_pi_interrupt(0x400); }')
(work/'fixture_pe.cpp').write_text(source)
compiler=json.loads((port/'reports/texture-sampler-tests.json').read_text())[0]['command'][0]
exe=work/'test.exe'
cmd=[compiler,'-std=c++17','-O1','-pthread','-I'+str(root/'include'),'-I'+str(work),str(port/'tools/pe_interrupt_tests.cpp'),str(root/'src/hw/mmio_dispatcher.cpp'),'-o',str(exe)]
rows=[]
for stage, command in [('compile',cmd),('execute',[str(exe)])]:
    run=subprocess.run(command,capture_output=True,text=True,timeout=90)
    rows.append(dict(stage=stage,command=command,exit_code=run.returncode,output=run.stdout+run.stderr))
    print(stage,run.returncode,run.stdout+run.stderr)
    if stage=='compile' and run.returncode:break
(port/f'reports/pe-interrupt-{args.label}.json').write_text(json.dumps(rows,indent=2)+'\n')
raise SystemExit(rows[-1]['exit_code'])
