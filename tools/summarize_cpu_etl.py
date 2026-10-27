"""Resolve offline ETW instruction samples against the exact captured PE symbols."""
import argparse,bisect,collections,csv,json,re,subprocess
from pathlib import Path
from cpu_capture_quality import quality

def symbol_group(name):
    """Disjoint leaf groups; inclusive stacks must never enter these totals."""
    if name.startswith('<external'):return 'external'
    if re.search(r'\b(?:interpret_one|interpret_step)\(',name):return 'interpreter'
    if re.match(r'func_[0-9A-Fa-f]{8}\b',name):return 'func_*'
    if name.startswith('native_guest_'):return 'native_guest_*'
    if name.startswith('native_block_'):return 'native_block_*'
    if name.startswith('nwii::runtime::'):return 'nwii::runtime::*'
    if name.startswith('<'):return 'unresolved native'
    return 'other host helpers'

def aggregate_symbols(functions,total):
    counts=collections.Counter()
    for row in functions:counts[symbol_group(row['name'])]+=row['samples']
    if sum(counts.values())!=total:raise ValueError('Leaf symbol groups do not cover all thread samples')
    return [dict(group=group,samples=n,share=n/total if total else 0) for group,n in counts.most_common()]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('capture',type=Path);args=ap.parse_args();p=args.capture
    meta=json.loads((p/'cpu-decoded.meta.json').read_text())
    capture_quality=quality(p,meta)
    proc=json.loads((p/'cpu-profile-process.json').read_text(encoding='utf-8-sig'))
    symbols=[]
    for line in (p/'cpu-profile-symbols.txt').read_text().splitlines():
        m=re.match(r'([0-9a-fA-F]+) [tT] (.+)',line)
        if m:symbols.append((int(m[1],16),m[2]))
    symbols.sort();addresses=[a for a,n in symbols]
    counts=collections.Counter();totals=collections.Counter();external=collections.Counter()
    modules=[(int(r['base']),int(r['size']),r['path']) for r in csv.DictReader((p/'cpu-decoded.modules.csv').open()) if int(r['pid']) in (0,proc['pid'])]
    module_counts=collections.Counter()
    for row in csv.DictReader((p/'cpu-decoded.samples.csv').open()):
        tid=int(row['tid']);ip=int(row['ip'],16);n=int(row['samples']);totals[tid]+=n
        if proc['base_address']<=ip<proc['base_address']+proc['module_size']:
            preferred=ip-proc['base_address']+0x140000000;i=bisect.bisect_right(addresses,preferred)-1
            name=symbols[i][1] if i>=0 else '<before first symbol>'
            counts[tid,name]+=n
        else:
            module=next((path for base,size,path in modules if base<=ip<base+size),'unresolved')
            counts[tid,'<external module>']+=n;external[tid,ip]+=n;module_counts[tid,module]+=n
    names=sorted({name for tid,name in counts if not name.startswith('<')})
    demangled={}
    if names:
        r=subprocess.run(['c++filt'],input='\n'.join(names)+'\n',capture_output=True,text=True,check=True)
        demangled=dict(zip(names,r.stdout.splitlines()))
    schedule=list(csv.DictReader((p/'cpu-decoded.schedule.csv').open()))
    inclusive=collections.Counter();stack_totals=collections.Counter()
    for row in csv.DictReader((p/'cpu-decoded.stacks.csv').open()):
        tid=int(row['tid']);stack_totals[tid]+=1;seen=set()
        for word in row['ips'].split():
            ip=int(word,16)
            if proc['base_address']<=ip<proc['base_address']+proc['module_size']:
                a=ip-proc['base_address']+0x140000000;i=bisect.bisect_right(addresses,a)-1
                if i>=0:seen.add(symbols[i][1])
        for name in seen:inclusive[tid,name]+=1
    stack_names=sorted({n for t,n in inclusive})
    if stack_names:
        r=subprocess.run(['c++filt'],input='\n'.join(stack_names)+'\n',capture_output=True,text=True,check=True)
        demangled.update(zip(stack_names,r.stdout.splitlines()))
    frequency=meta['frequency'];lines=['# Independent CPU profile','',f'Events: {meta["events"]}; target samples: {meta["target_samples"]}; lost events: {meta["events_lost"]}; lost buffers: {meta["buffers_lost"]}; unsupported layouts: {meta["unsupported_layouts"]}.','',
        'Samples measure CPU execution, not elapsed function latency. External DLL symbols are not yet resolved. Scheduling covers observed switch intervals; the first partial interval is excluded.','', '| Thread | Samples | Scheduled CPU ms | Off CPU ms |','|---|---:|---:|---:|']
    if (p/'profile-window.json').is_file():
        lines.insert(2,'This is an independent OS sampling window, not an XFB/frame-aligned capture. Use separate F4 statistics for frame-rate comparisons.')
    if capture_quality['loss_reported']:
        lines[2:2]=['WARNING: WPR reported '+str(capture_quality['wpr_dropped_events'])+' dropped events. CPU hotspots are directional; missing sample gaps do not establish blocking or its cause. Zero loss in the ETL header does not override this warning.','']
    for s in schedule:
        tid=int(s['tid']);lines.append(f'| {tid} | {totals[tid]} | {int(s["running_qpc"])*1000/frequency:.3f} | {int(s["off_qpc"])*1000/frequency:.3f} |')
    output=[]
    for tid,total in totals.most_common():
        lines+=['',f'## Thread {tid}','', 'All leaf samples by symbol prefix (disjoint; includes the full function tail):','', '| Symbol group | Samples | Thread CPU sample share |','|---|---:|---:|']
        ranked=sorted(((n,name) for (t,name),n in counts.items() if t==tid),reverse=True)
        functions=[dict(symbol=name,name=demangled.get(name,name),samples=n,share=n/total) for n,name in ranked]
        groups=aggregate_symbols(functions,total)
        for row in groups:lines.append(f'| `{row["group"]}` | {row["samples"]} | {100*row["share"]:.2f}% |')
        tail=sum(n for n,name in ranked[30:])
        lines+=['',f'Top 30 functions below; omitted tail: {tail} samples ({100*tail/total:.2f}%).','', '| Function | Samples | Thread CPU sample share |','|---|---:|---:|']
        for n,name in ranked[:30]:lines.append(f'| `{demangled.get(name,name)}` | {n} | {n/total*100:.2f}% |')
        lines+=['','External module breakdown:']
        for n,module in sorted(((n,m) for (t,m),n in module_counts.items() if t==tid),reverse=True)[:12]:lines.append(f'- {Path(module).name}: {n} samples ({100*n/total:.2f}%).')
        lines+=['',f'Inclusive native stacks ({stack_totals[tid]} matched samples; shares overlap):']
        for n,name in sorted(((n,m) for (t,m),n in inclusive.items() if t==tid),reverse=True)[:25]:lines.append(f'- `{demangled.get(name,name)}`: {n} ({100*n/max(1,stack_totals[tid]):.2f}%).')
        output.append(dict(tid=tid,total_samples=total,symbol_groups=groups,top30_omitted_samples=tail,functions=functions))
    (p/'cpu-decoded.summary.json').write_text(json.dumps(dict(metadata=meta,quality=capture_quality,threads=output,schedule=schedule,stack_totals=stack_totals,inclusive_native=[dict(tid=t,symbol=m,name=demangled.get(m,m),samples=n) for (t,m),n in inclusive.most_common()],external_modules=[dict(tid=t,module=m,samples=n) for (t,m),n in module_counts.most_common()],external_top=[dict(tid=t,ip=hex(ip),samples=n) for (t,ip),n in external.most_common(50)]),indent=2)+'\n')
    (p/'cpu-decoded.summary.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines[:110]))
if __name__=='__main__':main()
