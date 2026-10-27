"""Stage an opt-in bounded, read-only PNG data/CRC observer."""
from pathlib import Path

PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRuntime/src/core/os_hle_stubs.cpp'
s = p.read_text()
marker = '  // PNG_PROBE_V1: bounded observer; no guest state mutations.\n'
probe = r'''  // PNG_PROBE_V1: bounded observer; no guest state mutations.
  if (std::getenv("NWII_PNG_PROBE")) {
    static unsigned observations = 0;
    if (observations++ < 160) {
      auto valid = [](uint32_t a, uint32_t n) {
        return (a >= 0x80000000 && uint64_t(a) + n <= 0x81800000) ||
               (a >= 0x90000000 && uint64_t(a) + n <= 0x94000000);
      };
      auto bytes = [&](const char *label, uint32_t a, uint32_t n) {
        if (!valid(a, n)) return;
        std::cout << "[PNG_BYTES] " << label << " addr=" << std::hex << a
                  << " size=" << std::dec << n << " hex=";
        const char *digits = "0123456789abcdef";
        for (uint32_t i = 0; i < n; ++i) {
          uint8_t b = ctx.mmu.read8(a + i);
          std::cout << digits[b >> 4] << digits[b & 15];
        }
        std::cout << std::endl;
      };
      uint32_t c = ctx.gpr[3];
      if (func_addr == 0x803aa930) bytes("stored_crc", c, 4);
      if (func_addr == 0x803b0294 || func_addr == 0x803a39a4)
        bytes("crc_input", ctx.gpr[4], ctx.gpr[5] < 128 ? ctx.gpr[5] : 128);
      if (func_addr == 0x803aa9a0 || func_addr == 0x803aaac8 ||
          func_addr == 0x803a39a4 || func_addr == 0x803a5e14 ||
          func_addr == 0x803a40d4) {
        if (valid(c, 0x270)) {
          std::cout << "[PNG_CTX] call=" << std::hex << func_addr
                    << " context=" << c << " crc=" << ctx.mmu.read32(c + 0x260)
                    << " flags=" << ctx.mmu.read32(c + 0x1bc)
                    << " reader=" << ctx.mmu.read32(c + 0x1a0) << std::endl;
          bytes("context", c, 0x270);
          bytes("chunk", c + 0x26c, 4);
        }
      }
    }
  }
'''
needle = '  // Optional diagnostic observer: does not change guest state or control flow.\n'
if marker not in s:
    if needle not in s:
        raise SystemExit('PNG observer insertion context changed')
    s = s.replace(needle, probe + needle, 1)
    p.write_text(s)
print('Read-only PNG observer staged; enable NWII_PNG_PROBE and filtered call tracing.')
