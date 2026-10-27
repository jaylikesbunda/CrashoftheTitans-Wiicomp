// AX/ROM mailbox protocol, backed by decoded commands rather than acknowledgements.
#pragma once
#include "bridge.h"
#include "coefficients.h"
namespace NativeAX {
class Protocol {
public:
  DSP::HLE::DSPHLE dsp;
  std::unique_ptr<DSP::HLE::AXWiiUCode> ax;
  u32 crc=0;
  unsigned command_frames=0;
  explicit Protocol(decltype(dsp.system.memory.pointer) p):dsp(std::move(p)){}
  void Reset(){ax.reset();crc=0;state=State::Rom;rom_command=0;dsp.mail.pending.clear();command_frames=0;}
  void Send(u32 mail){
    if(state==State::Upload){
      upload[upload_index++]=mail;
      if(upload_index==10){
        if(upload[1])throw std::runtime_error("AX task DRAM download unsupported");
        Load(upload[3],upload[4]&0xffff);
        // Initial task uses INIT; a restored task uses RESUME.
        dsp.mail.PushMail(upload[7]||upload[8] ? 0xdcd10001:0xdcd10000,true);
      }
      return;
    }
    if(state==State::Rom){
      if(rom_command){
        switch(rom_command){
          case 0x80f3a001:code_address=mail;break;
          case 0x80f3a002:code_size=mail;break;
          case 0x80f3d001:Load(code_address,code_size);dsp.mail.PushMail(0xdcd10000,true);break;
          case 0x80f3b002:case 0x80f3c002:break;
          default:throw std::runtime_error("unsupported DSP ROM command");
        }
        rom_command=0;
      }else{
        switch(mail){
          case 0x80f3a001:case 0x80f3a002:case 0x80f3b002:case 0x80f3c002:case 0x80f3d001:rom_command=mail;break;
          default:throw std::runtime_error("unknown DSP ROM mail");
        }
      }
      return;
    }
    if(state==State::Address){
      ax->Execute(mail,command_size); // Produces SYNC only after actual PCM output.
      ++command_frames;
      dsp.mail.PushMail(0xdcd10002,true); // YIELD after sample/history writeback.
      state=State::Next;return;
    }
    if(state==State::Commands){
      if((mail&0xffff0000)!=0xbabe0000)throw std::runtime_error("AX expects command-list size");
      command_size=u16(mail);state=State::Address;return;
    }
    switch(mail&0xffff){
      case 0:state=State::Commands;dsp.mail.PushMail(0xdcd10001,true);break;
      case 1:state=State::Upload;upload_index=0;break;
      case 2:Reset();break;
      case 3:state=State::Commands;break; // CONTINUE has no acknowledgement.
      default:throw std::runtime_error("unknown AX task mail");
    }
  }
private:
  enum class State{Rom,Commands,Address,Next,Upload};
  State state=State::Rom;
  u32 rom_command=0,code_address=0,code_size=0;
  u16 command_size=0;
  std::array<u32,10> upload{};
  unsigned upload_index=0;
  void Load(u32 address,u32 size){
    if(!size||size>8192)throw std::runtime_error("invalid DSP IRAM image size");
    auto bytes=dsp.system.memory.GetPointerForRange(address,size);
    u32 hash=0;for(unsigned i=0;i<size;++i){hash^=bytes[i];hash=(hash<<3)|(hash>>29);}
    switch(hash){
      case 0x347112ba:case 0x4cc52064:case 0xd9c4bf34:case 0xadbc06bd:case 0xfa450138:case 0x7699af32:break;
      default:throw std::runtime_error("unknown DSP microcode hash; execution refused");
    }
    crc=hash;ax=std::make_unique<DSP::HLE::AXWiiUCode>(&dsp,crc);
    ax->LoadCoefficients(coefficients.data(),coefficients.size());state=State::Commands;
    std::cout<<"[AX] loaded ucode="<<std::hex<<crc<<" address="<<address<<" bytes="<<size<<std::dec<<"\n";
  }
};
}
