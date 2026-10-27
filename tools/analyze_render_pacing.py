"""Correlate render-wakeup stages in an NWII_FRAME_TRACE JSON capture; stdout only."""
import argparse,json,statistics
from pathlib import Path

def analyze(data):
    requests={};waits={}
    for e in data.get('traceEvents',[]):
        name=e.get('name','');args=e.get('args',{});ident=args.get('value',0)
        if name.startswith('gx.pacing.'):
            requests.setdefault(ident,{})[name.removeprefix('gx.pacing.')]=(e['ts'],args.get('aux',0))
        elif name.startswith('host.pacing.'):
            waits.setdefault(ident,{})[name.removeprefix('host.pacing.')]=(e['ts'],args.get('aux',0))
    values={k:[] for k in ['request_to_publish_us','publish_to_notify_us','notify_call_us','request_to_drain_us','event_notify_to_wait_return_us','mutex_wait_us','consume_mutex_wait_us','wait_return_to_drain_us','deadline_lateness_us','cv_combined_wait_us']}
    def delta(key,a,b):
        if a and b and b[0]>=a[0]:values[key].append(b[0]-a[0])
    for r in requests.values():
        delta('request_to_publish_us',r.get('request'),r.get('publish'))
        delta('publish_to_notify_us',r.get('publish'),r.get('notify'))
        delta('notify_call_us',r.get('notify'),r.get('notify_done'))
        delta('request_to_drain_us',r.get('request'),r.get('drain_start'))
        delta('consume_mutex_wait_us',r.get('consume_begin'),r.get('consume_lock'))
        if 'consume_lock' not in r:continue
        w=waits.get(r['consume_lock'][1],{});returned=w.get('wait_return')
        if returned:
            if returned[1]==1 and r.get('notify') and w.get('wait_begin') and r['notify'][0]>=w['wait_begin'][0]:
                delta('event_notify_to_wait_return_us',r['notify'],returned)
            delta('wait_return_to_drain_us',returned,r.get('drain_start'))
    for w in waits.values():
        delta('mutex_wait_us',w.get('lock_begin'),w.get('lock_acquired'))
        delta('cv_combined_wait_us',w.get('wait_begin'),w.get('cv_return_locked'))
        returned=w.get('wait_return')or w.get('cv_return_locked');begin=w.get('wait_begin')
        if returned and begin:
            deadline=w.get('fallback',begin)[1]
            values['deadline_lateness_us'].append(max(0,returned[0]-deadline))
    def summary(v):
        if not v:return dict(count=0)
        s=sorted(v);return dict(count=len(s),mean=statistics.mean(s),p95=s[min(len(s)-1,int((len(s)-1)*.95+.5))],max=max(s))
    return dict(request_cycles=len(requests),wait_cycles=len(waits),dropped_events=data.get('dropped_events',0),stages={k:summary(v)for k,v in values.items()},notes=['Timestamps are observable user-mode stages; they do not measure OS ready-to-run time.','CV return already owns the mutex, so wakeup and mutex delay cannot be separated in that mode.','Notification begin/end bracket signaling; a consumer may observe pending work before notification.','Capture boundaries or dropped events can leave incomplete cycles; only matched nonnegative intervals are summarized.'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('capture',type=Path);a=p.parse_args()
    print(json.dumps(analyze(json.loads(a.capture.read_text())),indent=2))
