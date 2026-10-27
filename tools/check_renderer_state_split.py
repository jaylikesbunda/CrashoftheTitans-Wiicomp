"""Differential check for the renderer-owned GX state split (NATIVE_RENDERER_STATE_SPLIT_V1).

Copies the runtime sources to a scratch area, applies tools/fix_renderer_state_split.py there (twice, to prove
idempotence; then --restore, to prove the reversal is byte-identical), builds two harnesses from tools/render_state_split_harness.cpp:

  OLD: the unpatched fifo_parser.cpp, with the renderer state loop writing gx::g_state (the staged behaviour);
  NEW: the patched fifo_parser.cpp and render_state.cpp, with the loop writing RenderState() after RenderStateBegin().

Both replay the same generated FIFO drains (BP/CP/XF/LOAD_INDX/draws/display lists/TLUT loads/EFB copies/PE
finish and token/0xFE masks/0x78 merges) through the real parser, and print the renderer-visible state digest at every
draw and at the end of every drain. The NEW harness runs in two orders: sync (parse, render, parse, ...) and queued
(parse every drain, then render them in order). All three traces must be byte-identical for every seed.

Usage: python tools/check_renderer_state_split.py [--seeds 8] [--batches 150]
Compiler: $NWII_GXX or g++ on PATH. Exit status 0 means exact.
"""
import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

p = Path(__file__).resolve().parents[1]
SRC_REL = Path('build/nwii-full-source/nWiiRuntime')
PARTS = ('src', 'include', 'CMakeLists.txt')


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f'failed: {" ".join(map(str, cmd))}')
    return r


def copy_runtime(src_root, dst_root):
    for part in PARTS:
        s, d = src_root / part, dst_root / part
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(s, d)


def same_tree(a, b):
    for part in PARTS:
        pa, pb = a / part, b / part
        if pa.is_dir():
            for f in pa.rglob('*'):
                if f.is_file():
                    rel = f.relative_to(pa)
                    if not (pb / rel).exists() or not filecmp.cmp(f, pb / rel, shallow=False):
                        return False
        elif not filecmp.cmp(pa, pb, shallow=False):
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=8)
    ap.add_argument('--batches', type=int, default=150)
    args = ap.parse_args()
    gxx = os.environ.get('NWII_GXX', 'g++')
    tmp = Path(tempfile.mkdtemp(prefix='render-split-check-'))
    try:
        live = p / SRC_REL
        old_root = tmp / 'old'
        copy_runtime(live, old_root)

        new_port = tmp / 'new'
        new_rt = new_port / SRC_REL
        copy_runtime(live, new_rt)
        (new_port / 'tools').mkdir(parents=True)
        (new_port / 'runtime-overlays').mkdir(parents=True)
        stage = p / 'tools/fix_renderer_state_split.py'
        shutil.copyfile(stage, new_port / 'tools/fix_renderer_state_split.py')
        for name in ('render_state.h', 'render_state.cpp'):
            shutil.copyfile(p / 'runtime-overlays' / name, new_port / 'runtime-overlays' / name)
        stage_new = new_port / 'tools/fix_renderer_state_split.py'

        run([sys.executable, str(stage_new)])
        after_first = tmp / 'after_first'
        copy_runtime(new_rt, after_first)
        run([sys.executable, str(stage_new)])
        if not same_tree(after_first, new_rt):
            raise SystemExit('stage is not idempotent')
        print('stage: apply ok, second apply is a no-op')

        run([sys.executable, str(stage_new), '--restore'])
        if not same_tree(old_root, new_rt):
            raise SystemExit('stage --restore did not return the tree to the pre-stage bytes')
        print('stage: --restore returns the pre-stage bytes exactly')
        run([sys.executable, str(stage_new)])

        harness = p / 'tools/render_state_split_harness.cpp'
        inc_old = ['-I' + str(old_root / 'include'), '-I' + str(old_root / 'src')]
        inc_new = ['-I' + str(new_rt / 'include'), '-I' + str(new_rt / 'src')]
        obj_dir = tmp / 'obj'
        obj_dir.mkdir()
        base = ['-std=c++20', '-O1']
        run([gxx, *base, '-c', *inc_old, str(old_root / 'src/hle/gx/fifo_parser.cpp'), '-o', str(obj_dir / 'old_parser.o')])
        run([gxx, *base, '-c', *inc_new, str(new_rt / 'src/hle/gx/fifo_parser.cpp'), '-o', str(obj_dir / 'new_parser.o')])
        run([gxx, *base, '-c', *inc_new, str(new_rt / 'src/hle/gx/render_state.cpp'), '-o', str(obj_dir / 'render_state.o')])
        run([gxx, *base, '-DHARNESS_OLD', *inc_old, str(harness), str(obj_dir / 'old_parser.o'), '-o', str(tmp / 'old_harness'), '-lpthread'])
        run([gxx, *base, '-DHARNESS_NEW', *inc_new, str(harness), str(obj_dir / 'new_parser.o'), str(obj_dir / 'render_state.o'),
             '-o', str(tmp / 'new_harness'), '-lpthread'])
        exe = '.exe' if os.name == 'nt' else ''
        old_bin, new_bin = tmp / ('old_harness' + exe), tmp / ('new_harness' + exe)

        failures = 0
        lines = 0
        for seed in range(1, args.seeds + 1):
            old = subprocess.run([str(old_bin), str(seed), str(args.batches), 'sync'], capture_output=True, text=True).stdout
            sync = subprocess.run([str(new_bin), str(seed), str(args.batches), 'sync'], capture_output=True, text=True).stdout
            queued = subprocess.run([str(new_bin), str(seed), str(args.batches), 'queued'], capture_output=True, text=True).stdout
            lines += old.count('\n')
            ok_sync, ok_queued = old == sync, old == queued
            print(f'seed {seed}: old={old.count(chr(10))} lines, new-sync {"same" if ok_sync else "DIFF"}, new-queued {"same" if ok_queued else "DIFF"}')
            if not (ok_sync and ok_queued):
                failures += 1
        print(f'{args.seeds} seeds, {lines} observation lines compared; {failures} failing seed(s)')
        return 1 if failures else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
