"""Remove the dispatcher-only O0 override after executing its return fixtures."""
from pathlib import Path
import json
p=Path(__file__).resolve().parents[1]
r=json.loads((p/'data/dispatcher-o2-tests.json').read_text())
assert len(r)==2 and all(x['exit_code']==0 for x in r) and r[0]['state']==r[1]['state']
for path in (p/'build/crash-generated/main_output.cpp',p/'build/nwii-full-source/nWiiRecomp/src/recompiler.cpp'):
    s=path.read_text()
    if path.name=='main_output.cpp':
        s=s.replace('#pragma GCC optimize ("O0")','#pragma GCC optimize ("O2")').replace('#pragma clang optimize off','#pragma clang optimize on')
    else:
        s=s.replace('#pragma GCC optimize ('+chr(92)+'"O0'+chr(92)+'")','#pragma GCC optimize ('+chr(92)+'"O2'+chr(92)+'")').replace('#pragma clang optimize off','#pragma clang optimize on')
    if s!=path.read_text():path.write_text(s)
path=p/'tools/build_native.py';s=path.read_text()
if "run('native-dispatcher-o2-stage'" not in s:
    anchor="run('native-game-build',";assert s.count(anchor)==1
    s=s.replace(anchor,"run('native-dispatcher-o2-stage',[sys.executable,PORT/'tools/fix_dispatcher_optimization.py'])\n"+anchor,1);path.write_text(s)
print('Dispatcher uses qualified O2; no game logic/timer changes.')
