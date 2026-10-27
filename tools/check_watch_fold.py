"""Check NATIVE_WATCH_FOLD_V1 on a private copy of the staged runtime (no staging, no game, no shared files touched).

1. Stage round trip on the copy: fold=0 and --restore leave the staged text byte-identical to the unfolded staging
   (so with the watch active/diagnostic build behaviour is exactly today's), fold=1 is idempotent and removable.
2. Behaviour: with the watch at 0 (the only state a folded build can be in), random stores through MMU::write8..write_f64
   and native_guest_fast::write8..write_f64 over MEM1, MEM2, page 0, the write-gather page, hardware registers and
   unmapped space give the same RAM hash and the same MMIO/FIFO/watch observer sequence in the unfolded and folded headers.
3. Instruction delta: straight-line stores compiled with the production flags, host instructions unfolded vs folded.
"""
from pathlib import Path
import ast, os, re, shutil, subprocess, sys, tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fix_watch_fold as F
from port_paths import winlibs_bin

PORT = Path(__file__).resolve().parents[1]
STAGED = PORT / 'build/nwii-full-source/nWiiRuntime'


def stubs():
    tree = ast.parse((PORT / 'tools/check_fastmem_static_base.py').read_text(encoding='utf-8'))
    text = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'STUBS' for t in n.targets))
    text = text.replace('void watch_hit(uint32_t,uint32_t,int){ ++g_observations; }',
                        'void observe(uint64_t v){ g_observations=(g_observations^v)*1099511628211ull; }\n'
                        'void watch_hit(uint32_t a,uint32_t v,int w){ observe(0x1000000000000ull^a^(uint64_t(v)<<20)^w); }')
    for name, tag in (('GX_WGPIPE_Write8(uint8_t)', 8), ('GX_WGPIPE_Write16(uint16_t)', 16), ('GX_WGPIPE_Write32(uint32_t)', 32),
                      ('GX_WGPIPE_WriteF32(float)', 33), ('GX_WGPIPE_WriteF64(double)', 64)):
        pass
    text = text.replace('void GX_WGPIPE_Write8(uint8_t){ ++g_observations; }', 'void GX_WGPIPE_Write8(uint8_t v){ observe(8000+v); }')
    text = text.replace('void GX_WGPIPE_Write16(uint16_t){ ++g_observations; }', 'void GX_WGPIPE_Write16(uint16_t v){ observe(16000+v); }')
    text = text.replace('void GX_WGPIPE_Write32(uint32_t){ ++g_observations; }', 'void GX_WGPIPE_Write32(uint32_t v){ observe(32000000ull+v); }')
    text = text.replace('void GX_WGPIPE_WriteF32(float){ ++g_observations; }', 'void GX_WGPIPE_WriteF32(float v){ uint32_t b;std::memcpy(&b,&v,4);observe(33000000000ull+b); }')
    text = text.replace('void GX_WGPIPE_WriteF64(double){ ++g_observations; }', 'void GX_WGPIPE_WriteF64(double v){ uint64_t b;std::memcpy(&b,&v,8);observe(b^0x6464); }')
    text = text.replace('void HW_Reg_Write16(uint32_t,uint16_t){} void HW_Reg_Write32(uint32_t,uint32_t){}',
                        'void HW_Reg_Write16(uint32_t a,uint16_t v){ observe(a*31ull+v); } void HW_Reg_Write32(uint32_t a,uint32_t v){ observe(a*37ull+v); }')
    return text


FIXTURE = r'''
#include "runtime/cpu_context.h"
#include "runtime/native_guest_fast.h"
#include <cstring>
#include <cstdio>
#include <memory>
namespace nwii::runtime { MMU* g_mmu=nullptr; }
using namespace nwii::runtime;
extern "C" void run_game(CPUContext&) {}
static uint32_t rng_state = 0x13572468u;
static uint32_t rnd() { rng_state ^= rng_state << 13; rng_state ^= rng_state >> 17; rng_state ^= rng_state << 5; return rng_state; }
static uint32_t pick() {
  const uint32_t r = rnd();
  switch (r % 12) {
    case 0: return 0x80000000u + (r >> 8) % 0x2000;                 // page 0 and MEM1 start
    case 1: return 0x80010000u + (r >> 8) % 0x1700000;              // MEM1
    case 2: return 0x817ffff0u + (r >> 8) % 32;                     // MEM1 end
    case 3: return 0x90000000u + (r >> 8) % 0x4000000;              // MEM2
    case 4: return 0x93fffff0u + (r >> 8) % 32;                     // MEM2 end
    case 5: return 0xcc008000u + (r >> 8) % 8;                      // write-gather
    case 6: return 0xcc000000u + (r >> 8) % 0x8000;                 // hardware registers
    case 7: return 0xc0000000u + (r >> 8) % 0x40000;                // uncached mirror
    case 8: return 0x00597790u + (r >> 8) % 16 - 8;                 // the old default watch neighbourhood
    case 9: return 0x80597790u + (r >> 8) % 16 - 8;
    default: return r;
  }
}
int main() {
  g_watch_addr = 0;
  auto context = std::make_unique<CPUContext>();
  MMU& m = context->mmu;
  g_mmu = &m;
  for (unsigned i = 0; i < 400000; ++i) {
    const uint32_t a = pick(), v = rnd();
    const uint64_t w = (uint64_t(v) << 32) | rnd();
    float f; std::memcpy(&f, &v, 4); double d; std::memcpy(&d, &w, 8);
    switch (i % 12) {
      case 0: m.write8(a, uint8_t(v)); break;
      case 1: m.write16(a, uint16_t(v)); break;
      case 2: m.write32(a, v); break;
      case 3: m.write64(a, w); break;
      case 4: m.write_f32(a, f); break;
      case 5: m.write_f64(a, d); break;
      case 6: native_guest_fast::write8(m, a, uint8_t(v)); break;
      case 7: native_guest_fast::write16(m, a, uint16_t(v)); break;
      case 8: native_guest_fast::write32(m, a, v); break;
      case 9: native_guest_fast::write64(m, a, w); break;
      case 10: native_guest_fast::write_f32(m, a, f); break;
      default: native_guest_fast::write_f64(m, a, d); break;
    }
  }
  uint64_t h = 1469598103934665603ull;
  for (size_t n = 0; n < m.mem1.size(); ++n) h = (h ^ m.mem1[n]) * 1099511628211ull;
  for (size_t n = 0; n < m.mem2.size(); ++n) h = (h ^ m.mem2[n]) * 1099511628211ull;
  std::printf("STATE ram=%016llx observer=%016llx\n", (unsigned long long)h, (unsigned long long)g_observations);
}
'''

# Four straight-line stack stores, as the generated code emits them, and the same through the production MMU accessor.
DELTA = r'''
#include "runtime/cpu_context.h"
#include "runtime/native_guest_fast.h"
using namespace nwii::runtime;
extern "C" void run_game(CPUContext&) {}
__attribute__((noinline)) void stack_stores(CPUContext& ctx) {
  const uint32_t sp = ctx.gpr[1];
  native_guest_fast::write32(ctx.mmu, sp + 8u, ctx.gpr[31]);
  native_guest_fast::write32(ctx.mmu, sp + 12u, ctx.gpr[30]);
  native_guest_fast::write32(ctx.mmu, sp + 16u, ctx.gpr[29]);
  native_guest_fast::write32(ctx.mmu, sp + 20u, ctx.gpr[28]);
}
__attribute__((noinline)) void mmu_stores(CPUContext& ctx) {
  const uint32_t sp = ctx.gpr[1];
  ctx.mmu.write32(sp + 8u, ctx.gpr[31]);
  ctx.mmu.write32(sp + 12u, ctx.gpr[30]);
  ctx.mmu.write32(sp + 16u, ctx.gpr[29]);
  ctx.mmu.write32(sp + 20u, ctx.gpr[28]);
}
'''


def compiler():
    return shutil.which('g++') or str(winlibs_bin() / 'g++.exe')


def make_tree(root, name, fold):
    tree = root / name
    shutil.copytree(STAGED / 'include', tree / 'include')
    (tree / 'src/core').mkdir(parents=True)
    shutil.copy2(STAGED / 'src/core/os_hle_stubs.cpp', tree / 'src/core/os_hle_stubs.cpp')
    F.apply(tree, True, True)  # normalise: if the staged tree was already folded, start from the unfolded text
    if fold:
        F.apply(tree, False, True)
    return tree


def snapshot(tree):
    return {p: p.read_bytes() for p in [tree / 'include/runtime/cpu_context.h', tree / 'src/core/os_hle_stubs.cpp']}


FLAGS = ['-std=c++20', '-O2', '-march=x86-64-v3', '-fwrapv', '-fno-strict-aliasing', '-ffp-contract=off',
         '-DNWII_STATIC_RAM_BASE', '-DNWII_WATCH_PAGE_MARKING', '-w']


def count_instructions(asm, symbol):
    inside, count = False, 0
    for line in asm.splitlines():
        if re.match(r'^_?%s:' % symbol, line) or re.match(r'^_Z\d+%s\w*:' % symbol, line):
            inside = True
            continue
        if inside:
            if line.startswith('\t.seh_endproc') or line.startswith('\t.cfi_endproc'):
                break
            s = line.strip()
            if s and not s.startswith(('.', '#')) and not s.endswith(':'):
                count += 1
    return count


def main():
    with tempfile.TemporaryDirectory(prefix='watch-fold-') as d:
        root = Path(d)
        base = make_tree(root, 'base', False)
        fold = make_tree(root, 'fold', True)
        base_text, fold_text = snapshot(base), snapshot(fold)
        assert base_text[base / 'include/runtime/cpu_context.h'] != fold_text[fold / 'include/runtime/cpu_context.h'], 'fold changed nothing'
        assert 'NWII_WATCH_FOLDED' not in base_text[base / 'include/runtime/cpu_context.h'].decode('utf-8', 'surrogateescape')
        # 1. round trips
        again = F.apply(fold, False, True)
        assert all(v == 0 for v in again.values()), ('not idempotent', again)
        assert snapshot(fold) == fold_text
        counts = F.apply(fold, False, True)
        spare = make_tree(root, 'spare', True)
        F.apply(spare, False, False)  # fold=0 on a folded tree == removal
        assert [v for v in snapshot(spare).values()] == [v for v in base_text.values()], 'fold=0 is not byte-identical'
        F.apply(spare, True, True)
        assert [v for v in snapshot(spare).values()] == [v for v in base_text.values()], 'restore is not byte-identical'
        expected = {'span': 1, 'snapshot': 4, 'write_gather': 5, 'initialiser': 1}
        probe = make_tree(root, 'probe', False)
        got = F.apply(probe, False, True)
        assert got == expected, (got, expected)
        print('stage: idempotent; fold=0 and --restore byte-identical to unfolded; edit counts', got)
        # 2. behaviour
        results = {}
        for name, tree in (('unfolded', base), ('folded', fold)):
            work = root / ('run-' + name); work.mkdir()
            code = work / 'fixture.cpp'
            code.write_text('#include "runtime/cpu_context.h"\n' + stubs() + FIXTURE, encoding='utf-8')
            exe = work / 'fixture.exe'
            subprocess.run([compiler(), *FLAGS, '-I', str(tree / 'include'), str(code), str(PORT / 'runtime-overlays/static_ram_base.cpp'),
                            '-static', '-o', str(exe)], check=True)
            out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=180)
            assert out.returncode == 0, out.stdout + out.stderr
            results[name] = out.stdout.strip()
        assert results['unfolded'] == results['folded'], results
        print('behaviour (watch 0):', results['folded'], '== unfolded')
        # 3. instruction delta
        deltas = {}
        for name, tree in (('unfolded', base), ('folded', fold)):
            work = root / ('asm-' + name); work.mkdir()
            code = work / 'delta.cpp'; code.write_text(DELTA, encoding='utf-8')
            asm = work / 'delta.s'
            subprocess.run([compiler(), *FLAGS, '-I', str(tree / 'include'), '-S', str(code), '-o', str(asm)], check=True)
            text = asm.read_text()
            deltas[name] = {fn: count_instructions(text, fn) for fn in ('stack_stores', 'mmu_stores')}
        for fn in ('stack_stores', 'mmu_stores'):
            print('%s: %d -> %d host instructions' % (fn, deltas['unfolded'][fn], deltas['folded'][fn]))
            assert deltas['folded'][fn] < deltas['unfolded'][fn], (fn, deltas)
        os_text = fold_text[fold / 'src/core/os_hle_stubs.cpp'].decode()
        assert 'if (NWII_WATCH_FOLDED)' in os_text and os_text.count('uint32_t g_watch_addr') == 1
    print('Watch fold check passed.')


if __name__ == '__main__':
    main()
