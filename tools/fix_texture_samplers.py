"""Apply per-unit guest texel filters/wrap, preserving shared image cache."""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
path=port/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
source=path.read_text()
marker='// NATIVE_TEXTURE_SAMPLERS_V1:'
if marker not in source:
    needle='static void NativeDrawArchive('
    assert source.count(needle)==1
    source=source.replace(needle,(port/'runtime-overlays/texture_sampler.inc').read_text()+'\n'+needle,1)
    needle='              glBindTexture(GL_TEXTURE_2D, tex);'
    assert source.count(needle)==1
    source=source.replace(needle,needle+'\n              NativeApplyTextureSampler(i,g_state.bp[(i<4?0x80:0xa0)+(i%4)]);',1)
    # Archive effective sampler parameters, including object overrides.
    needle='GLint value=0;if(texture)glGetTexParameteriv(GL_TEXTURE_2D,param,&value);word(value);'
    assert source.count(needle)==1
    source=source.replace(needle,'GLint value=0,sampler=0;glGetIntegeri_v(GL_SAMPLER_BINDING,unit,&sampler);if(sampler)glGetSamplerParameteriv(sampler,param,&value);else if(texture)glGetTexParameteriv(GL_TEXTURE_2D,param,&value);word(value);',1)
    path.write_text(source)
# Texgen restaging replaces the helper region but preserves draw-site calls.
# Reinstallation must not accumulate identical GL state updates at that site.
call='              NativeApplyTextureSampler(i,g_state.bp[(i<4?0x80:0xa0)+(i%4)]);\n'
if source.count(call)>1:
    source=source.replace(call,'')
    needle='              glBindTexture(GL_TEXTURE_2D, tex);\n'
    assert source.count(needle)==1
    source=source.replace(needle,needle+call,1)
    path.write_text(source)
main=port/'build/nwii-full-source/nWiiRuntime/src/core/main.cpp'
source=main.read_text()
if 'NATIVE_GL_SAMPLERS_CONTEXT' not in source:
    needle='    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 2);'
    assert source.count(needle)==1
    source=source.replace(needle,'    // NATIVE_GL_SAMPLERS_CONTEXT: samplers and GLSL 330 require GL 3.3.\n    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 3);',1)
    main.write_text(source)
print('Per-unit guest texture sampler state staged; mip-chain fidelity remains open.')
