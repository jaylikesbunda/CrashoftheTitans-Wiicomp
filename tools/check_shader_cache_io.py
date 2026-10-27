"""Owned bytes, bounded queue, write errors and shutdown drain without any GL context."""
from pathlib import Path
from port_paths import default_storage
import subprocess,json,os
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime';w=default_storage()/'shader-io-tests';w.mkdir(exist_ok=True)
code=r'''#include "runtime/native_shader_cache_io.h"
#include <iostream>
#include <cstring>
int main(){std::atomic<uint64_t> successes{0};unsigned checks=0;std::filesystem::create_directories("files");char header[24];for(unsigned i=0;i<24;++i)header[i]=char(i);
 {native_shader_cache::Writer writer;
  for(unsigned i=0;i<80;++i){std::vector<char> bytes(16384,char(i));if(!writer.enqueue(std::filesystem::path("files")/(std::to_string(i)+".bin"),header,std::move(bytes),successes))return 1;}
  writer.flush();if(successes!=80)return 2;
  if(writer.can_accept(9u<<20))return 3;++checks;
  if(!writer.enqueue("files/missing/file.bin",header,std::vector<char>(40),successes))return 4;writer.flush();if(writer.failed!=1)return 5;++checks;
  for(unsigned i=80;i<100;++i)if(!writer.enqueue(std::filesystem::path("files")/(std::to_string(i)+".bin"),header,std::vector<char>(16384,char(i)),successes))return 6;
 } // destructor must save the remaining bytes before releasing the success counter
 if(successes!=100)return 7;++checks;
 for(unsigned i=0;i<100;++i){std::ifstream f(std::filesystem::path("files")/(std::to_string(i)+".bin"),std::ios::binary);std::vector<char> got(16408);f.read(got.data(),got.size());if(!f || memcmp(got.data(),header,24))return 8;for(size_t j=24;j<got.size();++j)if(got[j]!=char(i))return 9;++checks;}
 std::cout<<"SHADER_IO "<<checks<<'/'<<checks<<'\n';}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code);rows=[]
for cmd in (['g++','-std=c++20','-O2','-static','-I',str(rt/'include'),str(cpp),'-o',str(exe)],[str(exe)]):
 env={k:v for k,v in os.environ.items() if not k.startswith('NWII_')};r=subprocess.run(cmd,cwd=w,capture_output=True,text=True,env=env,timeout=120);rows.append(dict(command=cmd,exit_code=r.returncode,output=r.stdout+r.stderr));print((r.stdout+r.stderr)[-2500:],flush=True)
 if r.returncode:break
(p/'reports/shader-cache-io-tests.json').write_text(json.dumps(rows,indent=2)+'\n');raise SystemExit(r.returncode)
