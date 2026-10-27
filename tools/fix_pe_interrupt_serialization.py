"""Serialize PE pending/enable transitions and their PI line updates."""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
path=port/'build/nwii-full-source/nWiiRuntime/src/hw/hw_pe.cpp'
source=path.read_text()
marker='// NATIVE_PE_TRANSACTION_V1'
if marker not in source:
    source=source.replace('#include <atomic>', '#include <atomic>\n#include <mutex>',1)
    needle='static std::atomic<uint32_t> g_pe_ctrl = 0;'
    assert source.count(needle)==1
    source=source.replace(needle, marker+'\n// CPU acknowledgement and GPU signals must update pending state and PI lines\n// as one transaction; atomics on individual fields cannot prevent lost lines.\nstatic std::mutex g_pe_transaction_mutex;\n'+needle,1)
    needles=[
        '            if (addr == 0xCC00100A) {',
        'void pe_signal_token(uint32_t token, bool raise_interrupt) {',
        'void pe_signal_finish() {',
    ]
    for needle in needles:
        assert source.count(needle)==1
        source=source.replace(needle, needle+'\n    std::lock_guard<std::mutex> transaction(g_pe_transaction_mutex);',1)
    path.write_text(source)
print('PE state and PI interrupt transitions serialized across CPU/GPU host threads.')
