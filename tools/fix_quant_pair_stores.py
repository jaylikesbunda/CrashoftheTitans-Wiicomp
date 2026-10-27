"""Pack quantized integer pairs only on unwatched, same-page ordinary RAM."""
from pathlib import Path
import argparse

MARKER = 'NATIVE_QUANT_PAIR_STORES_V1'
FLAG = '''// NATIVE_QUANT_PAIR_STORES_V1: process launch rollback, one shared initializer.
inline const bool native_quant_pair_enabled = std::getenv("NWII_NO_QUANT_PAIR") == nullptr;
'''
FAST = '''      // NATIVE_QUANT_PAIR_STORES_V1: no observer between these ordinary RAM stores.
      if (!W && native_quant_pair_enabled) {
        const uint32_t physical = addr & 0x3fffffffu;
        const unsigned bytes = (st_type == 4 || st_type == 6) ? 2u : 4u;
        const intptr_t bias = mmu.page_bias[physical >> 16];
        if (bias > 1 && (physical & 65535u) <= 65536u - bytes &&
            !native_watch_span_may_hit(physical, bytes)) {
          auto bits_for = [&](double value) -> uint32_t {
            switch (st_type) {
            case 4: return uint8_t(value * scale);
            case 5: return uint16_t(value * scale);
            case 6: return uint8_t(int8_t(value * scale));
            default: return uint16_t(int16_t(value * scale));
            }
          };
          const uint32_t first = bits_for(fval), second = bits_for(pval);
          uint8_t* destination = reinterpret_cast<uint8_t*>(bias + physical);
          if (bytes == 2) {
            const uint16_t packet = __builtin_bswap16(uint16_t((first << 8) | second));
            std::memcpy(destination, &packet, 2);
          } else {
            const uint32_t packet = __builtin_bswap32((first << 16) | second);
            std::memcpy(destination, &packet, 4);
          }
          return;
        }
      }
'''

def apply(source):
    if MARKER in source:
        assert source.count(FLAG) == 1 and source.count(FAST) == 1
        return source
    function = source.index('  __attribute__((always_inline)) inline void psq_store_val(')
    end = source.index('\n  }\n', function)+5
    body = source[function:end]
    anchor = '      auto store_element = [&](uint32_t a, double val) {'
    assert body.count(anchor) == 1
    assert '    if (st_type >= 4) {' in body
    assert 'float scale; std::memcpy(&scale, &scale_bits, sizeof(scale));' in body
    body = body.replace(anchor, FAST+anchor, 1)
    source = source[:function]+body+source[end:]
    namespace = 'namespace runtime {\n'
    assert source.count(namespace) == 1
    return source.replace(namespace, namespace+'\n'+FLAG, 1)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--header', type=Path, default=Path(__file__).resolve().parents[1]/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h')
    args = parser.parse_args()
    source = args.header.read_text()
    changed = apply(source)
    assert apply(changed) == changed
    if changed != source:
        args.header.write_text(changed)
    print('Quantized pair stores packed on proven ordinary RAM; rollback NWII_NO_QUANT_PAIR=1.')
