"""Clean-room build: `main.dol + function index + vendor sources + our overlays -> CrashNative.exe` in an empty directory.

This is the hermeticity test for the launcher's future on-device builder. It copies only the inputs a user machine would have
(sources, stage scripts, overlays, evidence tables, the pinned NWiiRecomp checkout, the DOL and the function index), starts from no staged
tree and no generated code, and runs the normal pipeline *with* generation. Then it compares the result with the long-lived tree:
  * every generated / split / hot chunk, normalised for the install path,
  * the staged runtime sources,
  * the exe size and, where the paths do not matter, its sha256.
Any stage that asserts on a pristine tree, depends on a leftover file, or orders itself wrongly shows up here.

    python tools/clean_room_build.py --dest <folder> [--jobs 10] [--no-build] [--compare-only]
"""
from pathlib import Path
import argparse, hashlib, json, os, shutil, subprocess, sys, time

from port_paths import PORT, INDEX, builds_dir, default_dol, default_storage
ap = argparse.ArgumentParser()
ap.add_argument('--dest', type=Path, default=builds_dir() / 'clean-room')
ap.add_argument('--jobs', type=int, default=10)
ap.add_argument('--resume', action='store_true', help='reuse a verified completed build or resume its unfinished compilation')
ap.add_argument('--dol', type=Path, default=default_dol())
ap.add_argument('--index', type=Path, default=INDEX)
ap.add_argument('--baseline-exe', type=Path, help='optional existing build used for the final byte comparison')
ap.add_argument('--no-build', action='store_true', help='only (re)create the clean tree')
ap.add_argument('--stage-only', action='store_true', help='generate and qualify patches, without compiling a game executable')
ap.add_argument('--compare-only', action='store_true')
ap.add_argument('--reuse-deps', action='store_true', help='reuse already-fetched SDL/glad/toml++ sources (offline-friendly)')
ap.add_argument('--deps', type=Path, help='directory holding sdl2-src, tomlplusplus-src and glad-src (default: the developer cache)')
ap.add_argument('--expected-builder-revision', help='launcher-pinned patch revision to require before building')
args = ap.parse_args()
import region
release = region.for_dol(args.dol)
if args.index == INDEX and not release.reference:
    args.index = release.index
# Snapshots of translated game code a developer tree may still hold; a clean build regenerates them.
GAME_CODE = ('bounded-blocks', 'leaf-before-*.cpp', 'hot-before-*.cpp', 'hot_fp_bank_candidate.cpp', 'sdk_sqrt_baseline.cpp', 'sdk_sqrt_local_loops.inc')
from builder_revision import validate as validate_builder_revision
patch_identity=validate_builder_revision(PORT,args.expected_builder_revision)
dest = args.dest.resolve()
cport = dest / 'native-port'
storage = dest / 'storage'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def norm(text):
    out = text.replace(str(PORT).replace('\\', '/'), '@PORT@').replace(str(cport).replace('\\', '/'), '@PORT@')
    out = out.replace(str(PORT), '@PORT@').replace(str(cport), '@PORT@')
    return out


def compare():
    rows = []
    for rel in ('build/crash-generated', 'build/crash-split', 'build/crash-hot'):
        a, b = PORT / rel, cport / rel
        if not a.exists() or not b.exists():
            rows.append(dict(tree=rel, status='missing', old=a.exists(), new=b.exists())); continue
        names = sorted({f.name for f in a.iterdir() if f.is_file()} | {f.name for f in b.iterdir() if f.is_file()})
        diff = []
        for n in names:
            fa, fb = a / n, b / n
            if not fa.exists() or not fb.exists(): diff.append((n, 'only in ' + ('old' if fa.exists() else 'new'))); continue
            ta, tb = norm(fa.read_text(errors='surrogateescape')), norm(fb.read_text(errors='surrogateescape'))
            if ta != tb: diff.append((n, 'content differs'))
        rows.append(dict(tree=rel, files=len(names), different=len(diff), examples=diff[:8]))
    rt_a, rt_b = PORT / 'build/nwii-full-source/nWiiRuntime', cport / 'build/nwii-full-source/nWiiRuntime'
    diff = []
    count = 0
    for sub in ('src', 'include'):
        for fa in sorted((rt_a / sub).rglob('*')):
            if not fa.is_file(): continue
            count += 1
            fb = rt_b / fa.relative_to(rt_a)
            if not fb.exists(): diff.append((str(fa.relative_to(rt_a)), 'missing in clean tree')); continue
            try:
                if norm(fa.read_text(errors='surrogateescape')) != norm(fb.read_text(errors='surrogateescape')): diff.append((str(fa.relative_to(rt_a)), 'content differs'))
            except Exception:
                if sha(fa) != sha(fb): diff.append((str(fa.relative_to(rt_a)), 'binary differs'))
    rows.append(dict(tree='nWiiRuntime sources', files=count, different=len(diff), examples=diff[:12]))
    exe_old = args.baseline_exe.resolve() if args.baseline_exe else default_storage() / 'toolchain/CrashNative.exe'
    exe_new = storage / 'toolchain/CrashNative.exe'
    if exe_new.exists():
        if exe_old.exists():
            rows.append(dict(tree='CrashNative.exe', old_size=exe_old.stat().st_size, new_size=exe_new.stat().st_size,
                             old_sha=sha(exe_old), new_sha=sha(exe_new), identical=sha(exe_old) == sha(exe_new)))
        else:
            rows.append(dict(tree='CrashNative.exe', baseline='missing', new_size=exe_new.stat().st_size,
                             new_sha=sha(exe_new), identical=None))
    report = dest / 'clean-room-compare.json'
    report.write_text(json.dumps(rows, indent=2) + '\n')
    for r in rows: print(json.dumps(r)[:400])
    print('report:', report)
    return rows


if args.compare_only:
    compare(); raise SystemExit

# ---- 1. verified reuse, or a fresh tree with only the inputs
from build_resources import request as build_request, cache_state, compile_jobs, memory_failure
wanted = build_request(patch_identity, args.dol, args.index)
resume_compile = False
if args.resume and not args.no_build and not args.stage_only and cport.exists():
    cached = cache_state(dest, wanted)
    if cached == 'complete':
        print('@progress|100|Verified existing build is ready', flush=True)
        print('Runtime ready:', storage/'toolchain/CrashNative.exe', flush=True)
        raise SystemExit(0)
    resume_compile = cached == 'compile'
if resume_compile:
    print('@progress|40|Resuming unfinished compilation', flush=True)
    print('Verified cached patch inputs, disc and index; keeping successful objects.', flush=True)
else:
    # ---- 1. fresh tree with only the inputs
    print('@progress|0.5|Preparing build files', flush=True)
    if cport.exists():
        # never delete anything outside our own clean-room directory
        assert dest.name == 'clean-room' or dest.name.startswith('clean-room'), 'refusing to wipe ' + str(dest)
        shutil.rmtree(dest, ignore_errors=True)
    for d in ('tools', 'compiler', 'runtime-overlays', 'patches', 'data', 'reports', 'reference'):
        if (PORT / d).exists():
            shutil.copytree(PORT / d, cport / d, ignore=shutil.ignore_patterns('__pycache__', '*.log', '*.pyc', *GAME_CODE))
    for f in ('runtime.toml',):
        if (PORT / f).exists(): shutil.copy2(PORT / f, cport / f)
    shutil.copy2(PORT/'builder-revision.json',cport/'builder-revision.json')
    shutil.copytree(PORT / 'vendor', cport / 'vendor')           # includes the pinned git checkout
    (cport / 'build').mkdir(parents=True)
    (dest / 'orig').mkdir(); (dest / 'export').mkdir()
    shutil.copy2(args.dol, dest / 'orig/main.dol'); shutil.copy2(args.index, dest / 'export/index.tsv')
    if not release.reference:
        # The stages name reference (PAL) addresses; rewrite them to this release's before anything runs.
        unmapped = region.rebase_tree(cport, release)
        toml = cport / 'runtime.toml'
        toml.write_bytes(region.runtime_toml(toml.read_bytes().decode(), release).encode())
        (dest / 'region-rebase.json').write_text(json.dumps({'release': release.game_id, 'unmapped': unmapped}, indent=1) + '\n')
        print('stage tree rebased to', release.game_id, '|', len(unmapped), 'files keep reference-only addresses')
    print('clean tree ready:', dest)
    print('@progress|2.0|Build files ready', flush=True)
    print('inputs: main.dol sha256', sha(dest / 'orig/main.dol')[:16], '| index rows', sum(1 for _ in open(dest / 'export/index.tsv')))
    if args.no_build: raise SystemExit

    # ---- 2. dependency cache (SDL2 / toml++ / glad sources are fetched by CMake; reuse ours to stay offline-capable)
    if args.reuse_deps:
        cached = args.deps or default_storage() / 'nwii-full/_deps'
        if cached.exists():
            (storage / 'nwii-full').mkdir(parents=True, exist_ok=True)
            shutil.copytree(cached, storage / 'nwii-full/_deps', dirs_exist_ok=True)

(dest/'build-request.json').write_text(json.dumps(wanted, indent=2)+'\n')

# ---- 3. the normal pipeline, with generation
start = time.monotonic()
builder = str(cport / 'tools/build_native.py')
common = [sys.executable, builder, '--storage', str(storage), '--jobs', str(args.jobs), '--dol', str(args.dol.resolve()), '--index', str(args.index.resolve())]
log = dest / 'clean-room-build.log'

# ---- progress for the launcher: "@progress|<percent>|<text>" lines on stdout. Everything the pipeline prints goes to the log file.
# Rough time split of a full build: translation/patch stages ~25%, the final compile ~75%. Percent never goes backwards, so a repeated
# patch pass cannot move the bar back.
import re, threading
_emit_lock = threading.Lock()
_progress = {'pct': 2.0}   # the two announcements before this point already reported 0.5 and 2.0


def emit(pct, text):
    with _emit_lock:
        _progress['pct'] = max(_progress['pct'], min(100.0, pct))
        print('@progress|%.1f|%s' % (_progress['pct'], text), flush=True)


PHASES = {   # stage -> (start %, end %, label); ninja's own [n/m] counter fills the range
    'native-configure': (3, 4, 'Configuring the build'),
    'native-generator-build': (4, 16, 'Building the recompiler'),
    'native-translation': (16, 20, 'Translating the game code'),
    'native-game-build': (40, 100, 'Compiling the game'),
}
STAGE_RE = re.compile(r'^native-[a-z0-9-]+$')
NINJA_RE = re.compile(rb'\[(\d+)/(\d+)\]')
PATCH_STAGES_EXPECTED = 190


class NinjaMonitor(threading.Thread):
    """Follows ninja's [n/m] counter in the active stage's own log file."""
    def __init__(self):
        super().__init__(daemon=True)
        self.stage, self.done, self.last = None, False, None

    def run(self):
        while not self.done:
            stage = self.stage
            if stage in PHASES:
                lo, hi, label = PHASES[stage]
                try:
                    with (cport / 'reports' / (stage + '.log')).open('rb') as fh:
                        fh.seek(0, 2)
                        fh.seek(max(0, fh.tell() - 4096))
                        found = NINJA_RE.findall(fh.read())
                    if found:
                        n, t = (int(x) for x in found[-1])
                        if (stage, n) != self.last:
                            self.last = (stage, n)
                            emit(lo + (hi - lo) * n / max(t, 1), '%s: %d of %d' % (label, n, t))
                except OSError:
                    pass
            time.sleep(1.0)


monitor = NinjaMonitor()
monitor.start()
patch_count = [0]
translated = [False]


def pretty(stage):
    return stage[len('native-'):].replace('-restage', '').replace('-stage', '').replace('-', ' ')


def run_pass(extra, logf):
    """Run one build_native pass, tee its output to the log and report progress. Returns (exit code, reached the final compile)."""
    proc = subprocess.Popen(common + extra, cwd=dest, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    reached_compile = False
    for raw in proc.stdout:
        logf.write(raw)
        logf.flush()
        line = raw.decode('utf-8', 'replace').strip()
        if not STAGE_RE.match(line):
            continue
        monitor.stage = line
        if line == 'native-game-build':
            reached_compile = True
        if line in PHASES:
            emit(PHASES[line][0], PHASES[line][2])
        elif line == 'native-translation-inventory':
            translated[0] = True
        elif not translated[0]:
            emit(_progress['pct'], 'Preparing: ' + pretty(line))   # before translation these only set up the tree
        else:
            patch_count[0] += 1
            emit(20 + 20 * min(1.0, patch_count[0] / PATCH_STAGES_EXPECTED), 'Applying patch: ' + pretty(line))
    return proc.wait(), reached_compile


def compile_remaining(logf, jobs):
    stage = 'native-game-build-recovery'
    PHASES[stage] = (max(40, _progress['pct']), 100, 'Finishing remaining compilation')
    monitor.stage = stage
    jobs = compile_jobs(jobs)
    message = f'[clean-room] continuing unfinished build with {jobs} compiler worker(s)'
    print(message, flush=True); logf.write((message+'\n').encode()); logf.flush()
    with (cport/'reports'/(stage+'.log')).open('wb') as stage_log:
        proc = subprocess.Popen(['cmake','--build',str(storage/'toolchain'),'--target','CrashNative','-j',str(jobs)],
            cwd=dest, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for raw in proc.stdout:
            stage_log.write(raw); stage_log.flush(); logf.write(raw); logf.flush()
        return proc.wait()

# A clean tree needs generation/staging; a verified final-compile cache does not.
with log.open('ab' if resume_compile else 'wb') as f:
    if resume_compile:
        rc = compile_remaining(f, args.jobs)
        reached_compile = True
    else:
        rc, _ = run_pass(['--generate-only'], f)
        reached_compile = False
        if rc == 0:
            for attempt in range(1, 4):
                rc, reached_compile = run_pass(['--skip-generation']+(['--stage-only'] if args.stage_only else []), f)
                if rc == 0 or reached_compile or attempt == 3:
                    break
                msg = f'[clean-room] patch pass {attempt} stopped before the compile (exit {rc}); repeating the idempotent patch pass'
                f.write((msg+'\n').encode()); f.flush(); print(msg, flush=True)
                emit(_progress['pct'], 'Repeating the patch pass')
    if rc and reached_compile and not args.stage_only:
        failure = cport/'reports'/('native-game-build-recovery.log' if resume_compile else 'native-game-build.log')
        if failure.exists() and memory_failure(failure.read_text(errors='replace')):
            print('Compiler memory exhausted; retaining objects and retrying with one worker.', flush=True)
            emit(_progress['pct'], 'Retrying compilation with one worker after memory exhaustion')
            rc = compile_remaining(f, 1)
monitor.done = True
if rc == 0:
    emit(100, 'Build finished')
print('pipeline exit', rc, 'in %.0f s' % (time.monotonic() - start), '| log', log)
if rc:
    print(log.read_text(errors='replace')[-5000:])
    raise SystemExit(rc)
if args.stage_only:
    print('Fresh generated/staged sources qualified; game compilation was not requested.')
    raise SystemExit(0)
from builder_revision import digest as inputs_digest
runtime_metadata=dict(builder_revision=patch_identity['revision'],builder_inputs=inputs_digest(patch_identity),dol_sha256=sha(args.dol),
                      executable_sha256=sha(storage/'toolchain/CrashNative.exe'))
(storage/'toolchain/runtime-build.json').write_text(json.dumps(runtime_metadata,indent=2)+'\n')
if (PORT / 'build/crash-generated').exists(): compare()   # a shipped builder has no long-lived tree to compare with
