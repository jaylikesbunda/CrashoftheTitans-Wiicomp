"""Optimize translated chunks after bounded O1/O2 equivalence qualification."""
from pathlib import Path
import json
p=Path(__file__).resolve().parents[1]
report=json.loads((p/'data/o2-translation-tests.json').read_text())
assert report.get('passed') and len(report['fixtures'])>=4
for path in (p/'tools/setup_full_build.py',p/'build/nwii-full-source/CMakeLists.txt'):
    s=path.read_text()
    old='-O1;-fwrapv;-fno-strict-aliasing'
    new='-O2;-fwrapv;-fno-strict-aliasing;-ffp-contract=off'
    assert old in s or new in s,path
    s=s.replace(old,new).replace('Optimize every generated chunk (-O1)','Optimize every generated chunk (-O2)')
    # Leave earlier per-source O1 declarations alone: all-chunk block overrides them.
    if path.read_text()!=s:path.write_text(s)
path=p/'tools/build_native.py';s=path.read_text()
if "run('native-o2-translation-stage'" not in s:
    anchor="run('native-game-build',";assert s.count(anchor)==1
    path.write_text(s.replace(anchor,"run('native-o2-translation-stage',[sys.executable,PORT/'tools/fix_o2_translation.py'])\n"+anchor,1))
print('All translated chunks use O2, wrapping arithmetic, no strict aliasing, no implicit FP contraction.')
