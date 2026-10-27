"""Never discard a draw solely because its shader was not compiled earlier."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';s=p.read_text()
start='  } else if (s_compiles_this_frame >= kMaxCompilesPerFrame) {'
if start in s:
    a=s.index(start);b=s.index('  } else {',a)
    s=s[:a]+'  // NATIVE_NO_SHADER_DRAW_DROPS: compile on first use, retain every draw.\n'+s[b:]
    p.write_text(s)
print('First-use shader compilation retains every submitted draw.')
