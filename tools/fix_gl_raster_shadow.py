"""Remove redundant raster driver calls across every production state writer."""
import re
from gl_state_shadow import p,specs,header
rt=p/'build/nwii-full-source/nWiiRuntime';support=p/'runtime-overlays/native_gl_raster_shadow.h'
value=header()
if not support.exists() or support.read_text()!=value:support.write_text(value)
names=['Enable','Disable']+[name for name,types,args,valid in specs]
pattern=re.compile(r'\bgl('+ '|'.join(names)+r')\(')
writers=[]
for f in (rt/'src').rglob('*.cpp'):
 s=f.read_text();
 if pattern.search(s):writers.append(f.relative_to(rt).as_posix())
assert set(writers)<=set(('src/core/main.cpp','src/hle/gx/renderer_gl.cpp')),'New external GL state writer requires qualification: '+str(writers)
# The only header writer is a balanced diagnostic readback: it restores the
# incoming scissor state and invokes no rendering/state-shadow functions inside.
header_writers=[f.relative_to(rt).as_posix() for f in (rt/'include').rglob('*.h') if pattern.search(f.read_text())]
assert header_writers==['include/runtime/native_resolution.h'],header_writers
readback=(rt/'include/runtime/native_resolution.h').read_text()
assert readback.count('glDisable(GL_SCISSOR_TEST);')==1 and readback.count('if(scissor)glEnable(GL_SCISSOR_TEST);')==1
assert len(pattern.findall(readback))==2 and 'NativeRaster' not in readback
for relative in ('src/core/main.cpp','src/hle/gx/renderer_gl.cpp'):
 f=rt/relative;s=f.read_text();baseline=p/'runtime-overlays'/('raster-before-'+f.name)
 if 'NATIVE_GL_RASTER_SHADOW_V1' not in s:
  if not baseline.exists():baseline.write_text(s)
  s=pattern.sub(lambda m:'NativeRaster'+m[1]+'(',s)
  s=s.replace('#include <glad/glad.h>','#include <glad/glad.h>\n// NATIVE_GL_RASTER_SHADOW_V1\n#include "'+support.as_posix()+'"',1)
  if relative=='src/core/main.cpp':s=s.replace('gl_ctx = SDL_GL_CreateContext(window);','native_raster::invalidate();\n      gl_ctx = SDL_GL_CreateContext(window);',1)
  f.write_text(s)
 # Earlier overlays may reinstall individual functions after the first pass.
 # Wrap any fresh setters even when the shared include is already installed.
 current=f.read_text();updated=pattern.sub(lambda m:'NativeRaster'+m[1]+'(',current)
 if current!=updated:f.write_text(updated)
print('Shared raster-state shadow installed across both writers; context changes invalidate state.')
