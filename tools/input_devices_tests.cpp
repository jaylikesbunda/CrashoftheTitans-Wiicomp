#define SDL_MAIN_HANDLED
#include "gamepad_source.h"
#include "runtime/native_prompt_device.h"
#include <SDL.h>
#include <cassert>
#include <iostream>
#include <fstream>
#include <filesystem>
using namespace nwii::runtime::input;
// Pinned SDL's internal event entry points avoid changing the desktop's focus or input.
extern "C" int SDL_SendKeyboardKey(Uint8,SDL_Scancode);
extern "C" int SDL_SendMouseButton(SDL_Window*,Uint32,Uint8,Uint8);
extern "C" void SDL_SetKeyboardFocus(SDL_Window*);
static GamepadSource source;
static WiimoteState motes[4];
static void update() {
    GameCubePadState pads[4]{};
    for(auto& m:motes){m=WiimoteState{};m.controller_connected=false;m.err=-1;}
    for(auto& p:pads)p.err=-1;
    source.update(pads,motes);
    assert(motes[0].controller_connected && motes[0].err==0 && pads[0].err==0);
    assert(!motes[1].controller_connected && motes[1].err==-1);
}
static void poll(int milliseconds) {
    const auto end=SDL_GetTicks64()+milliseconds;
    do {SDL_PumpEvents();update();SDL_Delay(8);}while(SDL_GetTicks64()<end);
}
static void keyboard() {
    SDL_SendKeyboardKey(SDL_PRESSED,SDL_SCANCODE_J);poll(300);
    assert((motes[0].buttons&0x0800) && native_prompts::device.load()==2);
    SDL_SendKeyboardKey(SDL_RELEASED,SDL_SCANCODE_J);poll(20);
    assert(!(motes[0].buttons&0x0800));
}
static void controller(Uint16 vendor,Uint16 product,unsigned family) {
    SDL_VirtualJoystickDesc desc{};
    desc.version=SDL_VIRTUAL_JOYSTICK_DESC_VERSION;desc.type=SDL_JOYSTICK_TYPE_GAMECONTROLLER;
    desc.naxes=6;desc.nbuttons=21;desc.vendor_id=vendor;desc.product_id=product;
    desc.name=family==0?"Xbox One Controller":"PS4 Controller";
    desc.button_mask=(1u<<21)-1;desc.axis_mask=(1u<<6)-1;
    const int index=SDL_JoystickAttachVirtualEx(&desc);assert(index>=0);
    auto* joystick=SDL_JoystickOpen(index);assert(joystick);poll(300);
    assert(native_prompts::device.load()==family); // Connecting alone refreshes glyphs.
    assert(SDL_JoystickSetVirtualButton(joystick,0,1)==0);poll(300);
    assert(native_prompts::device.load()==family && (motes[0].buttons&0x0800));
    assert(SDL_JoystickSetVirtualButton(joystick,0,0)==0);poll(20);
    keyboard(); // Also switch to KBM while a controller remains connected.
    const auto policy=std::filesystem::path(std::getenv("NWII_INPUT_MAP_DIR"))/"prompt-mode.txt";
    {std::ofstream file(policy);file<<"2 auto\n";}poll(300);assert(native_prompts::device.load()==family);
    {std::ofstream file(policy);file<<"6 auto\n";}poll(300);assert(native_prompts::device.load()==2);
    assert(SDL_JoystickSetVirtualButton(joystick,0,1)==0);poll(300);
    assert(native_prompts::device.load()==family);
    SDL_JoystickClose(joystick);assert(SDL_JoystickDetachVirtual(index)==0);poll(300);
    assert(native_prompts::device.load()==2); // Unplug refreshes without a keyboard event.
    assert(!(motes[0].buttons&0x0800));
    keyboard(); // A physical disconnect must not disconnect the guest remote/Nunchuk.
}
int main() {
    SDL_SetMainReady();assert(SDL_Init(SDL_INIT_VIDEO|SDL_INIT_GAMECONTROLLER)==0);
    auto* window=SDL_CreateWindow("Input device regression check",0,0,64,64,SDL_WINDOW_HIDDEN);assert(window);
    SDL_StopTextInput();SDL_SetKeyboardFocus(window);poll(40);assert(SDL_GetKeyboardFocus());
    keyboard();
    controller(0x045e,0x02ea,0);controller(0x054c,0x05c4,1);
    SDL_SendMouseButton(nullptr,0,SDL_PRESSED,SDL_BUTTON_RIGHT);update();assert(motes[0].buttons&0x0400);
    SDL_SendMouseButton(nullptr,0,SDL_RELEASED,SDL_BUTTON_RIGHT);update();assert(!(motes[0].buttons&0x0400));
    std::cout<<"Input device checks passed: KBM, Xbox, PS, disconnect/reconnect and guest connection continuity."<<std::endl;
    SDL_DestroyWindow(window);
}
