"""NATIVE_GL_STATE_REDUNDANCY_V1 -- skip redundant buffer binds in the batch helpers.

Per-draw GL state in renderer_gl.cpp is already shadowed (program, VAO, raster
caps, viewport/depth range, texture unit/binding/sampler, uniforms). These binds
are not:

  * glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, s_ebo) in FlushBatch, once per indexed
    flush. The element binding is VAO state, so the memo records the VAO it was
    set for and rebinds only when the current VAO differs.
  * glBindBuffer(GL_ARRAY_BUFFER, ...) in NativeUploadBatch and
    NativeRetainedGpuBind. NativeBindArrayBuffer memoises the last name bound.
    Every bind site is routed through it, and native_hud_gl.h restores the
    previous array buffer exactly.

The eviction path deletes a retained VAO and VBO. Deleting a bound object
unbinds it in GL, so that path resets both memos and the VAO shadow. Without the
reset, a recycled VAO name would be skipped by BindVertexArray.

Anchors are single-line strings inside the GL-call helpers only (NativeRetainedGpuBind,
NativeUploadBatch, FlushBatch). SetupDrawState, DrawPrimitive and Render are not touched.
The file is CRLF; inserted lines use CRLF.

Opt-out: NWII_NO_GL_STATE_REDUNDANCY=1 leaves the renderer untouched when applying.
--restore reverses every edit exactly. Apply and restore are both idempotent.
Usage: python fix_gl_state_redundancy.py [--restore] [renderer_gl.cpp]
"""
import os
import sys
from pathlib import Path

MARK = 'NATIVE_GL_STATE_REDUNDANCY_V1'
ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'

RETAINED_HEAD = 'static bool NativeRetainedGpuBind(size_t stride,unsigned generators){'
HELPERS = [
    '// NATIVE_GL_STATE_REDUNDANCY_V1: last GL_ARRAY_BUFFER name bound by the renderer (0 = unknown).',
    '// s_native_element_vao is the VAO for which GL_ELEMENT_ARRAY_BUFFER was last set to s_ebo.',
    'static GLuint s_native_array_buffer = 0;',
    'static GLuint s_native_element_vao = 0;',
    'static void NativeBindArrayBuffer(GLuint buffer) {',
    '  if (s_native_array_buffer != buffer) { glBindBuffer(GL_ARRAY_BUFFER, buffer); s_native_array_buffer = buffer; }',
    '}',
]

# (old, new): each old must occur exactly once in the file at the time it is applied.
EDITS = [
    (RETAINED_HEAD, '\r\n'.join(HELPERS) + '\r\n' + RETAINED_HEAD),
    ('entry.stamp=++s_geometry_gpu_stamp;native_gl_bind_shadow::BindVertexArray(entry.vao);glBindBuffer(GL_ARRAY_BUFFER,entry.vbo);',
     'entry.stamp=++s_geometry_gpu_stamp;native_gl_bind_shadow::BindVertexArray(entry.vao);NativeBindArrayBuffer(entry.vbo);'),
    ('glGenBuffers(1,&entry.vbo);glBindBuffer(GL_ARRAY_BUFFER,entry.vbo);',
     'glGenBuffers(1,&entry.vbo);glBindBuffer(GL_ARRAY_BUFFER,entry.vbo);s_native_array_buffer=entry.vbo;'),
    ('glDeleteVertexArrays(1,&it->vao);glDeleteBuffers(1,&it->vbo);',
     'glDeleteVertexArrays(1,&it->vao);glDeleteBuffers(1,&it->vbo);s_native_array_buffer=0;s_native_element_vao=0;native_gl_bind_shadow::vao()=0;'),
    ('native_gl_bind_shadow::BindVertexArray(s_vao);glBindBuffer(GL_ARRAY_BUFFER,s_vbo);',
     'native_gl_bind_shadow::BindVertexArray(s_vao);NativeBindArrayBuffer(s_vbo);'),
    ('    glBindBuffer(GL_ARRAY_BUFFER, s_vbo);',
     '    NativeBindArrayBuffer(s_vbo);'),
    ('    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,s_ebo);',
     '    { const GLuint vao_now=native_gl_bind_shadow::vao(); if(!vao_now || s_native_element_vao!=vao_now){ glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,s_ebo); s_native_element_vao=vao_now; } }'),
]


def apply_text(text):
    """Return (text, changed). Idempotent: a file already carrying MARK is returned as is."""
    if MARK in text:
        return text, False
    for old, new in EDITS:
        count = text.count(old)
        if count != 1:
            raise SystemExit('anchor count %d for: %s' % (count, old[:90]))
        text = text.replace(old, new, 1)
    return text, True


def restore_text(text):
    """Reverse apply_text exactly. Idempotent: a file without MARK is returned as is."""
    if MARK not in text:
        return text, False
    for old, new in reversed(EDITS):
        count = text.count(new)
        if count != 1:
            raise SystemExit('restore anchor count %d for: %s' % (count, new[:90]))
        text = text.replace(new, old, 1)
    if MARK in text:
        raise SystemExit('restore left the marker behind')
    return text, True


def main(argv):
    restore = '--restore' in argv
    args = [a for a in argv if not a.startswith('--')]
    path = Path(args[0]) if args else DEFAULT
    if not restore and os.environ.get('NWII_NO_GL_STATE_REDUNDANCY'):
        print('gl_state_redundancy: opted out (NWII_NO_GL_STATE_REDUNDANCY set), leaving %s' % path)
        return 0
    text = path.read_bytes().decode('utf-8')
    new, changed = restore_text(text) if restore else apply_text(text)
    if changed:
        path.write_bytes(new.encode('utf-8'))
    print('gl_state_redundancy: %s %s (%s)' % ('restored' if restore else 'applied',
                                              path, 'changed' if changed else 'already in that state'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
