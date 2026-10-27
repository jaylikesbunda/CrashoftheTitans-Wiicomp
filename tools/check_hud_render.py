"""Render the actual overlay at three sizes; verify sampler/viewport isolation and save previews."""
from pathlib import Path
from port_paths import default_storage
import json,subprocess
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
w=default_storage()/'hud-render-tests';w.mkdir(exist_ok=True)
code=r'''#define SDL_MAIN_HANDLED
#include "runtime/native_hud_gl.h"
#include <iostream>
#include <fstream>
int main(){SDL_SetMainReady();if(SDL_Init(SDL_INIT_VIDEO))return 2;
SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION,3);SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,SDL_GL_CONTEXT_PROFILE_CORE);
auto win=SDL_CreateWindow("HUD fixture",0,0,1920,1080,SDL_WINDOW_OPENGL|SDL_WINDOW_HIDDEN);auto ctx=SDL_GL_CreateContext(win);
if(!ctx||!gladLoadGLLoader((GLADloadproc)SDL_GL_GetProcAddress))return 3;
native_hud::on=true;native_hud::guest_tid=111;native_hud::render_tid=222;
native_guest_regions::fast_calls=137;native_guest_regions::legacy_calls=3;
{uint64_t t=0;native_stutter::Work w{100000,90000,7600000,410,540,2};native_stutter::Cost c{"gx.vertex_pack",'R',5,5};
 w.guest_wait={3,27,0,7100,420,170};w.field_wait={5,9,2,8000,2400,400};w.guest_context.pc=0x803bbfa0;w.guest_context.irq=0x100;
 for(unsigned i=0;i<60;++i){t+=33000;native_hud::stutters.observe({t,33,21,25,w},&c,1);}t+=74000;c.ms=22;w.vertices=180000;w.upload_bytes=13680000;native_hud::stutters.observe({t,74,29,34,w},&c,1);}
auto& s=native_hud::current;s.valid=true;s.new_fps=23.5;s.present_fps=49.2;s.xfb_fps=23.0;s.avg_ms=49.69;s.p95_ms=77.35;s.p99_ms=109.96;s.max_ms=124.56;s.guest_cpu_ms=26.63;s.render_cpu_ms=30.89;
for(unsigned i=0;i<120;++i)s.recent.push_back(35+double((i*37)%73));
s.rows={{"guest.idle_wait",111,13.755,52.4},{"host.field_wait",222,8.364,2.1},{"gx.vertex_pack",222,6.14,405.6},{"gx.decode",222,5.624,144.9},{"gx.decode",333,4.945,104.2},{"gx.decode",334,4.848,79.6},{"gx.decode",335,4.765,76.8},{"texture.lookup_hash",222,3.448,3244.5},{"gx.drain",222,3.088,18.8},{"gx.render",222,3.030,17.8},{"gx.parse",222,2.944,17.8},{"gx.driver_draw",222,2.519,405.6},{"gx.state",222,1.922,405.6},{"host.present",222,1.309,2.1}};
GLuint game_tex,game_sampler;glGenTextures(1,&game_tex);glGenSamplers(1,&game_sampler);
unsigned checks=0,failed=0;auto expect=[&](bool b,const char* n){++checks;if(!b){++failed;std::cout<<"FAIL "<<n<<'\n';}};
for(unsigned detail=0;detail<2;++detail)for(auto size:std::vector<std::pair<int,int>>{{960,720},{1266,970},{1920,1080}}){int W=size.first,H=size.second;native_hud_gl::detailed=detail;SDL_SetWindowSize(win,W,H);
 std::vector<unsigned char> reference;
 for(unsigned mode=0;mode<2;++mode){glViewport(7,11,W-17,H-21);glClearColor(.13f,.30f,.36f,1.f);glClear(GL_COLOR_BUFFER_BIT);
  glActiveTexture(GL_TEXTURE0);glBindTexture(GL_TEXTURE_2D,game_tex);
  glSamplerParameteri(game_sampler,GL_TEXTURE_MIN_FILTER,mode?GL_NEAREST_MIPMAP_NEAREST:GL_LINEAR);glSamplerParameteri(game_sampler,GL_TEXTURE_MAG_FILTER,mode?GL_NEAREST:GL_LINEAR);
  glSamplerParameterf(game_sampler,GL_TEXTURE_LOD_BIAS,mode?3.f:0.f);glBindSampler(0,game_sampler);glActiveTexture(GL_TEXTURE3);
  native_hud_gl::draw(W,H);
  GLint sampler=0,active=0,viewport[4];glGetIntegeri_v(GL_SAMPLER_BINDING,0,&sampler);glGetIntegerv(GL_ACTIVE_TEXTURE,&active);glGetIntegerv(GL_VIEWPORT,viewport);
  expect(sampler==GLint(game_sampler),"sampler restored");expect(active==GL_TEXTURE3,"active unit restored");expect(viewport[0]==7&&viewport[1]==11&&viewport[2]==W-17&&viewport[3]==H-21,"viewport restored");expect(glGetError()==GL_NO_ERROR,"GL error");
  std::vector<unsigned char> image(size_t(W)*H*4);glReadPixels(0,0,W,H,GL_RGBA,GL_UNSIGNED_BYTE,image.data());
  if(mode)expect(image==reference,"font independent of game sampler");else reference=image;
 }
 std::ofstream ppm((detail?"hud-detail-":"hud-")+std::to_string(H)+".ppm",std::ios::binary);ppm<<"P6\n"<<W<<' '<<H<<"\n255\n";
 for(int y=H-1;y>=0;--y)for(int x=0;x<W;++x)ppm.write(reinterpret_cast<const char*>(reference.data()+(size_t(y)*W+x)*4),3);
 std::cout<<"HUD_RENDER height="<<H<<" font_px="<<native_hud_gl::font.pixels<<" cell="<<native_hud_gl::font.cw<<'x'<<native_hud_gl::font.ch<<'\n';
}
{native_pipeline::saved_stores=~0ull;native_pipeline::requests=~0ull;native_pipeline::effect_fast=137;native_frame_limit::timestep_count=100;native_frame_limit::timestep_seconds=2;native_frame_limit::first_us=1000000;native_frame_limit::last_us=3000000;native_geometry::decoded_hits=~0ull;native_geometry::decoded_misses=~0ull;native_geometry::gpu_hits=~0ull;native_geometry::gpu_misses=~0ull;native_geometry::format_draws=~0ull;native_geometry::retained_rejects=~0ull;native_geometry::validated_bytes=~0ull;native_geometry::saved_upload_bytes=~0ull;
const auto json=native_hud_gl::report_json(s);std::ofstream report("hud-export.json");report<<json;report.close();++checks;if(json.find("\"fast_calls\":137")==std::string::npos || json.find("\"legacy_calls\":3")==std::string::npos)++failed;
const auto text=native_hud_gl::report_text(s);++checks;if(text.find("137 / legacy 3")==std::string::npos)++failed;
native_hud_gl::key(SDLK_F9);++checks;if(native_guest_regions::fast_calls.load() || native_guest_regions::legacy_calls.load() || native_pipeline::saved_stores.load() || native_pipeline::requests.load() || native_frame_limit::timestep_count.load())++failed;}
std::cout<<"HUD_RENDER_CHECKS "<<checks-failed<<'/'<<checks<<'\n';SDL_GL_DeleteContext(ctx);SDL_DestroyWindow(win);SDL_Quit();return failed?1:0;}
'''
cpp=w/'test.cpp';exe=w/'test.exe';cpp.write_text(code)
cmd=json.loads((p/'reports/gx-depth-mask-tests.json').read_text())[0]['command']
cmd=[str(cpp) if x.endswith(('gx-depth-tests/test.cpp','gx-depth-tests\\test.cpp')) else str(exe) if x.endswith(('gx-depth-tests/test.exe','gx-depth-tests\\test.exe')) else x for x in cmd]
cmd+=['-I',str(rt/'include')];rows=[]
for command in (cmd,[str(exe)]):
 r=subprocess.run(command,capture_output=True,text=True,timeout=180,cwd=w);rows.append(dict(command=command,exit_code=r.returncode,output=r.stdout+r.stderr));print((r.stdout+r.stderr)[-2500:],flush=True)
 if r.returncode:break
(p/'reports/hud-render-tests.json').write_text(json.dumps(rows,indent=2)+'\n')
if not r.returncode:
 exported=json.loads((w/'hud-export.json').read_text())
 assert exported['pipeline']['saved_stores']==(1<<64)-1
 assert exported['pipeline']['drain_requests']==(1<<64)-1
 assert exported['gameplay_timestep']['updates']==100 and exported['gameplay_timestep']['observed_seconds']==2
 assert exported['geometry_reuse']['decode_hits']==(1<<64)-1
 assert exported['geometry_reuse']['saved_upload_bytes']==(1<<64)-1
 from PIL import Image
 for file in w.glob('hud-*.ppm'):Image.open(file).save(file.with_suffix('.png'))
raise SystemExit(r.returncode)
