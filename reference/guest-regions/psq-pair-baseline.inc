__attribute__((always_inline)) inline CPUContext::NativePsqPair LegacyPsqLoad(CPUContext& c, uint32_t addr, uint32_t W, uint32_t I) {
    const uint32_t gqr_val = c.gqr[I];
    const uint32_t ld_type = (gqr_val >> 16) & 0x7;
    if (__builtin_expect(ld_type == 0, 1)) {
      CPUContext::NativePsqPair result;
      result.f = c.mmu.read_f32(addr);
      result.p = W ? 1. : double(c.mmu.read_f32(addr + 4));
      return result;
    }
    if (ld_type >= 4) {
      const uint32_t ld_scale = (gqr_val >> 24) & 0x3F;
      const int signed_scale = (ld_scale & 32u) ? int(ld_scale) - 64 : int(ld_scale);
      const uint32_t scale_bits = uint32_t(127 - signed_scale) << 23;
      float scale; std::memcpy(&scale, &scale_bits, sizeof(scale));
      auto load_element = [&](uint32_t a) -> double {
        switch (ld_type) {
        case 4: return c.mmu.read8(a) * scale;
        case 5: return c.mmu.read16(a) * scale;
        case 6: return (int8_t)c.mmu.read8(a) * scale;
        default: return (int16_t)c.mmu.read16(a) * scale;
        }
      };
      const uint32_t elem_size = (ld_type == 4 || ld_type == 6) ? 1 : 2;
      CPUContext::NativePsqPair result;
      result.f = load_element(addr);
      result.p = W == 0 ? load_element(addr + elem_size) : 1.0;
      return result;
    }
    return c.native_psq_load_slow_pair(addr, W, I);
  }
__attribute__((always_inline)) inline void LegacyPsqStore(CPUContext& c, double fval, double pval, uint32_t addr, uint32_t W, uint32_t I) {
    const uint32_t gqr_val = c.gqr[I];
    const uint32_t st_type = gqr_val & 0x7;
    if (__builtin_expect(st_type == 0, 1)) {
      c.mmu.write_f32(addr, float(fval));
      if (!W) c.mmu.write_f32(addr + 4, float(pval));
      return;
    }
    if (st_type >= 4) {
      const uint32_t st_scale = (gqr_val >> 8) & 0x3F;
      const int signed_scale = (st_scale & 32u) ? int(st_scale) - 64 : int(st_scale);
      const uint32_t scale_bits = uint32_t(127 + signed_scale) << 23;
      float scale; std::memcpy(&scale, &scale_bits, sizeof(scale));
      auto store_element = [&](uint32_t a, double val) {
        switch (st_type) {
        case 4: c.mmu.write8(a, (uint8_t)(val * scale)); break;
        case 5: c.mmu.write16(a, (uint16_t)(val * scale)); break;
        case 6: c.mmu.write8(a, (int8_t)(val * scale)); break;
        default: c.mmu.write16(a, (int16_t)(val * scale)); break;
        }
      };
      const uint32_t elem_size = (st_type == 4 || st_type == 6) ? 1 : 2;
      store_element(addr, fval);
      if (W == 0) store_element(addr + elem_size, pval);
      return;
    }
    c.native_psq_store_slow_val(fval, pval, addr, W, I);
  }
