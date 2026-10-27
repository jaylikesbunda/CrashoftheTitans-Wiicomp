"""NATIVE_SHADER_PRELOAD_V1 oracle: stage idempotence and exact restore on a scratch copy, a GL-free test of the
binary-cache reader on generated fixtures, and g++ -fsyntax-only on the overlay and the patched renderer_gl.cpp.

No game launch, no GL context, and nothing is written under the repository or the staged tree. Pass
NWII_DEPS_DIR to point at a toolchain _deps folder that holds glad/glad.h and SDL.h (the renderer includes them).
"""
from pathlib import Path
from port_paths import builds_dir, default_storage, winlibs_bin
import os, shutil, struct, subprocess, tempfile
import fix_shader_preload as stage

port = Path(__file__).resolve().parents[1]
staged = port / 'build/nwii-full-source/nWiiRuntime'
overlay = port / 'runtime-overlays/native_shader_preload.h'
LEAN_DEPS = Path('H:/NativePortBuilds/clean-room-arena-lean-20261022/storage/toolchain/_deps')

LOADER_TEST = r'''
#include "runtime/native_shader_preload.h"
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <thread>
using namespace native_shader_preload;
static int checks = 0;
static void expect(bool ok, const char *what) {
  if (!ok) { std::cout << "FAIL " << what << "\n"; std::exit(1); }
  ++checks;
}
static void wait_scanned(Loader &loader) {
  for (int i = 0; i < 30000 && !loader.scanned(); ++i) std::this_thread::sleep_for(std::chrono::milliseconds(1));
}
int main(int, char **argv) {
  const std::filesystem::path dir = argv[1];
  {  // Valid files come out newest first with their payloads; corrupt files are rejected.
    Loader loader;
    loader.start(dir);
    wait_scanned(loader);
    const Counts counts = loader.snapshot();
    expect(counts.read == 5 && counts.rejected == 5 && counts.skipped == 0, "5 valid, 5 rejected, none skipped");
    for (uint64_t i = 4; i + 1 > 0; --i) {
      Entry entry;
      expect(loader.take(entry), "queued entry");
      expect(entry.key == 0x1000 + i, "newest-first order");
      expect(entry.format == 0x8e40 + i, "format preserved");
      expect(entry.data.size() == 1000 && entry.data.front() == char(0x40 + i) && entry.data.back() == char(0x40 + i), "payload preserved");
      if (i == 0) break;
    }
    Entry none;
    expect(!loader.take(none) && loader.drained(), "queue empty after all takes");
  }
  {  // Entry bound: at most two files are read.
    Loader loader(2, size_t(64) << 20);
    loader.start(dir);
    wait_scanned(loader);
    expect(loader.snapshot().read == 2, "entry bound");
    Entry e;
    expect(loader.take(e) && loader.take(e) && !loader.take(e), "two entries queued");
  }
  {  // Queued-byte bound: 2500 bytes fits two 1000-byte binaries; nothing is taken until the scan ends.
    Loader loader(256, 2500);
    loader.start(dir);
    wait_scanned(loader);
    const Counts counts = loader.snapshot();
    expect(counts.read == 2 && counts.skipped == 3 && counts.rejected == 5, "byte bound skips without rejecting");
    Entry e;
    expect(loader.take(e) && loader.take(e) && !loader.take(e), "byte-bounded queue");
  }
  {  // Missing directory: nothing is read and the loader reports drained.
    Loader loader;
    loader.start(dir / "missing");
    wait_scanned(loader);
    expect(loader.drained() && loader.snapshot().read == 0, "missing directory");
  }
  {  // Destroying a running loader joins its thread.
    Loader loader;
    loader.start(dir);
  }
  {  // A loader never started destroys cleanly.
    Loader loader;
  }
  std::cout << "SHADER_PRELOAD_LOADER " << checks << "/" << checks << "\n";
  return 0;
}
'''


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    text = (result.stdout + result.stderr).rstrip()
    if text:
        print(text[-3000:])
    if result.returncode:
        raise SystemExit(f'failed: {command[0]} exit {result.returncode}')


def compiler():
    bin_dir = winlibs_bin()
    return str(bin_dir / 'g++.exe') if bin_dir else 'g++'


def deps():
    candidates = [os.environ.get('NWII_DEPS_DIR'), builds_dir() / 'toolchain/_deps',
                  default_storage() / 'toolchain/_deps', LEAN_DEPS]
    for candidate in candidates:
        if candidate and (Path(candidate) / 'glad-build/include/glad/glad.h').exists():
            return Path(candidate)
    raise SystemExit('glad/glad.h not found; set NWII_DEPS_DIR to a toolchain _deps folder')


def syntax(source, includes, workdir, extra=()):
    command = [compiler(), '-std=c++20', '-fsyntax-only', '-Wall', *extra]
    for include in includes:
        command += ['-I', str(include)]
    command.append(str(source))
    run(command, cwd=workdir, timeout=900)


def fixtures(directory):
    """Five valid binaries (1000 bytes, keys 0x1000..0x1004, newest is 0x1004) and five corrupt files."""
    directory.mkdir(parents=True)
    stamp = 1_700_000_000
    for i in range(5):
        key = 0x1000 + i
        payload = bytes([0x40 + i]) * 1000
        path = directory / f'{key:016x}.bin'
        path.write_bytes(struct.pack('<8sQII', b'NWPBC001', key, 0x8e40 + i, len(payload)) + payload)
        os.utime(path, (stamp + i * 10, stamp + i * 10))
    bad = {
        '0000000000002000.bin': struct.pack('<8sQII', b'XXXXXXXX', 0x2000, 1, 4) + b'abcd',
        '0000000000003001.bin': struct.pack('<8sQII', b'NWPBC001', 0x3000, 1, 4) + b'abcd',
        '0000000000004000.bin': struct.pack('<8sQII', b'NWPBC001', 0x4000, 1, 100) + b'x' * 50,
        '0000000000005000.bin': struct.pack('<8sQII', b'NWPBC001', 0x5000, 1, 10) + b'x' * 20,
        '0000000000006000.bin': struct.pack('<8sQII', b'NWPBC001', 0x6000, 1, 0),
        'notes.tmp': b'not a binary',
    }
    for name, data in bad.items():
        (directory / name).write_bytes(data)


def main():
    dependencies = deps()
    glad = dependencies / 'glad-build/include'
    sdl = dependencies / 'sdl2-src/include'
    original = stage.unblock(read_text(staged / stage.RENDERER))
    with tempfile.TemporaryDirectory(prefix='shader-preload-') as temp:
        temp = Path(temp)
        rt = temp / 'rt'
        shutil.copytree(staged / 'include', rt / 'include')
        (rt / stage.RENDERER).parent.mkdir(parents=True)
        (rt / stage.RENDERER).write_bytes(original.encode('utf-8', 'surrogateescape'))
        includes = [rt / 'include', glad, sdl]

        # Stage: apply, re-apply (no-op), restore (exact), apply again (same bytes as first apply).
        stage.apply(port, rt)
        first = (rt / stage.RENDERER).read_bytes()
        assert first != original.encode('utf-8', 'surrogateescape'), 'apply must change the renderer'
        assert (rt / stage.HEADER).read_bytes() == overlay.read_bytes(), 'overlay copied verbatim'
        stage.apply(port, rt)
        assert (rt / stage.RENDERER).read_bytes() == first, 'second apply must be a no-op'
        stage.restore(port, rt)
        assert (rt / stage.RENDERER).read_bytes() == original.encode('utf-8', 'surrogateescape'), 'restore must be exact'
        assert not (rt / stage.HEADER).exists(), 'restore removes the overlay copy'
        stage.apply(port, rt)
        assert (rt / stage.RENDERER).read_bytes() == first, 'apply after restore reproduces the first apply'
        print('SHADER_PRELOAD stage: apply, re-apply, restore and re-apply verified')

        # Loader: GL-free reader against generated fixtures.
        cases = temp / 'loader'
        cases.mkdir()
        fixtures(cases / 'cache')
        test_cpp = cases / 'loader_test.cpp'
        test_cpp.write_text(LOADER_TEST)
        test_exe = cases / 'loader_test.exe'
        run([compiler(), '-std=c++20', '-O1', '-Wall', '-Wextra', '-Werror', '-static', '-I', str(rt / 'include'),
             str(test_cpp), '-o', str(test_exe), '-pthread'], cwd=cases, timeout=900)
        run([str(test_exe), str(cases / 'cache')], cwd=cases, timeout=300)

        # Syntax: the overlay standalone (warnings are errors), the baseline renderer, then the patched renderer.
        wrapper = temp / 'overlay_wrapper.cpp'
        wrapper.write_text('#include "runtime/native_shader_preload.h"\n')
        syntax(wrapper, includes, temp, ('-Wextra', '-Werror'))
        print('SHADER_PRELOAD overlay compiles standalone')
        baseline = temp / 'baseline_renderer.cpp'
        baseline.write_bytes(original.encode('utf-8', 'surrogateescape'))
        syntax(baseline, includes + [rt / 'src/hle/gx'], temp)
        print('SHADER_PRELOAD baseline renderer_gl.cpp compiles in this environment')
        stage.apply(port, rt)
        syntax(rt / stage.RENDERER, includes + [rt / 'src/hle/gx'], temp)
        print('SHADER_PRELOAD patched renderer_gl.cpp compiles with -fsyntax-only')


def read_text(path):
    return path.read_bytes().decode('utf-8', 'surrogateescape')


if __name__ == '__main__':
    main()
