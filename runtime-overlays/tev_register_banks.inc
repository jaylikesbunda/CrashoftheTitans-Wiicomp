// BP E0..E7 each address writes either the regular or constant colour bank.
// Bit 23 selects the bank, not a replacement for the other bank's value.
// Keep RA and BG pairs independently; guest draws may rewrite only one pair.
static uint32_t s_native_tev_banks[2][8] = {};

static void NativeTevRegisterWrite(uint8_t reg, uint32_t val) {
  if (reg >= 0xe0 && reg <= 0xe7)
    s_native_tev_banks[(val >> 23) & 1][reg - 0xe0] = val;
}

static float NativeTevDecode11(uint32_t word, unsigned shift) {
  const int bits = int((word >> shift) & 0x7ff);
  return float(bits >= 1024 ? bits - 2048 : bits) / 255.0f;
}

static void NativeTevUniforms(float *color, float *konst) {
  for (unsigned bank = 0; bank < 2; ++bank) {
    float *out = bank ? konst : color;
    for (unsigned i = 0; i < 4; ++i) {
      const uint32_t ra = s_native_tev_banks[bank][i * 2];
      const uint32_t bg = s_native_tev_banks[bank][i * 2 + 1];
      out[i * 4] = NativeTevDecode11(ra, 0);
      out[i * 4 + 3] = NativeTevDecode11(ra, 12);
      out[i * 4 + 2] = NativeTevDecode11(bg, 0);
      out[i * 4 + 1] = NativeTevDecode11(bg, 12);
    }
  }
}

// Konst selectors are baked into generated GLSL and belong in the cache key.
static uint64_t NativeTevShaderHash(const GXState &state, uint8_t primitive) {
  uint64_t hash = state.GetShaderHash(primitive);
  for(unsigned t=0;t<8;++t)
    hash=(hash ^ ((state.xf[0x40+t]>>4)&7))*1099511628211ull;
  for(unsigned reg=0xee;reg<=0xf2;++reg)
    for(unsigned shift=0;shift<32;shift+=8)
      hash=(hash ^ ((state.bp[reg]>>shift)&255))*1099511628211ull;
  for(unsigned reg=6;reg<=0x27;++reg)
    for(unsigned shift=0;shift<32;shift+=8)
      hash=(hash ^ ((state.bp[reg]>>shift)&255))*1099511628211ull;
  for (unsigned reg = 0xf6; reg <= 0xfd; ++reg)
    for (unsigned shift = 0; shift < 32; shift += 8)
      hash = (hash ^ ((state.bp[reg] >> shift) & 0xff)) * 1099511628211ull;
  return hash;
}
