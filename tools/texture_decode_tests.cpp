// Differential test for runtime/native_texture_decode.h (NATIVE_TEXTURE_DECODE_FAST_V1). No game, no GL.
//
// oracle_*  : the per-pixel decoders of src/hle/texture_cache_gl.cpp, copied verbatim with the MMU as the only input
//             (ctx.mmu.readN replaced by mmu.readN). They are the reference the fast path must match byte for byte.
// The fast path is compared on random memory, for every non-paletted format, many sizes (non-multiples of the tile,
// 1x1 mip-like sizes), both RAM banks, and on addresses that straddle the end of a bank (the fast path must decline).
// CMPR: the DXT1 blocks from TryDxt1 are decompressed with an independent S3TC decoder and must reproduce the oracle.
//
// Build & run:  g++ -std=c++17 -O2 -I runtime-overlays texture_decode_tests.cpp -o texture_decode_tests && ./texture_decode_tests
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>
#include "native_texture_decode.h"

namespace {

struct Color { uint8_t r, g, b, a; bool operator==(const Color& o) const { return r == o.r && g == o.g && b == o.b && a == o.a; } };

struct FakeMMU {
    static constexpr uint32_t kMem1Limit = 1u << 20, kMem2Limit = 1u << 20;
    std::vector<uint8_t> mem1, mem2;
    uint8_t* bank(uint32_t a) { return (a & 0x10000000u) ? mem2.data() : mem1.data(); }
    const uint8_t* get_ptr(uint32_t a) const {
        const uint32_t p = a & 0x3fffffffu;
        return (p >= 0x10000000u) ? mem2.data() + (p - 0x10000000u) : mem1.data() + p;
    }
    uint8_t read8(uint32_t a) const { return get_ptr(a)[0]; }
    uint16_t read16(uint32_t a) const { const uint8_t* p = get_ptr(a); return uint16_t((p[0] << 8) | p[1]); }
    uint32_t read32(uint32_t a) const { const uint8_t* p = get_ptr(a); return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3]; }
};

// ---- oracle: verbatim from texture_cache_gl.cpp -----------------------------------------------------------------
static uint32_t oracle_get_tile_address(uint32_t x, uint32_t y, uint32_t bw, uint32_t bh, uint32_t bpp, uint32_t width) {
    uint32_t tiles_x = (width + bw - 1) / bw;
    uint32_t tx = x / bw;
    uint32_t ty = y / bh;
    uint32_t in_tile_x = x % bw;
    uint32_t in_tile_y = y % bh;
    uint32_t tile_idx = ty * tiles_x + tx;
    uint32_t tile_offset = tile_idx * bw * bh * bpp / 8;
    uint32_t in_tile_offset = (in_tile_y * bw + in_tile_x) * bpp / 8;
    return tile_offset + in_tile_offset;
}
static inline uint8_t c5to8(uint32_t v) { return (uint8_t)((v << 3) | (v >> 2)); }
static inline uint8_t c6to8(uint32_t v) { return (uint8_t)((v << 2) | (v >> 4)); }
static inline uint8_t c4to8(uint32_t v) { return (uint8_t)((v << 4) | v); }
static inline uint8_t c3to8(uint32_t v) { return (uint8_t)((v << 5) | (v << 2) | (v >> 1)); }
static inline Color unpack_rgb565(uint16_t c) { return { c5to8((c >> 11) & 0x1F), c6to8((c >> 5) & 0x3F), c5to8(c & 0x1F), 255 }; }
static inline Color unpack_rgb5a3(uint16_t c) {
    if (c & 0x8000) return { c5to8((c >> 10) & 0x1F), c5to8((c >> 5) & 0x1F), c5to8(c & 0x1F), 255 };
    return { c4to8((c >> 8) & 0xF), c4to8((c >> 4) & 0xF), c4to8(c & 0xF), c3to8((c >> 12) & 0x7) };
}
static void oracle_i4(FakeMMU& m, uint32_t addr, uint32_t width, uint32_t height, Color* out) {
    for (uint32_t y = 0; y < height; y++)
        for (uint32_t x = 0; x < width; x += 2) {
            uint32_t offset = oracle_get_tile_address(x, y, 8, 8, 4, width);
            uint8_t b = m.read8(addr + offset);
            uint8_t i1 = c4to8(b >> 4), i2 = c4to8(b & 0x0F);
            out[y * width + x] = {i1, i1, i1, i1};
            if (x + 1 < width) out[y * width + x + 1] = {i2, i2, i2, i2};
        }
}
static void oracle_i8(FakeMMU& m, uint32_t addr, uint32_t width, uint32_t height, Color* out) {
    for (uint32_t y = 0; y < height; y++)
        for (uint32_t x = 0; x < width; x++) {
            uint8_t i = m.read8(addr + oracle_get_tile_address(x, y, 8, 4, 8, width));
            out[y * width + x] = {i, i, i, i};
        }
}
static void oracle_ia4(FakeMMU& m, uint32_t addr, uint32_t width, uint32_t height, Color* out) {
    for (uint32_t y = 0; y < height; y++)
        for (uint32_t x = 0; x < width; x++) {
            uint8_t b = m.read8(addr + oracle_get_tile_address(x, y, 8, 4, 8, width));
            uint8_t a = c4to8(b >> 4), i = c4to8(b & 0x0F);
            out[y * width + x] = {i, i, i, a};
        }
}
static void oracle_ia8(FakeMMU& m, uint32_t addr, uint32_t width, uint32_t height, Color* out) {
    for (uint32_t y = 0; y < height; y++)
        for (uint32_t x = 0; x < width; x++) {
            uint32_t offset = oracle_get_tile_address(x, y, 4, 4, 16, width);
            uint8_t a = m.read8(addr + offset), i = m.read8(addr + offset + 1);
            out[y * width + x] = {i, i, i, a};
        }
}
static void oracle_rgb565(FakeMMU& m, uint32_t addr, uint32_t width, uint32_t height, Color* out) {
    for (uint32_t y = 0; y < height; y++)
        for (uint32_t x = 0; x < width; x++)
            out[y * width + x] = unpack_rgb565(m.read16(addr + oracle_get_tile_address(x, y, 4, 4, 16, width)));
}
static void oracle_rgb5a3(FakeMMU& m, uint32_t addr, uint32_t width, uint32_t height, Color* out) {
    for (uint32_t y = 0; y < height; y++)
        for (uint32_t x = 0; x < width; x++)
            out[y * width + x] = unpack_rgb5a3(m.read16(addr + oracle_get_tile_address(x, y, 4, 4, 16, width)));
}
static void oracle_rgba8(FakeMMU& m, uint32_t addr, uint32_t width, uint32_t height, Color* out) {
    uint32_t tiles_x = (width + 3) / 4;
    for (uint32_t y = 0; y < height; y++)
        for (uint32_t x = 0; x < width; x++) {
            uint32_t tile_offset = ((y / 4) * tiles_x + x / 4) * 64;
            uint32_t in_tile_offset = ((y % 4) * 4 + x % 4) * 2;
            uint16_t ar = m.read16(addr + tile_offset + in_tile_offset);
            uint16_t gb = m.read16(addr + tile_offset + 32 + in_tile_offset);
            out[y * width + x] = {(uint8_t)(ar & 0xFF), (uint8_t)(gb >> 8), (uint8_t)(gb & 0xFF), (uint8_t)(ar >> 8)};
        }
}
static void oracle_cmpr(FakeMMU& m, uint32_t addr, uint32_t width, uint32_t height, Color* out) {
    uint32_t tiles_x = (width + 7) / 8;
    for (uint32_t y = 0; y < height; y += 8)
        for (uint32_t x = 0; x < width; x += 8) {
            uint32_t tile_offset = ((y / 8) * tiles_x + x / 8) * 32;
            for (int by = 0; by < 2; by++)
                for (int bx = 0; bx < 2; bx++) {
                    uint32_t block_offset = tile_offset + (by * 2 + bx) * 8;
                    uint16_t c0 = m.read16(addr + block_offset), c1 = m.read16(addr + block_offset + 2);
                    uint32_t indices = m.read32(addr + block_offset + 4);
                    Color pal[4];
                    pal[0] = unpack_rgb565(c0);
                    pal[1] = unpack_rgb565(c1);
                    if (c0 > c1) {
                        pal[2] = { (uint8_t)((2*pal[0].r + pal[1].r)/3), (uint8_t)((2*pal[0].g + pal[1].g)/3), (uint8_t)((2*pal[0].b + pal[1].b)/3), 255 };
                        pal[3] = { (uint8_t)((pal[0].r + 2*pal[1].r)/3), (uint8_t)((pal[0].g + 2*pal[1].g)/3), (uint8_t)((pal[0].b + 2*pal[1].b)/3), 255 };
                    } else {
                        pal[2] = { (uint8_t)((pal[0].r + pal[1].r)/2), (uint8_t)((pal[0].g + pal[1].g)/2), (uint8_t)((pal[0].b + pal[1].b)/2), 255 };
                        pal[3] = { pal[2].r, pal[2].g, pal[2].b, 0 };
                    }
                    for (int py = 0; py < 4; py++)
                        for (int px = 0; px < 4; px++) {
                            int shift = 30 - 2 * (py * 4 + px);
                            int idx = (indices >> shift) & 3;
                            uint32_t out_x = x + bx * 4 + px, out_y = y + by * 4 + py;
                            if (out_x < width && out_y < height) out[out_y * width + out_x] = pal[idx];
                        }
                }
        }
}
static void oracle(uint32_t format, FakeMMU& m, uint32_t addr, uint32_t w, uint32_t h, Color* out) {
    switch (format) {
        case 0: oracle_i4(m, addr, w, h, out); break;
        case 1: oracle_i8(m, addr, w, h, out); break;
        case 2: oracle_ia4(m, addr, w, h, out); break;
        case 3: oracle_ia8(m, addr, w, h, out); break;
        case 4: oracle_rgb565(m, addr, w, h, out); break;
        case 5: oracle_rgb5a3(m, addr, w, h, out); break;
        case 6: oracle_rgba8(m, addr, w, h, out); break;
        case 14: oracle_cmpr(m, addr, w, h, out); break;
    }
}

// ---- independent S3TC (DXT1) reference decoder ------------------------------------------------------------------
static Color dxt_expand565(uint16_t c) {
    uint32_t r = (c >> 11) & 31, g = (c >> 5) & 63, b = c & 31;
    return { uint8_t((r << 3) | (r >> 2)), uint8_t((g << 2) | (g >> 4)), uint8_t((b << 3) | (b >> 2)), 255 };
}
static std::vector<Color> dxt1_decode(const std::vector<uint8_t>& blocks, uint32_t w, uint32_t h) {
    std::vector<Color> img(size_t(w) * h, Color{0, 0, 0, 0});
    const uint32_t bw = w / 4;
    for (uint32_t by = 0; by < h / 4; by++)
        for (uint32_t bx = 0; bx < bw; bx++) {
            const uint8_t* d = &blocks[(size_t(by) * bw + bx) * 8];
            const uint16_t c0 = uint16_t(d[0] | (d[1] << 8)), c1 = uint16_t(d[2] | (d[3] << 8));
            const uint32_t bits = uint32_t(d[4]) | (uint32_t(d[5]) << 8) | (uint32_t(d[6]) << 16) | (uint32_t(d[7]) << 24);
            Color pal[4];
            pal[0] = dxt_expand565(c0);
            pal[1] = dxt_expand565(c1);
            // 4-colour mode only (the caller guarantees c0 > c1)
            pal[2] = { uint8_t((2 * pal[0].r + pal[1].r) / 3), uint8_t((2 * pal[0].g + pal[1].g) / 3), uint8_t((2 * pal[0].b + pal[1].b) / 3), 255 };
            pal[3] = { uint8_t((pal[0].r + 2 * pal[1].r) / 3), uint8_t((pal[0].g + 2 * pal[1].g) / 3), uint8_t((pal[0].b + 2 * pal[1].b) / 3), 255 };
            for (uint32_t py = 0; py < 4; py++)
                for (uint32_t px = 0; px < 4; px++) {
                    const uint32_t k = py * 4 + px;
                    img[size_t(by * 4 + py) * w + bx * 4 + px] = pal[(bits >> (2 * k)) & 3];
                }
        }
    return img;
}

int failures = 0, fast_taken = 0, declined = 0, checks = 0;

void expect(bool ok, const char* what, uint32_t format, uint32_t w, uint32_t h, uint32_t addr) {
    ++checks;
    if (!ok) {
        ++failures;
        if (failures <= 20) std::printf("FAIL %s format=%u %ux%u addr=0x%08x\n", what, format, w, h, addr);
    }
}

}  // namespace

int main() {
    std::mt19937 rng(20261023);
    FakeMMU mmu;
    // Twice the bank size, so the oracle may read past a bank end while the fast path must decline there.
    mmu.mem1.resize(2 * FakeMMU::kMem1Limit);
    mmu.mem2.resize(2 * FakeMMU::kMem2Limit);
    for (auto& b : mmu.mem1) b = uint8_t(rng());
    for (auto& b : mmu.mem2) b = uint8_t(rng());

    const uint32_t formats[] = {0, 1, 2, 3, 4, 5, 6, 14};
    const uint32_t sizes[] = {1, 2, 3, 4, 5, 7, 8, 9, 12, 15, 16, 17, 31, 32, 33, 40, 64, 100};
    const uint32_t bases[] = {0x00000000u, 0x00001000u, 0x00012340u, 0x10000000u, 0x10004000u, 0x80000000u + 0x40u};

    for (uint32_t f : formats)
        for (uint32_t w : sizes)
            for (uint32_t h : sizes) {
                for (uint32_t base : bases) {
                    std::vector<Color> want(size_t(w) * h), got(size_t(w) * h, Color{1, 2, 3, 4});
                    oracle(f, mmu, base, w, h, want.data());
                    const bool took = native_texture_decode::TryDecode(mmu, base, w, h, f, got.data());
                    if (took) {
                        ++fast_taken;
                        expect(std::memcmp(want.data(), got.data(), want.size() * sizeof(Color)) == 0, "decode mismatch", f, w, h, base);
                    } else {
                        ++declined;
                    }
                }
                // A texture that runs past the end of MEM1 must be declined, never read through the pointer.
                std::vector<Color> sink(size_t(w) * h);
                const uint32_t edge = FakeMMU::kMem1Limit - 16;
                expect(!native_texture_decode::TryDecode(mmu, edge, w, h, f, sink.data()), "edge not declined", f, w, h, edge);
            }

    // Random texture data at random addresses, every format.
    for (int trial = 0; trial < 4000; trial++) {
        const uint32_t f = formats[rng() % 8];
        const uint32_t w = 1 + rng() % 96, h = 1 + rng() % 96;
        const uint32_t base = (rng() % 2 ? 0u : 0x10000000u) + (rng() % 0x80000u) * 2;
        std::vector<Color> want(size_t(w) * h), got(size_t(w) * h);
        oracle(f, mmu, base, w, h, want.data());
        if (native_texture_decode::TryDecode(mmu, base, w, h, f, got.data())) {
            ++fast_taken;
            expect(std::memcmp(want.data(), got.data(), want.size() * sizeof(Color)) == 0, "random decode mismatch", f, w, h, base);
        } else {
            ++declined;
        }
    }

    // CMPR -> DXT1 (4-colour blocks only): the decompressed blocks must reproduce the oracle exactly.
    int dxt_taken = 0;
    for (uint32_t w = 8; w <= 72; w += 8)
        for (uint32_t h = 8; h <= 72; h += 8)
            for (int trial = 0; trial < 3; trial++) {
                const uint32_t base = 0x00020000u + (rng() % 0x1000u) * 4;
                const uint32_t tiles = (w / 8) * (h / 8);
                for (uint32_t t = 0; t < tiles; t++)
                    for (uint32_t s = 0; s < 4; s++) {  // force 4-colour mode: c0 > c1
                        const uint32_t off = base + t * 32 + s * 8;
                        uint16_t c1 = uint16_t(rng() % 65535), c0 = uint16_t(c1 + 1 + rng() % (65535 - c1));
                        for (int k = 0; k < 2; k++) mmu.mem1[off + k] = uint8_t(c0 >> (8 * (1 - k)));
                        for (int k = 0; k < 2; k++) mmu.mem1[off + 2 + k] = uint8_t(c1 >> (8 * (1 - k)));
                    }
                std::vector<Color> want(size_t(w) * h);
                oracle(14, mmu, base, w, h, want.data());
                std::vector<uint8_t> dxt;
                const bool took = native_texture_decode::TryDxt1(mmu, base, w, h, 14, dxt);
                expect(took, "TryDxt1 refused 4-colour CMPR", 14, w, h, base);
                if (!took) continue;
                ++dxt_taken;
                const std::vector<Color> ref = dxt1_decode(dxt, w, h);
                expect(std::memcmp(want.data(), ref.data(), want.size() * sizeof(Color)) == 0, "DXT1 output differs from CMPR decode", 14, w, h, base);
            }
    // A block in 1-bit-alpha mode (c0 <= c1) must make TryDxt1 decline, and so must sizes that are not whole 8x8 tiles.
    {
        const uint32_t base = 0x00030000u;
        for (uint32_t off = 0; off < 32; off++) mmu.mem1[base + off] = 0;  // first block: c0 == c1 == 0
        std::vector<uint8_t> dxt;
        expect(!native_texture_decode::TryDxt1(mmu, base, 8, 8, 14, dxt), "TryDxt1 accepted a 1-bit-alpha block", 14, 8, 8, base);
        expect(!native_texture_decode::TryDxt1(mmu, base, 12, 8, 14, dxt), "TryDxt1 accepted width 12", 14, 12, 8, base);
        expect(!native_texture_decode::TryDxt1(mmu, base, 8, 8, 6, dxt), "TryDxt1 accepted RGBA8", 6, 8, 8, base);
    }

    std::printf("texture decode: %d checks, fast path taken %d, declined %d, CMPR->DXT1 %d, failures %d\n",
                checks, fast_taken, declined, dxt_taken, failures);
    if (fast_taken == 0 || dxt_taken == 0) { std::printf("FAIL no coverage\n"); return 1; }
    std::printf("%s\n", failures ? "TEXTURE_DECODE_TESTS FAIL" : "TEXTURE_DECODE_TESTS PASS");
    return failures ? 1 : 0;
}
