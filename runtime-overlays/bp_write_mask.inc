// Renderer-owned BP shadow: parse-time seeding must not replace prior draw
// state, and FE's write mask applies to exactly one subsequent BP write.
static uint32_t s_native_bp_shadow[256] = {};
static uint32_t s_native_bp_mask = 0xffffff;

static uint32_t NativeBpRegisterValue(uint8_t reg, uint32_t val) {
  const uint32_t resolved = (s_native_bp_shadow[reg] & ~s_native_bp_mask) |
                            (val & s_native_bp_mask);
  s_native_bp_shadow[reg] = resolved & 0xffffff;
  if (reg == 0xfe) s_native_bp_mask = resolved & 0xffffff;
  else s_native_bp_mask = 0xffffff;
  return resolved & 0xffffff;
}

static void NativeRestoreBpSnapshot() {
  std::memcpy(g_state.bp, s_native_bp_shadow, sizeof(s_native_bp_shadow));
}
