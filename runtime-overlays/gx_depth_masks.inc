// BP 0x40: enable, three-bit comparison, update enable. Our projection maps
// GX depth to increasing GL window depth, so comparisons retain their order.
static void ApplyZMode() {
  const uint32_t z = g_state.bp[0x40];
  static const GLenum compare[8] = {
      GL_NEVER, GL_LESS, GL_EQUAL, GL_LEQUAL,
      GL_GREATER, GL_NOTEQUAL, GL_GEQUAL, GL_ALWAYS};
  if (z & 1) glEnable(GL_DEPTH_TEST);
  else glDisable(GL_DEPTH_TEST);
  glDepthFunc(compare[(z >> 1) & 7]);
  glDepthMask((z & 0x10) ? GL_TRUE : GL_FALSE);
}

static void ApplyColorMask() {
  const uint32_t cm = g_state.bp[0x41];
  const GLboolean rgb = (cm & 8) ? GL_TRUE : GL_FALSE;
  glColorMask(rgb, rgb, rgb, (cm & 16) ? GL_TRUE : GL_FALSE);
}

// GX copy clears use the guest write enables, independently of depth testing.
// A host FIFO drain is not a frame boundary and must retain existing EFB depth.
static void NativeClearEfb() {
  ApplyColorMask();
  glDepthMask((g_state.bp[0x40] & 16) ? GL_TRUE : GL_FALSE);
  glClearDepth(double(g_state.bp[0x51] & 0xffffff) / 16777215.0);
  glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
}
