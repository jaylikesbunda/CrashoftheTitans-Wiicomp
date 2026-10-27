// NATIVE_TEXTURE_CPU_GL_SPLIT_V1: stage (a) for one drain. ProcessGXFifo runs this on the drain thread after Parse and
// the decode phase and before NativeSignalGpuCompletionEarly, so every guest-RAM read a texture needs happens while the
// guest is still held. It replays the drain's BP writes in command order on a mirror of the renderer's state, exactly as
// RendererGL::Render would apply them, and records one TextureRef per texture stage of every draw. Render then only
// resolves those references (stage b), in order, at each draw.
namespace {
struct NativeDrawTextures {
  uint8_t present = 0;
  nwii::runtime::hle::TextureCache::TextureRef ref[8];
};
// NATIVE_RENDER_QUEUE_V1: draw lists are per drain. The prepass fills s_draw_textures_ring[seq % 16] on the front end; a batch
// carries its seq, and Render reads the list through s_draw_textures, which the GL thread selects per batch (or the prepass
// selects on the sync path). Sixteen slots exceed the queue depth (at most 8) plus the batch executing.
std::vector<NativeDrawTextures> s_draw_textures_ring[16];
uint32_t s_prepass_seq = 0;
std::vector<NativeDrawTextures>* s_draw_textures = &s_draw_textures_ring[0];
size_t s_draw_index = 0;
// Mirror of RenderState() as Render will see it at each draw. Rebuilt from RenderState() whenever the state was
// replaced (savestate import), otherwise advanced only by the BP writes below.
GXState s_tex_mirror{};
uint64_t s_tex_mirror_epoch = ~uint64_t(0);
// BP shadow and one-shot 0xFE mask, as NativeBpRegisterValue keeps them for Render.
uint32_t s_tex_bp_shadow[256] = {};
uint32_t s_tex_bp_mask = 0xffffff;
// Same redundancy rule as NativeRedundantBp, evaluated against the mirror shadow (Render's shadow is not up to date yet).
bool NativeCpuRedundantBp(uint8_t reg, uint32_t val) {
  const bool ordinary = reg == 0 || reg == 0x0f || reg == 0xe8 || (reg >= 0x20 && reg <= 0x2f) || (reg >= 0x40 && reg <= 0x43) ||
                        (reg >= 0xc0 && reg <= 0xe7) || (reg >= 0xe9 && reg <= 0xfd);
  if (!ordinary) return false;
  const uint32_t resolved = ((s_tex_bp_shadow[reg] & ~s_tex_bp_mask) | (val & s_tex_bp_mask)) & 0xffffff;
  return resolved == s_tex_bp_shadow[reg];
}
// Same rule as the early pass's TLUT snapshot: a 0x65 load whose destination fits tlutMem is copied from guest RAM now.
void NativeCpuTlutLoad(uint32_t resolved) {
  uint32_t src = (s_tex_bp_shadow[0x64] & 0xFFFFFF) << 5;
  if (nwii::runtime::Config::get().platform == nwii::runtime::Platform::GameCube) src &= 0x01FFFFFF;
  const uint32_t dst = (resolved & 0x3FF) << 9, bytes = ((resolved >> 10) & 0x7FF) * 32;
  if (!nwii::runtime::g_mmu || dst + bytes > sizeof(s_tex_mirror.tlutMem)) {
    nwii::runtime::gx::ApplyBPRegisterTo(s_tex_mirror, 0x65, resolved);
    return;
  }
  if (bytes) {
    if (const uint8_t* p = nwii::runtime::g_mmu->get_ptr(src)) std::memcpy(&s_tex_mirror.tlutMem[dst], p, bytes);
    else for (uint32_t i = 0; i < bytes; ++i) s_tex_mirror.tlutMem[dst + i] = nwii::runtime::g_mmu->read8(src + i);
  }
  native_tlut::Changed();
}
} // namespace

void NativePrepareTextures(const CommandStream& commands) {
  static const bool used_only = std::getenv("NWII_ALL_TEXTURE_SLOTS") == nullptr; // same switch as Render
  // Render flushes the queue at its end. A drain that never reached Render must not leave ops behind for a later one.
  nwii::runtime::hle::TextureCache::get().drop_pending_ops(); // NATIVE_RENDER_QUEUE_V1: the GL side runs and clears the ops of earlier batches
  const uint32_t seq = s_prepass_seq++;
  std::vector<NativeDrawTextures>& draws = s_draw_textures_ring[seq % 16];
  draws.clear();
  if (s_tex_mirror_epoch != RenderStateEpoch()) {
    s_tex_mirror = RenderState();
    s_tex_mirror_epoch = RenderStateEpoch();
  }
  // NATIVE_RENDER_QUEUE_V1: the front end's copy of the BP shadow. The renderer's own copy is written by Render (the GL thread)
  // in queue mode, so it is read here only on the sync path.
  if (nwii::runtime::render_queue::Active()) nwii::runtime::render_queue::FrontBpShadow(s_tex_bp_shadow, s_tex_bp_mask);
  else { std::memcpy(s_tex_bp_shadow, s_native_bp_shadow, sizeof(s_tex_bp_shadow)); s_tex_bp_mask = s_native_bp_mask; }
  std::memcpy(s_tex_mirror.bp, s_tex_bp_shadow, sizeof(s_tex_mirror.bp)); // Render restores bp from the shadow per drain
  bool applied[256] = {};
  for (const auto& e : commands.entries) {
    if (e.kind == (uint8_t)GXCommandType::BPRegister) {
      const uint8_t reg = (uint8_t)e.reg;
      const bool duplicate = applied[reg] && !native_batch_boundaries::Deferred(reg) && NativeCpuRedundantBp(reg, e.val);
      const uint32_t resolved = ((s_tex_bp_shadow[reg] & ~s_tex_bp_mask) | (e.val & s_tex_bp_mask)) & 0xffffff;
      s_tex_bp_shadow[reg] = resolved;
      s_tex_bp_mask = reg == 0xfe ? resolved : 0xffffff;
      applied[reg] = true;
      if (duplicate) continue;
      if (reg == 0x65) NativeCpuTlutLoad(resolved);
      else nwii::runtime::gx::ApplyBPRegisterTo(s_tex_mirror, reg, resolved);
      if (reg == 0x52 && !(resolved & 0x4000)) { // EFB copy to a texture: the same guard the renderer applies before its copy
        const uint32_t dest = s_tex_mirror.efbCopyDest, w = s_tex_mirror.efbW, h = s_tex_mirror.efbH;
        if (dest && w > 0 && h > 0 && w <= 1024 && h <= 1024)
          nwii::runtime::hle::TextureCache::get().note_efb_copy(dest, resolved, s_tex_mirror.efbCopyStride, w, h);
      }
    } else if (e.kind == (uint8_t)GXCommandType::DrawPrimitive) {
      NativeDrawTextures draw;
      if (g_ctx_ptr) {
        const unsigned used = used_only ? NativeUsedTextureMask(s_tex_mirror) : 0xffu;
        for (int i = 0; i < 8; ++i) {
          if (!(used & (1u << i))) continue;
          const auto& stage = s_tex_mirror.texStages[i];
          if (stage.base_addr != 0 && stage.width > 0 && stage.height > 0) {
            draw.ref[i] = nwii::runtime::hle::TextureCache::get().prepare_texture(*g_ctx_ptr, stage, s_tex_mirror.tlutMem);
            draw.present |= uint8_t(1u << i);
          }
        }
      }
      draws.push_back(draw);
    }
  }
  if (!nwii::runtime::render_queue::Active()) { // sync path: this drain's ops and draws are what Render runs next
    nwii::runtime::hle::TextureCache::get().adopt_pending_ops();
    s_draw_textures = &draws;
  }
}

// NATIVE_RENDER_QUEUE_V1: front end, the drain whose draw list the next batch carries; GL thread, the list Render uses.
uint32_t NativeLastPrepassSeq() { return s_prepass_seq - 1; }
void NativeSelectDrawTextures(uint32_t seq) { s_draw_textures = &s_draw_textures_ring[seq % 16]; }

