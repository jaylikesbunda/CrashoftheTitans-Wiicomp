"""Build and race-test production render waits, plus reversible staging and trace correlation."""
from pathlib import Path
import argparse,json,os,shutil,subprocess,sys
import fix_render_pacing
from analyze_render_pacing import analyze
PORT=Path(__file__).resolve().parents[1]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=PORT.parent/'dump/render-pacing-check');a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    rt=PORT/'build/nwii-full-source/nWiiRuntime';source=(rt/'src/core/main.cpp').read_text();hud=(rt/'include/runtime/native_hud_gl.h').read_text()
    assert fix_render_pacing.main_source(source)==source
    assert fix_render_pacing.hud_source(hud)==hud
    plain=fix_render_pacing.restore(source);assert fix_render_pacing.restore(fix_render_pacing.main_source(plain))==plain
    # Exercise first install and re-stage after other marked transforms are restored.
    import fix_adaptive_gx_batching
    plain=fix_adaptive_gx_batching.restore(plain);staged=fix_adaptive_gx_batching.main_source(plain);assert fix_render_pacing.main_source(staged)==fix_render_pacing.main_source(fix_render_pacing.main_source(staged))
    cxx=shutil.which('g++')
    if not cxx:raise SystemExit('g++ is required for the Windows production-wait fixture')
    exe=a.output/'render_pacing_tests.exe'
    cmd=[cxx,'-std=c++20','-O2','-Wall','-Wextra','-pthread','-I',str(rt/'include'),str(PORT/'tools/render_pacing_tests.cpp'),'-o',str(exe)]
    compiled=subprocess.run(cmd,capture_output=True,text=True)
    (a.output/'compile.log').write_text(compiled.stdout+compiled.stderr)
    if compiled.returncode:print(compiled.stdout+compiled.stderr);raise SystemExit(compiled.returncode)
    # A synthetic trace checks attribution independently of scheduler luck in the fixture.
    def event(name,t,ident,aux=0):return dict(name=name,ts=t,args=dict(value=ident,aux=aux))
    synthetic=analyze(dict(traceEvents=[event('gx.pacing.request',10,1),event('gx.pacing.publish',13,1),event('gx.pacing.notify',15,1),event('gx.pacing.notify_done',17,1),event('host.pacing.wait_begin',14,2,100),event('host.pacing.wait_return',20,2,1),event('host.pacing.lock_begin',21,2),event('host.pacing.lock_acquired',29,2),event('gx.pacing.consume_lock',30,1,2),event('gx.pacing.drain_start',33,1,2)]))
    assert synthetic['stages']['mutex_wait_us']['max']==8
    assert synthetic['stages']['event_notify_to_wait_return_us']['max']==5
    assert synthetic['stages']['request_to_drain_us']['max']==23
    results=[]
    for mode in ['cv','timer']:
        env=os.environ.copy();env.pop('NWII_LEGACY_DRAIN_WAKE',None);env.pop('NWII_RENDER_WAIT_FILE',None);env['NWII_RENDER_WAIT']=mode;env['NWII_FRAME_TRACE']=str(a.output/mode)
        r=subprocess.run([str(exe)],env=env,capture_output=True,text=True,timeout=30);print(r.stdout+r.stderr,flush=True)
        if r.returncode:raise SystemExit(r.returncode)
        if mode=='timer'and os.name=='nt':assert 'mode=timer 'in r.stdout,'Native timer path was not exercised'
        data=json.loads((a.output/(mode+'.json')).read_text());report=analyze(data)
        assert not report['dropped_events']and report['stages']['request_to_drain_us']['count']>=160
        if mode=='timer'and os.name=='nt':assert report['stages']['mutex_wait_us']['count']>0 and report['stages']['event_notify_to_wait_return_us']['count']>0
        else:assert report['stages']['cv_combined_wait_us']['count']>0 and report['stages']['mutex_wait_us']['count']==0
        results.append(dict(mode=mode,output=r.stdout,analysis=report))
        env.pop('NWII_FRAME_TRACE');plain_run=subprocess.run([str(exe)],env=env,capture_output=True,text=True,timeout=30)
        print(plain_run.stdout+plain_run.stderr,flush=True)
        if plain_run.returncode:raise SystemExit(plain_run.returncode)
    # Existing legacy-drain launches must never select an event path they don't signal.
    env['NWII_RENDER_WAIT']='timer';env['NWII_LEGACY_DRAIN_WAKE']='1'
    legacy=subprocess.run([str(exe)],env=env,capture_output=True,text=True,timeout=30)
    print(legacy.stdout+legacy.stderr,flush=True)
    assert legacy.returncode==0 and 'mode=cv 'in legacy.stdout
    # One process switches the production backend 160 times while requests race it.
    env.pop('NWII_LEGACY_DRAIN_WAKE',None);env['NWII_RENDER_WAIT']='cv';env['NWII_FRAME_TRACE']=str(a.output/'live')
    control=a.output/'live-render-wait.txt';control.write_text('cv\n');env['NWII_RENDER_WAIT_FILE']=str(control.resolve())
    live=subprocess.run([str(exe),'--live'],env=env,capture_output=True,text=True,timeout=30)
    print(live.stdout+live.stderr,flush=True)
    assert live.returncode==0 and 'live=1 'in live.stdout
    analysis=analyze(json.loads((a.output/'live.json').read_text()))
    assert not analysis['dropped_events'] and analysis['stages']['cv_combined_wait_us']['count']>0 and analysis['stages']['mutex_wait_us']['count']>0
    results.append(dict(mode='live',output=live.stdout,analysis=analysis))
    (a.output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    print('Render pacing staging, both wait paths, live switching, coalescing, lost-wake races, deadlines and trace correlation passed.')
if __name__=='__main__':main()
