"""Preserve guest vertex depth and convert orthographic GX clip depth for GL."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';s=p.read_text()
before='    p[10] = sp[4];\n    p[14] = sp[5];'
after='    // NATIVE_ORTHO_DEPTH_V1: GX -w..0 becomes GL -w..w.\n    p[10] = 2.0f * sp[4];\n    p[14] = 2.0f * sp[5] + 1.0f;'
if before in s:s=s.replace(before,after)
elif after not in s:raise SystemExit('Orthographic matrix context changed')
before='  if (!CurrentIsPerspective())\n    bv.z = 0.0f;'
after='  // Preserve transformed guest Z for both projection types.'
if before in s:s=s.replace(before,after)
elif after not in s:raise SystemExit('Vertex depth context changed')
if p.read_text()!=s:p.write_text(s)
print('Orthographic depth conversion and guest vertex Z preserved.')
