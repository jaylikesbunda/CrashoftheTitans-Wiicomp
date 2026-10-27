"""Colour decode lookup tables, forced-inline colour reads, and a constant-size vertex pack copy.

Capture frame-cost-20261006-121918-356500, renderer thread: NativeDecodeDraw<false> 3.57 ms, DecodeColor
1.14 ms (four int-to-float divisions per colour), EmitVertex 4.30 ms, upload pack copy (per-vertex memcpy with a
run-time stride, ucrtbase<-FlushBatch 108 stacks). Lookup values are computed with the same division
(i/255.0f, i/31.0f, i/63.0f, i/15.0f) so every decoded float is bit-identical; the pack copy only
makes the stride a compile-time constant for the common 64+12*generators layouts.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx'
marker='NATIVE_DECODE_PACK_TUNING_V1'
f=rt/'fifo_parser.cpp';s=f.read_text()
if marker not in s:
    anchor='    void DecodeColor(const uint8_t* p, VtxAttrType clrType, float out[4]) {'
    assert s.count(anchor)==1
    lut='''    // NATIVE_DECODE_PACK_TUNING_V1: same divisions as DecodeColor, evaluated once per table entry.
    struct ColorLut {
        float b255[256], f31[32], f63[64], f15[16];
        ColorLut() {
            for (int i = 0; i < 256; ++i) b255[i] = i / 255.0f;
            for (int i = 0; i < 32; ++i) f31[i] = i / 31.0f;
            for (int i = 0; i < 64; ++i) f63[i] = i / 63.0f;
            for (int i = 0; i < 16; ++i) f15[i] = i / 15.0f;
        }
    };
    const ColorLut g_color_lut;
    __attribute__((always_inline)) inline void DecodeColorFast(const uint8_t* p, VtxAttrType clrType, float out[4]) {
        switch ((int)clrType) {
            case 0: {
                uint16_t c = (p[0] << 8) | p[1];
                out[0] = g_color_lut.f31[(c >> 11) & 0x1F];
                out[1] = g_color_lut.f63[(c >> 5) & 0x3F];
                out[2] = g_color_lut.f31[c & 0x1F];
                out[3] = 1.0f;
                break;
            }
            case 1:
            case 2:
                out[0] = g_color_lut.b255[p[0]];
                out[1] = g_color_lut.b255[p[1]];
                out[2] = g_color_lut.b255[p[2]];
                out[3] = 1.0f;
                break;
            case 3: {
                uint16_t c = (p[0] << 8) | p[1];
                out[0] = g_color_lut.f15[(c >> 12) & 0xF];
                out[1] = g_color_lut.f15[(c >> 8) & 0xF];
                out[2] = g_color_lut.f15[(c >> 4) & 0xF];
                out[3] = g_color_lut.f15[c & 0xF];
                break;
            }
            case 4: {
                uint32_t c = (p[0] << 16) | (p[1] << 8) | p[2];
                out[0] = g_color_lut.f63[(c >> 18) & 0x3F];
                out[1] = g_color_lut.f63[(c >> 12) & 0x3F];
                out[2] = g_color_lut.f63[(c >> 6) & 0x3F];
                out[3] = g_color_lut.f63[c & 0x3F];
                break;
            }
            default:
                out[0] = g_color_lut.b255[p[0]];
                out[1] = g_color_lut.b255[p[1]];
                out[2] = g_color_lut.b255[p[2]];
                out[3] = g_color_lut.b255[p[3]];
                break;
        }
    }

'''
    s=s.replace(anchor,lut+anchor,1)
    old='    bool ReadColorAttr(const std::vector<uint8_t>& fifo, size_t& fifo_offset, size_t fifo_size,'
    assert s.count(old)==1
    s=s.replace(old,'    __attribute__((always_inline)) inline bool ReadColorAttr(const std::vector<uint8_t>& fifo, size_t& fifo_offset, size_t fifo_size,',1)
    a=s.index('inline bool ReadColorAttr(');b=s.index('\n    }\n',a)
    body=s[a:b]
    assert body.count('DecodeColor(')==2
    s=s[:a]+body.replace('DecodeColor(','DecodeColorFast(')+s[b:]
    f.write_text(s)
f=rt/'renderer_gl.cpp';s=f.read_text()
if marker not in s:
    old='    for(size_t i=0;i<s_batch.size();++i)std::memcpy(packed.data()+i*stride,&s_batch[i],stride);\n'
    assert s.count(old)==1
    new='''    // NATIVE_DECODE_PACK_TUNING_V1: constant-size copies for the 64 + 12 * generators layouts.
    {
      uint8_t* dst=packed.data();const BatchVertex* src=s_batch.data();const size_t n=s_batch.size();
      auto pack=[&](auto size){constexpr size_t S=decltype(size)::value;for(size_t i=0;i<n;++i)std::memcpy(dst+i*S,src+i,S);};
      switch(stride){
        case 76:pack(std::integral_constant<size_t,76>{});break;case 88:pack(std::integral_constant<size_t,88>{});break;
        case 100:pack(std::integral_constant<size_t,100>{});break;case 112:pack(std::integral_constant<size_t,112>{});break;
        case 124:pack(std::integral_constant<size_t,124>{});break;case 136:pack(std::integral_constant<size_t,136>{});break;
        case 148:pack(std::integral_constant<size_t,148>{});break;
        default:for(size_t i=0;i<n;++i)std::memcpy(dst+i*stride,&s_batch[i],stride);
      }
    }
'''
    s=s.replace(old,new,1)
    if '#include <type_traits>' not in s:s=s.replace('#include <cstring>\n','#include <cstring>\n#include <type_traits>\n',1)
    f.write_text(s)
print('Decode lookup tables and constant-size vertex pack installed.')
