"""Stage opt-in IPC register/guest-credit diagnostics without state changes."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hw/hw_ipc.cpp';s=p.read_text()
needle='                (void)old_ctrl;\n'
probe='''                // IPC_PROBE_V1: observe completed control-register writes.
                if (std::getenv("NWII_IPC_PROBE")) {
                    std::cout << "[IPC_CTRL] old=" << std::hex << old_ctrl
                              << " write=" << val << " new=" << ipc_ppc_ctrl
                              << " msg=" << ipc_ppc_msg << " reply=" << ipc_arm_msg;
                    if (g_ctx_ptr) {
                        auto &c = *g_ctx_ptr;
                        std::cout << " lr=" << c.lr << " credits=" << c.mmu.read32(c.gpr[13] - 0x6818)
                                  << " enqueue=" << c.mmu.read32(0x806438e4)
                                  << " sent=" << c.mmu.read32(0x806438e0);
                    }
                    std::cout << std::dec << std::endl;
                }
'''
if 'IPC_PROBE_V1' not in s:
    if needle not in s:raise SystemExit('IPC probe context changed')
    p.write_text(s.replace(needle,probe+needle,1))
print('Read-only IPC probe staged.')
