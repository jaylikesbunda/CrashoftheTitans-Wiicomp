"""Profile run for the instrumented build (NATIVE_HOT_PGO_V1): plays a save-state scene silently, flushes the gcov counters through the
trigger file, and optionally collects/ships the profile.

Workflow (see tools/fix_hot_pgo.py for the stage itself):
  1. NWII_BUILD_HOT_PGO=generate python tools/clean_room_build.py --dest <short path, e.g. C:\\ctb\\clean-room-usa> ...
  2. python <dest>/native-port/tools/pgo_train.py --exe <dest>/storage/toolchain/CrashNative.exe --game <game folder> --state scene.nwstate \\
         --seconds 90 --collect --ship RQJE7D
  3. rebuild normally (NWII_BUILD_HOT_PGO unset = shipped): units whose staged text matches data/pgo/<release>/ use the profile.

The scene decides what the profile favours: take it from real gameplay (F1 saves a state). Several runs (--state may repeat, each
trained for --seconds) are merged by gcov, so cover gameplay, a menu and a cutscene when possible. Audio is muted (SDL dummy driver).

  --exe     instrumented CrashNative.exe (built in generate mode)
  --game    extracted game folder (the one holding sys/ and files/)
  --state   save state (NWII_LOAD_STATE); repeat the option for several scenes
  --press   hex WPAD button mask pressed every 0.9 s during the run (default 0800); 0 disables input
"""
from pathlib import Path
import argparse, os, shutil, subprocess, sys, tempfile, time

PORT = Path(__file__).resolve().parents[1]


def run_scene(exe, game, toml, state, seconds, press, workdir):
    live = workdir / 'live.txt'
    live.write_text('# live WPAD commands\n', encoding='ascii')
    trigger = workdir / 'pgo.trigger'
    done = Path(str(trigger) + '.done')
    for f in (trigger, done):
        f.unlink(missing_ok=True)
    env = dict(os.environ,
               SDL_AUDIODRIVER='dummy', NWII_HEADLESS='0', NWII_WALLTB='1', NWII_OFFSCREEN='1', NWII_TURBO='1',
               NWII_FPS_LIMIT='120', NWII_SINGLE_VI='1', NWII_WPAD_LIVE=str(live), NWII_PGO_TRIGGER=str(trigger),
               NWII_NAND_DIR=str(workdir / 'nand'), NWII_STATE_DIR=str(workdir / 'state'))
    if state:
        env['NWII_LOAD_STATE'] = str(Path(state).resolve())
    (workdir / 'nand').mkdir(exist_ok=True)
    with (workdir / 'stdout.log').open('wb') as so, (workdir / 'stderr.log').open('wb') as se:
        proc = subprocess.Popen([str(exe), str(game), str(toml)], cwd=workdir, env=env, stdout=so, stderr=se)
        start = time.monotonic()
        sent = 0
        while time.monotonic() - start < seconds and proc.poll() is None:
            if press and time.monotonic() - start >= 3 + sent * 0.9:
                with live.open('a', encoding='ascii') as f:
                    f.write(f'{press} 200\n')
                sent += 1
            time.sleep(0.1)
        if proc.poll() is not None:
            raise SystemExit(f'the game exited early (code {proc.returncode}); see {workdir / "stderr.log"}')
        trigger.write_text('1')
        for _ in range(120):   # the watcher flushes within a second or two; large profiles take longer
            if done.exists():
                break
            time.sleep(0.5)
        else:
            proc.kill()
            raise SystemExit('the profile was not flushed (no .done file); is this an instrumented build?')
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(f'scene done: {state or "(boot)"} for {seconds}s')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--exe', type=Path, required=True)
    ap.add_argument('--game', type=Path, required=True)
    ap.add_argument('--toml', type=Path, default=PORT / 'runtime.toml')
    ap.add_argument('--state', action='append', default=[])
    ap.add_argument('--seconds', type=float, default=90)
    ap.add_argument('--press', default='0800')
    ap.add_argument('--collect', action='store_true', help='copy the .gcda files into the profile store afterwards')
    ap.add_argument('--ship', metavar='RELEASE', help='also copy the store into data/pgo/RELEASE')
    a = ap.parse_args()
    if not a.exe.is_file():
        raise SystemExit('missing instrumented exe ' + str(a.exe))
    if not (a.game / 'sys').is_dir():
        raise SystemExit('--game must be the extracted game folder (sys/ and files/)')
    if a.press == '0':
        a.press = ''
    states = a.state or [None]
    with tempfile.TemporaryDirectory(prefix='pgo-train-') as tmp:
        for i, state in enumerate(states):
            work = Path(tmp) / str(i)
            work.mkdir()
            run_scene(a.exe, a.game, a.toml, state, a.seconds, a.press, work)
    stage = [sys.executable, str(Path(__file__).with_name('fix_hot_pgo.py'))]
    if a.collect or a.ship:
        subprocess.run(stage + ['--collect'], check=True)
    if a.ship:
        subprocess.run(stage + ['--ship', a.ship], check=True)


if __name__ == '__main__':
    main()
