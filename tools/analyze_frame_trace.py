"""Summarize completed frames, exclusive CPU scopes, deferred GPU regions and PCs."""
from pathlib import Path
import argparse,json,math,statistics,collections,csv

def pct(values,p):
    return sorted(values)[max(0,math.ceil(len(values)*p)-1)] if values else 0

def analyze(data):
    events=data['traceEvents'];frames=sorted((e for e in events if e['name']=='completed.frame'),key=lambda e:e['ts'])
    start=data['start_us'];end=data['stop_us'];threads=collections.defaultdict(list)
    for e in events:
        if e['ph']=='X' and e['name']!='completed.frame' and e.get('dur',0)>0:
            lo=max(start,e['ts']);hi=min(end,e['ts']+e['dur'])
            if hi>lo:threads[e['tid']].append(dict(name=e['name'],lo=lo,hi=hi,weight=e['args'].get('weight',1),children=[]))
    segments=[];overlaps=0
    for tid,spans in threads.items():
        stack=[]
        for s in sorted(spans,key=lambda s:(s['lo'],-s['hi'])):
            while stack and stack[-1]['hi']<=s['lo']:stack.pop()
            if stack:
                if s['hi']<=stack[-1]['hi']:stack[-1]['children'].append(s)
                else:overlaps+=1
            stack.append(s)
        for s in spans:
            at=s['lo']
            for child in sorted(s['children'],key=lambda c:c['lo']):
                if child['lo']>at:segments.append((at,child['lo'],s['name'],s['weight'],tid))
                at=max(at,child['hi'])
            if at<s['hi']:segments.append((at,s['hi'],s['name'],s['weight'],tid))
    rows=[];stage_totals=collections.Counter();hostcpu={e['args']['frame']:e['args']['value'] for e in events if e['name']=='host.cpu_us'}
    present_events=[]
    gpu=[e for e in events if e['cat']=='gpu'];counts=collections.Counter();pcs=collections.Counter();gpu_totals=collections.Counter()
    for e in events:
        if e['name']=='guest.pc':pcs[(e['args']['value'],e['args']['aux'])]+=1
        if e['name'] in ('texture.hit','texture.miss','texture.efb_hit','texture.efb_fallback','texture.efb_inside'):counts[e['name']]+=1
        if e['name']=='host.present' and e['ph']=='i':present_events.append(e)
        if e['name'] in ('batch.vertices','fifo.bytes','decode.vertices'):
            counts[e['name']]+=e['args']['value']
            if e['name']=='batch.vertices':counts['batch.indices']+=e['args']['aux']
            if e['name']=='decode.vertices':counts['draw.bytes']+=e['args']['aux']
    for f in frames:
        lo=f['ts'];hi=lo+f['dur'];stages=collections.Counter()
        for a,b,name,weight,tid in segments:
            us=max(0,min(b,hi)-max(a,lo))*weight
            if us:stages[name]+=us/1000
        gpu_us=0
        for e in gpu:
            dur=e['args']['aux'];overlap=max(0,min(e['ts']+dur,hi)-max(e['ts'],lo))
            if dur:
                amount=e['args']['value']*overlap/dur*e['args'].get('weight',1)
                gpu_totals[e['name']]+=amount
                # Narrow operation queries nest inside broad regions; never add
                # them to the compatibility broad-region metric a second time.
                if e['name'] in ('gpu.render_region_us','gpu.presentation_region_us'):gpu_us+=amount
        fid=f['args']['frame'];stage_totals.update(stages)
        rows.append(dict(frame=fid,wall_ms=f['dur']/1000,guest_cpu_ms=f['args']['value']/1000,
                         host_cpu_ms=hostcpu.get(fid,0)/1000,gpu_region_ms_estimated=gpu_us/1000,exclusive_scope_ms=dict(stages)))
    metrics={}
    for key in ('wall_ms','guest_cpu_ms','host_cpu_ms','gpu_region_ms_estimated'):
        values=[r[key] for r in rows];metrics[key]=dict(mean=statistics.mean(values) if values else 0,median=statistics.median(values) if values else 0,p95=pct(values,.95))
    stages=[]
    sampled_names={e['name'] for e in events if e['ph']=='X' and e['args'].get('weight',1)>1}
    for name,total in stage_totals.most_common():
        values=[r['exclusive_scope_ms'].get(name,0) for r in rows]
        stages.append(dict(name=name,mean_ms=total/len(rows),p95_ms=pct(values,.95),estimated=name in sampled_names))
    pending=[e for e in events if e['name']=='gpu.pending_queries'];last=pending[-1]['args'] if pending else {}
    span_s=max(1e-9,(end-start)/1e6);fresh=sum(1 for e in present_events if e['args'].get('value'))
    field_ns=next((e['args'].get('aux') for e in present_events if e['args'].get('aux')),0)
    period_ms=field_ns/1e6 if field_ns else 1000/60
    fresh_events=[e for e in present_events if e['args'].get('value')]
    gaps=[(b['ts']-a['ts'])/1000 for a,b in zip(fresh_events,fresh_events[1:])]  # ms between new images on screen
    rates=dict(presented_fps=len(present_events)/span_s,new_frame_fps=fresh/span_s,xfb_copy_fps=len(frames)/span_s,field_period_ms=period_ms,
               region_target_fps=1000/period_ms,new_image_gap_ms=dict(median=statistics.median(gaps) if gaps else 0,p95=pct(gaps,.95),p99=pct(gaps,.99),worst=max(gaps) if gaps else 0),
               missed_fields=sum(1 for e,n in zip(present_events,present_events[1:]) if (n['ts']-e['ts'])/1000>1.5*period_ms),measured=bool(present_events))
    return dict(completed_frames=len(rows),reason=data.get('reason'),budget_ms=period_ms,rates=rates,
                observed_completed_fps=1000000*len(frames)/sum(f['dur'] for f in frames) if frames and sum(f['dur'] for f in frames) else 0,
                metrics=metrics,stages=stages,counts=dict(counts),frames=rows,
                gpu_regions=[dict(name=k,mean_ms=v/1000/len(rows)) for k,v in gpu_totals.most_common()] if rows else [],
                pc_samples=[dict(pc=f'{pc:08X}',lr=f'{lr:08X}',samples=n) for (pc,lr),n in pcs.most_common(30)],
                dropped_events=data.get('dropped_events',0),overlapping_scopes=overlaps,
                unfinished_cpu_scopes=data.get('open_cpu_scopes',0),
                missing_gpu_queries=data.get('missing_gpu_queries',last.get('value',0)+last.get('aux',0)),guest_cpu_available=data.get('guest_cpu_available',False),
                limitations=['Guest CPU time is OS thread accounting; PC observations sample callback points, not unbiased instruction profiles.',
                            'callback.sample uses each event sampling weight; scope elapsed time can include preemption/driver waits. Missing events invalidate complete scope totals.',
                            'CPU scope self time excludes recorded children; overlapping CPU threads and GPU durations must not be added.',
                            'GPU region durations are deferred GL timestamps, can include submission gaps; per-frame allocation uses CPU-region overlap and is estimated.',
                            'Capture boundaries can split renderer regions; first/last frame GPU estimates can be incomplete.',
                            'This identifies bottleneck candidates, not a proven dependency critical path or a complete replay capture.'])

def write_report(path):
    result=analyze(json.loads(path.read_text()))
    out=path.with_suffix('.summary.json');out.write_text(json.dumps(result,indent=2)+'\n')
    stage_names=[s['name'] for s in result['stages']]
    with path.with_suffix('.frames.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['frame','wall_ms','guest_cpu_ms','host_cpu_ms','gpu_region_ms_estimated',*stage_names]);writer.writeheader()
        for r in result['frames']:
            writer.writerow({**{k:v for k,v in r.items() if k!='exclusive_scope_ms'},**r['exclusive_scope_ms']})
    rt=result['rates']
    rate_line=(f"Rates over {result['completed_frames']} frames: presented {rt['presented_fps']:.1f}/s (what Fraps counts), new images on screen {rt['new_frame_fps']:.1f}/s, XFB copies {rt['xfb_copy_fps']:.1f}/s; "
               f"region target {rt['region_target_fps']:.1f}/s ({rt['field_period_ms']:.2f} ms field). New-image gap median {rt['new_image_gap_ms']['median']:.1f} / p95 {rt['new_image_gap_ms']['p95']:.1f} / p99 {rt['new_image_gap_ms']['p99']:.1f} / worst {rt['new_image_gap_ms']['worst']:.1f} ms; late presents {rt['missed_fields']}.") if rt['measured'] else 'Present counters not recorded by this build (older exe): only the XFB-copy rate below is available.'
    lines=['# Native frame trace',f"Completed frames: {result['completed_frames']}; observed completed-XFB rate: {result['observed_completed_fps']:.2f} FPS.",rate_line,'',
           '| Measurement | Mean ms | Median ms | p95 ms |','|---|---:|---:|---:|']
    for name,m in result['metrics'].items():lines.append(f"| {name} | {m['mean']:.3f} | {m['median']:.3f} | {m['p95']:.3f} |")
    lines+=['',f"Frame budget for this region: {result['budget_ms']:.3f} ms ({1000/result['budget_ms']:.1f}/s). Times overlap; do not sum these rows.",'','| Exclusive CPU scope (elapsed) | Mean ms/frame | p95 ms |','|---|---:|---:|']
    for s in result['stages']:lines.append(f"| {s['name']}{' (sampled estimate)' if s['estimated'] else ''} | {s['mean_ms']:.3f} | {s['p95_ms']:.3f} |")
    lines+=['','| GPU region (overlapping; draw samples estimated) | Mean ms/frame |','|---|---:|']
    for s in result['gpu_regions']:lines.append(f"| {s['name']} | {s['mean_ms']:.3f} |")
    lines+=['',f"Dropped events: {result['dropped_events']}; missing GPU queries: {result['missing_gpu_queries']}; unfinished CPU scopes: {result['unfinished_cpu_scopes']}; overlapping scopes: {result['overlapping_scopes']}.",'']
    lines.extend('- '+x for x in result['limitations'])
    path.with_suffix('.summary.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines[:12]));return result

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('trace',type=Path);args=ap.parse_args();write_report(args.trace)
