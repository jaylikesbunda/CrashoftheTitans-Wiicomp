#pragma once
#include <array>
#include <atomic>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <thread>
#include <vector>
#include "runtime/native_frame_trace.h"
namespace native_shader_cache {
// GL binary queries stay on the context owner. This worker only saves owned
// bytes; it never receives a GL object, game RAM pointer or renderer state.
class Writer {
 struct Job {std::filesystem::path path;std::array<char,24> header;std::vector<char> data;std::atomic<uint64_t>* successes;};
 std::mutex mutex;std::condition_variable wake,idle;std::deque<Job> jobs;
 std::thread thread;size_t bytes=0;bool stopping=false,busy=false;
 void run(){for(;;){Job job;{std::unique_lock<std::mutex> lock(mutex);wake.wait(lock,[&]{return stopping || !jobs.empty();});if(jobs.empty() && stopping)return;
   job=std::move(jobs.front());jobs.pop_front();busy=true;}
   auto temporary=job.path;temporary+=".tmp";bool saved=false;
   {native_trace::Scope write("shader.cache_disk_write");std::ofstream out(temporary,std::ios::binary|std::ios::trunc);
    if(out){out.write(job.header.data(),job.header.size());out.write(job.data.data(),job.data.size());out.close();saved=bool(out);}}
   std::error_code error;if(saved){native_trace::Scope rename("shader.cache_rename");std::filesystem::rename(temporary,job.path,error);saved=!error;}
   if(!saved){++failed;std::filesystem::remove(temporary,error);}else job.successes->fetch_add(1,std::memory_order_relaxed);
   {std::lock_guard<std::mutex> lock(mutex);bytes-=job.data.capacity();busy=false;if(jobs.empty())idle.notify_all();}
  }}
public:
 std::atomic<uint64_t> failed{0},skipped{0};
 bool can_accept(size_t size){std::lock_guard<std::mutex> lock(mutex);return !stopping && jobs.size()<128 && size<=(8u<<20) && bytes<=(8u<<20)-size;}
 bool enqueue(std::filesystem::path path,const void* header,std::vector<char> data,std::atomic<uint64_t>& success){
  std::lock_guard<std::mutex> lock(mutex);if(stopping || jobs.size()>=128 || data.capacity()>(8u<<20) || bytes>(8u<<20)-data.capacity()){++skipped;return false;}
  if(!thread.joinable()){try{thread=std::thread([this]{run();});}catch(const std::system_error&){++failed;return false;}}
  Job job{std::move(path),{},std::move(data),&success};std::memcpy(job.header.data(),header,24);bytes+=job.data.capacity();jobs.push_back(std::move(job));wake.notify_one();return true;
 }
 void flush(){std::unique_lock<std::mutex> lock(mutex);idle.wait(lock,[&]{return jobs.empty() && !busy;});}
 ~Writer(){ {std::lock_guard<std::mutex> lock(mutex);stopping=true;}wake.notify_one();if(thread.joinable())thread.join();}
};
}
