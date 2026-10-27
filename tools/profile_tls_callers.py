"""Attribute emulated TLS sampled stacks to their immediate native caller."""
import bisect,collections,csv,json,re,subprocess,sys
from pathlib import Path
p=Path(sys.argv[1]);proc=json.loads((p/'cpu-profile-process.json').read_text(encoding='utf-8-sig'))
syms=[]
for line in (p/'cpu-profile-symbols.txt').read_text().splitlines():
    m=re.match(r'([0-9a-fA-F]+) [tT] (.+)',line)
    if m:syms.append((int(m[1],16),m[2]))
syms.sort();addresses=[a for a,n in syms];callers=collections.Counter()
for row in csv.DictReader((p/'cpu-decoded.stacks.csv').open()):
    names=[]
    for w in row['ips'].split():
        ip=int(w,16)
        if proc['base_address']<=ip<proc['base_address']+proc['module_size']:
            i=bisect.bisect_right(addresses,ip-proc['base_address']+0x140000000)-1
            names.append(syms[i][1] if i>=0 else '?')
    for i,name in enumerate(names):
        if name=='__emutls_get_address':
            callers[int(row['tid']),names[i+1] if i+1<len(names) else '<unwound>']+=1;break
names=sorted({n for t,n in callers});r=subprocess.run(['c++filt'],input='\n'.join(names)+'\n',capture_output=True,text=True,check=True);dem=dict(zip(names,r.stdout.splitlines()))
out=[dict(tid=t,caller=dem[n],symbol=n,samples=v) for (t,n),v in callers.most_common()]
(p/'cpu-decoded.tls-callers.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out[:25],indent=2))
