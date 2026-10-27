// NATIVE_WPAD_EMULATION: retail WPAD ABI adapter, original KPAD retained.
// Timeline: wall milliseconds, retail WPAD button mask, connected(0/1).
// RQJP7D ABI evidence: 803EC524, 803ECE80, 803ED020, 803F28D4.
#include <fstream>
#include <sstream>

struct NativeWpadEvent { uint64_t ms; uint16_t buttons; bool connected; int8_t sx=0, sy=0; };
struct NativeWpadChannel {
  uint32_t sampling=0, connect=0, extension=0, buffer=0, count=0, format=3;
  uint32_t index=0xFFFFFFFF;
  bool dpd=false;
};
struct NativeWpadState {
  bool enabled=true, connected=false, physical=true;
  uint16_t buttons=0; int8_t sx=0, sy=0;
  float ir_x=0.5f, ir_y=0.5f; bool ir_valid=false; // NATIVE_WPAD_IR_V1: pointer in [0,1] screen space
  uint64_t next_sample=0;
  size_t next_event=0;
  std::chrono::steady_clock::time_point start;
  NativeWpadChannel channels[4];
  std::vector<NativeWpadEvent> events;
  std::string live_path; size_t live_lines=0; uint64_t live_checked=0;
  NativeWpadState() {
    start=std::chrono::steady_clock::now();
    const char* live=std::getenv("NWII_WPAD_LIVE");
    if (live && *live) { live_path=live; physical=false; connected=true; enabled=true; start=std::chrono::steady_clock::now();
      std::cout<<"[NativeWPAD] Live command file enabled: "<<live<<"\n"; }
    const char* path=std::getenv("NWII_WPAD_SCRIPT");
    if (!path || !*path) return;
    physical=false; connected=true; enabled=false;
    std::ifstream f(path);
    if (!f) { std::cerr<<"[NativeWPAD] Cannot open script "<<path<<"\n"; return; }
    std::string line; uint64_t previous=0;
    while (std::getline(f,line)) {
      if (line.empty() || line[0]=='#') continue;
      std::istringstream row(line); uint64_t ms; unsigned mask, connected;
      if (!(row>>ms>>std::hex>>mask>>std::dec>>connected) || mask>0xFFFF || connected>1 ||
          (!events.empty() && ms<previous)) {
        std::cerr<<"[NativeWPAD] Invalid timeline; emulation disabled\n"; events.clear(); return;
      }
      events.push_back({ms,static_cast<uint16_t>(mask),connected!=0}); previous=ms;
    }
    if (events.empty()) return;
    enabled=true; start=std::chrono::steady_clock::now();
    std::cout<<"[NativeWPAD] Retail Wiimote + Nunchuk script enabled: "<<path<<"\n";
  }
};
static NativeWpadState& native_wpad_state() { static NativeWpadState s; return s; }
static uint32_t native_wpad_size(uint32_t format) {
  return format<3 ? 0x2A : format<6 ? 0x32 : format<9 ? 0x36 : 0x5A;
}
static bool native_wpad_ram(uint32_t p,uint32_t size) {
  return (p>=0x80000000 && uint64_t(p)+size<=0x81800000) ||
         (p>=0x90000000 && uint64_t(p)+size<=0x94000000);
}
// NATIVE_WPAD_IR_V1: two sensor-bar dots for a pointer at (nx,ny) in [0,1] screen space, in the SDK's status coordinates.
// Evidence (original KPAD, 803F2444/803F481C): x is the raw camera x (0..1023, 0x3FF invalid); y is stored as 0x2FF-rawY, i.e. image-down
// (0x2FF invalid); size 0 or trace id 0xF invalid. The camera sees the bar move opposite to the pointing direction, so the dots sit at
// the image centre minus the pointer offset in both axes.
// The map is expressed in bar widths (the dot separation), which is the unit a pointer is measured in: a 16:9 TV about 2.2 bar widths
// either side of centre horizontally and about 1.25 vertically, with the bar below the screen. The ranges below are deliberately wider than
// the screen (X 3.0, Y 2.4 bars) so every screen edge is reachable with margin; the game clamps the cursor to the screen. Both dots move
// together (the centre is clamped, not each dot) so the separation, which KPAD uses as its scale, never changes.
struct NativeIrDot { int16_t x, y; uint16_t size; uint8_t trace; };
static void native_wpad_ir_dots(float nx,float ny,NativeIrDot out[2]) {
  auto envf=[](const char* name,float def){const char* e=std::getenv(name);return e?static_cast<float>(std::atof(e)):def;};
  static const float rx=envf("NWII_IR_RANGE_X",3.0f), ry=envf("NWII_IR_RANGE_Y",2.4f), bar=envf("NWII_IR_BAR_Y",1.0f), sep=envf("NWII_IR_SEP",95.f);
  static const float sx=std::getenv("NWII_IR_INVERT_X")?-1.f:1.f, sy=std::getenv("NWII_IR_INVERT_Y")?-1.f:1.f;
  nx=nx<0.f?0.f:nx>1.f?1.f:nx; ny=ny<0.f?0.f:ny>1.f?1.f:ny;
  float cx=512.f-sx*(nx-0.5f)*2.f*rx*sep, cy=384.f+bar*sep-sy*(ny-0.5f)*2.f*ry*sep;
  const float half=sep*0.5f;
  cx=cx<half+1.f?half+1.f:cx>1021.f-half?1021.f-half:cx; cy=cy<1.f?1.f:cy>765.f?765.f:cy;
  for (int i=0;i<2;++i)
    out[i]={static_cast<int16_t>(cx+(i?half:-half)+0.5f),static_cast<int16_t>(cy+0.5f),6,static_cast<uint8_t>(i)};
}
static void native_wpad_packet(CPUContext& ctx,uint32_t p,uint32_t format,bool connected,uint16_t buttons) {
  const uint32_t size=native_wpad_size(format);
  if (!native_wpad_ram(p,size)) return;
  for (uint32_t i=0;i<size;++i) ctx.mmu.write8(p+i,0);
  ctx.mmu.write8(p+0x29,connected ? 0 : 0xFF);
  if (!connected) return;
  ctx.mmu.write16(p,buttons & (format>=3 && format<6 ? 0xFF1F : 0x9F1F)); // retail NC includes C/Z
  ctx.mmu.write16(p+6,204); // zero-calibrated stationary +1g, SDK calibration fallback
  for (unsigned i=0;i<4;++i) ctx.mmu.write16(p+0xA+i*8,0x2FF); // invalid IR dots
  { auto& ir=native_wpad_state(); if (ir.ir_valid) { // NATIVE_WPAD_IR_V1
      NativeIrDot dots[2]; native_wpad_ir_dots(ir.ir_x,ir.ir_y,dots);
      for (unsigned i=0;i<2;++i) { const uint32_t o=p+8+i*8;
        ctx.mmu.write16(o,static_cast<uint16_t>(dots[i].x)); ctx.mmu.write16(o+2,static_cast<uint16_t>(dots[i].y));
        ctx.mmu.write16(o+4,dots[i].size); ctx.mmu.write8(o+6,dots[i].trace); } } }
  ctx.mmu.write8(p+0x28,1); // extension type: Nunchuk
  if (format>=3 && format<6) { auto& st=native_wpad_state(); ctx.mmu.write8(p+0x30,static_cast<uint8_t>(st.sx)); ctx.mmu.write8(p+0x31,static_cast<uint8_t>(st.sy)); }
  if (format>=3 && format<6) ctx.mmu.write16(p+0x2E,204);
}
static void native_wpad_advance(CPUContext& ctx,uint64_t ms) {
  auto& s=native_wpad_state();
  if (s.physical) {
    static const unsigned controller_index=[](){
      const char* value=std::getenv("NWII_CONTROLLER_INDEX");
      if (!value || !*value) return 0u;
      char* end=nullptr; const long index=std::strtol(value,&end,10);
      return end && !*end && index>=0 && index<4 ? static_cast<unsigned>(index) : 0u;
    }();
    const auto state=nwii::runtime::input::InputManager::get().get_wiimote_state(controller_index);
    const bool connected=state.controller_connected;
    if (connected!=s.connected) {
      s.connected=connected; auto& c=s.channels[0];
      if (c.connect) ctx.queue_callback(c.connect,0,connected ? 0 : 0xFFFFFFFF);
      if (connected && c.extension) ctx.queue_callback(c.extension,0,1);
      std::cout<<"[NativeWPAD] SDL controller connected="<<connected<<"\n";
    }
    s.buttons=connected ? static_cast<uint16_t>(state.buttons) : 0;
    s.sx=connected ? state.nunchuk_x : 0; s.sy=connected ? state.nunchuk_y : 0;
    s.ir_x=state.ir_x; s.ir_y=state.ir_y; s.ir_valid=connected; // NATIVE_WPAD_IR_V1
    return;
  }
  if (!s.live_path.empty() && ms>=s.live_checked+200) {
    // Live command file: each new line "<retail buttons hex> <hold ms>" presses then releases.
    s.live_checked=ms; std::ifstream f(s.live_path); std::string line; size_t n=0;
    while (std::getline(f,line)) {
      if (line.empty() || line[0]=='#') continue;
      if (n++<s.live_lines) continue;
      std::istringstream row(line); unsigned mask=0; uint64_t hold=0; int sx=0, sy=0;
      if (row>>std::hex>>mask>>std::dec>>hold) {
        row>>sx>>sy;
        uint64_t at=ms; if (!s.events.empty() && s.events.back().ms>at) at=s.events.back().ms;
        s.events.push_back({at,static_cast<uint16_t>(mask&0xFFFF),true,static_cast<int8_t>(sx),static_cast<int8_t>(sy)});
        s.events.push_back({at+(hold?hold:150),0,true,0,0});
        std::cout<<"[NativeWPAD] live command buttons="<<std::hex<<mask<<std::dec<<" hold="<<hold<<"\n";
      }
      s.live_lines=n;
    }
  }
  while (s.next_event<s.events.size() && s.events[s.next_event].ms<=ms) {
    const auto e=s.events[s.next_event++];
    if (e.connected!=s.connected) {
      s.connected=e.connected;
      auto& c=s.channels[0];
      if (c.connect) ctx.queue_callback(c.connect,0,s.connected ? 0 : 0xFFFFFFFF);
      if (s.connected && c.extension) ctx.queue_callback(c.extension,0,1);
    }
    s.buttons=e.buttons; s.sx=e.sx; s.sy=e.sy;
    std::cout<<"[NativeWPAD] ms="<<ms<<" buttons="<<std::hex<<s.buttons<<std::dec
             <<" connected="<<s.connected<<"\n";
  }
}
static void native_wpad_poll(CPUContext& ctx) {
  auto& s=native_wpad_state();
  if (!s.enabled || ctx.in_callback || !(ctx.msr & 0x8000)) return;
  const auto ms=static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now()-s.start).count());
  native_wpad_advance(ctx,ms);
  const uint64_t tb=ctx.read_timebase();
  const uint32_t bus=ctx.mmu.read32(0x800000F8);
  if (!bus || tb<s.next_sample || ctx.pending_cb_count.load(std::memory_order_relaxed)>0) return;
  s.next_sample=tb+bus/400; // 100 Hz on the guest hardware clock; no wall-clock callback storm
  auto& c=s.channels[0];
  if (!s.connected || !c.sampling) return;
  if (c.buffer && c.count && c.count<=1024) {
    c.index=(c.index+1)%c.count;
    const uint32_t size=native_wpad_size(c.format);
    if (native_wpad_ram(c.buffer,size*c.count))
      native_wpad_packet(ctx,c.buffer+c.index*size,c.format,true,s.buttons);
  }
  ctx.queue_callback(c.sampling,0,0);
}
bool native_wpad_dispatch(CPUContext& ctx,uint32_t target) {
  auto& s=native_wpad_state();
  if (!s.enabled) return false;
  switch (target) {
    case 0x803EC524: case 0x803ECE80: case 0x803EC5B0: case 0x803EC618:
    case 0x803EC680: case 0x803EC6E8: case 0x803EC730: case 0x803ED020:
    case 0x803EC364: case 0x803EEE98: case 0x803EEF0C:
    case 0x803EC7E0: case 0x803EC898: break;
    default: return false;
  }
  if (s.physical) native_wpad_advance(ctx,0);
  const uint32_t chan=ctx.gpr[3], p=ctx.gpr[4];
  if (chan>=4) { ctx.gpr[3]=0xFFFFFFFF; ctx.pc=ctx.lr; return true; }
  auto& c=s.channels[chan]; const bool connected=chan==0 && s.connected;
  switch (target) {
    case 0x803EC524:
      if (p && native_wpad_ram(p,4)) ctx.mmu.write32(p,connected ? 1 : 0xFF);
      ctx.gpr[3]=connected ? 0 : 0xFFFFFFFF; break;
    case 0x803ECE80:
      native_wpad_packet(ctx,p,c.format,connected,s.buttons); break;
    case 0x803EC5B0: { const auto old=c.sampling; c.sampling=p; ctx.gpr[3]=old;
      std::cout<<"[NativeWPAD] sampling chan="<<chan<<" cb="<<std::hex<<p<<std::dec<<"\n"; break; }
    case 0x803EC618: { const auto old=c.connect; c.connect=p; ctx.gpr[3]=old; break; }
    case 0x803EC680: { const auto old=c.extension; c.extension=p; ctx.gpr[3]=old; break; }
    case 0x803EC6E8: ctx.gpr[3]=c.format; break;
    case 0x803EC730:
      if (!connected) ctx.gpr[3]=0xFFFFFFFF;
      else if (p>5) ctx.gpr[3]=0xFFFFFFFE; // only implemented Wiimote/Nunchuk formats
      else { c.format=p; ctx.gpr[3]=0;
        std::cout<<"[NativeWPAD] format chan="<<chan<<" fmt="<<p<<"\n"; } break;
    case 0x803ED020:
      c.buffer=p; c.count=ctx.gpr[5]; c.index=0xFFFFFFFF;
      if (c.count<=1024 && native_wpad_ram(p,native_wpad_size(c.format)*c.count))
        for (uint32_t i=0;i<c.count;++i) native_wpad_packet(ctx,p+i*native_wpad_size(c.format),c.format,false,0);
      break;
    case 0x803EC364:
      if (ctx.gpr[5] && native_wpad_ram(ctx.gpr[5],6))
        for (unsigned i=0;i<3;++i) ctx.mmu.write16(ctx.gpr[5]+i*2,204);
      break;
    case 0x803EC7E0: case 0x803EC898:
      // WPADInfo is the 0x18-byte SDK block copied from controller+0x838.
      // Status parser 803F1A60 gives dpd/speaker/attach/lowBat/rumble and battery/LED bytes.
      if (connected && native_wpad_ram(p,0x18)) {
        for (unsigned i=0;i<0x18;++i) ctx.mmu.write8(p+i,0);
        ctx.mmu.write32(p,c.dpd ? 1 : 0);ctx.mmu.write32(p+8,1);
        ctx.mmu.write8(p+0x14,4);ctx.mmu.write8(p+0x15,1);
      }
      if (target==0x803EC898 && ctx.gpr[5])
        ctx.queue_callback(ctx.gpr[5],chan,connected ? 0 : 0xFFFFFFFF);
      ctx.gpr[3]=connected ? 0 : 0xFFFFFFFF;break;
    case 0x803EEE98: ctx.gpr[3]=c.dpd ? 1 : 0; break;
    case 0x803EEF0C:
      if (connected) c.dpd=p!=0;
      if (ctx.gpr[5]) ctx.queue_callback(ctx.gpr[5],chan,connected ? 0 : 0xFFFFFFFF);
      ctx.gpr[3]=connected ? 0 : 0xFFFFFFFF; break;
  }
  ctx.pc=ctx.lr;
  return true;
}
