#pragma once
// NATIVE_TEXTURE_DECODE_FAST_V1: pointer-based texture decoders for the non-paletted GX formats, and the DXT1 conversion
// used by the CMPR upload path. Each decoder is the logic of TextureCache::decode_* with the MMU reads replaced by byte
// loads from one host pointer. The pointer is only used when every byte the decoder can touch lies in one RAM bank;
// otherwise TryDecode returns false and the caller runs the original per-pixel decoder.
// Opt-out: NWII_NO_FAST_TEXTURE_DECODE=1 (decoders), NWII_NO_CMPR_PASSTHROUGH=1 (DXT1 upload).
// Palette formats (C4/C8/C14X2) keep their existing native_palette_decode path.
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <vector>

namespace native_texture_decode {

inline bool FastEnabled() {
    static const bool on = std::getenv("NWII_NO_FAST_TEXTURE_DECODE") == nullptr;
    return on;
}
inline bool CmprPassthroughEnabled() {
    static const bool on = std::getenv("NWII_NO_CMPR_PASSTHROUGH") == nullptr;
    return on;
}

// Same expansions as texture_cache_gl.cpp (c5to8, c6to8, c4to8, c3to8).
inline uint8_t c5to8(uint32_t v) { return (uint8_t)((v << 3) | (v >> 2)); }
inline uint8_t c6to8(uint32_t v) { return (uint8_t)((v << 2) | (v >> 4)); }
inline uint8_t c4to8(uint32_t v) { return (uint8_t)((v << 4) | v); }
inline uint8_t c3to8(uint32_t v) { return (uint8_t)((v << 5) | (v << 2) | (v >> 1)); }

// Format numbers of gx::TextureFormat (texture_cache_gl.h).
enum : uint32_t { kI4 = 0, kI8 = 1, kIA4 = 2, kIA8 = 3, kRGB565 = 4, kRGB5A3 = 5, kRGBA8 = 6, kCMPR = 14 };

// Byte offset of texel (x, y) inside a tiled texture; identical to get_tile_address in texture_cache_gl.cpp.
inline uint32_t TileAddress(uint32_t x, uint32_t y, uint32_t bw, uint32_t bh, uint32_t bpp, uint32_t width) {
    const uint32_t tiles_x = (width + bw - 1) / bw;
    const uint32_t tile_idx = (y / bh) * tiles_x + x / bw;
    const uint32_t tile_offset = tile_idx * bw * bh * bpp / 8;
    const uint32_t in_tile_offset = ((y % bh) * bw + x % bw) * bpp / 8;
    return tile_offset + in_tile_offset;
}

inline uint16_t BE16(const uint8_t* p) { return uint16_t((p[0] << 8) | p[1]); }
inline uint32_t BE32(const uint8_t* p) { return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3]; }

inline void UnpackRgb565(uint16_t c, uint8_t out[4]) {
    out[0] = c5to8((c >> 11) & 0x1F);
    out[1] = c6to8((c >> 5) & 0x3F);
    out[2] = c5to8(c & 0x1F);
    out[3] = 255;
}
inline void UnpackRgb5a3(uint16_t c, uint8_t out[4]) {
    if (c & 0x8000) {
        out[0] = c5to8((c >> 10) & 0x1F);
        out[1] = c5to8((c >> 5) & 0x1F);
        out[2] = c5to8(c & 0x1F);
        out[3] = 255;
        return;
    }
    out[0] = c4to8((c >> 8) & 0xF);
    out[1] = c4to8((c >> 4) & 0xF);
    out[2] = c4to8(c & 0xF);
    out[3] = c3to8((c >> 12) & 0x7);
}

// RAM window: the host pointer for guest address `addr` when `needed` bytes from it lie in one bank. Mirrors the
// test decode_paletted uses. M is MMU (kMem1Limit, kMem2Limit, get_ptr).
template <class M>
const uint8_t* Window(M& mmu, uint32_t addr, uint32_t needed) {
    const uint32_t p = addr & 0x3fffffffu;
    const bool span = (p < M::kMem1Limit && needed <= M::kMem1Limit - p) ||
                      (p >= 0x10000000u && p - 0x10000000u < M::kMem2Limit && needed <= M::kMem2Limit - (p - 0x10000000u));
    return span ? mmu.get_ptr(addr) : nullptr;
}

// Tiled formats: bytes used by the decoder (whole tiles, as the original reads them).
inline uint32_t TiledBytes(uint32_t width, uint32_t height, uint32_t tw, uint32_t th, uint32_t tile_bytes) {
    return ((width + tw - 1) / tw) * ((height + th - 1) / th) * tile_bytes;
}

// Decoder bodies over a host pointer. Out is any struct with r, g, b, a members (raylib Color).
template <class C>
void DecodeFast(uint32_t format, const uint8_t* p, uint32_t width, uint32_t height, C* out) {
    uint8_t px[4];
    switch (format) {
        case kI4:
            for (uint32_t y = 0; y < height; y++)
                for (uint32_t x = 0; x < width; x += 2) {
                    const uint8_t b = p[TileAddress(x, y, 8, 8, 4, width)];
                    const uint8_t i1 = c4to8(b >> 4), i2 = c4to8(b & 0x0F);
                    out[y * width + x] = {i1, i1, i1, i1};
                    if (x + 1 < width) out[y * width + x + 1] = {i2, i2, i2, i2};
                }
            return;
        case kI8:
            for (uint32_t y = 0; y < height; y++)
                for (uint32_t x = 0; x < width; x++) {
                    const uint8_t i = p[TileAddress(x, y, 8, 4, 8, width)];
                    out[y * width + x] = {i, i, i, i};
                }
            return;
        case kIA4:
            for (uint32_t y = 0; y < height; y++)
                for (uint32_t x = 0; x < width; x++) {
                    const uint8_t b = p[TileAddress(x, y, 8, 4, 8, width)];
                    const uint8_t a = c4to8(b >> 4), i = c4to8(b & 0x0F);
                    out[y * width + x] = {i, i, i, a};
                }
            return;
        case kIA8:
            for (uint32_t y = 0; y < height; y++)
                for (uint32_t x = 0; x < width; x++) {
                    const uint32_t off = TileAddress(x, y, 4, 4, 16, width);
                    const uint8_t a = p[off], i = p[off + 1];
                    out[y * width + x] = {i, i, i, a};
                }
            return;
        case kRGB565:
            for (uint32_t y = 0; y < height; y++)
                for (uint32_t x = 0; x < width; x++) {
                    UnpackRgb565(BE16(p + TileAddress(x, y, 4, 4, 16, width)), px);
                    out[y * width + x] = {px[0], px[1], px[2], px[3]};
                }
            return;
        case kRGB5A3:
            for (uint32_t y = 0; y < height; y++)
                for (uint32_t x = 0; x < width; x++) {
                    UnpackRgb5a3(BE16(p + TileAddress(x, y, 4, 4, 16, width)), px);
                    out[y * width + x] = {px[0], px[1], px[2], px[3]};
                }
            return;
        case kRGBA8: {
            const uint32_t tiles_x = (width + 3) / 4;
            for (uint32_t y = 0; y < height; y++)
                for (uint32_t x = 0; x < width; x++) {
                    const uint32_t tile_offset = ((y / 4) * tiles_x + x / 4) * 64;
                    const uint32_t in_tile = ((y % 4) * 4 + x % 4) * 2;
                    const uint16_t ar = BE16(p + tile_offset + in_tile);
                    const uint16_t gb = BE16(p + tile_offset + 32 + in_tile);
                    out[y * width + x] = {(uint8_t)(ar & 0xFF), (uint8_t)(gb >> 8), (uint8_t)(gb & 0xFF), (uint8_t)(ar >> 8)};
                }
            return;
        }
        case kCMPR: {
            const uint32_t tiles_x = (width + 7) / 8;
            for (uint32_t y = 0; y < height; y += 8)
                for (uint32_t x = 0; x < width; x += 8) {
                    const uint32_t tile_offset = ((y / 8) * tiles_x + x / 8) * 32;
                    for (int by = 0; by < 2; by++)
                        for (int bx = 0; bx < 2; bx++) {
                            const uint8_t* block = p + tile_offset + (by * 2 + bx) * 8;
                            const uint16_t c0 = BE16(block), c1 = BE16(block + 2);
                            const uint32_t indices = BE32(block + 4);
                            uint8_t pal[4][4];
                            UnpackRgb565(c0, pal[0]);
                            UnpackRgb565(c1, pal[1]);
                            if (c0 > c1) {
                                for (int k = 0; k < 3; k++) {
                                    pal[2][k] = (uint8_t)((2 * pal[0][k] + pal[1][k]) / 3);
                                    pal[3][k] = (uint8_t)((pal[0][k] + 2 * pal[1][k]) / 3);
                                }
                                pal[2][3] = pal[3][3] = 255;
                            } else {
                                for (int k = 0; k < 3; k++) pal[2][k] = (uint8_t)((pal[0][k] + pal[1][k]) / 2);
                                pal[2][3] = 255;
                                pal[3][0] = pal[2][0]; pal[3][1] = pal[2][1]; pal[3][2] = pal[2][2]; pal[3][3] = 0;
                            }
                            for (int py = 0; py < 4; py++)
                                for (int pxl = 0; pxl < 4; pxl++) {
                                    const int shift = 30 - 2 * (py * 4 + pxl);
                                    const int idx = (indices >> shift) & 3;
                                    const uint32_t out_x = x + bx * 4 + pxl, out_y = y + by * 4 + py;
                                    if (out_x < width && out_y < height)
                                        out[out_y * width + out_x] = {pal[idx][0], pal[idx][1], pal[idx][2], pal[idx][3]};
                                }
                        }
                }
            return;
        }
        default:
            return;
    }
}

// Decodes a non-paletted format from RAM. False when the fast path does not apply (disabled, paletted or unknown
// format, empty size, or the texture is not wholly inside one RAM bank); the caller then runs the original decoder.
template <class M, class C>
bool TryDecode(M& mmu, uint32_t addr, uint32_t width, uint32_t height, uint32_t format, C* out) {
    if (!FastEnabled() || !width || !height) return false;
    uint32_t needed = 0;
    switch (format) {
        case kI4:     needed = TiledBytes(width, height, 8, 8, 32); break;
        case kI8:     needed = TiledBytes(width, height, 8, 4, 32); break;
        case kIA4:    needed = TiledBytes(width, height, 8, 4, 32); break;
        case kIA8:
        case kRGB565:
        case kRGB5A3: needed = TiledBytes(width, height, 4, 4, 32); break;
        case kRGBA8:  needed = TiledBytes(width, height, 4, 4, 64); break;
        case kCMPR:   needed = TiledBytes(width, height, 8, 8, 32); break;
        default: return false;
    }
    const uint8_t* p = Window(mmu, addr, needed);
    if (!p) return false;
    DecodeFast(format, p, width, height, out);
    return true;
}

// DXT1 build from CMPR tiles. Succeeds only when the texture is a whole number of 8x8 tiles and every
// block is in 4-colour mode (c0 > c1). The 1-bit-alpha mode decodes its transparent index to (mid colour, alpha 0),
// which DXT1 cannot express, so such a texture keeps the decoder. Indices are re-ordered: GX stores the index of
// texel k in bits 31-2k..30-2k of a big-endian word, DXT1 stores it in bits 2k..2k+1 of a little-endian word.
// `out` receives (width/4)*(height/4) blocks of 8 bytes, row-major, as GL_COMPRESSED_RGBA_S3TC_DXT1_EXT expects.
template <class M>
bool TryDxt1(M& mmu, uint32_t addr, uint32_t width, uint32_t height, uint32_t format, std::vector<uint8_t>& out) {
    if (!CmprPassthroughEnabled() || format != kCMPR || !width || !height || (width % 8) || (height % 8)) return false;
    const uint32_t needed = (width / 8) * (height / 8) * 32;
    const uint8_t* p = Window(mmu, addr, needed);
    if (!p) return false;
    const uint32_t blocks_x = width / 4, blocks_y = height / 4;
    out.assign(size_t(blocks_x) * blocks_y * 8, 0);
    for (uint32_t ty = 0; ty < height / 8; ty++)
        for (uint32_t tx = 0; tx < width / 8; tx++)
            for (uint32_t by = 0; by < 2; by++)
                for (uint32_t bx = 0; bx < 2; bx++) {
                    const uint8_t* block = p + (ty * (width / 8) + tx) * 32 + (by * 2 + bx) * 8;
                    const uint16_t c0 = BE16(block), c1 = BE16(block + 2);
                    if (c0 <= c1) return false;
                    const uint32_t gx_indices = BE32(block + 4);
                    uint32_t dxt_indices = 0;
                    for (int k = 0; k < 16; k++)
                        dxt_indices |= ((gx_indices >> (30 - 2 * k)) & 3u) << (2 * k);
                    uint8_t* dst = out.data() + size_t((ty * 2 + by) * blocks_x + (tx * 2 + bx)) * 8;
                    dst[0] = uint8_t(c0 & 0xFF); dst[1] = uint8_t(c0 >> 8);
                    dst[2] = uint8_t(c1 & 0xFF); dst[3] = uint8_t(c1 >> 8);
                    dst[4] = uint8_t(dxt_indices & 0xFF); dst[5] = uint8_t((dxt_indices >> 8) & 0xFF);
                    dst[6] = uint8_t((dxt_indices >> 16) & 0xFF); dst[7] = uint8_t(dxt_indices >> 24);
                }
    return true;
}

}  // namespace native_texture_decode
