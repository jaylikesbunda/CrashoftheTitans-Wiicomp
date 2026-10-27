"""Co-op: four emulated Wii Remotes. Controller slot n (SDL, in connection order) drives WPAD channel n, so a second controller
plugged in before or during play is Wii Remote 2. Connect, extension and sampling callbacks carry the channel number; each channel
reports its own buttons, Nunchuk stick, accelerometers and pointer. Scripted/live command modes (NWII_WPAD_SCRIPT/LIVE) keep
driving channel 0 only.

Edits src/hle/ios.cpp after every WPAD stage (fix_wpad_emulation, fix_input_mapping, the IR pointer and phase stages). Contained and
fail-soft: all anchors are checked before anything is written; if one has moved, nothing changes, a one-line reason is printed and
the stage exits 0. Idempotent.
"""
from pathlib import Path
import sys

MARK = 'NATIVE_WPAD_COOP_V1'
EDITS = []   # (anchor, replacement), applied in order
IOS = Path(__file__).resolve().parents[1] / 'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp'


CRLF, LF = chr(13) + chr(10), chr(10)


class Skip(Exception):
    pass


def read(path):
    """Text with LF line ends, plus whether the file used CRLF (written back the same way)."""
    raw = path.read_bytes().decode('utf-8', errors='surrogateescape')
    return raw.replace(CRLF, LF), CRLF in raw


def write(path, text, crlf):
    path.write_bytes((text.replace(LF, CRLF) if crlf else text).encode('utf-8', errors='surrogateescape'))



def rep(s, old, new, count=1):
    n = s.count(old)
    if n != count:
        raise Skip('ios.cpp: expected %d occurrence(s) of %r, found %d' % (count, old.strip()[:70], n))
    return s.replace(old, new)


EDITS.append(('''struct NativeWpadChannel {
  uint32_t sampling=0, connect=0, extension=0, buffer=0, count=0, format=3;
  uint32_t index=0xFFFFFFFF;
  bool dpd=false;
};''', '''struct NativeWpadChannel {
  uint32_t sampling=0, connect=0, extension=0, buffer=0, count=0, format=3;
  uint32_t index=0xFFFFFFFF;
  bool dpd=false;
  // NATIVE_WPAD_COOP_V1: the controller state this channel reports.
  bool connected=false; uint16_t buttons=0; int8_t sx=0, sy=0;
  float ax=0,ay=0,az=1, nax=0,nay=0,naz=1;
  float ir_x=0.5f, ir_y=0.5f; bool ir_valid=false;
};'''))

# Packets read the channel's own input.
EDITS.append(('''static void native_wpad_packet(CPUContext& ctx,uint32_t p,uint32_t format,bool connected,uint16_t buttons) {''',
    '''static void native_wpad_packet(CPUContext& ctx,uint32_t p,uint32_t format,bool connected,uint16_t buttons,const NativeWpadChannel& in) {'''))
EDITS.append(('''    auto& ms=native_wpad_state();
    auto raw=[](float g)->uint16_t{int v=static_cast<int>(g*204.f+(g>=0.f?0.5f:-0.5f)); v=v<-511?-511:v>511?511:v; return static_cast<uint16_t>(static_cast<int16_t>(v));};
    ctx.mmu.write16(p+2,raw(ms.ax)); ctx.mmu.write16(p+4,raw(ms.ay)); ctx.mmu.write16(p+6,raw(ms.az));''',
    '''    const auto& ms=in;
    auto raw=[](float g)->uint16_t{int v=static_cast<int>(g*204.f+(g>=0.f?0.5f:-0.5f)); v=v<-511?-511:v>511?511:v; return static_cast<uint16_t>(static_cast<int16_t>(v));};
    ctx.mmu.write16(p+2,raw(ms.ax)); ctx.mmu.write16(p+4,raw(ms.ay)); ctx.mmu.write16(p+6,raw(ms.az));'''))
EDITS.append(('''  { auto& ir=native_wpad_state(); if (ir.ir_valid) { // NATIVE_WPAD_IR_V1''', '''  { const auto& ir=in; if (ir.ir_valid) { // NATIVE_WPAD_IR_V1'''))
EDITS.append(('''  if (format>=3 && format<6) { auto& st=native_wpad_state(); ctx.mmu.write8(''', '''  if (format>=3 && format<6) { const auto& st=in; ctx.mmu.write8('''))
EDITS.append(('''    auto& ms=native_wpad_state();
    auto raw=[](float g)->uint16_t{int v=static_cast<int>(g*204.f+(g>=0.f?0.5f:-0.5f)); v=v<-511?-511:v>511?511:v; return static_cast<uint16_t>(static_cast<int16_t>(v));};
    ctx.mmu.write16(p+0x2A,raw(ms.nax));''', '''    const auto& ms=in;
    auto raw=[](float g)->uint16_t{int v=static_cast<int>(g*204.f+(g>=0.f?0.5f:-0.5f)); v=v<-511?-511:v>511?511:v; return static_cast<uint16_t>(static_cast<int16_t>(v));};
    ctx.mmu.write16(p+0x2A,raw(ms.nax));'''))

# Physical controllers: every slot feeds its channel.
EDITS.append(('''    const auto state=nwii::runtime::input::InputManager::get().get_wiimote_state(controller_index);
    const bool connected=state.controller_connected;
    if (connected!=s.connected) {
      s.connected=connected; auto& c=s.channels[0];
      if (c.connect) ctx.queue_callback(c.connect,0,connected ? 0 : 0xFFFFFFFF);
      if (connected && c.extension) ctx.queue_callback(c.extension,0,1);
      std::cout<<"[NativeWPAD] SDL controller connected="<<connected<<"\\n";
    }
    s.buttons=connected ? static_cast<uint16_t>(state.buttons) : 0;
    s.sx=connected ? state.nunchuk_x : 0; s.sy=connected ? state.nunchuk_y : 0;
    s.ir_x=state.ir_x; s.ir_y=state.ir_y; s.ir_valid=connected; // NATIVE_WPAD_IR_V1
    s.ax=state.accel_x; s.ay=state.accel_y; s.az=state.accel_z; s.nax=state.nunchuk_ax; s.nay=state.nunchuk_ay; s.naz=state.nunchuk_az; // NATIVE_INPUT_MAP_V1
    return;''', '''    for (unsigned chan=0;chan<4;++chan) { // NATIVE_WPAD_COOP_V1: controller slot n is Wii Remote n+1
      const unsigned slot=chan==0 ? controller_index : (chan==controller_index ? 0u : chan);
      const auto state=nwii::runtime::input::InputManager::get().get_wiimote_state(slot);
      auto& c=s.channels[chan];
      const bool connected=state.controller_connected;
      if (connected!=c.connected) {
        c.connected=connected;
        if (c.connect) ctx.queue_callback(c.connect,chan,connected ? 0 : 0xFFFFFFFF);
        if (connected && c.extension) ctx.queue_callback(c.extension,chan,1);
        std::cout<<"[NativeWPAD] Wii Remote "<<chan+1<<" connected="<<connected<<"\\n";
      }
      c.buttons=connected ? static_cast<uint16_t>(state.buttons) : 0;
      c.sx=connected ? state.nunchuk_x : 0; c.sy=connected ? state.nunchuk_y : 0;
      c.ir_x=state.ir_x; c.ir_y=state.ir_y; c.ir_valid=connected; // NATIVE_WPAD_IR_V1
      c.ax=state.accel_x; c.ay=state.accel_y; c.az=state.accel_z; c.nax=state.nunchuk_ax; c.nay=state.nunchuk_ay; c.naz=state.nunchuk_az; // NATIVE_INPUT_MAP_V1
    }
    { const auto& c0=s.channels[0]; s.connected=c0.connected; s.buttons=c0.buttons; s.sx=c0.sx; s.sy=c0.sy; } // diagnostics
    return;'''))

# Scripted modes drive channel 0 through the old fields; mirror them into the channel after each advance.
EDITS.append(('''    s.buttons=e.buttons; s.sx=e.sx; s.sy=e.sy;
    std::cout<<"[NativeWPAD] ms="<<ms<<" buttons="<<std::hex<<s.buttons<<std::dec
             <<" connected="<<s.connected<<"\\n";
  }
}''', '''    s.buttons=e.buttons; s.sx=e.sx; s.sy=e.sy;
    std::cout<<"[NativeWPAD] ms="<<ms<<" buttons="<<std::hex<<s.buttons<<std::dec
             <<" connected="<<s.connected<<"\\n";
  }
  { auto& c0=s.channels[0]; c0.connected=s.connected; c0.buttons=s.buttons; c0.sx=s.sx; c0.sy=s.sy; c0.ir_valid=s.ir_valid; c0.ir_x=s.ir_x; c0.ir_y=s.ir_y; } // NATIVE_WPAD_COOP_V1
}'''))

# Sampling: every connected channel gets its packet and callback.
EDITS.append(('''  auto& c=s.channels[0];
  if (!s.connected || !c.sampling) return;
  if (c.buffer && c.count && c.count<=1024) {
    c.index=(c.index+1)%c.count;
    const uint32_t size=native_wpad_size(c.format);
    if (native_wpad_ram(c.buffer,size*c.count))
      native_wpad_packet(ctx,c.buffer+c.index*size,c.format,true,s.buttons);
  }
  ctx.queue_callback(c.sampling,0,0);''', '''  for (unsigned chan=0;chan<4;++chan) { // NATIVE_WPAD_COOP_V1
    auto& c=s.channels[chan];
    if (!c.connected || !c.sampling) continue;
    if (c.buffer && c.count && c.count<=1024) {
      c.index=(c.index+1)%c.count;
      const uint32_t size=native_wpad_size(c.format);
      if (native_wpad_ram(c.buffer,size*c.count))
        native_wpad_packet(ctx,c.buffer+c.index*size,c.format,true,c.buttons,c);
    }
    ctx.queue_callback(c.sampling,chan,0);
  }'''))

EDITS.append(('''  auto& c=s.channels[chan]; const bool connected=chan==0 && s.connected;''',
    '''  auto& c=s.channels[chan]; const bool connected=c.connected; // NATIVE_WPAD_COOP_V1'''))
EDITS.append(('''      native_wpad_packet(ctx,p,c.format,connected,s.buttons); break;''',
    '''      native_wpad_packet(ctx,p,c.format,connected,c.buttons,c); break;'''))
EDITS.append(('''        for (uint32_t i=0;i<c.count;++i) native_wpad_packet(ctx,p+i*native_wpad_size(c.format),c.format,false,0);''',
    '''        for (uint32_t i=0;i<c.count;++i) native_wpad_packet(ctx,p+i*native_wpad_size(c.format),c.format,false,0,c);'''))


def apply(s):
    for old, new in EDITS:
        s = rep(s, old, new)
    return s


def main(path):
    if not path.is_file():
        raise Skip('missing ' + str(path))
    s, crlf = read(path)
    if MARK in s:
        print('Co-op Wii Remotes already installed.')
        return
    write(path, apply(s), crlf)
    print('Co-op Wii Remotes installed: four WPAD channels.')


if __name__ == '__main__':
    try:
        main(Path(sys.argv[1]) if len(sys.argv) > 1 else IOS)
    except Skip as reason:
        print('Co-op Wii Remotes not installed: ' + str(reason))
