"""Route every native texture binding owner through a shared context shadow."""
from pathlib import Path
import argparse,re

MARKER='NATIVE_TEXTURE_BINDING_SHADOW_INCLUDE_V1'
FILES=['src/core/main.cpp','src/hle/gx/renderer_gl.cpp','src/hle/texture_cache_gl.cpp',
       'include/runtime/native_hud_gl.h','include/runtime/native_resolution.h']
INCLUDE='#include "runtime/native_gl_texture_shadow.h" // '+MARKER+'\n'
RENAMES={'glActiveTexture':'NativeTextureActive','glBindTexture':'NativeTextureBind',
         'glBindSampler':'NativeTextureBindSampler','glDeleteTextures':'NativeTextureDelete',
         'glGenTextures':'NativeTextureGenerate','glGenSamplers':'NativeTextureGenerateSamplers',
         'glDeleteSamplers':'NativeTextureDeleteSamplers'}
CONTEXT_OLD='      gl_ctx = SDL_GL_CreateContext(window);'
CONTEXT_NEW='      native_texture_shadow::invalidate(); // New context has unknown bindings.\n'+CONTEXT_OLD

def restore(source,context=False):
    if MARKER not in source:
        if any(re.search(r'\b'+name+r'\s*\(',source)for name in RENAMES.values()):
            raise RuntimeError('Partial texture-binding shadow stage')
        return source
    include=re.search(r'^#include "runtime/native_gl_texture_shadow\.h"[^\n]*\n',source,re.M)
    if include is None:
        # fix_gl_program_shadow.py appends its own include to this line (the marker comment then rides
        # on the bind-shadow include), and that stage has no restore, so the merged form is the
        # canonical staged state and there is nothing left to undo here.
        return source
    source=source[:include.start()]+source[include.end():]
    source=source.replace(' // '+MARKER,'')
    for old,new in RENAMES.items():source=re.sub(r'\b'+new+r'(?=\s*\()',old,source)
    if context:
        if source.count(CONTEXT_NEW)!=1:raise RuntimeError('Missing texture context invalidation')
        source=source.replace(CONTEXT_NEW,CONTEXT_OLD,1)
    return source

def apply(source,context=False):
    if MARKER in source:
        if re.search(r'^#include "runtime/native_gl_texture_shadow\.h"[^\n]*\n',source,re.M) is None:
            return source          # merged into the bind-shadow include: canonical, nothing to do
        canonical=restore(source,context)
        if apply(canonical,context)!=source:raise RuntimeError('Noncanonical texture shadow stage')
        return source
    restore(source,context)
    source=INCLUDE+source
    for old,new in RENAMES.items():source=re.sub(r'\b'+old+r'(?=\s*\()',new,source)
    if context:
        if source.count(CONTEXT_OLD)!=1:raise RuntimeError('Missing GL context creation')
        source=source.replace(CONTEXT_OLD,CONTEXT_NEW,1)
    return source

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--restore',action='store_true');args=parser.parse_args()
    rt=args.port/'build/nwii-full-source/nWiiRuntime';edits=[]
    for name in FILES:
        path=rt/name
        # A pristine vendor tree has no generated HUD/resolution overlays yet.
        # Restore is a no-op for those absent overlays; apply still requires all
        # reviewed owners, and missing vendor source files remain an error.
        if args.restore and name.startswith('include/runtime/native_') and not path.exists():continue
        before=path.read_text();after=(restore if args.restore else apply)(before,name=='src/core/main.cpp')
        edits.append((path,before,after))
    # Fail closed if a new owner appeared outside the reviewed set.
    if not args.restore:
        raw=re.compile(r'\b(?:'+ '|'.join(RENAMES)+r')\s*\(')
        for sub in ['src','include']:
            for path in(rt/sub).rglob('*'):
                if path.suffix not in ['.cpp','.h','.inc'] or path.name=='native_gl_texture_shadow.h':continue
                if path.relative_to(rt).as_posix() not in FILES and raw.search(path.read_text(errors='replace')):
                    raise RuntimeError('Unrouted GL texture owner: '+str(path))
    for path,before,after in edits:
        if before!=after:path.write_text(after)
    if not args.restore:
        header=rt/'include/runtime/native_gl_texture_shadow.h'
        value=(args.port/'runtime-overlays/native_gl_texture_shadow.h').read_text()
        if not header.exists()or header.read_text()!=value:header.write_text(value)
    print('Texture binding shadow restored.'if args.restore else 'All texture binding owners share context/deletion-aware state.')

if __name__=='__main__':main()
