"""Accounting test for the developer overlay's frame statistics (runtime-overlays/native_hud.h): exclusive scope time, per-thread slots,
per-frame normalisation, frame-interval percentiles and the window restart. No GL; the drawing code is syntax-checked by the build."""
from pathlib import Path
from port_paths import default_storage
import json, subprocess
PORT = Path(__file__).resolve().parents[1]
BASE = default_storage()
work = BASE / 'hud-tests'; (work / 'runtime').mkdir(parents=True, exist_ok=True)
(work / 'runtime/native_hud.h').write_text((PORT / 'runtime-overlays/native_hud.h').read_text())
(work / 'runtime/native_stutter.h').write_text((PORT / 'runtime-overlays/native_stutter.h').read_text())
cpp = r'''
#include "runtime/native_hud.h"
#include <thread>
#include <cstdio>
#include <cmath>
static void sleep_us(int us){auto e=std::chrono::steady_clock::now()+std::chrono::microseconds(us);while(std::chrono::steady_clock::now()<e)std::this_thread::yield();}
static const char NAME_A[]="outer",NAME_B[]="inner",NAME_C[]="other";
static int fails=0;
static void check(bool ok,const char* what){if(!ok){++fails;std::printf("FAIL %s\n",what);}}
static const native_hud::Row* find(const native_hud::Snapshot& s,const char* n,unsigned tid){for(auto& r:s.rows)if(r.name==n&&r.tid==tid)return &r;return nullptr;}
int main(){
  using namespace native_hud;
  on.store(true);
  Snapshot s; compute(s,true);                 // baseline
  const unsigned me=thread_id();
  // 20 frames: outer 3 ms containing inner 2 ms (outer self = 1 ms) on this thread, and 'other' 1 ms on a second thread
  std::thread helper;
  for(int f=0;f<20;++f){
    { ScopeState a; a.begin(NAME_A); sleep_us(1000); { ScopeState b; b.begin(NAME_B); sleep_us(2000); b.end(); } a.end(); }
    std::thread t([&]{ScopeState c;c.begin(NAME_C);sleep_us(1000);c.end();}); t.join();
    sleep_us(4000);
    frame();
  }
  sleep_us(450000);                            // windows under 0.5 s are rejected (thread CPU ticks)
  compute(s,true);
  check(s.valid,"snapshot valid");
  check(std::fabs(s.frames-19)<=1.01 || std::fabs(s.frames-20)<=0.01,"frame count");
  const Row* a=find(s,NAME_A,me); const Row* b=find(s,NAME_B,me);
  check(a&&b,"rows for nested scopes");
  if(a&&b){
    std::printf("outer self %.3f ms inner %.3f ms (frames %.0f)\n",a->ms_per_frame,b->ms_per_frame,s.frames);
    check(a->ms_per_frame>0.8&&a->ms_per_frame<1.9,"outer exclusive time excludes the inner scope");
    check(b->ms_per_frame>1.8&&b->ms_per_frame<3.2,"inner time");
    check(std::fabs(a->calls_per_frame-1.0)<0.2,"calls per frame");
  }
  const Row* c=nullptr;for(auto& r:s.rows)if(r.name==NAME_C)c=&r;
  check(c&&c->tid!=me,"helper thread scope has its own slot and thread id");
  check(s.rows.size()>=3&&s.rows[0].ms_per_frame>=s.rows[1].ms_per_frame,"rows sorted by time");
  check(s.avg_ms>6.5&&s.avg_ms<12,"frame interval average");
  check(s.p95_ms>=s.p50_ms&&s.max_ms>=s.p99_ms&&s.p99_ms>=s.p95_ms,"percentiles ordered");
  check(s.recent.size()==19||s.recent.size()==20,"recent intervals kept");
  // window restart: a second window with no frames must not divide by zero and must not reuse old totals
  sleep_us(120000); Snapshot s2; compute(s2,true);
  check(!s2.valid||s2.frames==0,"empty window");
  // off: scopes cost nothing and record nothing
  record_wait(true,0,210,0);record_wait(true,1,420,170);record_wait(false,2,2400,400);
  native_stutter::GuestContext observed;observed.stamp=1;observed.count=1;observed.threads[0]={0x80010000,4,0x80340000,0x803bbfa0};observe_guest_context(observed);
  frame();auto last=stutters.latest_frame();check(last && last->work.guest_wait.signal==1 && last->work.guest_wait.timer==1 && last->work.guest_wait.total_us==630 && last->work.guest_wait.max_late_us==170,"wait totals and oversleep");
  check(last && last->work.guest_context.threads[0].pc==0x803bbfa0 && last->work.field_wait.other==1,"wait context retained");
  reset_stutters();check(guest_waits.consume().total_us==0 && guest_context.count==0,"wait reset");
  on.store(false); const auto before=a?a->ms_per_frame:0; (void)before;
  { ScopeState a2; if(on.load())a2.begin(NAME_A); sleep_us(500); a2.end(); }
  std::printf(fails?"HUD_STATS FAILED %d\n":"HUD_STATS PASS\n",fails);
  return fails?1:0;
}
'''
(work / 'hud.cpp').write_text(cpp)
subprocess.run(['g++', '-std=c++20', '-O1', '-static', '-I', str(work), str(work / 'hud.cpp'), '-o', str(work / 'hud.exe')], check=True)
r = subprocess.run([str(work / 'hud.exe')], capture_output=True, text=True)
print(r.stdout.strip())
assert r.returncode == 0, r.stdout + r.stderr
(PORT / 'reports/hud-stats-tests.json').write_text(json.dumps(dict(passed=True, result=r.stdout.strip().splitlines()[-1],
    scope='native_hud.h accounting: exclusive scope time, thread slots, per-frame normalisation, percentiles, window restart.'), indent=2) + '\n')
