"""Honor GX depth comparisons/writes and colour writes across FIFO drains."""
from pathlib import Path

PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s = p.read_text()
marker = '// NATIVE_GX_DEPTH_MASKS_V1\n'
overlay = (PORT / 'runtime-overlays/gx_depth_masks.inc').read_text()
start = s.index(marker) if marker in s else s.index('static void ApplyZMode() {')
end = s.index('// Apply the GX blend state', start)
s = s[:start] + marker + overlay + '\n' + s[end:]
needle = '  uint32_t cm = g_state.bp[0x41];\n'
replacement = needle + '  ApplyColorMask();\n'
if replacement not in s:
    if s.count(needle) != 1:
        raise SystemExit('ApplyBlend context changed')
    s = s.replace(needle, replacement, 1)
old = '    glDepthMask(GL_TRUE);\n    glClear(GL_DEPTH_BUFFER_BIT);'
new = '''    // NATIVE_EFB_DEPTH_PERSIST_V1: initialize once, retain across drains.
    static bool depth_initialized = false;
    if (!depth_initialized) {
      glDepthMask(GL_TRUE);
      glClearDepth(1.0);
      glClear(GL_DEPTH_BUFFER_BIT);
      depth_initialized = true;
    }'''
if old in s:
    s = s.replace(old, new, 1)
elif new not in s:
    # Final tracing wraps glClear in a GPU scope without changing initialization.
    a = s.find('// NATIVE_EFB_DEPTH_PERSIST_V1:')
    b = s.find('depth_initialized = true;', a)
    block = s[a:b] if a >= 0 and b > a else ''
    block=block.replace('NativeRasterDepthMask(', 'glDepthMask(')
    if not all(t in block for t in ('static bool depth_initialized = false;',
            'if (!depth_initialized)', 'glDepthMask(GL_TRUE);',
            'glClearDepth(1.0);', 'glClear(GL_DEPTH_BUFFER_BIT);')):
        raise SystemExit('Render depth initialization context changed')
# The one glClear inside the overlay remains; replace the two original copy
# clear sites only. Observer staging runs before this tool on every build.
before, after = s.split('// Apply the GX blend state', 1)
after = after.replace('glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);', 'NativeClearEfb();')
s = before + '// Apply the GX blend state' + after
if p.read_text() != s:
    p.write_text(s)
print('GX depth/masks staged; EFB depth retained between host drains.')
