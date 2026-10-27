"""Indirect-texturing debug views (overlay key F8) for tracking down the XP-orb shimmer.

The orbs are drawn with the refraction material: TEV stage 0 is the orb's own 128x128 palette texture, stage 1 is a 32x32 environment map
looked up through an indirect stage (a 4x4 IA8 texture addressed by projected position). To see which part produces the speckle while
looking at it, F8 cycles: 0 normal | 1 no warp (indirect offset forced to 0) | 2 indirect-using stages contribute nothing |
3 only the indirect-using stages (plain stages white) | 4 indirect map read unfiltered (texelFetch). Modes only change shaders that have
indirect stages. The mode is part of the shader cache key (only when non-zero, so normal shaders keep their cache entries).

runtime-overlays/indirect_codegen.inc holds the generator-side code (installed by fix_indirect_texturing.py); this stage adds the include,
the call after each stage's texture fetch, and the cache-key mix. Idempotent.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime/src/hle/gx'
MARK = 'NATIVE_IND_DEBUG_V1'


def apply(path, old, new, probe=None):
    s = path.read_text(errors='surrogateescape')
    if (probe or new) in s:
        return
    assert s.count(old) == 1, (path.name, old[:80], s.count(old))
    path.write_text(s.replace(old, new, 1), errors='surrogateescape')


g = rt / 'tev_shader_gen.cpp'
apply(g, '#include "runtime/gx/tev_shader_gen.h"\n', '#include "runtime/gx/tev_shader_gen.h"\n#include "runtime/native_hud.h" // ' + MARK + '\n', '#include "runtime/native_hud.h"')
# after the plain / indirect texture fetch of every stage
apply(g, '            } else fs << "    texColor=texture(uTex" << unsigned(stage.texMap) << ",tevUV);\\n";\n        } else {\n            NativeWriteIndirect(fs,state,i);',
      '            } else fs << "    texColor=texture(uTex" << unsigned(stage.texMap) << ",tevUV);\\n";\n'
      '            NativeIndirectDebugOverride(fs,state,i); // ' + MARK + '\n'
      '        } else {\n            NativeWriteIndirect(fs,state,i);',
      'NativeIndirectDebugOverride(fs,state,i);')

r = rt / 'renderer_gl.cpp'
# An earlier stage may have replaced the argument (GetShaderHash(0x90)), so match the statement by shape.
import re
src = r.read_text(errors='surrogateescape')
if 'native_hud::ind_debug.load' not in src:
    pat = re.compile(r'(  uint64_t hash = state\.GetShaderHash\([^)]*\);[^\n]*\n)')
    assert len(pat.findall(src)) == 1, ('renderer_gl.cpp', 'GetShaderHash', len(pat.findall(src)))
    mix = ('  if (const int ind_debug = native_hud::ind_debug.load(std::memory_order_relaxed)) hash = (hash ^ (0x9e3779b97f4a7c15ull * uint64_t(ind_debug))) * 1099511628211ull; // ' + MARK + '\n')
    r.write_text(pat.sub(lambda m: m.group(1) + mix, src, count=1), errors='surrogateescape')
print('indirect debug installed')
