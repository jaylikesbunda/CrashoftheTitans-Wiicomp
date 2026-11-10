"""Harness for tools/fix_hot_pgo.py (NATIVE_HOT_PGO_V1): scope, shipped-profile matching, stale units, path-length guard, rollback.

Works on a synthetic staged tree (NWII_PGO_BUILD / NWII_PGO_SHIPPED / NWII_BUILD_STORAGE), so it needs no build and no game files.
It does need a g++ (the stage records and compares the compiler version): PATH, else the launcher's builder-pack toolchain.
"""
from pathlib import Path
import hashlib, importlib.util, json, os, re, shutil, subprocess, sys, tempfile

PORT = Path(__file__).resolve().parents[1]
STAGE = PORT / 'tools/fix_hot_pgo.py'
sys.path.insert(0, str(PORT / 'tools'))
_spec = importlib.util.spec_from_file_location('fix_hot_pgo', STAGE)
pgo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pgo)   # the stage's own hashing, so the harness cannot drift from it
failures = 0


def check(cond, msg):
    global failures
    print(('ok   ' if cond else 'FAIL ') + msg)
    failures += 0 if cond else 1


def gxx_dir():
    found = shutil.which('g++')
    if found:
        return str(Path(found).parent)
    pack = PORT / 'launcher/src-tauri/builder-pack/toolchain/mingw64/bin'
    return str(pack) if (pack / 'g++.exe').is_file() else None


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stage(build, shipped, storage, mode, scope=None, extra=()):
    env = dict(os.environ, NWII_PGO_BUILD=str(build), NWII_PGO_SHIPPED=str(shipped), NWII_BUILD_STORAGE=str(storage),
               NWII_BUILD_HOT_PGO=mode)
    env.pop('NWII_PGO_SCOPE', None)
    if scope:
        env['NWII_PGO_SCOPE'] = scope
    d = gxx_dir()
    if d:
        env['PATH'] = d + os.pathsep + env['PATH']
    p = subprocess.run([sys.executable, str(STAGE), *extra], env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def make_tree(root, hot=3, chunks=4, split=(1,)):
    build = root / 'build'
    for sub in ('crash-hot', 'crash-generated', 'crash-split', 'nwii-full-source'):
        (build / sub).mkdir(parents=True)
    (build / 'nwii-full-source/CMakeLists.txt').write_text('project(x)\n')
    for i in range(hot):
        (build / f'crash-hot/hot_{i:03d}.cpp').write_text(f'void hot{i}() {{}}\n/*PGO_UNCOLD*/' if False else f'void hot{i}() {{}}\n')
    for i in range(chunks):
        (build / f'crash-generated/output_{i}.cpp').write_text(f'void gen{i}() {{}}\n')
    for i in split:   # a split copy replaces the generated file in the unit list
        (build / f'crash-split/output_{i}.cpp').write_text(f'void split{i}() {{}}\n')
    return build


def units_of(build):
    hot = sorted((build / 'crash-hot').glob('hot_*.cpp'))
    gen = sorted((build / 'crash-split' / c.name if (build / 'crash-split' / c.name).exists() else c)
                 for c in (build / 'crash-generated').glob('output_*.cpp'))
    return hot, gen


def make_shipped(shipped, release, build, units, compiler, opt='O2'):
    d = shipped / release
    d.mkdir(parents=True)
    manifest = dict(schema=1, opt=opt, compiler=compiler, dropped_cold=False, units={}, profiles={})
    for f in units:
        manifest['units'][f.name] = pgo.unit_sha(f)
        data = b'gcda:' + f.name.encode()
        (d / (f.name + '.gcda')).write_bytes(data)
        manifest['profiles'][f.name + '.gcda'] = sha(data)
    (d / 'hot-pgo-manifest.json').write_text(json.dumps(manifest))
    return manifest


def block(build):
    t = (build / 'nwii-full-source/CMakeLists.txt').read_text()
    return t[t.index('# NATIVE_HOT_PGO_V1'):] if '# NATIVE_HOT_PGO_V1' in t else ''


def compiler_of():
    d = gxx_dir()
    env = dict(os.environ, PATH=(d + os.pathsep + os.environ['PATH']) if d else os.environ['PATH'])
    return subprocess.run(['g++', '--version'], env=env, capture_output=True, text=True).stdout.splitlines()[0]


def main():
    if not gxx_dir():
        print('SKIP: no g++ on PATH and no builder-pack toolchain')
        return 0
    compiler = compiler_of()
    with tempfile.TemporaryDirectory(prefix='pgo') as tmp:
        tmp = Path(tmp)

        # 1. generate: hot units + guest chunks (split copy preferred), instrumented, with a manifest naming every unit
        build = make_tree(tmp / 'a')
        rc, out = stage(build, tmp / 'a/none', tmp / 'a/storage', 'generate')
        b = block(build)
        hot, gen = units_of(build)
        check(rc == 0, 'generate succeeds: ' + out.strip().splitlines()[-1] if out.strip() else 'generate succeeds')
        check('-fprofile-generate' in b and all(f.as_posix() in b for f in hot + gen), 'generate instruments hot units and every chunk')
        check((build / 'crash-split/output_1.cpp').as_posix() in b and (build / 'crash-generated/output_1.cpp').as_posix() not in b,
              'a split chunk is profiled instead of its generated original')
        manifest = json.loads((build / 'pgo/hot-pgo-manifest.json').read_text())
        check(set(manifest['units']) == {f.name for f in hot + gen}, 'manifest lists hot units and chunks')

        # 2. scope=hot keeps the original scope
        rc, out = stage(build, tmp / 'a/none', tmp / 'a/storage', 'generate', scope='hot')
        b = block(build)
        check(all(f.as_posix() in b for f in hot) and not any(f.as_posix() in b for f in gen), 'NWII_PGO_SCOPE=hot instruments hot units only')

        pinned = all(f.read_text().startswith('#line 1 "' + f.name + '"') for f in hot + gen)
        check(pinned, 'generate pins every unit to its own name (#line), so a profile is valid in any install folder')
        stage(build, tmp / 'a/none', tmp / 'a/storage', 'shipped', extra=('--restore',))
        check(not any(f.read_text().startswith('#line') for f in hot + gen), '--restore removes the pins')

        # 3. shipped profile that matches: used for hot units and chunks, copied beside the (future) objects
        build = make_tree(tmp / 'b')
        hot, gen = units_of(build)
        shipped = tmp / 'b/shipped'
        make_shipped(shipped, 'REL', build, hot + gen, compiler)
        rc, out = stage(build, shipped, tmp / 'b/storage', 'shipped')
        b = block(build)
        check(rc == 0 and '-fprofile-use' in b and all(f.as_posix() in b for f in hot + gen), 'shipped profile applies to hot units and chunks')
        check(all(list((tmp / 'b/storage/toolchain').rglob(f.name + '.gcda')) for f in hot + gen), 'profiles are copied next to the object files')

        # 4. a changed chunk is compiled without a profile; the others keep theirs
        victim = gen[0]
        victim.write_text('void changed() {}\n')
        shutil.rmtree(tmp / 'b/storage')
        rc, out = stage(build, shipped, tmp / 'b/storage', 'shipped')
        b = block(build)
        check(victim.as_posix() not in b and gen[1].as_posix() in b and hot[0].as_posix() in b, 'a unit whose text changed is left unprofiled')

        # 5. an old hot-only shipped profile still applies (share is measured over the units it profiled)
        build = make_tree(tmp / 'c')
        hot, gen = units_of(build)
        shipped = tmp / 'c/shipped'
        make_shipped(shipped, 'OLD', build, hot, compiler)
        rc, out = stage(build, shipped, tmp / 'c/storage', 'shipped')
        b = block(build)
        check(all(f.as_posix() in b for f in hot) and not any(f.as_posix() in b for f in gen), 'hot-only shipped profile still applies to the hot units')

        # 6. too few matching units, wrong compiler, wrong level: no block, build proceeds with the normal flags
        for n, (label, edit) in enumerate((('stale profile', lambda m: m['units'].update({k: 'x' for k in m['units']})),
                                           ('other compiler', lambda m: m.update(compiler='g++ other 1.0')),
                                           ('other level', lambda m: m.update(opt='Os')))):
            root = tmp / f'd{n}'
            build = make_tree(root)
            hot, gen = units_of(build)
            shipped = root / 'shipped'
            m = make_shipped(shipped, 'REL', build, hot + gen, compiler)
            edit(m)
            (shipped / 'REL/hot-pgo-manifest.json').write_text(json.dumps(m))
            rc, out = stage(build, shipped, root / 'storage', 'shipped')
            check(rc == 0 and block(build) == '' and 'not used' in out, f'{label}: no profile block, build continues')

        # 7. profile path over the Windows limit: unit left unprofiled
        build = make_tree(tmp / 'e')
        hot, gen = units_of(build)
        shipped = tmp / 'e/shipped'
        make_shipped(shipped, 'REL', build, hot + gen, compiler)
        deep = tmp / 'e' / ('long' * 60) / 'storage'
        rc, out = stage(build, shipped, deep, 'shipped')
        check(rc == 0 and 'over 250 characters' in out or block(build) == '', 'over-long profile paths are skipped, not failed')

        # 8. off / restore remove the block
        build = make_tree(tmp / 'f')
        stage(build, tmp / 'f/none', tmp / 'f/storage', 'generate')
        check(block(build) != '', 'block present before restore')
        stage(build, tmp / 'f/none', tmp / 'f/storage', 'shipped', extra=('--restore',))
        check(block(build) == '', '--restore removes the block')

    # unit_sha ignores the pin and the install path
    nl = chr(10)
    base = 'void f() {}' + nl
    with tempfile.TemporaryDirectory(prefix='pgo') as tmp2:
        u = Path(tmp2) / 'u.cpp'
        root = PORT.as_posix()
        u.write_text('#include "' + root + '/runtime-overlays/x.h"' + nl + base)
        h1 = pgo.unit_sha(u)
        u.write_text('#line 1 "u.cpp"' + nl + '#include "' + root + '/runtime-overlays/x.h"' + nl + base)
        h2 = pgo.unit_sha(u)
        u.write_text('#include "@PORT@/runtime-overlays/x.h"' + nl + base)
        h3 = pgo.unit_sha(u)
        check(h1 == h2 == h3, 'unit hash ignores the pin and the install path')
        u.write_text('#include "@PORT@/runtime-overlays/x.h"' + nl + 'void g() {}' + nl)
        check(pgo.unit_sha(u) != h3, 'unit hash still changes with the code')
    print('failures: %d' % failures)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
