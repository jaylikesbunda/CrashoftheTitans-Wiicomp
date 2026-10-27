#pragma once
// NATIVE_GL_BIND_SHADOW_V1: vertex-array binds have exactly four call sites in
// the renderer, so one shadow is exact. GL_ARRAY_BUFFER is intentionally not
// shadowed here: NativeStreamUpload / glBufferData rebind it mid-batch.
namespace native_gl_bind_shadow {
inline unsigned& vao() { static unsigned value = 0; return value; }
inline void BindVertexArray(unsigned value) {
    if (value != vao()) { glBindVertexArray(value); vao() = value; }
}
}
