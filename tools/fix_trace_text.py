"""Optional bounded guest-string observations on filtered native call traces."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/core/os_hle_stubs.cpp';s=p.read_text()
needle='''            << std::dec << "\\n";
}
} '''
replacement='''            << std::dec << "\\n";
  // Optional diagnostic observer: does not change guest state or control flow.
  if (std::getenv("NWII_TRACE_TEXT")) {
    for (int r = 3; r <= 7; ++r) {
      uint32_t a = ctx.gpr[r];
      if (!((a >= 0x80000000 && a < 0x81800000) ||
            (a >= 0x90000000 && a < 0x94000000))) continue;
      std::string text; bool printable = true;
      for (unsigned i = 0; i < 256; ++i) {
        uint8_t ch = ctx.mmu.read8(a + i);
        if (!ch) break;
        if (ch == '\\n') text += "\\\\n";
        else if (ch == '\\r') text += "\\\\r";
        else if (ch == '\\t') text += "\\\\t";
        else if (ch >= 32 && ch <= 126) text += char(ch);
        else { printable = false; break; }
      }
      if (printable && text.size() >= 3)
        std::cout << "[GuestText] r" << r << " = " << text << std::endl;
    }
  }
}
} '''
if needle in s:s=s.replace(needle,replacement)
elif '[GuestText]' not in s:raise SystemExit('Trace observer patch context changed')
if p.read_text()!=s:p.write_text(s)
print('Optional guest-text observer staged; guest state is unchanged.')
