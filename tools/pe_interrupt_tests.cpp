#include <atomic>
#include <thread>
#include <future>
#include <chrono>
#include <iostream>
#include "runtime/hw/hw.h"
static std::atomic<bool> armed{false}, entered{false}, release_clear{false};
static void fixture_before_finish_clear() {
    if (!armed.exchange(false)) return;
    entered = true;
    while (!release_clear.load()) std::this_thread::yield();
}
#include "fixture_pe.cpp"
namespace nwii::runtime::hw {
std::atomic<uint32_t> pi_intsr{0}, pi_intmr{0};
}
int main() {
    using namespace nwii::runtime;
    using namespace nwii::runtime::hw;
    auto& mmio = MMIODispatcher::get(); register_pe(mmio);
    int total=0, passed=0;
    auto check = [&](bool value, const char* name) {
        ++total; passed += value; if (!value) std::cout << "FAIL " << name << '\n';
    };
    mmio.write16(0xcc00100a, 0xf);
    armed=true;
    std::thread cpu([&] { mmio.write16(0xcc00100a, 0xb); });
    while (!entered.load()) std::this_thread::yield();
    auto gpu=std::async(std::launch::async, [] { pe_signal_finish(); });
    // Old implementation finishes here; serialized implementation blocks until
    // the CPU's acknowledgement transaction has finished.
    gpu.wait_for(std::chrono::milliseconds(150));
    release_clear=true; cpu.join(); gpu.get();
    check(g_pe_finish_pending && (g_pe_ctrl & 2), "finish pending and enabled");
    check((pi_intsr.load() & 0x400)!=0, "finish line survives concurrent acknowledgement");
    mmio.write16(0xcc00100a, 0xb);
    check(!g_pe_finish_pending && !(pi_intsr.load() & 0x400), "finish acknowledgement clears line");
    mmio.write16(0xcc00100a, 0);
    pe_signal_finish();
    check(g_pe_finish_pending && !(pi_intsr.load() & 0x400), "disabled finish retains pending without line");
    mmio.write16(0xcc00100a, 2);
    check((pi_intsr.load() & 0x400)!=0, "enabling pending finish asserts line");
    pe_signal_token(0x1234, false);
    check(mmio.read16(0xcc00100e)==0x1234 && !g_pe_token_pending, "noninterrupt token");
    pe_signal_token(0x5678, true);
    check(g_pe_token_pending && !(pi_intsr.load() & 0x200), "disabled token retains pending");
    mmio.write16(0xcc00100a, 3);
    check((pi_intsr.load() & 0x600)==0x600, "enable both pending lines");
    mmio.write16(0xcc00100a, 7);
    check(!g_pe_token_pending && (pi_intsr.load() & 0x600)==0x400, "token acknowledgement preserves finish");
    std::thread writes([&] { for(int i=0;i<100000;++i) mmio.write16(0xcc00100a, i&15); });
    std::thread finishes([] { for(int i=0;i<100000;++i) pe_signal_finish(); });
    std::thread tokens([] { for(int i=0;i<100000;++i) pe_signal_token(i,true); });
    writes.join(); finishes.join(); tokens.join();
    check(bool(pi_intsr.load()&0x400)==bool(g_pe_finish_pending && (g_pe_ctrl&2)), "quiescent finish line consistency");
    check(bool(pi_intsr.load()&0x200)==bool(g_pe_token_pending && (g_pe_ctrl&1)), "quiescent token line consistency");
    std::cout << passed << '/' << total << " PE interrupt checks\n";
    return passed==total?0:1;
}
