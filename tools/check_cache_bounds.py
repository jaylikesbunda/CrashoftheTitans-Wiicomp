"""NATIVE_CACHE_BOUNDS_V1 oracle: stage idempotence/restore on a scratch copy, then GL-free fixtures built from the
patched production functions (no game, no GL context, no change to the real staged tree)."""
from pathlib import Path
import os, re, shutil, subprocess, tempfile
from port_paths import winlibs_bin
import fix_cache_bounds as stage

port = Path(__file__).resolve().parents[1]
staged = port / 'build/nwii-full-source/nWiiRuntime'
overlays = port / 'runtime-overlays'
tools = port / 'tools'
MARKERS = re.compile(r'/\* NATIVE_CACHE_BOUNDS_V1:\w+ [A-Za-z0-9+/=]* \*/|/\* END_NATIVE_CACHE_BOUNDS_V1:\w+ \*/')


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    print((result.stdout + result.stderr).rstrip())
    if result.returncode:
        raise SystemExit(f'failed: {command[0]} exit {result.returncode}')


def lf(text):
    return text.replace('\r\n', '\n')


def cut(text, begin, end):
    a = text.index(begin)
    return text[a:text.index(end, a)]


with tempfile.TemporaryDirectory(prefix='cache-bounds-') as temp:
    temp = Path(temp)
    rt = temp / 'rt'
    originals = {}
    for rel, _ in stage.FILES:
        (rt / rel).parent.mkdir(parents=True, exist_ok=True)
        # The staged tree may already carry the stage (a build ran it): the scratch copy starts from the canonical text.
        text = stage.unblock(stage.read(staged / rel))
        stage.write(rt / rel, text)
        originals[rel] = (rt / rel).read_bytes()
    shutil.copyfile(overlays / 'native_perf_census.h', rt / 'include/runtime/native_perf_census.h')

    stage.apply(port, rt)
    first = {rel: (rt / rel).read_bytes() for rel, _ in stage.FILES}
    assert all(first[rel] != originals[rel] for rel, _ in stage.FILES), 'every file must change'
    stage.apply(port, rt)
    assert first == {rel: (rt / rel).read_bytes() for rel, _ in stage.FILES}, 'second apply must be a no-op'
    patched = {rel: stage.read(rt / rel) for rel, _ in stage.FILES}
    for rel, text in patched.items():
        assert '\r\n' not in text or text.count('\r\n') == text.count('\n'), rel + ': mixed line endings'
    print('STAGE_IDEMPOTENT ok')

    shutil.copytree(rt, temp / 'rt2')
    stage.restore(port, temp / 'rt2')
    assert all((temp / 'rt2' / rel).read_bytes() == originals[rel] for rel, _ in stage.FILES), 'restore must be exact'
    assert not (temp / 'rt2' / stage.HEADER).exists()
    stage.apply(port, temp / 'rt2')
    assert all((temp / 'rt2' / rel).read_bytes() == first[rel] for rel, _ in stage.FILES)
    print('STAGE_RESTORE ok')

    renderer = lf(patched['src/hle/gx/renderer_gl.cpp'])
    texture = lf(patched['src/hle/texture_cache_gl.cpp'])
    original_texture = lf(originals['src/hle/texture_cache_gl.cpp'].decode('utf-8', 'surrogateescape'))

    def functions(text, names):
        return '\n'.join(stage.function_text(text, f'void TextureCache::{name}() {{') for name in names)

    texture_code = (functions(texture, ['evict_old', 'prune_hot_keys', 'note_cache_size']).replace('TextureCache::', 'NewCache::') + '\n'
                    + functions(original_texture, ['evict_old']).replace('TextureCache::', 'RefCache::'))
    uniform = stage.function_text(renderer, 'static bool NativeUniformChanged(')
    source = (tools / 'cache_bounds_tests.cpp').read_text()
    for marker, body in (('//@@TEXTURE_FUNCTIONS@@', texture_code),
                         ('//@@SHADER_STRUCT@@', cut(renderer, 'struct GLShader {', 'uint64_t g_stat_hash0')),
                         ('//@@UNIFORM_FUNCTION@@', uniform),
                         ('//@@TRIM_BLOCK@@', cut(renderer, '// NATIVE_CACHE_BOUNDS_V1: shader cache bound.', 'static void SetupDrawState(const GXCommand &cmd) {'))):
        assert source.count(marker) == 1
        source = source.replace(marker, MARKERS.sub('', body))
    # The uniform function carries the bound as a literal (the older source-reuse fixture slices it without headers).
    assert 'cache.size()>=65536' in uniform
    header = (overlays / 'native_cache_bounds.h').read_text()
    assert 'kUniformEntriesMax=65536' in header
    include = temp / 'include/runtime'
    include.mkdir(parents=True)
    (include / 'native_cache_bounds.h').write_text(header)
    compiler = shutil.which('g++') or str(winlibs_bin() / 'g++.exe')
    flags = ['-std=c++20', '-O2', '-Wall', '-Wextra', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-parameter']
    (temp / 'core.cpp').write_text(source)
    run([compiler, *flags, '-I', str(temp / 'include'), str(temp / 'core.cpp'), '-o', str(temp / 'core.exe')])
    env = {k: v for k, v in os.environ.items() if not k.startswith('NWII_')}
    run([str(temp / 'core.exe')], env=env, timeout=600)

    run([compiler, *flags, '-I', str(rt / 'include'), str(tools / 'pc_rows_tests.cpp'), '-o', str(temp / 'pc.exe')])
    run([str(temp / 'pc.exe')], env={**env, 'NWII_PERF_CENSUS': '1', 'NWII_PERF_PC_HISTOGRAM': '1'}, timeout=120)
    print('CHECK_CACHE_BOUNDS ok')
