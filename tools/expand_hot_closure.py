"""One bounded call-graph hop; preserve the measured hot list independently."""
from pathlib import Path
import re,json,collections
def expand(root,hot):
 root=Path(root);excluded=set(json.loads((root/'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
 bodies={};hotset=set(hot);counts=collections.Counter()
 for file in sorted((root/'build/crash-generated').glob('output_*.cpp')):
  if file.name=='output_74.cpp':continue
  s=file.read_text();heads=list(re.finditer(r'^void (func_[0-9A-F]{8})\(NWII_CTX_PARAM ctx\) \{$',s,re.M))
  for i,m in enumerate(heads):
   name=m[1]
   if name[5:] in excluded:continue
   end=s.find('\n}',m.start())+2;body=s[m.start():end];bodies[name]=body
   if name in hotset:counts.update(re.findall(r'\b(func_[0-9A-F]{8})\(ctx\)',body))
 selected=[]
 for name,n in counts.most_common():
  if name in hotset or name not in bodies or n<8:continue
  body=bodies[name]
  if body.count('\n')>1200:continue
  selected.append(dict(name=name,call_sites=n,lines=body.count('\n')))
  if len(selected)==64:break
 (root/'reports/hot-closure.json').write_text(json.dumps(dict(one_hop=True,min_sites=8,max_functions=64,max_lines=1200,excluded_frozen=True,selected=selected),indent=2)+'\n')
 return [r['name'] for r in selected]
