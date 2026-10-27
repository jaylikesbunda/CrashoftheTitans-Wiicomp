"""Render-to-texture (EFB copy) guard sized from the copy's real format, plus counters for when a copy texture is abandoned.

EFB copies are kept as GL textures keyed by destination address and are never written back to guest RAM. TextureCache::get_texture uses
the GL copy only while the guest bytes at the destination still equal a snapshot taken at copy time (efb_guest_unchanged); otherwise it
decodes guest RAM, which holds whatever was there before (never the copied image) -> random-looking noise or the effect vanishing.
The snapshot length was efbW*efbH*2 for every copy, regardless of format and half-scale:
  * I4/I8/IA4 copies (0.5-1 byte/pixel) and half-scale copies guarded 2-8x the real footprint, so unrelated guest writes next to the
    copy made the check fail -> the copy was dropped for that draw (flicker) and RAM bytes were sampled (noise);
  * RGBA8 copies (4 bytes/pixel) guarded only half their footprint.
The length now follows the copy register (BP 0x52: target format bits 3-6 -> real format fmt/2+(fmt&1)*8, half-scale bit 9) with GX
tile rounding and the copy stride (BP 0x4D), i.e. exactly the bytes the hardware copy writes. The renderer passes the resolved BP 0x52
value. Trace marks texture.efb_hit / texture.efb_fallback / texture.efb_inside (lookup inside a copy but not at its start) show at the
next capture whether copies are still being abandoned. Idempotent.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime/src'
MARK = 'NATIVE_EFB_COPY_GUARD_V1'


def apply(path, old, new, probe):
    s = path.read_text(errors='surrogateescape')
    if probe in s:
        return
    assert s.count(old) == 1, (path.name, old[:70], s.count(old))
    path.write_text(s.replace(old, new, 1), errors='surrogateescape')


tc = rt / 'hle/texture_cache_gl.cpp'
helper = '''namespace nwii::runtime::hle {
// ''' + MARK + ''': bytes a GX EFB copy writes (format, half scale, tiles, stride). Set by the renderer before register_efb_texture.
uint32_t g_native_efb_copy_value = 0, g_native_efb_copy_stride = 0;
uint32_t NativeEfbCopyBytes(uint32_t copy, uint32_t w, uint32_t h, uint32_t stride) {
    if (copy & 0x200u) { w = (w + 1) / 2; h = (h + 1) / 2; }               // half_scale
    const uint32_t tpf = (copy >> 3) & 0xFu, fmt = tpf / 2 + (tpf & 1u) * 8; // EFB copy format
    uint32_t tw = 4, th = 4, bpp = 16;
    switch (fmt) {
        case 0x0: tw = 8; th = 8; bpp = 4; break;                             // R4 / I4
        case 0x1: case 0x2: case 0x7: case 0x8: case 0x9: case 0xA: tw = 8; th = 4; bpp = 8; break; // R8, RA4, A8, R8, G8, B8
        case 0x6: tw = 4; th = 4; bpp = 32; break;                            // RGBA8
        default: tw = 4; th = 4; bpp = 16; break;                             // RA8, RGB565, RGB5A3, RG8, GB8
    }
    const uint32_t rows = (h + th - 1) / th, row_bytes = (w + tw - 1) / tw * tw * th * bpp / 8;
    if (!rows || !row_bytes) return 0;
    return (rows - 1) * std::max(stride, row_bytes) + row_bytes;
}
}
'''
apply(tc, 'namespace nwii::runtime { extern CPUContext *g_ctx_ptr; }',
      'namespace nwii::runtime { extern CPUContext *g_ctx_ptr; }\n' + helper, 'uint32_t NativeEfbCopyBytes(')
apply(tc, '        uint32_t bytes = gx::g_state.efbW * gx::g_state.efbH * 2;\n        bytes = std::min<uint32_t>(std::max<uint32_t>(bytes, 32), 1u << 20);',
      '        uint32_t bytes = NativeEfbCopyBytes(g_native_efb_copy_value, gx::g_state.efbW, gx::g_state.efbH, g_native_efb_copy_stride); // ' + MARK + '\n'
      '        bytes = std::min<uint32_t>(std::max<uint32_t>(bytes, 32), 1u << 22);\n'
      '        efb_extent[addr & 0x01FFFFFF] = bytes;',
      'NativeEfbCopyBytes(g_native_efb_copy_value')
apply(tc, '    if (GLuint efb = find_efb_texture(stage.base_addr)) {\n        if (efb_guest_unchanged(ctx, stage.base_addr))\n            return efb;\n    }',
      '    if (GLuint efb = find_efb_texture(stage.base_addr)) {\n'
      '        if (efb_guest_unchanged(ctx, stage.base_addr)) { native_trace::mark("texture.efb_hit"); return efb; } // ' + MARK + '\n'
      '        native_trace::mark("texture.efb_fallback", stage.base_addr, stage.format); // copy abandoned: guest bytes at the destination changed\n'
      '    } else if (!efb_extent.empty() && native_trace::active.load(std::memory_order_relaxed)) {\n'
      '        const uint32_t a = stage.base_addr & 0x01FFFFFF;\n'
      '        for (const auto& e : efb_extent) if (a > e.first && a < e.first + e.second) { native_trace::mark("texture.efb_inside", stage.base_addr, a - e.first); break; }\n'
      '    }',
      'native_trace::mark("texture.efb_fallback"')
h = p / 'build/nwii-full-source/nWiiRuntime/include/runtime/texture_cache_gl.h'
apply(h, '    std::unordered_map<uint32_t, GLuint> efb_textures;',
      '    std::unordered_map<uint32_t, GLuint> efb_textures;\n    std::unordered_map<uint32_t, uint32_t> efb_extent; // ' + MARK + ': guarded copy footprint by destination',
      'efb_extent;')

r = rt / 'hle/gx/renderer_gl.cpp'
apply(r, '            NativeCopyEfbTexture(tex,g_state.efbSrcX,g_state.efbSrcY,w,h,NativeResolutionScale());\n',
      '            NativeCopyEfbTexture(tex,g_state.efbSrcX,g_state.efbSrcY,w,h,NativeResolutionScale());\n'
      '            nwii::runtime::hle::g_native_efb_copy_value = bp_value; nwii::runtime::hle::g_native_efb_copy_stride = g_state.efbCopyStride; // ' + MARK + '\n',
      'nwii::runtime::hle::g_native_efb_copy_value = bp_value;')
s = r.read_text(errors='surrogateescape')
if 'extern uint32_t g_native_efb_copy_value' not in s:  # file scope (the copy path itself lives inside namespace nwii::runtime::gx)
    s = '#include <cstdint>\nnamespace nwii::runtime::hle { extern uint32_t g_native_efb_copy_value, g_native_efb_copy_stride; } // ' + MARK + '\n' + s
    r.write_text(s, errors='surrogateescape')
print('EFB copy guard installed')
