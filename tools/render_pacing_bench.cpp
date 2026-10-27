// Synthetic scheduling workload using the actual production wait and signal classes.
// No game, SDL window, GPU context, guest RAM, or guest execution is required.
#include "runtime/native_render_wait.h"
#include "runtime/native_drain_signal.h"
#include <iostream>
#include <iomanip>
#include <thread>
#include <vector>
#include <algorithm>
#include <numeric>
using namespace nwii::runtime;
using Clock=native_render_wait::Clock;
using namespace std::chrono_literals;
static uint64_t ns(){return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count());}
static void distribution(const std::vector<double>& input){
    auto v=input;std::sort(v.begin(),v.end());
    const auto percentile=[&](double p){return v.empty()?0:v[size_t((v.size()-1)*p+.5)];};
    std::cout<<"{\"count\":"<<v.size()<<",\"mean\":"<<(v.empty()?0:std::accumulate(v.begin(),v.end(),0.0)/v.size())
             <<",\"p50\":"<<percentile(.5)<<",\"p95\":"<<percentile(.95)<<",\"p99\":"<<percentile(.99)<<",\"max\":"<<(v.empty()?0:v.back())<<'}';
}
static void run(unsigned fps,bool gx,double seconds){
    const unsigned frames=unsigned(seconds*fps),events=gx?unsigned(seconds*2500):0;
    const auto period=std::chrono::nanoseconds(1000000000ull/fps);
    std::atomic<bool> pending{false},producer_done{!gx};std::mutex mutex;std::condition_variable cv;
    native_drain::Signal signal(pending,mutex,cv);
    std::vector<uint64_t> publications(events+1);std::vector<double> wake_us,lateness_us,interval_us,generator_late_us;
    native_render_wait::Resources producer_timer(true);
    std::atomic<bool> start_producer{false};uint64_t calls=0,drains=0,last_generation=0;
    std::thread producer;
    if(gx)producer=std::thread([&]{
        while(!start_producer.load(std::memory_order_acquire))std::this_thread::yield();
        auto due=Clock::now();
        for(unsigned i=1;i<=events;++i){
            due+=400us;
#ifdef _WIN32
            auto remaining=std::chrono::duration_cast<std::chrono::nanoseconds>(due-Clock::now()).count();
            if(remaining>0&&producer_timer.ready()){
                LARGE_INTEGER timeout{};timeout.QuadPart=-std::max<int64_t>(1,remaining/100);
                if(SetWaitableTimer(producer_timer.timer,&timeout,0,nullptr,nullptr,FALSE))WaitForSingleObject(producer_timer.timer,1000);
            }
            else if(remaining>0)std::this_thread::sleep_until(due);
#else
            std::this_thread::sleep_until(due);
#endif
            const auto now=Clock::now();generator_late_us.push_back(std::max(0.0,std::chrono::duration<double,std::micro>(now-due).count()));
            // Immutable entry published before the production pending flag/mutex release.
            publications[i]=ns();signal.request();
        }
        producer_done.store(true,std::memory_order_release);
    });
#ifdef _WIN32
    const uint64_t cpu0=native_trace::cpu_us(GetCurrentThread());ULONG64 cycles0=0,cycles1=0;QueryThreadCycleTime(GetCurrentThread(),&cycles0);
#else
    const uint64_t cpu0=0;
#endif
    const auto begin=Clock::now();auto deadline=begin,previous=begin;start_producer.store(true,std::memory_order_release);
    unsigned completed=0;
    while(completed<frames||!producer_done.load(std::memory_order_acquire)||pending.load(std::memory_order_acquire)){
        // Same late-deadline policy as the current host loop; only the wait backend varies.
        deadline+=period;if(deadline<Clock::now())deadline=Clock::now();
        for(;;){
            auto now=Clock::now();if(now>=deadline)break;
            const auto receipt=signal.consume();
            if(receipt.pending){
                const auto observed=ns();const auto first=last_generation+1;
                if(first<=events)wake_us.push_back((observed-publications[first])/1000.0);
                last_generation=receipt.generation;++drains;native_render_wait::DrainStart();
                // Fixed small CPU processing cost, identical in both modes.
                const auto work_end=Clock::now()+50us;while(Clock::now()<work_end)std::atomic_signal_fence(std::memory_order_seq_cst);
                continue;
            }
            std::unique_lock<std::mutex> lock(mutex);++calls;
            native_render_wait::Wait(lock,cv,pending,deadline,std::min(deadline,now+2ms));
        }
        const auto end=Clock::now();lateness_us.push_back(std::max(0.0,std::chrono::duration<double,std::micro>(end-deadline).count()));
        interval_us.push_back(std::chrono::duration<double,std::micro>(end-previous).count());previous=end;++completed;
    }
    if(producer.joinable())producer.join();
    const auto receipt=signal.consume();
    if(receipt.pending){const auto observed=ns();const auto first=last_generation+1;if(first<=events)wake_us.push_back((observed-publications[first])/1000.0);last_generation=receipt.generation;++drains;native_render_wait::DrainStart();}
    if(last_generation!=events){std::cerr<<"Unconsumed publications: "<<events<<" / "<<last_generation<<'\n';std::exit(2);}
#ifdef _WIN32
    const double cpu_ms=(native_trace::cpu_us(GetCurrentThread())-cpu0)/1000.0;QueryThreadCycleTime(GetCurrentThread(),&cycles1);
    const uint64_t cycles=cycles1-cycles0;
#else
    const double cpu_ms=0;const uint64_t cycles=0;(void)cpu0;
#endif
    const double wall_ms=std::chrono::duration<double,std::milli>(Clock::now()-begin).count();
    std::cout<<"{\"mode\":\""<<native_render_wait::Mode()<<"\",\"fps\":"<<fps<<",\"gx\":"<<(gx?"true":"false")
             <<",\"wall_ms\":"<<wall_ms<<",\"render_cpu_ms\":"<<cpu_ms<<",\"render_cpu_percent_one_core\":"<<100*cpu_ms/wall_ms
             <<",\"render_cycles\":"<<cycles<<",\"fields\":"<<completed<<",\"wait_calls\":"<<calls<<",\"publications\":"<<events<<",\"drains\":"<<drains
             <<",\"deadline_lateness_us\":";distribution(lateness_us);std::cout<<",\"field_interval_us\":";distribution(interval_us);
    std::cout<<",\"request_to_consume_us\":";distribution(wake_us);std::cout<<",\"producer_schedule_lateness_us\":";distribution(generator_late_us);std::cout<<"}\n";
}
int main(int argc,char** argv){
#ifdef _WIN32
    // Explicit control: standalone default resolution can differ from SDL's process.
    // Dynamic loading keeps this benchmark independent of SDL and winmm link flags.
    HMODULE winmm=nullptr;using PeriodFn=UINT(WINAPI*)(UINT);PeriodFn end_period=nullptr;bool period_active=false;
    if(argc>2&&std::atoi(argv[2])==1){
        winmm=LoadLibraryW(L"winmm.dll");
        const auto begin_period=winmm?reinterpret_cast<PeriodFn>(GetProcAddress(winmm,"timeBeginPeriod")):nullptr;
        end_period=winmm?reinterpret_cast<PeriodFn>(GetProcAddress(winmm,"timeEndPeriod")):nullptr;
        period_active=begin_period&&end_period&&begin_period(1)==0;
        if(!period_active){std::cerr<<"Could not request 1 ms timer resolution\n";return 3;}
    }
#endif
    native_render_wait::Initialize();std::cout<<std::fixed<<std::setprecision(3);
    const double duration=argc>1?std::max(.2,std::atof(argv[1])):1.5;
    for(unsigned fps:{60u,120u})for(bool gx:{false,true})run(fps,gx,duration);
#ifdef _WIN32
    if(period_active)end_period(1);if(winmm)FreeLibrary(winmm);
#endif
}
