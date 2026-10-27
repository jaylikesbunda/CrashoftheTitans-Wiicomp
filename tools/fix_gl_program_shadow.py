"""NATIVE_GL_PROGRAM_SHADOW_V1 / NATIVE_GL_BIND_SHADOW_V1 -- skip redundant binds.

SetupDrawState calls glUseProgram unconditionally once per FlushBatch, and
FlushBatch re-binds s_vao every batch. There is exactly one glUseProgram call
site and exactly four glBindVertexArray call sites in the runtime, so single
shadows are exact. GL_ARRAY_BUFFER is deliberately NOT shadowed:
NativeStreamUpload / glBufferData rebind it mid-batch. Idempotent, marker based.
"""
from pathlib import Path

PROGRAM_MARK = 'NATIVE_GL_PROGRAM_SHADOW_V1'
BIND_MARK = 'NATIVE_GL_BIND_SHADOW_V1'
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime'

# Shared header (VAO shadow).
(rt / 'include/runtime/native_gl_bind_shadow.h').write_text(
    (p / 'runtime-overlays/native_gl_bind_shadow.h').read_text())

f = rt / 'src/hle/gx/renderer_gl.cpp'
s = f.read_text()

if PROGRAM_MARK not in s:
    old = '  glUseProgram(s_active_shader.id);\n'
    new = ('  { // ' + PROGRAM_MARK + ': one program-bind site, so a shadow is exact.\n'
           '    static GLuint s_native_program = 0;\n'
           '    if (s_active_shader.id != s_native_program) { glUseProgram(s_active_shader.id); s_native_program = s_active_shader.id; }\n'
           '  }\n')
    assert s.count(old) == 1, ('glUseProgram anchor', s.count(old))
    s = s.replace(old, new, 1)

if BIND_MARK not in s:
    include = '#include "runtime/native_gl_bind_shadow.h" // ' + BIND_MARK
    anchor = '#include "runtime/native_gl_texture_shadow.h"'
    if anchor in s:
        s = s.replace(anchor, anchor + '\n' + include, 1)
    else:
        s = include + '\n' + s
    assert s.count('glBindVertexArray(') == 4, ('glBindVertexArray sites', s.count('glBindVertexArray('))
    s = s.replace('glBindVertexArray(', 'native_gl_bind_shadow::BindVertexArray(')

if s != f.read_text():
    f.write_text(s)
print('Redundant glUseProgram / glBindVertexArray skipped when unchanged.')
