#pragma once
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include "runtime/native_render_wait.h"
namespace nwii::runtime::native_drain {
// Publication shares the waiter's mutex. Once the predicate is checked false,
// a publisher cannot slip between that check and the atomic unlock/park.
class Signal {
 std::atomic<bool>& pending;std::mutex& mutex;std::condition_variable& cv;
 uint64_t generation=0,oldest=0,wake_generation=0;
 bool publish_locked(uint64_t now,uint64_t request_stamp,uint64_t lock_stamp,bool park_rearm){
  ++generation;const bool first=!pending.exchange(true,std::memory_order_release);
  if(first){oldest=now;wake_generation=generation;
   native_render_wait::At("gx.pacing.request",request_stamp,wake_generation,park_rearm?1:0);
   native_render_wait::At("gx.pacing.publish",lock_stamp,wake_generation,park_rearm?1:0);}
  return first;
 }
public:
 struct Receipt {bool pending=false;uint64_t generation=0,latency=0;};
 Signal(std::atomic<bool>& p,std::mutex& m,std::condition_variable& c):pending(p),mutex(m),cv(c){}
 // Requires the caller's wait mutex; used to arm bytes found at park entry.
 bool request_locked(uint64_t now=0){const uint64_t stamp=native_render_wait::Stamp();return publish_locked(now,stamp,stamp,true);}
 bool request(uint64_t now=0){
  const uint64_t stamp=native_render_wait::Stamp();bool first;uint64_t id=0;
  {std::lock_guard<std::mutex> lock(mutex);first=publish_locked(now,stamp,native_render_wait::Stamp(),false);if(first)id=wake_generation;}
  if(first){native_render_wait::Notify(id);cv.notify_one();native_render_wait::Mark("gx.pacing.notify_done",id);}
  return first;
 }
 Receipt consume(uint64_t now=0){
  const uint64_t lock_begin=native_render_wait::Stamp();
  std::lock_guard<std::mutex> lock(mutex);
  if(!pending.exchange(false,std::memory_order_acq_rel))return {};
  native_render_wait::At("gx.pacing.consume_begin",lock_begin,wake_generation,native_render_wait::current_wait);
  native_render_wait::Mark("gx.pacing.consume_lock",wake_generation,native_render_wait::current_wait);
  native_render_wait::consumed_request=wake_generation;
  Receipt result{true,generation,oldest && now>=oldest?now-oldest:0};oldest=0;return result;
 }
};
}
