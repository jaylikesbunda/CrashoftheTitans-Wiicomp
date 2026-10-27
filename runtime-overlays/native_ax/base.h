// Derived from Dolphin AX.h / AX.cpp, GPL-2.0-or-later.
namespace DSP::HLE {
enum AXMixControl
{
  // clang-format off
  MIX_MAIN_L      = 0x000001,
  MIX_MAIN_L_RAMP = 0x000002,
  MIX_MAIN_R      = 0x000004,
  MIX_MAIN_R_RAMP = 0x000008,
  MIX_MAIN_S      = 0x000010,
  MIX_MAIN_S_RAMP = 0x000020,

  MIX_AUXA_L      = 0x000040,
  MIX_AUXA_L_RAMP = 0x000080,
  MIX_AUXA_R      = 0x000100,
  MIX_AUXA_R_RAMP = 0x000200,
  MIX_AUXA_S      = 0x000400,
  MIX_AUXA_S_RAMP = 0x000800,

  MIX_AUXB_L      = 0x001000,
  MIX_AUXB_L_RAMP = 0x002000,
  MIX_AUXB_R      = 0x004000,
  MIX_AUXB_R_RAMP = 0x008000,
  MIX_AUXB_S      = 0x010000,
  MIX_AUXB_S_RAMP = 0x020000,

  MIX_AUXC_L      = 0x040000,
  MIX_AUXC_L_RAMP = 0x080000,
  MIX_AUXC_R      = 0x100000,
  MIX_AUXC_R_RAMP = 0x200000,
  MIX_AUXC_S      = 0x400000,
  MIX_AUXC_S_RAMP = 0x800000,

  MIX_ALL_RAMPS   = 0xAAAAAA,
  // clang-format on
};

class AXUCode {
protected:
  DSPHLE* m_dsphle;
  u32 m_crc;
  MailSink& m_mail_handler;
  // 32 * 5 because 32 samples per millisecond, for max 5 milliseconds.
  int m_samples_main_left[32 * 5]{};
  int m_samples_main_right[32 * 5]{};
  int m_samples_main_surround[32 * 5]{};
  int m_samples_auxA_left[32 * 5]{};
  int m_samples_auxA_right[32 * 5]{};
  int m_samples_auxA_surround[32 * 5]{};
  int m_samples_auxB_left[32 * 5]{};
  int m_samples_auxB_right[32 * 5]{};
  int m_samples_auxB_surround[32 * 5]{};

  u16 m_cmdlist[512]{};
  u32 m_cmdlist_size = 0;

  // Table of coefficients for polyphase sample rate conversion.
  // The coefficients aren't always available (they are part of the DSP DROM)
  // so we also need to know if they are valid or not.
  std::optional<u32> m_coeffs_checksum = std::nullopt;
  std::array<s16, 0x800> m_coeffs{};

  u16 m_compressor_pos = 0;

  std::unique_ptr<Accelerator> m_accelerator;

  struct BufferDesc
  {
    int* ptr;
    int samples_per_milli;
  };

  template <int Millis, size_t BufCount>
  void InitMixingBuffers(u32 init_addr, const std::array<BufferDesc, BufCount>& buffers)
  {
    auto& system = m_dsphle->GetSystem();
    auto& memory = system.GetMemory();
    std::array<u16, 3 * BufCount> init_array;
    memory.CopyFromEmuSwapped(init_array.data(), init_addr, sizeof(init_array));
    for (size_t i = 0; i < BufCount; ++i)
    {
      const BufferDesc& buf = buffers[i];
      s32 value = s32((u32(init_array[3 * i]) << 16) | init_array[3 * i + 1]);
      s16 delta = init_array[3 * i + 2];
      if (value == 0)
      {
        memset(buf.ptr, 0, Millis * buf.samples_per_milli * sizeof(int));
      }
      else
      {
        for (int j = 0; j < Millis * buf.samples_per_milli; ++j)
          buf.ptr[j] = value + j * delta;
      }
    }
  }

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
void AXUCode::RunCompressor(u16 threshold, u16 release_frames, u32 table_addr, u32 millis)
{
  // check for L/R samples exceeding the threshold
  bool triggered = false;
  for (u32 i = 0; i < 32 * millis; ++i)
  {
    if (std::abs(m_samples_main_left[i]) > int(threshold) ||
        std::abs(m_samples_main_right[i]) > int(threshold))
    {
      triggered = true;
      break;
    }
  }

  const u32 frame_byte_size = 32 * millis * sizeof(s16);
  u32 table_offset = 0;
  if (triggered)
  {
    // one attack frame based on previous frame
    table_offset = m_compressor_pos * frame_byte_size;
    // next frame will start release
    m_compressor_pos = release_frames;
  }
  else if (m_compressor_pos)
  {
    // release
    --m_compressor_pos;
    // the release ramps are located after the attack ramps
    constexpr u32 ATTACK_ENTRY_COUNT = 11;
    table_offset = (ATTACK_ENTRY_COUNT + m_compressor_pos) * frame_byte_size;
  }
  else
  {
    return;
  }

  // apply the selected ramp
  auto& memory = m_dsphle->GetSystem().GetMemory();
  u16* ramp = reinterpret_cast<u16*>(
      memory.GetPointerForRange(table_addr + table_offset, 32 * millis * sizeof(u16)));
  for (u32 i = 0; i < 32 * millis; ++i)
  {
    u16 coef = Common::swap16(*ramp++);
    m_samples_main_left[i] = (s64(m_samples_main_left[i]) * coef) >> 15;
    m_samples_main_right[i] = (s64(m_samples_main_right[i]) * coef) >> 15;
  }
}

}
