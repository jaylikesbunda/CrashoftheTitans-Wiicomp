"""Opt-in GX FIFO desync diagnostic (NWII_GX_DESYNC_PROBE=1). Read-only; parsing is unchanged.

Keeps the last consumed bytes across drains and a ring of recent draw headers (cmd, vertex count, computed vertex size,
VAT summary). When a large unparsed remainder stays unchanged for several drains it prints the consumed tail, the
remainder head and the recent draw headers, to show which command ended just before the desync."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
BS = chr(92)


def patch(path, edits, marker):
    s = path.read_bytes().decode('utf-8')
    nl = '\r\n' if '\r\n' in s else '\n'
    if marker in s:
        return 'already staged'
    for old, new in edits:
        if old not in s.replace('\r\n', '\n'):
            raise SystemExit(f'{path.name}: context changed: {old[:50]!r}')
        s = s.replace('\r\n', '\n').replace(old, new)
    path.write_bytes(s.replace('\n', nl).encode('utf-8'))
    return 'staged'


# ---- fifo_parser.cpp: ring of recent draws
parser = PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp'
ring_decl = '''struct DrawLogEntry { uint8_t cmd; uint16_t count; uint32_t vsize; uint8_t vat_idx; int pos_mask, pos_type, pos_elems, nrm_mask, nrm_elems, clr0_mask, tex0_mask; size_t have; };
DrawLogEntry g_draw_ring[32];
uint64_t g_draw_ring_n = 0;
static void ParseStream(const std::vector<uint8_t>& fifo, size_t& offset, std::vector<GXCommand>& commands, int depth);'''
ring_use = '''            size_t need = 3 + (size_t)vtx_count * vsize;
            g_draw_ring[g_draw_ring_n++ % 32] = DrawLogEntry{cmd, vtx_count, (uint32_t)vsize, vat_idx, (int)vat.posMask, (int)vat.posType,
                (int)vat.posElements, (int)vat.nrmMask, (int)vat.nrmElements, (int)vat.clrMask[0], (int)vat.texMask[0], fifo_size - offset};'''
msg = patch(parser, [
    ('static void ParseStream(const std::vector<uint8_t>& fifo, size_t& offset, std::vector<GXCommand>& commands, int depth);', ring_decl),
    ('            size_t need = 3 + (size_t)vtx_count * vsize;', ring_use),
], 'g_draw_ring')
print('fifo_parser:', msg)

# ---- gx.cpp: drain-level probe
gx = PORT / 'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp'
old = '    nwii::runtime::gx::FifoParser::Parse(s_parse_buf, commands);'
new = '''    static const bool desync_probe = std::getenv("NWII_GX_DESYNC_PROBE") != nullptr;
    std::vector<uint8_t> desync_pre;
    if (desync_probe) desync_pre = s_parse_buf;
    nwii::runtime::gx::FifoParser::Parse(s_parse_buf, commands);
    if (desync_probe) {
        static std::vector<uint8_t> hist;
        static size_t last_rem = 0;
        static int same = 0, shown = 0;
        const size_t consumed = desync_pre.size() >= s_parse_buf.size() ? desync_pre.size() - s_parse_buf.size() : 0;
        hist.insert(hist.end(), desync_pre.begin(), desync_pre.begin() + consumed);
        if (hist.size() > 1024) hist.erase(hist.begin(), hist.end() - 1024);
        if (s_parse_buf.size() >= 4096 && s_parse_buf.size() == last_rem) ++same; else same = 0;
        last_rem = s_parse_buf.size();
        if (same == 5 && shown < 2) {
            ++shown;
            printf("[GXDESYNC] remainder=%zu consumed_this_drain=%zu@@n[GXDESYNC] consumed tail (%zu bytes):", s_parse_buf.size(), consumed, std::min<size_t>(hist.size(), 300));
            for (size_t k = 0; k < hist.size(); ++k) printf(" %02x", hist[k]);
            printf("@@n[GXDESYNC] remainder head:");
            for (size_t k = 0; k < s_parse_buf.size(); ++k) printf(" %02x", s_parse_buf[k]);
            printf("@@n");
            for (int k = 15; k >= 0; --k) {
                if (nwii::runtime::gx::g_draw_ring_n < (uint64_t)k + 1) continue;
                const auto& e = nwii::runtime::gx::g_draw_ring[(nwii::runtime::gx::g_draw_ring_n - 1 - k) % 32];
                printf("[GXDESYNC] draw-%d cmd=0x%02X count=%u vsize=%u vat=%u pos(mask=%d type=%d elems=%d) nrm(mask=%d elems=%d) clr0=%d tex0=%d have=%zu@@n",
                       k, e.cmd, e.count, e.vsize, e.vat_idx, e.pos_mask, e.pos_type, e.pos_elems, e.nrm_mask, e.nrm_elems, e.clr0_mask, e.tex0_mask, e.have);
            }
            fflush(stdout);
        }
    }'''.replace('@@n', BS + 'n')
decl = '''namespace nwii::runtime::gx {
struct DrawLogEntry { uint8_t cmd; uint16_t count; uint32_t vsize; uint8_t vat_idx; int pos_mask, pos_type, pos_elems, nrm_mask, nrm_elems, clr0_mask, tex0_mask; size_t have; };
extern DrawLogEntry g_draw_ring[32];
extern uint64_t g_draw_ring_n;
}
void ProcessGXFifo() {'''
s = gx.read_bytes().decode('utf-8')
if 'NWII_GX_DESYNC_PROBE' in s and 'g_draw_ring' in s:
    print('gx.cpp: already staged')
else:
    nl = '\r\n' if '\r\n' in s else '\n'
    t = s.replace('\r\n', '\n')
    # replace earlier (ring-less) probe block if present
    if 'NWII_GX_DESYNC_PROBE' in t:
        a = t.index('    static const bool desync_probe')
        b = t.index('    auto t1 = std::chrono::steady_clock::now();', a)
        t = t[:a] + old + '\n' + t[b:]
    if old not in t or 'void ProcessGXFifo() {' not in t:
        raise SystemExit('gx.cpp context changed')
    t = t.replace(old, new, 1).replace('void ProcessGXFifo() {', decl, 1)
    gx.write_bytes(t.replace('\n', nl).encode('utf-8'))
    print('gx.cpp: staged')
