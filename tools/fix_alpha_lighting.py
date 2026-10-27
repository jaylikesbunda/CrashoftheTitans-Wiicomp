"""Evaluate XF RGB and alpha lighting under their independent control words."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/tev_shader_gen.cpp'
s=p.read_text()
marker='// NATIVE_INDEPENDENT_ALPHA_LIGHTING'
if marker not in s:
    start=s.index('vec4 dolphin_chan(')
    end=s.index('\n)";',start)
    old=s[start:end]
    loop=old[old.index('    if (lit) {'):old.index('    return clamp(mat')]
    new='''// NATIVE_INDEPENDENT_ALPHA_LIGHTING
vec4 native_chan_light(int ctrl, vec3 pos, vec3 nrm, vec4 ambient) {
    bool lit = (ctrl & 2) != 0;
    int diffusefunc = (ctrl >> 7) & 3;
    int attnfunc = (ctrl >> 9) & 3;
    int lightmask = ((ctrl >> 2) & 0xF) | (((ctrl >> 11) & 0xF) << 4);
    vec4 lacc = lit ? ambient : vec4(1.0);
'''+loop+'''    return clamp(lacc, 0.0, 1.0);
}
vec4 dolphin_chan(int chan, vec3 pos, vec3 nrm, vec4 vcol) {
    int rgbctrl = uChanCtrl[chan];
    int alphactrl = uChanCtrl[chan + 2];
    vec4 mat = vec4((rgbctrl & 1) != 0 ? vcol.rgb : uMatColor[chan].rgb,
                    (alphactrl & 1) != 0 ? vcol.a : uMatColor[chan].a);
    vec4 rgbamb = (rgbctrl & 0x40) != 0 ? vcol : uAmbColor[chan];
    vec4 alphaamb = (alphactrl & 0x40) != 0 ? vcol : uAmbColor[chan];
    vec4 rgb = native_chan_light(rgbctrl, pos, nrm, rgbamb);
    vec4 alpha = native_chan_light(alphactrl, pos, nrm, alphaamb);
    return clamp(mat * vec4(rgb.rgb, alpha.a), 0.0, 1.0);
}
'''
    p.write_text(s[:start]+new+s[end:])
print('Independent alpha material, ambient, mask, diffuse and attenuation staged.')
