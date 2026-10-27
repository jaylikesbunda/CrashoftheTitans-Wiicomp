"""Stage replay plus an offline smoothstep witness for the camera glide."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import fix_camera_glide as stage
from port_paths import winlibs_bin

PORT = Path(__file__).resolve().parents[1]

STUB_CONTEXT = r'''#pragma once
#include <cstdint>
#include <cstring>
namespace nwii::runtime {
struct CPUContext {
  struct MMU {
    unsigned char mem[4096]{};
    uint32_t read32(uint32_t a) { uint32_t v; std::memcpy(&v, mem + a, 4); return v; }
    void write32(uint32_t a, uint32_t v) { std::memcpy(mem + a, &v, 4); }
    uint8_t read8(uint32_t a) { return mem[a]; }
    void write8(uint32_t a, uint8_t v) { mem[a] = v; }
    float read_f32(uint32_t a) { float v; std::memcpy(&v, mem + a, 4); return v; }
    void write_f32(uint32_t a, float v) { std::memcpy(mem + a, &v, 4); }
  } mmu;
  uint32_t gpr[32]{};
};
}  // namespace nwii::runtime
'''

FIXTURE = r'''
#include "runtime/native_camera_glide.h"
#include <cstdint>
#include <cstdio>
#include <cstring>

using namespace native_camera_glide;
using nwii::runtime::CPUContext;

static bool same(float a, float b) { return std::memcmp(&a, &b, sizeof(float)) == 0; }

static CameraFrame F(float x, float y, float z, float lx, float ly, float lz) {
  CameraFrame f;
  f.position[0] = x; f.position[1] = y; f.position[2] = z;
  f.look_at[0] = lx; f.look_at[1] = ly; f.look_at[2] = lz;
  f.state = 2; f.volume_index = 0; f.interest_count = 1; f.jump_lock = false;
  return f;
}

static void emit(const char* name, bool ret, const Glide& g,
                 const CameraFrame& in, const CameraFrame& out) {
  const bool xz = same(in.position[0], out.position[0]) &&
                  same(in.position[2], out.position[2]) &&
                  same(in.look_at[0], out.look_at[0]) &&
                  same(in.look_at[2], out.look_at[2]);
  std::printf("%s %d %d %.9g %.9g %.9g %.9g %.9g %.9g %.9g %.9g %d\n",
              name, ret ? 1 : 0, g.armed ? 1 : 0, g.timer, g.duration,
              out.position[1], out.look_at[1], in.position[1], in.look_at[1],
              g.start_y, g.start_look_y, xz ? 1 : 0);
}

static void run(const char* name, Glide& g, const CameraFrame& in, float dt,
                const Limits& limits) {
  CameraFrame out;
  const bool ret = Step(g, dt, in, out, limits);
  emit(name, ret, g, in, out);
}

static void seed(Glide& g, float x, float y, float z, float ly, const Limits& limits) {
  g = Glide{};
  CameraFrame f = F(x, y, z, x, ly, z), out;
  Step(g, 0.05f, f, out, limits);  // first frame only
}

static void check_apply() {
  CPUContext ctx;
  const uint32_t self = 0x100;
  ctx.mmu.write_f32(self + 0x74, 1.0f);
  ctx.mmu.write_f32(self + 0x78, 10.0f);
  ctx.mmu.write_f32(self + 0x7c, 2.0f);
  ctx.mmu.write_f32(self + 0x80, 1.0f);
  ctx.mmu.write_f32(self + 0x84, 10.5f);
  ctx.mmu.write_f32(self + 0x88, 2.0f);
  ctx.mmu.write32(self + 0xa8, 2);
  ctx.mmu.write32(self + 0xfc, 0);
  ctx.mmu.write32(self + 0x1d4, 1);
  ctx.mmu.write32(self + 0xc4, 0x200);
  ctx.mmu.write32(0x200 + 0x04, 0x300);
  ctx.mmu.write8(0x300 + 0x3c, 0);
  Apply(ctx, self, 0.05f);  // first frame: learn the camera

  ctx.mmu.write_f32(self + 0x78, 11.0f);
  ctx.mmu.write_f32(self + 0x84, 11.5f);
  const uint32_t x0 = ctx.mmu.read32(self + 0x74), z0 = ctx.mmu.read32(self + 0x7c);
  const uint32_t lx0 = ctx.mmu.read32(self + 0x80), lz0 = ctx.mmu.read32(self + 0x88);
  Apply(ctx, self, 0.05f);
  const uint32_t x1 = ctx.mmu.read32(self + 0x74), z1 = ctx.mmu.read32(self + 0x7c);
  const uint32_t lx1 = ctx.mmu.read32(self + 0x80), lz1 = ctx.mmu.read32(self + 0x88);
  const bool xz = x0 == x1 && z0 == z1 && lx0 == lx1 && lz0 == lz1;
  std::printf("apply %d %.9g %.9g %d\n", Enabled() ? 1 : 0,
              ctx.mmu.read_f32(self + 0x78), ctx.mmu.read_f32(self + 0x84), xz ? 1 : 0);
}

int main() {
  const Limits limits;
  std::printf("enabled %d\n", Enabled() ? 1 : 0);

  {
    Glide g;
    CameraFrame f = F(1, 5, 2, 1, 5.5f, 2);
    run("first", g, f, 0.05f, limits);
    CameraFrame s = F(1, 5.05f, 2, 1, 5.55f, 2);
    run("steady", g, s, 0.05f, limits);
  }
  {
    Glide g;
    seed(g, 1, 10, 2, 10.5f, limits);
    CameraFrame f = F(1, 11, 2, 1, 11.5f, 2);
    for (int i = 1; i <= 5; ++i) {
      char name[16];
      std::snprintf(name, sizeof name, "up%d", i);
      run(name, g, f, 0.05f, limits);
    }
  }
  {
    Glide g;
    seed(g, 1, 10, 2, 10.5f, limits);
    CameraFrame f = F(1, 9, 2, 1, 9.5f, 2);
    for (int i = 1; i <= 5; ++i) {
      char name[16];
      std::snprintf(name, sizeof name, "down%d", i);
      run(name, g, f, 0.05f, limits);
    }
  }
  {
    Glide g;
    seed(g, 1, 10, 2, 10.5f, limits);
    CameraFrame f = F(1, 1, 2, 1, 1.5f, 2);
    run("fall", g, f, 0.05f, limits);
  }
  {
    Glide g;
    seed(g, 1, 10, 2, 10.5f, limits);
    CameraFrame f = F(6, 11, 2, 6, 11.5f, 2);
    run("teleport", g, f, 0.05f, limits);
  }
  {
    Glide g;
    seed(g, 1, 10, 2, 10.5f, limits);
    CameraFrame f = F(1, 19, 2, 1, 19.5f, 2);
    run("vteleport", g, f, 0.05f, limits);
  }
  {
    Glide g;
    seed(g, 1, 10, 2, 10.5f, limits);
    CameraFrame f = F(1, 11, 2, 1, 11.5f, 2);
    f.jump_lock = true;
    run("jump", g, f, 0.05f, limits);
  }
  {
    Glide g;
    seed(g, 1, 10, 2, 10.5f, limits);
    CameraFrame f = F(1, 11, 2, 1, 11.5f, 2);
    f.interest_count = 2;
    run("multi", g, f, 0.05f, limits);
  }
  {
    Glide g;
    seed(g, 1, 10, 2, 10.5f, limits);
    CameraFrame f = F(1, 11, 2, 1, 11.5f, 2);
    f.volume_index = -1;
    run("novol", g, f, 0.05f, limits);
  }
  {
    Glide g;
    seed(g, 1, 10, 2, 10.5f, limits);
    CameraFrame f = F(1, 11, 2, 1, 11.5f, 2);
    f.state = 1;
    run("state", g, f, 0.05f, limits);
  }
  {
    CameraFrame f = F(1, 11, 2, 1, 11.5f, 2);
    Glide g;
    seed(g, 1, 10, 2, 10.5f, limits);
    run("dt0", g, f, 0.0f, limits);
    seed(g, 1, 10, 2, 10.5f, limits);
    run("dtneg", g, f, -0.01f, limits);
    seed(g, 1, 10, 2, 10.5f, limits);
    run("dtmax", g, f, 0.1f, limits);
  }
  {
    Glide g;
    seed(g, 0, 0, 0, 0, limits);
    CameraFrame f = F(0, 0.3f, 0, 0, 0.5f, 0);
    run("minbound", g, f, 0.05f, limits);
    seed(g, 0, 0, 0, 0, limits);
    CameraFrame h = F(0, 8.0f, 0, 0, 0.5f, 0);
    run("maxbound", g, h, 0.05f, limits);
    seed(g, 0, 10, 0, 10.5f, limits);
    CameraFrame x = F(0.3f, 11, 0, 0.3f, 11.5f, 0);
    run("xzbound", g, x, 0.05f, limits);
    seed(g, 0, 10, 0, 10.5f, limits);
    CameraFrame y = F(0.4f, 11, 0, 0.4f, 11.5f, 0);
    run("xzover", g, y, 0.05f, limits);
  }

  if (Enabled()) {
    check_apply();
  } else {
    CPUContext ctx;
    ctx.mmu.write_f32(0x178, 5.0f);
    ctx.mmu.write_f32(0x184, 6.0f);
    const float y0 = ctx.mmu.read_f32(0x178), l0 = ctx.mmu.read_f32(0x184);
    Apply(ctx, 0x100, 0.05f);
    std::printf("noop %.9g %.9g %.9g %.9g\n", y0, ctx.mmu.read_f32(0x178),
                l0, ctx.mmu.read_f32(0x184));
  }
  return 0;
}
'''


class Checker:
    def __init__(self):
        self.count = 0

    def __call__(self, condition, message):
        self.count += 1
        if not condition:
            raise AssertionError(message)


def parse_rows(text):
    rows, meta = {}, {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == 'enabled':
            meta['enabled'] = int(parts[1])
        elif parts[0] == 'apply':
            meta['apply'] = (int(parts[1]), float(parts[2]), float(parts[3]), int(parts[4]))
        elif parts[0] == 'noop':
            meta['noop'] = tuple(float(v) for v in parts[1:])
        else:
            rows[parts[0]] = {
                'ret': int(parts[1]), 'armed': int(parts[2]),
                'timer': float(parts[3]), 'duration': float(parts[4]),
                'out_y': float(parts[5]), 'out_look': float(parts[6]),
                'in_y': float(parts[7]), 'in_look': float(parts[8]),
                'start_y': float(parts[9]), 'start_look': float(parts[10]),
                'xz': int(parts[11]),
            }
    return rows, meta


def smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def check_rows(c, rows, required_glide):
    expected = {
        'first', 'steady', 'up1', 'up2', 'up3', 'up4', 'up5',
        'down1', 'down2', 'down3', 'down4', 'down5', 'fall', 'teleport',
        'vteleport', 'jump', 'multi', 'novol', 'state', 'dt0', 'dtneg',
        'dtmax', 'minbound', 'maxbound', 'xzbound', 'xzover',
    }
    c(set(rows) == expected, 'fixture scenarios: %r' % (set(rows) ^ expected))
    for name, row in rows.items():
        c(row['xz'] == 1, '%s changed X/Z' % name)
        if name in required_glide:
            c(row['ret'] == 1, '%s did not glide' % name)
            finished = row['timer'] / row['duration'] >= 1.0
            if finished:
                c(row['out_y'] == row['in_y'], '%s did not snap to the guest Y' % name)
                c(row['out_look'] == row['in_look'], '%s did not snap to the guest look Y' % name)
            else:
                t = smoothstep(row['timer'] / row['duration']) if row['duration'] > 0 else 1.0
                want = row['start_y'] + (row['in_y'] - row['start_y']) * t
                want_look = row['start_look'] + (row['in_look'] - row['start_look']) * t
                c(abs(row['out_y'] - want) <= 2e-5, '%s Y %r != %r' % (name, row['out_y'], want))
                c(abs(row['out_look'] - want_look) <= 2e-5,
                  '%s look Y %r != %r' % (name, row['out_look'], want_look))
            c(row['armed'] == (0 if finished else 1), '%s armed state' % name)
        else:
            c(row['ret'] == 0, '%s glided unexpectedly' % name)
            c(row['armed'] == 0, '%s stayed armed' % name)
            c(row['out_y'] == row['in_y'], '%s changed Y' % name)
            c(row['out_look'] == row['in_look'], '%s changed look Y' % name)

    # Exact timer cadence and the finish/disarm edge.
    for prefix in ('up', 'down'):
        for i in range(1, 5):
            row = rows['%s%d' % (prefix, i)]
            c(abs(row['timer'] - 0.05 * i) < 1e-6, '%s timer' % prefix)
    c(rows['up4']['armed'] == 0 and rows['down4']['armed'] == 0, 'finish disarm')
    c(rows['up5']['ret'] == 0 and rows['down5']['ret'] == 0, 'post-glide pass-through')
    c(rows['first']['out_y'] == 5.0, 'first frame passthrough')


def run_fixture(exe, overrides):
    env = dict(os.environ)
    for name in ('NWII_CAMERA_GLIDE_MS', 'NWII_NO_CAMERA_GLIDE',
                 'NWII_CAMERA_GLIDE_MIN_Y', 'NWII_CAMERA_GLIDE_MAX_Y',
                 'NWII_CAMERA_GLIDE_MAX_XZ'):
        env.pop(name, None)
    env.update(overrides)
    result = subprocess.run([str(exe)], env=env, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


def compiler():
    found = shutil.which('g++')
    if found:
        return found
    winget = winlibs_bin()
    if winget:
        return str(winget / 'g++.exe')
    raise SystemExit('g++ not found')


def check_fixture(c, work):
    include = work / 'include/runtime'
    include.mkdir(parents=True)
    (include / 'cpu_context.h').write_text(STUB_CONTEXT)
    (include / 'native_camera_glide.h').write_bytes(
        (PORT / 'runtime-overlays/native_camera_glide.h').read_bytes())
    source = work / 'fixture.cpp'
    source.write_text(FIXTURE)
    exe = work / 'fixture.exe'
    command = [compiler(), '-std=c++20', '-O2', '-Wall', '-Wextra',
               '-I' + str(work / 'include'), str(source), '-o', str(exe)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    c(True, 'fixture compiled')

    # Default: no env switch -> helper must be inert.
    rows, meta = parse_rows(run_fixture(exe, {}))
    c(meta['enabled'] == 0, 'helper enabled without NWII_CAMERA_GLIDE_MS')
    c('noop' in meta, 'no-op probe missing')
    before_y, after_y, before_look, after_look = meta['noop']
    c(before_y == after_y and before_look == after_look, 'Apply was not a no-op')
    check_rows(c, rows, {'up1', 'up2', 'up3', 'up4', 'down1', 'down2', 'down3',
                         'down4', 'minbound', 'maxbound', 'xzbound'})

    # Enabled: pure Step stays identical, Apply writes only the two Y floats.
    rows, meta = parse_rows(run_fixture(exe, {'NWII_CAMERA_GLIDE_MS': '200'}))
    c(meta['enabled'] == 1, 'helper did not enable with NWII_CAMERA_GLIDE_MS')
    check_rows(c, rows, {'up1', 'up2', 'up3', 'up4', 'down1', 'down2', 'down3',
                         'down4', 'minbound', 'maxbound', 'xzbound'})
    c('apply' in meta, 'enabled Apply probe missing')
    apply_enabled, out_y, out_look, xz = meta['apply']
    c(apply_enabled == 1, 'Apply probe did not see the enabled config')
    c(abs(out_y - 10.15625) <= 2e-5, 'Apply Y %r' % out_y)
    c(abs(out_look - 10.65625) <= 2e-5, 'Apply look Y %r' % out_look)
    c(xz == 1, 'Apply changed X/Z')

    # Kill switch wins over a duration.
    rows, meta = parse_rows(run_fixture(
        exe, {'NWII_CAMERA_GLIDE_MS': '200', 'NWII_NO_CAMERA_GLIDE': '1'}))
    c(meta['enabled'] == 0, 'kill switch ignored')
    before_y, after_y, before_look, after_look = meta['noop']
    c(before_y == after_y and before_look == after_look, 'killed Apply wrote state')


SAMPLE = (
    'void func_80045F18(CPUContext& ctx) {\r\n'
    '#include "runtime/cpu_context.h"\r\n'
    '    ctx.pc = 0x80047824; func_80047824(ctx);\r\n'
    '    if (ctx.pc != 0x800460D8) return;\r\n'
    '    ++ctx.inst_count;\r\n'
    'loc_800460D8: ;\r\n'
    '}\r\n'
)


def check_stage(c, work):
    patched, changed = stage.patch_text(SAMPLE)
    c(changed, 'stage did not change the anchor')
    c(stage.INCLUDE in patched and stage.CALL in patched, 'include/call missing')
    c(patched.count(stage.INCLUDE) == 1, 'include inserted more than once')
    c(stage.restore(patched) == SAMPLE, 'restore is not byte-exact')
    again, _ = stage.patch_text(patched)
    c(again == patched, 'stage is not idempotent')
    c(stage.restore(again) == SAMPLE, 'idempotent restore mismatch')

    lf = SAMPLE.replace('\r\n', '\n')
    patched_lf, changed_lf = stage.patch_text(lf)
    c(changed_lf and '\r\n' not in patched_lf, 'LF chunk gained CRLF')
    c(stage.restore(patched_lf) == lf, 'LF restore mismatch')

    plain = 'int main() { return 0; }\n'
    c(stage.patch_text(plain) == (plain, False), 'non-anchor chunk changed')
    try:
        stage.patch_text(SAMPLE + SAMPLE)
    except ValueError:
        c(True, 'multiple anchors accepted')
    else:
        c(False, 'multiple anchors accepted')

    # Full tree: crash-generated and crash-split, header copy, dry-run, rollback.
    root = work / 'tree'
    generated = root / 'build/crash-generated'
    split = root / 'build/crash-split'
    generated.mkdir(parents=True)
    split.mkdir(parents=True)
    (root / 'runtime-overlays').mkdir(parents=True)
    shutil.copyfile(PORT / 'runtime-overlays/native_camera_glide.h',
                    root / 'runtime-overlays/native_camera_glide.h')
    runtime = root / 'build/nwii-full-source/nWiiRuntime'
    (runtime / 'include/runtime').mkdir(parents=True)
    first = generated / 'output_5.cpp'
    second = split / 'output_9.cpp'
    untouched = generated / 'output_6.cpp'
    first.write_bytes(SAMPLE.encode('utf-8', 'surrogateescape'))
    second.write_bytes(SAMPLE.replace('output_5', 'output_9').encode('utf-8', 'surrogateescape'))
    untouched.write_bytes(b'int main() { return 0; }\n')
    untouched_before = untouched.read_bytes()

    changed_paths, header_changed = stage.apply(root, runtime=runtime)
    c(len(changed_paths) == 2, 'tree stage changed %d files' % len(changed_paths))
    c(header_changed, 'tree stage did not copy the helper')
    c(stage.restore(first.read_bytes().decode('utf-8', 'surrogateescape')) == SAMPLE,
      'tree restore mismatch')
    c(untouched.read_bytes() == untouched_before, 'non-anchor file changed')
    helper = runtime / 'include/runtime/native_camera_glide.h'
    c(helper.read_bytes() == (PORT / 'runtime-overlays/native_camera_glide.h').read_bytes(),
      'helper copy mismatch')
    snapshot = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    stamp = helper.stat().st_mtime_ns
    changed_paths, header_changed = stage.apply(root, runtime=runtime)
    c(not changed_paths and not header_changed, 'second tree stage was not a no-op')
    c(snapshot == {path: path.read_bytes() for path in root.rglob('*') if path.is_file()},
      'second tree stage rewrote files')
    c(stamp == helper.stat().st_mtime_ns, 'helper rewrite changed the timestamp')

    dry = work / 'dry'
    (dry / 'build/crash-generated').mkdir(parents=True)
    (dry / 'runtime-overlays').mkdir(parents=True)
    shutil.copyfile(PORT / 'runtime-overlays/native_camera_glide.h',
                    dry / 'runtime-overlays/native_camera_glide.h')
    dry_runtime = dry / 'build/nwii-full-source/nWiiRuntime'
    dry_file = dry / 'build/crash-generated/output_5.cpp'
    dry_file.write_bytes(SAMPLE.encode('utf-8', 'surrogateescape'))
    changed_paths, header_changed = stage.apply(dry, runtime=dry_runtime, dry_run=True)
    c(len(changed_paths) == 1 and header_changed, 'dry-run did not report changes')
    c(dry_file.read_bytes() == SAMPLE.encode('utf-8', 'surrogateescape'),
      'dry-run rewrote the chunk')
    c(not (dry_runtime / 'include/runtime/native_camera_glide.h').exists(),
      'dry-run copied the helper')

    # NWII_BUILD_CAMERA_GLIDE=0 must be a complete no-op.
    env = dict(os.environ)
    env['NWII_BUILD_CAMERA_GLIDE'] = '0'
    disabled = subprocess.run(
        [sys.executable, str(PORT / 'tools/fix_camera_glide.py'),
         '--port', str(dry), '--runtime', str(dry_runtime)],
        env=env, capture_output=True, text=True)
    c(disabled.returncode == 0, 'disabled stage failed')
    c('disabled' in disabled.stdout, 'disabled stage did not report the no-op')
    c(dry_file.read_bytes() == SAMPLE.encode('utf-8', 'surrogateescape'),
      'disabled stage rewrote the chunk')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path)
    args = parser.parse_args()
    checker = Checker()
    if args.work:
        work = args.work.resolve()
        if work == PORT or PORT in work.parents:
            raise SystemExit('test artifacts must be outside the repo')
        work.mkdir(parents=True, exist_ok=True)
        check_stage(checker, work)
        check_fixture(checker, work / 'fixture')
    else:
        with tempfile.TemporaryDirectory(prefix='camera-glide-') as directory:
            work = Path(directory)
            check_stage(checker, work)
            check_fixture(checker, work / 'fixture')
    print('PASS %d assertions' % checker.count)


if __name__ == '__main__':
    main()
