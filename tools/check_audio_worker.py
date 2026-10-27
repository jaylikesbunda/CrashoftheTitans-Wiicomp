"""AX command lists on a worker thread give the same mails and the same guest RAM as the synchronous protocol (no game launch).

Uses the captured retail memory image and the real microcode boot of tools/check_ax_backend.py. The same mail sequence is sent to two
independent copies of guest RAM: one Protocol runs every command list synchronously (async_exec unset), the other posts the list to a thread
(async_exec set), and the thread is joined where hw_dsp.cpp joins it (before the replies are read). Every reply vector and all of MEM1 / MEM2 must
match, over the retail frames and over repeated frames (state carried between lists).
"""
from pathlib import Path
from port_paths import default_storage
import subprocess, json
P = Path(__file__).resolve().parents[1]
B = default_storage()
W = B / 'ax-tests'
W.mkdir(exist_ok=True)
cpp = W / 'worker_test.cpp'
cpp.write_text(r'''
#include "protocol.h"
#include <fstream>
#include <thread>
#include <functional>
struct Image { std::vector<u8> m1, m2; Image():m1(24*1024*1024),m2(64*1024*1024){} };
static u8* at(Image& im,u32 a,size_t n){a&=0x3fffffff;auto& m=(a&0x10000000)?im.m2:im.m1;u32 off=(a&0x10000000)?a-0x10000000:a;return off<=m.size()&&n<=m.size()-off?m.data()+off:nullptr;}
static void load(const char* file,std::vector<u8>& dst){std::ifstream f(file,std::ios::binary);f.read((char*)dst.data(),dst.size());if(size_t(f.gcount())!=dst.size())throw std::runtime_error("capture unavailable");}
int main(int argc,char**argv){
  if(argc!=4){std::cout<<"usage: mem1 mem2 coefficients\n";return 2;}
  Image* imgs=new Image[2];
  for(int k=0;k<2;++k){load(argv[1],imgs[k].m1);load(argv[2],imgs[k].m2);}
  std::vector<u8> coeff(4096);load(argv[3],coeff);
  NativeAX::Protocol sync([&](u32 a,size_t n){return at(imgs[0],a,n);});
  NativeAX::Protocol async([&](u32 a,size_t n){return at(imgs[1],a,n);});
  std::thread worker;
  async.async_exec=[&](std::function<void()> work){ if(worker.joinable())worker.join(); worker=std::thread(std::move(work)); };
  unsigned steps=0,bad=0;
  auto step=[&](u32 mail,const char* what){
    ++steps;
    sync.job_posted=false;sync.Send(mail);
    std::vector<u32> a=sync.dsp.mail.pending;sync.dsp.mail.pending.clear();
    // production order: join the previous job, then Send; the replies are read only after the join
    if(worker.joinable())worker.join();
    async.dsp.mail.pending.clear();
    async.job_posted=false;async.Send(mail);
    if(async.job_posted&&worker.joinable())worker.join();
    std::vector<u32> b=async.dsp.mail.pending;async.dsp.mail.pending.clear();
    if(a!=b){++bad;std::cout<<"MAIL MISMATCH at step "<<steps<<" ("<<what<<") sync="<<a.size()<<" async="<<b.size()<<"\n";}
  };
  sync.Reset();async.Reset();
  for(u32 mail:{0x80f3a001u,0x80579120u,0x80f3c002u,0u,0x80f3a002u,8192u,0x80f3b002u,0u,0x80f3d001u,0x10u})step(mail,"boot");
  if(sync.crc!=0x347112ba||async.crc!=0x347112ba){std::cout<<"ucode not loaded\n";return 1;}
  unsigned frames=0;
  for(int round=0;round<24;++round){
    step(0xbabe0080,"size");step(0x80624cc0,"command list A");++frames;
    step(0xcdd10003,"continue");step(0xbabe0080,"size");step(0x80624d40,"command list B");++frames;
    step(0xcdd10003,"continue");
  }
  if(worker.joinable())worker.join();
  bool ram_equal=imgs[0].m1==imgs[1].m1&&imgs[0].m2==imgs[1].m2;
  if(!ram_equal){++bad;std::cout<<"RAM MISMATCH after "<<frames<<" command lists\n";}
  if(sync.command_frames!=async.command_frames){++bad;std::cout<<"frame count mismatch "<<sync.command_frames<<" vs "<<async.command_frames<<"\n";}
  std::cout<<"AUDIO_WORKER steps="<<steps<<" command_lists="<<frames<<" frames="<<sync.command_frames<<" mismatching="<<bad<<"\n";
  return bad?1:0;
}
''')
capture = B / 'toolchain/runtime-captures/user-resolution-audio-clock'
hdr = P / 'build/nwii-full-source/nWiiRuntime/include/runtime/native_ax'
commands = [['g++', '-std=c++20', '-O1', '-fwrapv', '-fno-strict-aliasing', '-pthread', '-static', '-I', str(hdr), str(cpp), '-o', str(W / 'worker_test.exe')],
            [str(W / 'worker_test.exe'), str(capture / 'mem1-cinetime.bin'), str(capture / 'mem2-cinetime.bin'), str(P / 'reference/dolphin-ax/dsp_coef.bin')]]
results = []
for cmd in commands:
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.returncode, p.stdout + p.stderr, flush=True)
    results.append(dict(command=cmd, exit_code=p.returncode, output=p.stdout + p.stderr))
    (P / 'reports/audio-worker-tests.json').write_text(json.dumps(results, indent=2) + '\n')
    if p.returncode:
        raise SystemExit(p.returncode)
