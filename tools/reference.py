"""Isolated Dolphin reference profile and replay launcher; no UI automation."""
import argparse, hashlib, json, os, subprocess, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; REF=ROOT/'native-port/reference'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def prepare():
    cfg=REF/'user/Config'; cfg.mkdir(parents=True,exist_ok=True)
    # New profile: never copy existing user NAND, identities or personal saves.
    settings={'Dolphin.ini':'''[Core]
CPUThread = False
EnableCheats = False
DSPHLE = True
[Interface]
ConfirmStop = False
[Display]
Fullscreen = False
[Movie]
PauseMovie = True
[DSP]
DumpAudio = True
''','GFX.ini':'''[Hardware]
VSync = False
[Settings]
InternalResolution = 1
wideScreenHack = False
DumpFramesAsImages = True
[Enhancements]
MaxAnisotropy = 0
''','WiimoteNew.ini':'''[Wiimote1]
Device = DInput/0/Keyboard Mouse
Buttons/A = J
Buttons/B = K
Buttons/1 = `1`
Buttons/2 = `2`
Buttons/- = Q
Buttons/+ = E
D-Pad/Up = UP
D-Pad/Down = DOWN
D-Pad/Left = LEFT
D-Pad/Right = RIGHT
Extension = Nunchuk
Nunchuk/Buttons/C = LCONTROL
Nunchuk/Buttons/Z = LSHIFT
Nunchuk/Stick/Up = W
Nunchuk/Stick/Down = S
Nunchuk/Stick/Left = A
Nunchuk/Stick/Right = D
''','WiimoteSource.ini':'[Wiimote1]\nSource = 1\n'}
    for name,text in settings.items():
        p=cfg/name
        if not p.exists():p.write_text(text)
    manifest=REF/'checkpoints.json'
    if not manifest.exists():
        manifest.write_text(json.dumps({'game':'RQJP7D','status':'capture_pending','checkpoints':[{'id':i,'status':'pending','movie':None,'frame':None,'screenshot':None,'state':None,'observations':[]} for i in ('startup_menu','first_level_loaded','movement','combat','titan_possession','checkpoint','save_reload','effects_audio','cinematic')]},indent=2))
    pins={'dol_sha256':sha(ROOT/'orig/main.dol'),'dolphin_sha256':sha(ROOT/'Dolphin-x64/Dolphin.exe'),'profile_sha256':{p.name:sha(p) for p in cfg.glob('*.ini')},'disc_path':'Crash of the Titans (Europe).iso','disc_hash_status':'not_yet_hashed','input_policy':'Real DTM recording required for gameplay; elapsed host time is not an input trace.'}
    if not (REF/'pins.json').exists(): (REF/'pins.json').write_text(json.dumps(pins,indent=2))
ap=argparse.ArgumentParser(); ap.add_argument('--launch',action='store_true');ap.add_argument('--movie',type=Path);ap.add_argument('--state',type=Path);ap.add_argument('--seconds',type=float);ap.add_argument('--dump',action='store_true');args=ap.parse_args()
prepare()
command=[str(ROOT/'Dolphin-x64/Dolphin.exe'),'-u',str(REF/'user'),'-e',str(ROOT/'Crash of the Titans (Europe).iso')]
if args.movie:command+=['-m',str(args.movie.resolve())]
if args.state:command+=['-s',str(args.state.resolve())]
if args.dump:command+=['-C','Dolphin.Movie.DumpFrames=True','-C','GFX.Settings.DumpFramesAsImages=True']
(REF/'last-command.json').write_text(json.dumps(command,indent=2));print(subprocess.list2cmdline(command))
if args.launch:
    with (REF/'launch.log').open('wb') as log:
        proc=subprocess.Popen(command,stdout=log,stderr=log)
        try: proc.wait(timeout=args.seconds)
        except subprocess.TimeoutExpired:
            proc.terminate();proc.wait(timeout=10)
            print('Stopped only the launched reference instance after the bounded capture period; not a completed gameplay session.')
    captures=[{'file':str(p.relative_to(REF)),'size':p.stat().st_size} for p in (REF/'user/Dump').rglob('*') if p.is_file()]
    (REF/'capture-inventory.json').write_text(json.dumps({'exit_code':proc.returncode,'captures':captures,'recorded_input':str(args.movie) if args.movie else None},indent=2)); print(f'{len(captures)} captured artifacts')
