"""CPU-owned periodic IPC snapshot and atomic VI/PE event counters; no guest writes."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
SRC=PORT/'build/nwii-full-source/nWiiRuntime/src'
def change(relative, needle, replacement, marker):
    p=SRC/relative;s=p.read_text()
    if marker in s:return
    if s.count(needle)!=1:raise SystemExit(f'wait observer context changed: {relative}')
    p.write_text(s.replace(needle,replacement,1))
change('hw/hw_vi.cpp','void vi_trigger_interrupt() {',
       'std::atomic<uint64_t> native_vi_events{0}; // NATIVE_WAIT_VI\nvoid vi_trigger_interrupt() {\n  ++native_vi_events;', 'NATIVE_WAIT_VI')
change('hw/hw_pe.cpp','void pe_signal_token(uint32_t token, bool raise_interrupt) {',
       'std::atomic<uint64_t> native_pe_tokens{0}, native_pe_finishes{0}; // NATIVE_WAIT_PE\nvoid pe_signal_token(uint32_t token, bool raise_interrupt) {\n    ++native_pe_tokens;', 'NATIVE_WAIT_PE')
change('hw/hw_pe.cpp','void pe_signal_finish() {',
       'void pe_signal_finish() {\n    ++native_pe_finishes; // NATIVE_WAIT_FINISH', 'NATIVE_WAIT_FINISH')
change('hw/hw_ipc.cpp','static std::deque<uint32_t> ipc_replies;',
       '''static std::deque<uint32_t> ipc_replies;
// NATIVE_WAIT_IPC: called only from the CPU thread, which owns this state.
void native_wait_ipc_snapshot() {
  std::cout << "[WaitIPC] ctrl=0x" << std::hex << ipc_ppc_ctrl
            << " sent=0x" << ipc_ppc_msg << " reply=0x" << ipc_arm_msg
            << std::dec << " queued=" << ipc_replies.size() << std::endl;
}''', 'NATIVE_WAIT_IPC')
p=SRC/'hle/ios.cpp';s=p.read_text()
marker='// NATIVE_WAIT_DECLARATIONS'
if marker not in s:
    s='''// NATIVE_WAIT_DECLARATIONS
namespace nwii::runtime::hw {
extern std::atomic<uint64_t> native_vi_events, native_pe_tokens, native_pe_finishes;
void native_wait_ipc_snapshot();
}
'''+s
    s='#include <atomic>\n'+s
    p.write_text(s)
print('Read-only wait telemetry staged.')
