"""Opt-in (NWII_PI_FIFO_PROBE=1): log guest writes/reads of PI FIFO registers (0xCC00300C/10/14) with PC/LR. Read-only."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
BS = chr(92)
p = PORT / 'build/nwii-full-source/nWiiRuntime/src/hw/hw_pi.cpp'
s = p.read_bytes().decode('utf-8')
nl = '\r\n' if '\r\n' in s else '\n'
t = s.replace('\r\n', '\n')
if 'NWII_PI_FIFO_PROBE' in t:
    print('already staged')
else:
    old = '            if (addr == 0xCC00300C) pi_fifo_base = val;'
    new = '''            if (addr >= 0xCC00300C && addr <= 0xCC003014 && std::getenv("NWII_PI_FIFO_PROBE")) {
                static int shown = 0;
                if (shown++ < 400)
                    std::cout << "[PIFIFO] write addr=0x" << std::hex << addr << " val=0x" << val << " pc=0x" << nwii::runtime::g_ctx_ptr->pc
                              << " lr=0x" << nwii::runtime::g_ctx_ptr->lr << std::dec << "@@n" << std::flush;
            }
            if (addr == 0xCC00300C) pi_fifo_base = val;'''.replace('@@n', BS + 'n')
    if old not in t:
        raise SystemExit('hw_pi context changed')
    if 'extern CPUContext' not in t:
        t = t.replace('namespace nwii::runtime::hw {', 'namespace nwii::runtime { extern CPUContext *g_ctx_ptr; }' + chr(10) + 'namespace nwii::runtime::hw {', 1)
    p.write_bytes(t.replace(old, new, 1).replace(chr(10), nl).encode('utf-8'))
    print('PI FIFO probe staged')
