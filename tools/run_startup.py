"""Bounded native boot. Logs real output; only terminates the child it launches."""
from pathlib import Path
import argparse, datetime, hashlib, json, os, subprocess, time
from port_paths import PORT,default_game_files,default_storage
ap=argparse.ArgumentParser()
ap.add_argument('--exe',type=Path,default=default_storage()/'toolchain/CrashNative.exe')
ap.add_argument('--game',type=Path,default=default_game_files(),help="the disc's extracted files/ folder")
ap.add_argument('--seconds',type=float,default=20)
ap.add_argument('--name',default='startup')
ap.add_argument('--frame-trace',type=Path,help='Opt-in completed-frame trace prefix; arm by writing <prefix>.arm')
ap.add_argument('--trace',action='store_true')
ap.add_argument('--sample',action='store_true',help='Opt-in CPU/callback/decrementer diagnostic logging')
ap.add_argument('--clock',choices=('virtual','wall'),default='virtual',help='Explicit existing runtime timebase mode; wall continues through guest idle')
ap.add_argument('--trace-only',default='')
ap.add_argument('--trace-text',action='store_true')
ap.add_argument('--png-probe',action='store_true')
ap.add_argument('--ipc-probe',action='store_true')
ap.add_argument('--ai-probe',action='store_true')
ap.add_argument('--window',action='store_true')
ap.add_argument('--offscreen',action='store_true',help='Hidden GL window with no desktop buffer swaps; requires --window')
ap.add_argument('--shot-on-demand',action='store_true',help='With --screenshots: capture only when <capture>/efb.now exists')
ap.add_argument('--screenshots',action='store_true',help='Capture runtime EFB BMPs; requires --window')
ap.add_argument('--efb-probe',action='store_true',help='Bounded draw/pre-copy pixel observer; requires --window')
ap.add_argument('--efb-probe-after',type=float,default=0,help='Delay bounded pixel observation by seconds after first draw/clear')
ap.add_argument('--draw-probe',action='store_true',help='Bounded draw, vertex and frame signature diagnostics')
ap.add_argument('--startup-profile',action='store_true',help='CPU-owned callback-point profile and thread stacks')
ap.add_argument('--fp-context-probe',action='store_true',help='Bounded CPU-owned FPR2 and context transition history')
ap.add_argument('--wpad-live',type=Path,help='Live command file polled by the WPAD adapter: lines of hex-buttons hold-ms')
ap.add_argument('--wpad-script',type=Path,help='Opt-in retail Wiimote/Nunchuk timeline: wall-ms hex-buttons connected')
ap.add_argument('--font-probe-after',type=float,default=None,help='Observe font page counts after this many seconds; requires --startup-profile')
ap.add_argument('--spin-probe-pc',default='',help='Hex callback PC for bounded register/object snapshots')
ap.add_argument('--spin-probe-after',type=float,default=30)
args=ap.parse_args()
if args.font_probe_after is not None and not args.startup_profile:ap.error('--font-probe-after requires --startup-profile')
if args.spin_probe_pc and not args.startup_profile:ap.error('--spin-probe-pc requires --startup-profile')
if args.screenshots and not args.window:ap.error('--screenshots requires --window')
if args.offscreen and not args.window:ap.error('--offscreen requires --window')
if args.efb_probe and not args.window:ap.error('--efb-probe requires --window')
exe=args.exe.resolve();reports=PORT/'reports';reports.mkdir(exist_ok=True)
state=exe.parent/'runtime-state';state.mkdir(exist_ok=True)
cmd=[str(exe),str(args.game.resolve()),str(PORT/'runtime.toml')]
env=os.environ.copy();env['NWII_HEADLESS']='0' if args.window else '1'
if args.frame_trace:
    args.frame_trace.parent.mkdir(parents=True,exist_ok=True)
    env['NWII_FRAME_TRACE']=str(args.frame_trace.resolve())
else:env.pop('NWII_FRAME_TRACE',None)
if args.offscreen:env['NWII_OFFSCREEN']='1'
else:env.pop('NWII_OFFSCREEN',None)
if args.wpad_live:
    args.wpad_live.parent.mkdir(parents=True,exist_ok=True);args.wpad_live.write_text('# live WPAD commands: hex-buttons hold-ms'+chr(10));env['NWII_WPAD_LIVE']=str(args.wpad_live.resolve())
else:env.pop('NWII_WPAD_LIVE',None)
if args.wpad_script:env['NWII_WPAD_SCRIPT']=str(args.wpad_script.resolve(strict=True))
else:env.pop('NWII_WPAD_SCRIPT',None)
if args.sample:env['NWII_SAMPLE']='1'
else:env.pop('NWII_SAMPLE',None)
if args.clock=='wall':env['NWII_WALLTB']='1'
else:env.pop('NWII_WALLTB',None)
if args.fp_context_probe:env['NWII_FP_CONTEXT_PROBE']='1'
else:env.pop('NWII_FP_CONTEXT_PROBE',None)
if args.startup_profile:env['NWII_STARTUP_PROFILE']='1'
else:env.pop('NWII_STARTUP_PROFILE',None)
if args.font_probe_after is not None:env['NWII_FONT_PROBE_AFTER']=str(args.font_probe_after)
else:env.pop('NWII_FONT_PROBE_AFTER',None)
if args.spin_probe_pc:
    env['NWII_SPIN_PROBE_PC']=args.spin_probe_pc
    env['NWII_SPIN_PROBE_AFTER']=str(args.spin_probe_after)
else:
    env.pop('NWII_SPIN_PROBE_PC',None);env.pop('NWII_SPIN_PROBE_AFTER',None)
if args.trace or args.trace_only:env['NWII_TRACE_CALLS']='1'
else:env.pop('NWII_TRACE_CALLS',None)
if args.trace_only:env['NWII_TRACE_ONLY']=args.trace_only
else:env.pop('NWII_TRACE_ONLY',None)
if args.trace_text:env['NWII_TRACE_TEXT']='1'
else:env.pop('NWII_TRACE_TEXT',None)
if args.png_probe:env['NWII_PNG_PROBE']='1'
else:env.pop('NWII_PNG_PROBE',None)
if args.ipc_probe:env['NWII_IPC_PROBE']='1'
else:env.pop('NWII_IPC_PROBE',None)
if args.ai_probe:env['NWII_AI_PROBE']='1'
else:env.pop('NWII_AI_PROBE',None)
capture=exe.parent/'runtime-captures'/args.name
if args.efb_probe:
    capture.mkdir(parents=True,exist_ok=True)
    env['NWII_EFB_PROBE']=str(capture/'probe')
    env['NWII_EFB_PROBE_AFTER']=str(args.efb_probe_after)
else:env.pop('NWII_EFB_PROBE',None)
if args.draw_probe:
    for name in ('NWII_DRAWLOG','NWII_VTXTRACE','NWII_FRAMESIG'):env[name]='1'
if args.screenshots:
    capture.mkdir(parents=True,exist_ok=True)
    env['NWII_SCREENSHOT']=str(capture/'efb')
    if args.shot_on_demand:env['NWII_SCREENSHOT_ON_DEMAND']='1'
else:env.pop('NWII_SCREENSHOT',None)
start=time.monotonic();timed_out=False
logs=exe.parent/'runtime-logs';logs.mkdir(exist_ok=True)
out=logs/(args.name+'.stdout.log');err=logs/(args.name+'.stderr.log')
with out.open('wb') as stdout,err.open('wb') as stderr:
    p=subprocess.Popen(cmd,cwd=state,env=env,stdout=stdout,stderr=stderr)
    try:p.wait(timeout=args.seconds)
    except subprocess.TimeoutExpired:
        timed_out=True;p.kill();p.wait()
result={'command':cmd,'sample':args.sample,'clock':args.clock,'offscreen':args.offscreen,'wpad_script':str(args.wpad_script.resolve()) if args.wpad_script else None,'headless':not args.window,'trace':args.trace or bool(args.trace_only),'trace_only':args.trace_only,'trace_text':args.trace_text,
        'fp_context_probe':args.fp_context_probe,'startup_profile':args.startup_profile,'font_probe_after':args.font_probe_after,'spin_probe_pc':args.spin_probe_pc,'spin_probe_after':args.spin_probe_after,'png_probe':args.png_probe,'ipc_probe':args.ipc_probe,'ai_probe':args.ai_probe,'efb_probe':args.efb_probe,'efb_probe_after':args.efb_probe_after,'draw_probe':args.draw_probe,'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'seconds':time.monotonic()-start,'timed_out':timed_out,'exit_code':p.returncode,
        'executable_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),
        'stdout':str(out),'stderr':str(err),
        'screenshots':[str(x) for x in sorted(capture.glob('*.bmp'))] if args.screenshots else [],
        'efb_probe_images':[str(x) for x in sorted(capture.glob('*.ppm'))] if args.efb_probe else []}
(reports/(args.name+'.json')).write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
def tail(path):
    with path.open('rb') as f:
        f.seek(max(0,path.stat().st_size-6000));return f.read().decode(errors='replace')
print('STDOUT TAIL:\n'+tail(out))
print('STDERR TAIL:\n'+tail(err))
