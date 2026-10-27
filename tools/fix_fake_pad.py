"""Development-only stand-in for an SDL controller (NWII_FAKE_PAD=<file>).

The runtime has two input paths: scripted live input (NWII_WPAD_LIVE), which
starts connected and bypasses the physical controller code, and the physical
path used with a real gamepad (connect state, buttons and stick from SDL).
Scripted runs therefore cannot reproduce problems that depend on the physical
path. With NWII_FAKE_PAD set, the physical path is fed from a text file:
    <connected 0/1> <buttons hex> <stick_x> <stick_y>
so a real-controller session can be reproduced without hardware.
"""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
path=port/'build/nwii-full-source/nWiiRuntime/src/input/input_manager.cpp'
text=path.read_text()
marker='NATIVE_FAKE_PAD_V1'
if marker not in text:
    for inc in ('<fstream>','<chrono>','<cstdlib>'):
        if inc not in text:
            text=text.replace('#include <iostream>','#include <iostream>\n#include '+inc,1)
    old='''    for (auto& source : sources) {
        source->update(gc_pads, wii_motes);
    }'''
    new='''    for (auto& source : sources) {
        source->update(gc_pads, wii_motes);
    }
    // NATIVE_FAKE_PAD_V1
    {
        static const char* fake = std::getenv("NWII_FAKE_PAD");
        if (fake && *fake) {
            static auto last = std::chrono::steady_clock::time_point{};
            static int fc = 0, fx = 0, fy = 0; static unsigned fb = 0;
            const auto now = std::chrono::steady_clock::now();
            if (now - last > std::chrono::milliseconds(40)) {
                last = now;
                std::ifstream f(fake); int c = 0, x = 0, y = 0; unsigned b = 0;
                if (f >> c >> std::hex >> b >> std::dec >> x >> y) { fc = c; fb = b; fx = x; fy = y; }
            }
            wii_motes[0].controller_connected = (fc != 0);
            wii_motes[0].buttons = fb;
            wii_motes[0].nunchuk_x = (int8_t)fx; wii_motes[0].nunchuk_y = (int8_t)fy;
            wii_motes[0].err = 0;
        }
    }'''
    assert text.count(old)==1
    text=text.replace(old,new,1)
    path.write_text(text)
print('Fake physical pad available via NWII_FAKE_PAD=<file>.')
