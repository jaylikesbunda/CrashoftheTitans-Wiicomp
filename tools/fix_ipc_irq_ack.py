"""Honor the guest's PPC IPC IRQ-flag acknowledgment (W1C, bit 30)."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hw/hw_ipc.cpp';s=p.read_text()
needle='            case 0x00000C: {\n'
replacement='''            // IPC_IRQ_ACK_V1: PPC_IRQFLAG is write-one-to-clear.
            // Enabled pending Y1/Y2 reassert the level after acknowledgment.
            case 0x000030: {
                if (val & 0x40000000u) {
                    clear_pi_interrupt(0x00004000);
                    if ((ipc_ppc_ctrl & 0x14) == 0x14 ||
                        (ipc_ppc_ctrl & 0x22) == 0x22)
                        g_ipc_interrupt_delay = 50;
                    else
                        g_ipc_interrupt_delay = 0;
                }
                break;
            }
'''
if 'IPC_IRQ_ACK_V1' not in s:
    if needle not in s:raise SystemExit('IPC IRQ acknowledgment context changed')
    p.write_text(s.replace(needle,replacement+needle,1))
print('PPC IPC IRQ acknowledgment staged.')
