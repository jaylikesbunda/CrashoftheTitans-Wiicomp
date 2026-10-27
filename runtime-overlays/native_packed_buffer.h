#pragma once
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <utility>
namespace nwii::runtime::gx {
// NATIVE_PACKED_POOL_V1: only the final immutable owner returns its allocation.
// Exact-capacity reuse leaves draw/cache accounting unchanged. The fixed list
// requires no allocation on release; oversized and small blocks use delete[].
namespace native_packed_pool {
inline bool Enabled(){static const bool value=!std::getenv("NWII_NO_PACKED_POOL");return value;}
struct State {
 static constexpr size_t Slots=64,MinWords=4096,MaxWords=1024*1024,MaxBytes=32*1024*1024;
 struct Entry {float* data=nullptr;size_t count=0;};
 std::mutex mutex;std::array<Entry,Slots> entries{};size_t cached_bytes=0,next_evict=0;
 ~State(){for(auto& e:entries)delete[] e.data;}
 float* Take(size_t count){
  std::lock_guard<std::mutex> lock(mutex);
  for(auto& e:entries)if(e.data && e.count==count){float* result=e.data;e.data=nullptr;cached_bytes-=count*sizeof(float);return result;}
  return nullptr;
 }
 void Return(float* data,size_t count) noexcept {
  if(count<MinWords || count>MaxWords){delete[] data;return;}
  std::array<Entry,Slots> retired;size_t retired_count=0;
  {std::lock_guard<std::mutex> lock(mutex);
   for(;;){
    if(count*sizeof(float)<=MaxBytes-cached_bytes){bool stored=false;for(auto& e:entries)if(!e.data){e={data,count};cached_bytes+=count*sizeof(float);stored=true;break;}if(stored)break;}
    // Retire old free blocks when capacities change. Every victim is already
    // owner-free; dispose after unlocking, with no vector/allocation on return.
    for(size_t i=0;i<Slots;++i){const size_t slot=(next_evict+i)%Slots;auto& e=entries[slot];if(e.data){retired[retired_count++]=e;cached_bytes-=e.count*sizeof(float);e.data=nullptr;next_evict=(slot+1)%Slots;break;}}
   }
  }
  for(size_t i=0;i<retired_count;++i)delete[] retired[i].data;
 }
};
inline std::shared_ptr<State> SharedState(){static const auto state=std::make_shared<State>();return state;}
}
inline float* NativePackedNewWords(size_t count){return new float[count];}
__attribute__((noinline)) inline std::shared_ptr<float[]> NativePackedPooledWords(size_t count){
 auto state=native_packed_pool::SharedState();float* data=state->Take(count);
 if(!data)data=NativePackedNewWords(count);
 // The deleter owns the pool, so return is safe on another worker or during
 // shutdown after the function-static owner has already been destroyed.
 return std::shared_ptr<float[]>(data,[state=std::move(state),count](float* p) noexcept {state->Return(p,count);});
}
__attribute__((always_inline)) inline std::shared_ptr<float[]> NativePackedAllocateWords(size_t count){
 if(count<native_packed_pool::State::MinWords || count>native_packed_pool::State::MaxWords)
  return std::shared_ptr<float[]>(NativePackedNewWords(count));
 if(!native_packed_pool::Enabled())return std::shared_ptr<float[]>(NativePackedNewWords(count));
 return NativePackedPooledWords(count);
}
// Float objects are allocated without zero-fill. A completed decode writes every
// stored word before the buffer is published to the renderer.
class NativePackedBuffer {
 std::shared_ptr<float[]> words;size_t used=0,capacity=0;
 void detach(){if(words && !words.unique()){auto p=NativePackedAllocateWords(capacity);if(used)std::memcpy(p.get(),words.get(),used*sizeof(float));words=std::move(p);}}
public:
 NativePackedBuffer()=default;
 // Completed draws share immutable storage. A writer gets a private copy.
 NativePackedBuffer(const NativePackedBuffer& b)=default;
 NativePackedBuffer(NativePackedBuffer&& b) noexcept:words(std::move(b.words)),used(std::exchange(b.used,0)),capacity(std::exchange(b.capacity,0)){}
 NativePackedBuffer& operator=(NativePackedBuffer b) noexcept {swap(b);return *this;}
 void swap(NativePackedBuffer& b) noexcept {words.swap(b.words);std::swap(used,b.used);std::swap(capacity,b.capacity);}
 void resize(size_t n){if(n>capacity){size_t next=std::max(n,capacity*2);auto p=NativePackedAllocateWords(next);if(used)std::memcpy(p.get(),words.get(),used*sizeof(float));words=std::move(p);capacity=next;}used=n;}
 void clear(){used=0;}
 bool empty()const{return used==0;}
 size_t size()const{return used;}
 size_t allocated_bytes()const{return capacity*sizeof(float);}
 float* data(){detach();return words.get();}
 const float* data()const{return words.get();}
 const void* identity()const{return words.get();}
};
}
