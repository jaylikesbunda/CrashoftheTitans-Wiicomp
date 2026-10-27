"""Adapt the retained GPL Dolphin AXWii implementation to a bounded RAM/mail bridge.

No guest SDK function is replaced: the backend consumes hardware DSP commands.
"""
from pathlib import Path
import re
P=Path(__file__).resolve().parents[1]
R=P/'reference/dolphin-ax'
O=P/'runtime-overlays/native_ax'
O.mkdir(exist_ok=True)
def read(n): return (R/n).read_text()
def clean(s): return re.sub(r'^#include[^\n]*\n','',s,flags=re.M)
def write(n,s):
    p=O/n
    if not p.exists() or p.read_text()!=s:p.write_text(s)

write('structs.h',clean(read('AXStructs.h')))
h=clean(read('DSPAccelerator.h')).replace('  void DoState(PointerWrap& p);','')
a=h.index('  union SampleFormat');b=h.index('  s16 m_gain',a)
h=h[:a]+'''  struct SampleFormat { u16 hex=0; } m_sample_format;
'''+h[b:]
write('accelerator.h',h)
s=clean(read('DSPAccelerator.cpp'))
a=s.index('void Accelerator::DoState');b=s.index('constexpr u32 START_END',a)
s=s[:a]+s[b:]
for field,typ,shift,mask in [('size','FormatSize',0,3),('decode','FormatDecode',2,3),('gain_scale','FormatGainScale',4,3),('unk','u16',6,1023)]:
    s=s.replace('m_sample_format.'+field,f'{typ}((m_sample_format.hex >> {shift}) & {mask})')
s=re.sub(r'(?:ERROR|WARN)_LOG_FMT\([\s\S]*?\);','throw std::runtime_error("unsupported DSP accelerator format/access");',s)
write('accelerator.inc',s)
s=clean(read('AXVoice.h')).replace('Common::BitCastPtr<u16>(&pb)','reinterpret_cast<u16*>(&pb)')
s=re.sub(r'DolphinAnalytics::Instance\(\).ReportGameQuirk\(GameQuirk::(\w+)\);',r'NativeQuirk("\1");',s)
write('voice.h',s)
h=clean(read('AXWii.h')).replace('  void Initialize() override;','').replace('  void DoState(PointerWrap& p) override;','')
write('wii.h',h)
s=clean(read('AXWii.cpp'))
s=re.sub(r'INFO_LOG_FMT\([^\n]*\);','',s)
a=s.index('void AXWiiUCode::Initialize()');b=s.index('void AXWiiUCode::HandleCommandList()',a)
s=s[:a]+s[b:]
a=s.index('void AXWiiUCode::DoState');s=s[:a]+'} // namespace DSP::HLE\n'
s=s.replace('  while (pb_addr)\n','  std::unordered_set<u32> visited;\n  while (pb_addr)\n')
s=s.replace('    ReadPB(memory, pb_addr, pb);','    if (!visited.insert(pb_addr).second || visited.size()>1024) throw std::runtime_error("cyclic/oversized AX voice list");\n    ReadPB(memory, pb_addr, pb);')
# Check command/operand lengths before any command performs side effects.
s=s.replace('  u32 curr_idx = 0;','  ValidateCommands();\n  u32 curr_idx = 0;',1)
write('wii.inc',s)
h=read('AX.h')
enum=h[h.index('enum AXMixControl'):h.index('class AXUCode')]
buffers=h[h.index('  // 32 * 5'):h.index('  // Constructs without')]
init=h[h.index('  struct BufferDesc'):h.index('  void SetupProcessing',h.index('  struct BufferDesc'))]
compress=read('AX.cpp');a=compress.index('void AXUCode::RunCompressor');b=compress.index('void AXUCode::OutputSamples',a)
write('base.h','''// Derived from Dolphin AX.h / AX.cpp, GPL-2.0-or-later.
namespace DSP::HLE {
'''+enum+'''class AXUCode {
protected:
  DSPHLE* m_dsphle;
  u32 m_crc;
  MailSink& m_mail_handler;
'''+buffers+init+'''
  AXUCode(DSPHLE* dsp,u32 crc,bool):m_dsphle(dsp),m_crc(crc),m_mail_handler(dsp->mail) {}
  virtual void HandleCommandList()=0;
  void RunCompressor(u16,u16,u32,u32);
  void ValidateCommands() {
    static constexpr unsigned modern[]={2,2,2,2,2,5,5,5,13,13,4,5,5,8,0};
    static constexpr unsigned old[]={2,2,2,2,2,0,5,5,5,13,13,4,4,4,8,0};
    bool legacy=m_crc==0xfa450138||m_crc==0x7699af32;
    unsigned end=legacy?15:14;
    for(unsigned i=0;i<m_cmdlist_size;) {
      auto cmd=m_cmdlist[i++];
      if(cmd>end) throw std::runtime_error("unknown AX command");
      unsigned n=legacy?old[cmd]:modern[cmd];
      if(!legacy&&m_crc==0xd9c4bf34&&(cmd==11||cmd==12))--n;
      if(n>m_cmdlist_size-i) throw std::runtime_error("truncated AX command operands");
      i+=n;
      if(cmd==end)return;
    }
    throw std::runtime_error("AX command list has no END");
  }
public:
  virtual ~AXUCode()=default;
  void Execute(u32 addr,u16 size) {
    if(!size||size>=512)throw std::runtime_error("invalid AX command-list size");
    m_cmdlist_size=size;
    m_dsphle->GetSystem().GetMemory().CopyFromEmuSwapped(m_cmdlist,addr,size*2);
    HandleCommandList();
  }
  void LoadCoefficients(const u8* bytes,size_t size) {
    if(size!=4096)throw std::runtime_error("invalid AX coefficient table");
    for(size_t i=0;i<2048;++i)m_coeffs[i]=s16(u16(bytes[i*2])<<8|bytes[i*2+1]);
    m_coeffs_checksum=1;
  }
};
'''+compress[a:b]+'}\n')
print('Prepared bounded Dolphin AXWii backend:',O)
