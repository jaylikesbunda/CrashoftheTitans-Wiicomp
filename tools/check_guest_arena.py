"""Checks for tools/fix_guest_arena.py and runtime-overlays/native_guest_arena.{h,cpp}. Nothing is written to the repository.

Always (a scratch copy of the staged tree under --work):
  stage      the stage applies, is idempotent, and --restore returns every byte (CRLF / LF mixes included); every anchor
             in cpu_context.h, native_guest_fast.h, CMakeLists.txt, virtual_disc.cpp, native_savestate_ios.inc and the generated
             sources matches exactly once / the expected number of times
  fold       fix_watch_fold.py still applies on top of / underneath the arena stage and both restore to the original text
  alias      every mirror of both banks sees the same bytes as MMU::mem1/mem2 and the 0x20000000 / 0x30000000 windows
  fault      >= 1.2M randomised loads and stores (every width, MMIO, write-gather, page 0 word 12, holes, bank edges, watched pages)
             through the MMU, the previous accessors and the arena agree on results, RAM and the ordered MMIO / pipe / watch
             observations; longjmp out of a faulting store
  decoder    thousands of (instruction form x register x addressing mode) variants run on RAM and on a faulting address
  misc       page 0, kernel reads into trapped pages, watch hits, the fault-site recorder, the write tracker
for the layouts fixed, dynamic and mask (--layouts to choose).
Optional:
  --differential UNIT=ID,ID,...   compile a real hot unit (build/crash-hot/UNIT.cpp) with the previous and the arena accessors, run
                                   randomised guest states and compare CPUContext, RAM and the MMIO / pipe observation stream
  --measure UNIT                   host instruction counts and code size of that unit, old versus new
Usage: check_guest_arena.py [--work DIR] [--layouts fixed,dynamic,mask] [--ops N] [--differential hot_002=8028199C,80306A1C]
"""
from pathlib import Path
import argparse, filecmp, os, re, shutil, subprocess, sys, tempfile
from port_paths import winlibs_bin

PORT = Path(__file__).resolve().parents[1]
TESTS = PORT / 'tools/guest_arena_tests'
FLAGS = ['-std=c++20', '-O2', '-march=x86-64-v3', '-mfma', '-fwrapv', '-fno-strict-aliasing', '-ffp-contract=off', '-DNWII_BUILT_WITH_FMA=1',
         '-DNWII_STATIC_RAM_BASE', '-DNWII_WATCH_PAGE_MARKING']
LAYOUT_FLAGS = {'fixed': [], 'dynamic': ['-DNWII_ARENA_DYNAMIC_BASE=1'], 'mask': ['-DNWII_ARENA_MASK=1']}


def tool(name):
    found = shutil.which(name)
    if found:
        return found
    bin_dir = winlibs_bin()
    return str(bin_dir / (name + '.exe')) if bin_dir else name


def run(cmd, **kw):
    kw.setdefault('check', True)
    return subprocess.run([str(c) for c in cmd], **kw)


def digest(root):
    out = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def build_scratch(work, units):
    """A scratch native-port tree: staged runtime sources, a few generated units, the three new overlays."""
    port = work / 'port'
    if port.exists():
        shutil.rmtree(port)
    build = port / 'build'
    runtime = build / 'nwii-full-source/nWiiRuntime'
    src = PORT / 'build/nwii-full-source/nWiiRuntime'
    shutil.copytree(src / 'include', runtime / 'include')
    for relative in ('src/hle/virtual_disc.cpp', 'src/hle/native_savestate_ios.inc', 'src/core/os_hle_stubs.cpp'):
        (runtime / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / relative, runtime / relative)
    shutil.copy2(PORT / 'build/nwii-full-source/CMakeLists.txt', build / 'nwii-full-source/CMakeLists.txt')
    (port / 'runtime-overlays').mkdir(parents=True)
    for name in ('native_guest_arena.h', 'native_guest_arena_fifo.h', 'native_guest_arena.cpp', 'native_watch_fold.h'):
        shutil.copy2(PORT / 'runtime-overlays' / name, port / 'runtime-overlays' / name)
    for directory in ('crash-hot', 'crash-split', 'crash-generated'):
        (build / directory).mkdir()
    for unit in units:
        shutil.copy2(PORT / 'build/crash-hot' / (unit + '.cpp'), build / 'crash-hot' / (unit + '.cpp'))
    splits = sorted((PORT / 'build/crash-split').glob('output_4*.cpp'))[:2]
    for path in splits:
        shutil.copy2(path, build / 'crash-split' / path.name)
    shutil.copy2(PORT / 'build/crash-generated/functions.h', build / 'crash-generated/functions.h')
    return port


def stage(port, *flags, restore=False, mode='fixed', extra_env=None):
    env = dict(os.environ, NWII_BUILD_GUEST_ARENA='1', **(extra_env or {}))
    cmd = [sys.executable, PORT / 'tools/fix_guest_arena.py', '--port', port, '--mode', mode] + ([ '--restore'] if restore else []) + list(flags)
    return run(cmd, env=env, capture_output=True, text=True).stdout.strip()


def check_tracker_default():
    """The software write tracker is on unless NWII_ARENA_TRACK=0 (the default flip is one line in the overlay)."""
    text = (PORT / 'runtime-overlays/native_guest_arena.cpp').read_text()
    assert "(!track || track[0] != '0')" in text, 'NWII_ARENA_TRACK default is not on'
    assert "track[0] == '1'" not in text, 'opt-in tracker check still present'
    print('tracker default: on (NWII_ARENA_TRACK=0 opts out)')


def check_text():
    """The generated-code rewrite on hand-written statements: ordinary stores, write-gather sites, hardware-touching functions."""
    sys.path.insert(0, str(PORT / 'tools'))
    import fix_guest_arena as F
    body = (
        'NWII_GUEST_ABI void func_80300000(NWII_CTX_PARAM ctx) {\n'
        '    ctx.mmu.write32(g3 + 8, g4); // stw\n'
        '    ctx.mmu.write_f32(g29 + -32768, (float)f0); // stfs: pipe\n'
        '    ctx.mmu.write8(ctx.gpr[7] + -32768, g0); // stb: pipe\n'
        '    g5 = -872382464; // lis 0xCC008000\n'
        '    ctx.mmu.write32(g5, g0); // pipe by a known base\n'
        '    g5 = g6 + 4;\n'
        '    ctx.mmu.write32(g5, g0); // the base changed: ordinary\n'
        '    ctx.mmu.write_f64(ea, f1);\n'
        '    ctx.psq_store_val(f2, p2, g30 + -32768, 0, 0);\n'
        '}\n'
        'NWII_GUEST_ABI void func_803B4020(NWII_CTX_PARAM ctx) {\n'
        '    ctx.mmu.write32(g3 + 8, g4); // a hardware-touching function keeps the checked store\n'
        '}\n')
    out = F.arena_source(body, F.slow_functions(PORT))
    for want in ('native_arena::st32(ctx.mmu, g3 + 8, g4)', 'native_arena::fifof32(ctx.mmu, g29 + -32768,', 'native_arena::fifo8(ctx.mmu, ctx.gpr[7] + -32768,',
                 'native_arena::fifo32(ctx.mmu, g5, g0); // pipe by a known base', 'native_arena::st32(ctx.mmu, g5, g0); // the base changed', 'native_arena::stf64(ctx.mmu, ea, f1)',
                 'ctx.psq_store_fifo(f2, p2, g30 + -32768', 'ctx.mmu.write32(g3 + 8, g4); // a hardware-touching'):
        assert want in out, want
    assert F.restore_generated(out) == body, 'generated text restore is not exact'
    assert F.arena_source(out, F.slow_functions(PORT)) == out, 'rewrite is not idempotent'
    print('text: stores, write-gather sites (displacement and known-base), psq pipe stores and the hardware function list rewrite and restore exactly')


def check_stage(work, units):
    port = build_scratch(work, units)
    pristine = work / 'pristine'
    if pristine.exists():
        shutil.rmtree(pristine)
    shutil.copytree(port / 'build', pristine)
    for mode in ('fixed', 'dynamic', 'mask'):
        stage(port, mode=mode)
        first = digest(port / 'build')
        stage(port, mode=mode)
        assert digest(port / 'build') == first, 'stage is not idempotent (%s)' % mode
        runtime = port / 'build/nwii-full-source/nWiiRuntime/include/runtime'
        for name in ('native_guest_arena.h', 'native_guest_arena_fifo.h'):
            assert (runtime / name).exists(), name
        text = (runtime / 'cpu_context.h').read_bytes().decode('utf-8', 'surrogateescape')
        assert text.count('NATIVE_GUEST_ARENA_V1') >= 16, 'cpu_context.h edits missing'
        assert 'native_arena::ldf32' in text and 'psq_store_fifo' in text
        cmake = (port / 'build/nwii-full-source/CMakeLists.txt').read_text()
        assert 'native_guest_arena.cpp' in cmake and ('NWII_ARENA_MASK=1' in cmake) == (mode == 'mask')
        assert ('# NATIVE_GUEST_ARENA_V1 OFF' in cmake) == (mode != 'mask')
        generated = ''.join(p.read_bytes().decode('utf-8', 'surrogateescape') for p in (port / 'build/crash-hot').glob('*.cpp'))
        assert 'native_arena::st32(ctx.mmu, ' in generated or 'native_arena::stf32(ctx.mmu, ' in generated, 'no generated store was rewritten'
        stores = len(re.findall(r'native_arena::(?:st|fifo)\w+\(ctx\.mmu, ', generated))
        fifo = len(re.findall(r'native_arena::fifo\w*\(ctx\.mmu, ', generated))
        print('  %-7s staged: %d generated stores rewritten (%d write-gather sites), idempotent' % (mode, stores, fifo))
        stage(port, restore=True, mode=mode)
        assert digest(port / 'build') == digest(pristine), 'restore is not exact (%s)' % mode
    # Defaults: NWII_BUILD_GUEST_ARENA unset stages the arena (dynamic layout); =0 restores the exact pre-arena text; unset on a tree
    # without the fixed-RAM build stays off instead of failing.
    def plain(**over):
        env = {k: v for k, v in os.environ.items() if k not in ('NWII_BUILD_GUEST_ARENA', 'NWII_BUILD_GUEST_ARENA_MODE')}
        env.update(over)
        return run([sys.executable, PORT / 'tools/fix_guest_arena.py', '--port', port], env=env, capture_output=True, text=True).stdout
    cmake_path = port / 'build/nwii-full-source/CMakeLists.txt'
    plain()
    assert 'NWII_ARENA_DYNAMIC_BASE=1' in cmake_path.read_text() and 'native_guest_arena.cpp' in cmake_path.read_text(), 'unset does not stage the dynamic arena'
    first = digest(port / 'build')
    plain()
    assert digest(port / 'build') == first, 'default stage is not idempotent'
    plain(NWII_BUILD_GUEST_ARENA_MODE='fixed')
    assert 'NWII_ARENA_DYNAMIC_BASE' not in cmake_path.read_text(), 'NWII_BUILD_GUEST_ARENA_MODE=fixed did not switch layout'
    plain(NWII_BUILD_GUEST_ARENA='0')
    assert digest(port / 'build') == digest(pristine), 'NWII_BUILD_GUEST_ARENA=0 does not restore the exact pre-arena text'
    plain(NWII_BUILD_GUEST_ARENA='0')
    assert digest(port / 'build') == digest(pristine), 'opt-out restore is not idempotent'
    original_cmake = cmake_path.read_bytes()
    cmake_path.write_bytes(original_cmake.replace(b'NWII_STATIC_RAM_BASE', b'NWII_NO_STATIC_RAM'))
    before = digest(port / 'build')
    message = plain()
    assert digest(port / 'build') == before and 'staying off' in message, 'default arena must stay off without the fixed-RAM build'
    cmake_path.write_bytes(original_cmake)
    assert digest(port / 'build') == digest(pristine)
    print('defaults: unset stages the dynamic arena, =0 restores exactly, unset without the fixed-RAM build stays off')
    # hot-only stores: the cold chunks keep the checked MMU stores
    stage(port, extra_env={'NWII_BUILD_GUEST_ARENA_STORES': 'hot'})
    cold = ''.join(p.read_bytes().decode('utf-8', 'surrogateescape') for p in (port / 'build/crash-split').glob('*.cpp'))
    hot = ''.join(p.read_bytes().decode('utf-8', 'surrogateescape') for p in (port / 'build/crash-hot').glob('*.cpp'))
    assert 'native_arena::st' not in cold and 'native_arena::st' in hot, 'NWII_BUILD_GUEST_ARENA_STORES=hot'
    stage(port, restore=True)
    assert digest(port / 'build') == digest(pristine), 'restore after hot-only staging is not exact'
    print('stage: apply / idempotent / exact restore passed for fixed, dynamic and mask (and hot-only stores)')
    return port


def check_fold(work, port):
    """fix_watch_fold.py composes with the arena stage in either order."""
    fold = PORT / 'tools/fix_watch_fold.py'
    runtime = port / 'build/nwii-full-source/nWiiRuntime'
    original = digest(runtime)
    for order in ('fold-then-arena', 'arena-then-fold'):
        env = dict(os.environ, NWII_BUILD_WATCH_FOLD='1')
        env.pop('NWII_BUILD_GUEST_ARENA', None)
        # fix_watch_fold computes PORT from its own location: point it at the scratch runtime
        def run_fold(*extra):
            run([sys.executable, fold, '--runtime', runtime] + list(extra), env=env, capture_output=True)
        # the fold stage needs the real overlay next to the staged tree; the scratch port has it
        if order == 'fold-then-arena':
            run_fold(); folded = digest(runtime); stage(port)
        else:
            stage(port); run_fold(); folded = None
        stage(port, restore=True)
        if order == 'fold-then-arena':
            assert digest(runtime) == folded, 'arena restore did not return the folded text'
        run_fold('--restore')
        after = digest(runtime)
        after.pop('include/runtime/native_watch_fold.h', None)
        base = dict(original)
        base.pop('include/runtime/native_watch_fold.h', None)
        assert after == base, 'fold + arena (%s) do not restore to the original text' % order
    print('fold: fix_watch_fold.py composes with the arena stage in both orders')


def compile_test(work, name, layout, sources, include, extra=()):
    exe = work / ('%s_%s.exe' % (name, layout))
    cmd = [tool('g++')] + FLAGS + LAYOUT_FLAGS[layout] + list(extra) + ['-I', include, '-I', work / 'old_inc', '-I', TESTS, '-I', work / 'gen'] + [str(s) for s in sources] + ['-o', exe]
    run(cmd)
    return exe


def check_runtime(work, port, layouts, ops):
    runtime_include = port / 'build/nwii-full-source/nWiiRuntime/include'
    pristine_include = work / 'pristine/nwii-full-source/nWiiRuntime/include'
    old = work / 'old_inc/old'
    old.mkdir(parents=True, exist_ok=True)
    for name in ('native_fastmem.h', 'native_guest_fast.h'):
        shutil.copy2(pristine_include / 'runtime' / name, old / name)
    gen = work / 'gen'
    gen.mkdir(exist_ok=True)
    env = dict(os.environ)
    env['PATH'] = str(winlibs_bin() or '') + os.pathsep + env['PATH']
    for layout in layouts:
        stage(port, mode=layout)
        include = port / 'build/nwii-full-source/nWiiRuntime/include'
        arena = port / 'runtime-overlays/native_guest_arena.cpp'
        prefix = 'ds' if layout == 'mask' else ''
        run([sys.executable, TESTS / 'gen_decoder_variants.py', gen / 'decoder_variants', '4000', '11'] + ([prefix] if prefix else []))
        exe = compile_test(work, 'alias', layout, [TESTS / 'arena_alias.cpp', arena], include) if layout != 'mask' else None
        if exe:
            out = run([exe], env=env, capture_output=True, text=True, cwd=work).stdout
            assert 'arena alias passed' in out, out
            print('alias [%s]: %s' % (layout, out.strip()))
        exe = compile_test(work, 'fault', layout, [TESTS / 'arena_fault.cpp', arena], include)
        out = run([exe, str(ops)], env=env, capture_output=True, text=True, cwd=work).stdout
        assert 'arena fault parity passed' in out and 'FAIL' not in out and '[MMU vs arena]' not in out, out
        print('fault [%s]: %s' % (layout, out.strip().splitlines()[-1]))
        exe = compile_test(work, 'decoder', layout, [TESTS / 'arena_decoder.cpp', gen / 'decoder_variants.S', arena], include)
        out = run([exe], env=env, capture_output=True, text=True, cwd=work).stdout
        assert 'arena decoder passed' in out, out
        print('decoder [%s]: %s' % (layout, out.strip()))
        exe = compile_test(work, 'misc', layout, [TESTS / 'arena_misc.cpp', arena] + ([PORT / 'runtime-overlays/ram_write_watch.cpp'] if layout == 'mask' else []), include)
        for track in (False, True) if layout != 'mask' else (False,):
            e = dict(env)
            e['NWII_ARENA_TRACK'] = '1' if track else '0'  # the tracker is the default; '0' is the opt-out
            out = run([exe], env=e, capture_output=True, text=True, cwd=work).stdout
            assert 'arena misc passed' in out, out
        print('misc [%s]: page 0, kernel reads, watch, recorder%s passed' % (layout, '' if layout == 'mask' else ', tracker'))
        stage(port, restore=True, mode=layout)


def differential(work, port, spec, trials):
    unit, ids = spec.split('=')
    env = dict(os.environ)
    env['PATH'] = str(winlibs_bin() or '') + os.pathsep + env['PATH']
    targets = ','.join('{"func_%s", func_%s, 0x%su}' % (i, i, i) for i in ids.split(','))
    results = {}
    for tree in ('old', 'new'):
        stage(port, restore=(tree == 'old'))
        build = port / 'build'
        unit_path = PORT / 'build/crash-hot' / (unit + '.cpp')
        stubs = work / ('stubs_' + tree)
        stubs.mkdir(exist_ok=True)
        run([sys.executable, TESTS / 'gen_stubs.py', unit_path, stubs / 'unit_stubs.inc'])
        include = build / 'nwii-full-source/nWiiRuntime/include'
        extra = [port / 'runtime-overlays/native_guest_arena.cpp'] if tree == 'new' else [PORT / 'runtime-overlays/static_ram_base.cpp']
        # the previous allocator is only present while the stage is restored
        exe = work / ('diff_%s.exe' % tree)
        run([tool('g++')] + FLAGS + ['-Os', '-DUNIT_PATH="%s"' % unit_path.as_posix(), '-DTARGETS=' + targets, '-I', stubs, '-I', TESTS, '-I', include,
             '-I', PORT / 'build/crash-generated', TESTS / 'diff_harness.cpp'] + [str(e) for e in extra] + ['-o', exe])
        out = work / ('diff_%s.txt' % tree)
        with open(out, 'w') as handle:
            run([exe, str(trials)], env=env, stdout=handle, stderr=subprocess.DEVNULL)
        results[tree] = out.read_bytes()
    stage(port, restore=True)
    assert results['old'] == results['new'], 'differential run differs (diff %s %s)' % (work / 'diff_old.txt', work / 'diff_new.txt')
    print('differential %s: %d trials of %d functions identical (CPUContext, RAM, MMIO / pipe stream)' % (unit, results['new'].count(b'\n'), len(ids.split(','))))


def measure(work, port, unit):
    env = dict(os.environ)
    env['PATH'] = str(winlibs_bin() or '') + os.pathsep + env['PATH']
    sizes = {}
    for tree in ('old', 'new'):
        stage(port, restore=(tree == 'old'))
        include = port / 'build/nwii-full-source/nWiiRuntime/include'
        obj = work / ('%s_%s.o' % (tree, unit))
        run([tool('g++')] + FLAGS + ['-Os', '-I', include, '-I', PORT / 'build/crash-generated', '-c', PORT / 'build/crash-hot' / (unit + '.cpp'), '-o', obj])
        text = run([tool('size'), obj], capture_output=True, text=True).stdout.splitlines()[1].split()[0]
        dump = run([tool('objdump'), '-d', '--no-show-raw-insn', obj], capture_output=True, text=True).stdout
        sizes[tree] = (int(text), sum(1 for line in dump.splitlines() if re.match(r'^\s+[0-9a-f]+:\s', line)))
    stage(port, restore=True)
    print('measure %s: text %d -> %d bytes (%.1f%%), host instructions %d -> %d (%.1f%%)' % (
        unit, sizes['old'][0], sizes['new'][0], 100.0 * sizes['new'][0] / sizes['old'][0] - 100,
        sizes['old'][1], sizes['new'][1], 100.0 * sizes['new'][1] / sizes['old'][1] - 100))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', type=Path, default=None)
    ap.add_argument('--layouts', default='dynamic,fixed', help='comma separated: dynamic,fixed,mask')
    ap.add_argument('--ops', type=int, default=20000, help='randomised accesses per watch configuration (x3 accessor sets x4 configurations)')
    ap.add_argument('--differential', action='append', default=[], metavar='UNIT=ID,ID')
    ap.add_argument('--trials', type=int, default=200)
    ap.add_argument('--measure', action='append', default=[], metavar='UNIT')
    args = ap.parse_args()
    if os.name != 'nt':
        print('guest arena checks need Windows')
        return
    work = args.work or Path(tempfile.mkdtemp(prefix='guest-arena-'))
    work.mkdir(parents=True, exist_ok=True)
    units = sorted({s.split('=')[0] for s in args.differential} | set(args.measure)) or ['hot_000']
    check_text()
    check_tracker_default()
    port = check_stage(work, units)
    check_fold(work, port)
    check_runtime(work, port, [x for x in args.layouts.split(',') if x], args.ops)
    for spec in args.differential:
        differential(work, port, spec, args.trials)
    for unit in args.measure:
        measure(work, port, unit)
    print('Guest arena checks passed (work directory: %s).' % work)


if __name__ == '__main__':
    main()
