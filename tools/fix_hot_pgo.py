"""NATIVE_HOT_PGO_V1: opt-in GCC profile-guided optimisation of the promoted hot guest units (build/crash-hot/hot_*.cpp).

Nothing here changes guest semantics: only compiler flags (and, in generate mode, one extra translation unit that flushes
the profile). The floating-point/aliasing flags of the hot units are untouched (-ffp-contract=off, -fwrapv,
-fno-strict-aliasing, -march=x86-64-v3, -mfma stay from fix_hot_split.py).

This stage must run LAST, after every stage that rewrites hot-unit text (it hashes the final bytes). build_native.py runs it
after the arena stage (default off). Workflow (see --help):

  1. python tools/build_native.py --stage-only                  (stage sources as usual)
     NWII_BUILD_HOT_PGO=generate python tools/fix_hot_pgo.py    (instrumented flags + dump hook + source hashes)
     cmake --build <storage>/toolchain --target CrashNative     (instrumented exe)
  2. play the target scene; create pgo-dump.trigger next to the exe (or exit normally) to flush
     python tools/fix_hot_pgo.py --collect                      (copy the .gcda files into the profile store)
  2b. python tools/fix_hot_pgo.py --ship RQJP7D                  (copy the store into data/pgo/RQJP7D/ with per-file hashes)
  3. python tools/build_native.py --stage-only
     NWII_BUILD_HOT_PGO=use python tools/fix_hot_pgo.py         (refuses if any hot unit differs from the profiled text)
     cmake --build <storage>/toolchain --target CrashNative

Environment:
  NWII_BUILD_HOT_PGO   shipped (default) | off (removes this stage's CMake block) | generate | use
                       shipped: use the profile committed under data/pgo/<release>/ for every hot unit whose text still matches it,
                       and silently fall back to the normal flags for everything else (never fails a build; see cmd_shipped)
                       Default on. A unit is profiled only while its text matches the committed profile, so after any stage change that rewrites
                       hot units (the default arena did) the profile must be retaken: generate, play, --collect, --ship RQJP7D.
  NWII_PGO_OPT         O2 (default) | Os | O1 - the level of BOTH generate and use; GCC requires the same CFG in both
  NWII_PGO_BUILD       staged tree root (default: <port>/build; for tests)
  NWII_PGO_DIR         profile store (default: <build>/pgo)
  NWII_PGO_DROP_COLD   1: remove __attribute__((cold)) from native_guest_resume_* (reversible; use in generate AND use)
  NWII_PGO_NO_VALUES   1: generate with -fno-profile-values (no indirect-call/divisor histograms; avoids the dump-while-running race)
  NWII_PGO_STALE       skip: in use mode, units whose text changed since profiling are compiled without a profile instead of refusing

Why copy the .gcda files: on Windows GCC cannot write mangled paths under -fprofile-dir (the drive colon breaks the directory
name) and -fprofile-use=<file> is read as a directory, so the only working layout is the default one - the profile sits beside
the object file and is named after it (hot_000.cpp.obj -> hot_000.cpp.gcda). --collect and use mode copy between that layout and
the store. -Werror=missing-profile makes a missing profile a build error rather than a silent no-op.
"""
from pathlib import Path
import argparse, hashlib, json, os, re, shutil, subprocess, sys

PORT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT / 'tools'))
from port_paths import default_storage

MARK = '# NATIVE_HOT_PGO_V1'
END = '# END_NATIVE_HOT_PGO_V1'
NL = '\n'
BUILD = Path(os.environ.get('NWII_PGO_BUILD') or PORT / 'build')   # staged tree root (override for tests)
HOT = BUILD / 'crash-hot'
CMAKE = BUILD / 'nwii-full-source/CMakeLists.txt'
DUMP_TU = PORT / 'runtime-overlays/native_pgo_dump.cpp'
UNCOLD = re.compile(r'(__attribute__\(\(noinline,)cold,(optimize\("O2"\)\)\) void native_guest_resume_)')
COLD_MARK = '/*PGO_UNCOLD*/'


def store_dir():
    return Path(os.environ.get('NWII_PGO_DIR') or BUILD / 'pgo')


def toolchain():
    return Path(os.environ['NWII_BUILD_STORAGE'] if os.environ.get('NWII_BUILD_STORAGE') else default_storage()) / 'toolchain'


def object_dir(source):
    """Where CMake/Ninja puts the object (and therefore GCC puts the .gcda) of a source outside the source tree."""
    p = source.resolve().as_posix()
    p = re.sub(r'^([A-Za-z]):', r'\1_', p)
    return toolchain() / 'CMakeFiles/CrashNative.dir' / Path(p.lstrip('/')).parent


def hot_files():
    return sorted(HOT.glob('hot_*.cpp'))


def read(path):
    return path.read_bytes().decode('utf-8', 'surrogateescape')


def write(path, text):
    path.write_bytes(text.encode('utf-8', 'surrogateescape'))   # keep the file's own line endings


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gcc_version():
    try:
        return subprocess.run(['g++', '--version'], capture_output=True, text=True).stdout.splitlines()[0]
    except Exception:
        return 'unknown'


def strip_block(text):
    while MARK in text:
        a = text.index(MARK)
        b = text.find(END, a)
        text = text[:a].rstrip(NL) + NL + (text[b + len(END):].lstrip(NL) if b >= 0 else '')
    return text


def uncold(files, enable):
    """Reversible: native_guest_resume_* lose `cold` (enable) or get it back (disable). Returns number of files touched."""
    touched = 0
    for f in files:
        t = read(f)
        if enable:
            n = UNCOLD.sub(lambda m: m[1] + m[2].replace(') void', ') ' + COLD_MARK + ' void', 1), t)
        else:
            n = re.sub(r'(__attribute__\(\(noinline,)(optimize\("O2"\)\)\)) ' + re.escape(COLD_MARK) + r' void native_guest_resume_',
                       lambda m: m[1] + 'cold,' + m[2] + ' void native_guest_resume_', t)
        if n != t:
            write(f, n)
            touched += 1
    return touched


def write_block(files, options, extra=''):
    t = strip_block(read(CMAKE))
    listing = ''.join('    "' + f.as_posix() + '"' + NL for f in files)
    block = NL + MARK + NL
    if files:
        block += 'set_property(SOURCE' + NL + listing + '    APPEND PROPERTY COMPILE_OPTIONS "' + ';'.join(options) + '")' + NL
    block += extra + END + NL
    write(CMAKE, t.rstrip(NL) + NL + block)


SHIPPED = PORT / 'data/pgo'
SHIPPED_MIN_SHARE = 0.5   # a shipped profile is used only when at least this share of the hot units still match it


def manifest_path():
    return store_dir() / 'hot-pgo-manifest.json'


def cmd_off():
    if CMAKE.exists():
        write(CMAKE, strip_block(read(CMAKE)))
    print('hot PGO stage off: CMake block removed')


def current_manifest(opt):
    return dict(schema=1, opt=opt, compiler=gcc_version(), dropped_cold=os.environ.get('NWII_PGO_DROP_COLD') == '1',
                units={f.name: sha(f) for f in hot_files()})


def cmd_generate(opt):
    files = hot_files()
    if not files:
        raise SystemExit('no build/crash-hot/hot_*.cpp - stage the sources first (build_native.py --stage-only)')
    if not DUMP_TU.is_file():
        raise SystemExit('missing ' + str(DUMP_TU))
    drop = os.environ.get('NWII_PGO_DROP_COLD') == '1'
    uncold(files, drop) if drop else uncold(files, False)
    manifest = current_manifest(opt)
    store_dir().mkdir(parents=True, exist_ok=True)
    manifest_path().write_text(json.dumps(manifest, indent=1))
    for old in store_dir().glob('*.gcda'):
        old.unlink()
    for f in files:  # stale counters from an earlier instrumented run would be merged into the new profile
        g = object_dir(f) / (f.name + '.gcda')
        if g.exists():
            g.unlink()
    options = ['-' + opt, '-fprofile-generate', '-fprofile-update=single'] + (['-fno-profile-values'] if os.environ.get('NWII_PGO_NO_VALUES') == '1' else [])
    extra = 'target_sources(CrashNative PRIVATE "' + DUMP_TU.as_posix() + '")' + NL + 'target_link_options(CrashNative PRIVATE -fprofile-generate)' + NL
    write_block(files, options, extra)
    print(f'generate: {len(files)} hot units instrumented at -{opt}; manifest {manifest_path()}')
    print('next: cmake --build', toolchain(), '--target CrashNative')


def cmd_collect():
    manifest = json.loads(manifest_path().read_text())
    store_dir().mkdir(parents=True, exist_ok=True)
    copied, missing = 0, []
    for name in manifest['units']:
        src = object_dir(HOT / name) / (name + '.gcda')
        if src.is_file():
            shutil.copy2(src, store_dir() / (name + '.gcda'))
            copied += 1
        else:
            missing.append(name)
    manifest['collected'] = {n: (store_dir() / (n + '.gcda')).stat().st_size for n in manifest['units'] if (store_dir() / (n + '.gcda')).is_file()}
    manifest_path().write_text(json.dumps(manifest, indent=1))
    print(f'collected {copied} profiles into {store_dir()}; units never executed (no .gcda): {len(missing)}')
    if missing:
        print('  ' + ', '.join(missing[:12]) + (' ...' if len(missing) > 12 else ''))


def cmd_ship(release):
    """Copy the collected store into data/pgo/<release>/ and record the sha256 of every profile (the build re-checks them)."""
    manifest = json.loads(manifest_path().read_text())
    dest = SHIPPED / release
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    manifest.pop('collected', None)
    manifest['profiles'] = {}
    for n in sorted(manifest['units']):
        g = store_dir() / (n + '.gcda')
        if g.is_file():
            shutil.copy2(g, dest / g.name)
            manifest['profiles'][g.name] = sha(g)
    (dest / 'hot-pgo-manifest.json').write_text(json.dumps(manifest, indent=1) + NL)
    print(f'shipped {len(manifest["profiles"])} profiles of {len(manifest["units"])} units into {dest}')


def cmd_shipped(opt):
    """Opt-in profile use that can never fail a build. Every check that does not hold (no staged units, compiler differs, level
    differs, a profile file missing or altered, too few units matching) removes this stage's CMake block and returns, so the
    build proceeds with the normal flags. Units whose text differs from the profiled text are compiled without a profile."""
    def skip(why):
        try:
            uncold(hot_files(), False)
            cmd_off()
        except Exception:
            pass
        print('hot PGO (shipped profile) not used: ' + why)
    try:
        files = hot_files()
        if not files:
            return skip('no staged hot units')
        if not SHIPPED.is_dir():
            return skip('no shipped profile')
        uncold(files, False)
        now = dict(units={f.name: sha(f) for f in files}, compiler=gcc_version())
        best = None
        for d in sorted(p for p in SHIPPED.iterdir() if p.is_dir()):
            try:
                m = json.loads((d / 'hot-pgo-manifest.json').read_text())
                if m.get('opt') != opt or m.get('dropped_cold') or m.get('compiler') != now['compiler'] or now['compiler'] == 'unknown':
                    continue
                good = [n for n, h in now['units'].items()
                        if m['units'].get(n) == h and m.get('profiles', {}).get(n + '.gcda') and (d / (n + '.gcda')).is_file()
                        and sha(d / (n + '.gcda')) == m['profiles'][n + '.gcda']]
            except Exception:
                continue
            if best is None or len(good) > len(best[1]):
                best = (d, good)
        if best is None or len(best[1]) < SHIPPED_MIN_SHARE * len(files):
            return skip('no shipped profile matches this build (%d of %d units)' % (len(best[1]) if best else 0, len(files)))
        use = []
        for f in files:
            if f.name in best[1]:
                dest = object_dir(f)
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(best[0] / (f.name + '.gcda'), dest / (f.name + '.gcda'))
                use.append(f)
        options = ['-' + opt, '-fprofile-use', '-fprofile-correction', '-fprofile-partial-training',
                   '-Wno-missing-profile', '-Wno-coverage-mismatch', '-Wno-error=coverage-mismatch']
        write_block(use, options)
        print(f'hot PGO (shipped profile {best[0].name}): {len(use)} of {len(files)} hot units compiled with a profile at -{opt}')
    except Exception as e:
        skip('%s: %s' % (type(e).__name__, e))


def cmd_use(opt):
    if not manifest_path().is_file():
        raise SystemExit('no profile manifest in ' + str(store_dir()) + ' - run generate and --collect first')
    manifest = json.loads(manifest_path().read_text())
    if manifest.get('opt') != opt:
        raise SystemExit(f"profile was taken at -{manifest.get('opt')} but use requested -{opt}; GCC needs the same CFG (set NWII_PGO_OPT)")
    drop = os.environ.get('NWII_PGO_DROP_COLD') == '1'
    if bool(manifest.get('dropped_cold')) != drop:
        raise SystemExit('NWII_PGO_DROP_COLD differs from the profiling run')
    files = hot_files()
    uncold(files, drop) if drop else uncold(files, False)
    now = current_manifest(opt)
    if now['compiler'] != manifest['compiler']:
        raise SystemExit('compiler changed: ' + manifest['compiler'] + ' -> ' + now['compiler'])
    stale = [n for n, h in now['units'].items() if manifest['units'].get(n) != h]
    if stale and os.environ.get('NWII_PGO_STALE') != 'skip':
        raise SystemExit(f'{len(stale)} hot unit(s) differ from the profiled text (e.g. {stale[:4]}); re-profile, or NWII_PGO_STALE=skip to leave them unprofiled')
    use, unprofiled = [], []
    for f in files:
        g = store_dir() / (f.name + '.gcda')
        if f.name in stale or not g.is_file():
            unprofiled.append(f.name)
            continue
        dest = object_dir(f)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(g, dest / (f.name + '.gcda'))
        use.append(f)
    options = ['-' + opt, '-fprofile-use', '-fprofile-correction', '-fprofile-partial-training', '-Werror=missing-profile']
    write_block(use, options)
    print(f'use: {len(use)} hot units compiled with profile at -{opt}; {len(unprofiled)} left unprofiled {unprofiled[:6]}')
    print('next: cmake --build', toolchain(), '--target CrashNative')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--collect', action='store_true', help='copy .gcda files from the instrumented build tree into the profile store')
    ap.add_argument('--restore', action='store_true', help='remove this stage from CMakeLists.txt and restore the cold attribute')
    ap.add_argument('--ship', metavar='RELEASE', help='copy the collected store into data/pgo/RELEASE (committed profile for that disc release)')
    a = ap.parse_args()
    if a.ship:
        return cmd_ship(a.ship)
    if not CMAKE.is_file():
        if a.restore or os.environ.get('NWII_BUILD_HOT_PGO', 'shipped') in ('off', 'shipped'):
            return print('hot PGO stage: no staged CMakeLists.txt, nothing to do')
        raise SystemExit('staged CMakeLists.txt missing: ' + str(CMAKE))
    opt = os.environ.get('NWII_PGO_OPT', 'O2')
    if opt not in ('O2', 'Os', 'O1'):
        raise SystemExit('NWII_PGO_OPT must be O2, Os or O1')
    if a.restore:
        uncold(hot_files(), False)
        return cmd_off()
    if a.collect:
        return cmd_collect()
    mode = os.environ.get('NWII_BUILD_HOT_PGO', 'shipped')
    if mode == 'off':
        return cmd_off()
    if mode == 'generate':
        return cmd_generate(opt)
    if mode == 'use':
        return cmd_use(opt)
    if mode == 'shipped':
        return cmd_shipped(opt)
    raise SystemExit('NWII_BUILD_HOT_PGO must be off, generate, use or shipped')


if __name__ == '__main__':
    main()
