"""Value-passing psq slow paths, for the register-promotion pass (tools/promote_regs.py).

native_psq_load_slow(frD, addr, W, I) writes fpr[frD] / ps1[frD] and native_psq_store_slow(frS, ...) reads fpr[frS] / ps1[frS]; both go
through ctx memory, so a promoted FP register would have to be spilled and reloaded around every quantised load or store. These variants
return / take the two values instead and share the original bodies (textually derived, so the quantisation semantics cannot drift):
    NativePsqPair native_psq_load_slow_pair(addr, W, I)
    void native_psq_store_slow_val(double f, double p, addr, W, I)
Idempotent; runs after fix_broad_memory_decode / fix_inline_psq stages (the header must already hold the final slow paths).
"""
from pathlib import Path
import re
p = Path(__file__).resolve().parents[1]
f = p / 'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h'
s = f.read_text()
MARK = 'NATIVE_PSQ_VALUE_HELPERS_V1'
if MARK not in s:
    def grab(name):
        a = s.index('__attribute__((noinline)) void ' + name + '(')
        b = s.index('\n  }\n', a) + len('\n  }\n')
        return a, b
    la, lb = grab('native_psq_load_slow')
    sa, sb = grab('native_psq_store_slow')
    load = s[la:lb]
    store = s[sa:sb]
    new_load = load.replace('void native_psq_load_slow(uint32_t frD, uint32_t addr, uint32_t W, uint32_t I) {', 'NativePsqPair native_psq_load_slow_pair(uint32_t addr, uint32_t W, uint32_t I) {\n    NativePsqPair result;')
    assert new_load != load
    new_load = new_load.replace('fpr[frD] = load_element(addr);', 'result.f = load_element(addr);')
    new_load = new_load.replace('ps1[frD] = load_element(addr + elem_size);', 'result.p = load_element(addr + elem_size);')
    new_load = new_load.replace('ps1[frD] = 1.0;', 'result.p = 1.0;')
    assert 'frD' not in new_load, 'unexpected psq load body'
    new_load = new_load.rstrip('\n')
    assert new_load.endswith('\n  }')
    new_load = new_load[:-len('\n  }')] + '\n    return result;\n  }\n'
    new_store = store.replace('void native_psq_store_slow(uint32_t frS, uint32_t addr, uint32_t W, uint32_t I) {', 'void native_psq_store_slow_val(double fval, double pval, uint32_t addr, uint32_t W, uint32_t I) {')
    assert new_store != store
    new_store = new_store.replace('fpr[frS]', 'fval').replace('ps1[frS]', 'pval')
    assert 'frS' not in new_store, 'unexpected psq store body'
    block = '  // ' + MARK + ': see tools/fix_psq_value_helpers.py\n  struct NativePsqPair { double f; double p; };\n' + new_load + new_store
    s = s[:sb] + block + s[sb:] if sb > lb else s[:lb] + block + s[lb:]
    f.write_text(s)
print('psq value helpers installed')

# ---- inline quantised forms (item 5 of the guest-thread trim): the fast f32 case plus the 8/16-bit integer types 4-7, semantics copied
# from native_psq_load_slow_pair / native_psq_store_slow_val; every other GQR type still goes through the out-of-line function.
MARK2 = 'NATIVE_PSQ_QUANT_INLINE_V1'
s = f.read_text()
if MARK2 not in s:
    anchor = '  inline void ps_add(uint32_t D, uint32_t A, uint32_t B) {'
    assert s.count(anchor) == 1
    block = r'''  // NATIVE_PSQ_QUANT_INLINE_V1: see tools/fix_psq_value_helpers.py
  __attribute__((always_inline)) inline NativePsqPair psq_load_pair(uint32_t addr, uint32_t W, uint32_t I) {
    const uint32_t gqr_val = gqr[I];
    const uint32_t ld_type = (gqr_val >> 16) & 0x7;
    if (__builtin_expect(ld_type == 0, 1)) {
      NativePsqPair result;
      result.f = mmu.read_f32(addr);
      result.p = W ? 1. : double(mmu.read_f32(addr + 4));
      return result;
    }
    if (ld_type >= 4) {
      const uint32_t ld_scale = (gqr_val >> 24) & 0x3F;
      const int signed_scale = (ld_scale & 32u) ? int(ld_scale) - 64 : int(ld_scale);
      const uint32_t scale_bits = uint32_t(127 - signed_scale) << 23;
      float scale; std::memcpy(&scale, &scale_bits, sizeof(scale));
      auto load_element = [&](uint32_t a) -> double {
        switch (ld_type) {
        case 4: return mmu.read8(a) * scale;
        case 5: return mmu.read16(a) * scale;
        case 6: return (int8_t)mmu.read8(a) * scale;
        default: return (int16_t)mmu.read16(a) * scale;
        }
      };
      const uint32_t elem_size = (ld_type == 4 || ld_type == 6) ? 1 : 2;
      NativePsqPair result;
      result.f = load_element(addr);
      result.p = W == 0 ? load_element(addr + elem_size) : 1.0;
      return result;
    }
    return native_psq_load_slow_pair(addr, W, I);
  }
  __attribute__((always_inline)) inline void psq_store_val(double fval, double pval, uint32_t addr, uint32_t W, uint32_t I) {
    const uint32_t gqr_val = gqr[I];
    const uint32_t st_type = gqr_val & 0x7;
    if (__builtin_expect(st_type == 0, 1)) {
      mmu.write_f32(addr, float(fval));
      if (!W) mmu.write_f32(addr + 4, float(pval));
      return;
    }
    if (st_type >= 4) {
      const uint32_t st_scale = (gqr_val >> 8) & 0x3F;
      const int signed_scale = (st_scale & 32u) ? int(st_scale) - 64 : int(st_scale);
      const uint32_t scale_bits = uint32_t(127 + signed_scale) << 23;
      float scale; std::memcpy(&scale, &scale_bits, sizeof(scale));
      auto store_element = [&](uint32_t a, double val) {
        switch (st_type) {
        case 4: mmu.write8(a, (uint8_t)(val * scale)); break;
        case 5: mmu.write16(a, (uint16_t)(val * scale)); break;
        case 6: mmu.write8(a, (int8_t)(val * scale)); break;
        default: mmu.write16(a, (int16_t)(val * scale)); break;
        }
      };
      const uint32_t elem_size = (st_type == 4 || st_type == 6) ? 1 : 2;
      store_element(addr, fval);
      if (W == 0) store_element(addr + elem_size, pval);
      return;
    }
    native_psq_store_slow_val(fval, pval, addr, W, I);
  }
'''
    s = s.replace(anchor, block + anchor)
    f.write_text(s)
print('psq quant inline installed')
