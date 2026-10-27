"""Execute captured retail AX commands and independent PCM/ADPCM voice witnesses."""
from pathlib import Path
from port_paths import default_storage
import subprocess,json
P=Path(__file__).resolve().parents[1]
B=default_storage()
W=B/'ax-tests';W.mkdir(exist_ok=True)
cpp=W/'test.cpp'
cpp.write_text(r'''
#include "protocol.h"
#include <fstream>
std::vector<u8> m1(24*1024*1024),m2(64*1024*1024);
u8* ptr(u32 a,size_t n){a&=0x3fffffff;auto& m=(a&0x10000000)?m2:m1;u32 off=(a&0x10000000)?a-0x10000000:a;return off<=m.size()&&n<=m.size()-off?m.data()+off:nullptr;}
void load(const char* file,std::vector<u8>& dst){std::ifstream f(file,std::ios::binary);f.read((char*)dst.data(),dst.size());if(size_t(f.gcount())!=dst.size())throw std::runtime_error("capture unavailable");}
int main(int argc,char**argv){
  DSP::HLE::DSPHLE dsp(ptr);DSP::HLE::AXWiiUCode ax(&dsp,0x347112ba);
  unsigned total=0,bad=0;auto check=[&](bool ok,const char* name){++total;if(!ok){++bad;std::cout<<"FAIL "<<name<<"\n";}};
  auto& mem=dsp.GetSystem().GetMemory();
  auto put16=[&](u32 a,u16 v){mem.CopyToEmuSwapped(a,&v,2);};
  auto get16=[&](u32 a){u16 v;mem.CopyFromEmuSwapped(&v,a,2);return v;};
  // Synthetic direct PCM16 voice: known sequence and inclusive loop boundary.
  DSP::HLE::AXPBWii pb{};pb.running=1;pb.src_type=2;pb.vol_env.cur_volume=s16(0x8000);pb.mixer_control_lo=3;
  pb.mixer.main_left.volume=pb.mixer.main_right.volume=0x8000;
  pb.audio_addr.sample_format=10;pb.adpcm.gain=0x800;pb.audio_addr.looping=1;
  pb.audio_addr.cur_addr_lo=pb.audio_addr.loop_addr_lo=0x800;
  pb.audio_addr.end_addr_lo=0x87f;
  for(unsigned i=0;i<128;++i)put16(0x1000+i*2,u16(i*123-7000));
  // New AX has no 10-byte updates section in guest RAM.
  auto writepb=[&](){auto raw=(const u8*)&pb;size_t off=offsetof(DSP::HLE::AXPBWii,updates);mem.CopyToEmuSwapped<u16>(0x4000,(const u16*)raw,off);mem.CopyToEmuSwapped<u16>(0x4000+off,(const u16*)(raw+off+10),sizeof(pb)-off-10);};
  const u16 commands[]={0,0,0x3000,4,0,0x4000,11,0x8000,0,0x5000,0,0x6000,14};
  mem.CopyToEmuSwapped(0x2000,commands,sizeof(commands));writepb();
  ax.Execute(0x2000,14);
  for(unsigned i=0;i<96;++i){s16 expected=s16(i*123-7000);check(s16(get16(0x6000+4*i))==expected,"PCM right samples");check(s16(get16(0x6002+4*i))==expected,"PCM left samples");}
  constexpr size_t cur=offsetof(DSP::HLE::AXPBWii,audio_addr)+12-10;
  check(get16(0x4000+cur+2)==0x860,"96 samples consumed");
  ax.Execute(0x2000,14);check(get16(0x4000+cur+2)==0x840,"inclusive PCM loop");
  for(unsigned i=0;i<96;++i)check(s16(get16(0x6000+4*i))==s16(((i+96)%128)*123-7000),"PCM loop sample order");
  check(dsp.mail.pending.size()==2&&dsp.mail.pending[0]==0xdcd10004,"PCM output sync ordering");
  // One-shot end, PCM8 sign extension through gain, ADPCM header skipping.
  pb.audio_addr.looping=0;pb.audio_addr.cur_addr_lo=0x800;pb.audio_addr.end_addr_lo=0x807;writepb();ax.Execute(0x2000,14);
  check(get16(0x4010)==0,"one-shot stops");check(s16(get16(0x6000+4*8))==0,"one-shot remainder silent");
  pb.audio_addr.sample_format=0x19;pb.adpcm.gain=0x100;pb.audio_addr.looping=1;pb.audio_addr.cur_addr_lo=pb.audio_addr.loop_addr_lo=0x1000;pb.audio_addr.end_addr_lo=0x107f;pb.adpcm.yn1=pb.adpcm.yn2=0;
  for(unsigned i=0;i<128;++i)*ptr(0x1000+i,1)=u8(i+128);writepb();ax.Execute(0x2000,14);
  for(unsigned i=0;i<96;++i)check(s16(get16(0x6000+4*i))==s16((i+128)*256),"PCM8 signed samples");
  pb.audio_addr.sample_format=0;pb.audio_addr.cur_addr_lo=pb.audio_addr.loop_addr_lo=0x2002;pb.audio_addr.end_addr_lo=0x20ff;pb.adpcm.gain=0;pb.adpcm.pred_scale=0;pb.adpcm.yn1=pb.adpcm.yn2=0;
  std::fill(ptr(0x1000,128),ptr(0x1080,0),u8(0x11));for(unsigned i=0;i<128;i+=8)*ptr(0x1000+i,1)=0;
  writepb();ax.Execute(0x2000,14);for(unsigned i=0;i<96;++i)check(s16(get16(0x6000+4*i))==1,"ADPCM zero predictor and header skip");
  // Malformed commands must reject before processing.
  put16(0x2000,0xffff);bool rejected=false;try{ax.Execute(0x2000,14);}catch(const std::exception&){rejected=true;}check(rejected,"unknown command rejected");
  if(argc==4){load(argv[1],m1);load(argv[2],m2);std::vector<u8> coeff(4096);load(argv[3],coeff);ax.LoadCoefficients(coeff.data(),coeff.size());
    unsigned active=0,changed=0;u32 head=0x80625c40;
    while(head){u16 w[64];mem.CopyFromEmuSwapped(w,head,sizeof(w));if(w[8]==1)++active;u32 old=u32(w[61])<<16|w[62]; // corrected below by independent raw address offsets
      head=u32(w[0])<<16|w[1];if(active>1024)throw std::runtime_error("capture list cycle");}
    auto before1=m1,before2=m2;ax.Execute(0x80624cc0,128);
    head=0x80625c40;while(head){u16 w[64];mem.CopyFromEmuSwapped(w,head,sizeof(w));size_t off=(head&0x3fffffff);auto p=before1.data()+off;
      if((p[16]<<8|p[17])==1 && std::memcmp(p+0x7a,ptr(head+0x7a,4),4))++changed;
      head=u32(w[0])<<16|w[1];}
    check(active>0&&changed>0,"captured active voices consume real samples");
    unsigned pcm=0;for(unsigned i=0;i<384;++i)pcm+=*ptr(0x80625820+i,1)!=0;
    check(pcm>0,"captured retail command output nonzero PCM");
    std::cout<<"CAPTURE active="<<active<<" advanced="<<changed<<" PCM_nonzero_bytes="<<pcm<<"\n";
    NativeAX::Protocol protocol(ptr);protocol.Reset();
    for(u32 mail:{0x80f3a001u,0x80579120u,0x80f3c002u,0u,0x80f3a002u,8192u,0x80f3b002u,0u,0x80f3d001u,0x10u})protocol.Send(mail);
    check(protocol.crc==0x347112ba&&protocol.dsp.mail.pending.size()==1&&protocol.dsp.mail.pending[0]==0xdcd10000,"captured ucode boot and INIT");
    protocol.dsp.mail.pending.clear();protocol.Send(0xbabe0080);check(protocol.dsp.mail.pending.empty(),"size mail has no premature completion");
    protocol.Send(0x80624cc0);check(protocol.command_frames==1&&protocol.dsp.mail.pending==std::vector<u32>{0xdcd10004,0xdcd10002},"actual PCM SYNC then YIELD");
    protocol.dsp.mail.pending.clear();protocol.Send(0xcdd10003);check(protocol.dsp.mail.pending.empty(),"CONTINUE has no spurious INIT");
    protocol.Send(0xbabe0080);protocol.Send(0x80624d40);check(protocol.command_frames==2,"second retail command buffer processed");
  }
  std::cout<<"RESULT "<<total-bad<<"/"<<total<<"\n";return bad?1:0;
}
''')
capture=B/'toolchain/runtime-captures/user-resolution-audio-clock'
commands=[['g++','-std=c++20','-O1','-fwrapv','-fno-strict-aliasing','-static','-I',str(P/'runtime-overlays/native_ax'),str(cpp),'-o',str(W/'test.exe')],
          [str(W/'test.exe'),str(capture/'mem1-cinetime.bin'),str(capture/'mem2-cinetime.bin'),str(P/'reference/dolphin-ax/dsp_coef.bin')]]
results=[]
for cmd in commands:
    p=subprocess.run(cmd,capture_output=True,text=True);print(p.returncode,p.stdout+p.stderr,flush=True)
    results.append(dict(command=cmd,exit_code=p.returncode,output=p.stdout+p.stderr))
    (P/'reports/ax-backend-tests.json').write_text(json.dumps(results,indent=2)+'\n')
    if p.returncode:raise SystemExit(p.returncode)
