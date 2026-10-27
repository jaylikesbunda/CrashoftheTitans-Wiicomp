// Exercise the production signal and both production wait paths without booting the game.
#include "runtime/native_render_wait.h"
#include "runtime/native_drain_signal.h"
#include <iostream>
#include <thread>
#include <functional>
using namespace nwii::runtime;
using namespace std::chrono_literals;
static unsigned checks=0,failures=0;
static void check(bool ok,const char* what){++checks;if(!ok){++failures;std::cout<<"FAIL "<<what<<'\n';}}
int main(int argc,char** argv){
    native_render_wait::Initialize();
    const bool live=argc>1&&std::string(argv[1])=="--live";
    const char* control=std::getenv("NWII_RENDER_WAIT_FILE");
    check(!live||(control&&*control),"live fixture has a dedicated control file");
    if(live&&(!control||!*control))return 2;
    const auto write_control=[&](const char* text){std::ofstream file(std::filesystem::u8path(control),std::ios::binary|std::ios::trunc);file<<text;};
#ifdef _WIN32
    const HANDLE original_event=native_render_wait::State().event,original_timer=native_render_wait::State().timer;
#endif
    if(live){
        check(native_render_wait::State().ready(),"live fixture exercises actual timer handles");
        for(const char* invalid:{"","ti","bad\n","timer-and-some-more-data"}){
            write_control(invalid);native_render_wait::Poll(true);
            check(!native_render_wait::RequestedTimer(),"invalid control retains initial CV selection");
        }
    }
    const bool traced=native_trace::enabled();
    if(traced){native_trace::start_us=native_trace::now_us();native_trace::frame=1;native_trace::phase=2;native_trace::active=true;native_trace::local_buffer();}
    std::atomic<bool> pending{false};std::mutex mutex;std::condition_variable cv;
    native_drain::Signal signal(pending,mutex,cv);
    auto consume=[&]{auto r=signal.consume(native_trace::now_us());if(r.pending)native_render_wait::DrainStart();return r;};
    auto wait=[&](auto deadline,native_render_wait::Resources& resources)->bool {
        std::unique_lock<std::mutex> lock(mutex);
        const bool requested=native_render_wait::Wait(lock,cv,pending,deadline,std::min(deadline,native_render_wait::Clock::now()+2ms),resources);
        check(lock.owns_lock(),"wait returns with drain mutex owned");return requested;
    };
    check(signal.request(10),"first publication notifies");
    for(unsigned i=0;i<100;++i)check(!signal.request(11+i),"requests coalesce while pending");
    check(wait(native_render_wait::Clock::now()+100ms,native_render_wait::State()),"pre-published work bypasses sleeping");
    auto r=consume();check(r.pending&&r.generation==101,"coalesced generation is retained");
    check(!consume().pending,"consumption clears predicate once");
    // Any remembered event is allowed one harmless wake, never fabricated work.
    check(!wait(native_render_wait::Clock::now()+3ms,native_render_wait::State()),"stale event does not fabricate a GX request");
    check(!wait(native_render_wait::Clock::now()-1ms,native_render_wait::State()),"expired deadline never invents work");
    // Missing native handles must use the production CV path, even when timer is requested.
    native_render_wait::Resources unavailable(false);
    check(!wait(native_render_wait::Clock::now()+3ms,unavailable),"unavailable timer/event falls back to CV");
    // One persistent producer avoids exhausting the frame tracer's thread slots.
    std::mutex jobs_mutex;std::condition_variable jobs_cv;std::function<void()> job;bool done=false,stop=false;
    std::thread producer([&]{if(traced)native_trace::local_buffer();for(;;){std::function<void()> fn;{std::unique_lock<std::mutex> l(jobs_mutex);jobs_cv.wait(l,[&]{return stop||bool(job);});if(stop)return;fn=std::move(job);job={};}fn();{std::lock_guard<std::mutex> l(jobs_mutex);done=true;}jobs_cv.notify_one();}});
    auto dispatch=[&](std::function<void()> fn){std::lock_guard<std::mutex> l(jobs_mutex);done=false;job=std::move(fn);jobs_cv.notify_one();};
    auto joined=[&]{std::unique_lock<std::mutex> l(jobs_mutex);jobs_cv.wait(l,[&]{return done;});};
    // Publish before, during and after kernel-wait entry; a long field exposes lost events.
    for(unsigned i=0;i<160;++i){
        if(live){
            const bool enabled=i%2==0;
            // Include work published using the previous mode, before the new selection.
            if(i%3==0)signal.request(native_trace::now_us());
            write_control(enabled?"timer\n":"cv\n");native_render_wait::Poll(true);
            check(native_render_wait::RequestedTimer()==enabled,"live file changes selection without restarting");
#ifdef _WIN32
            check(native_render_wait::State().event==original_event&&native_render_wait::State().timer==original_timer,"live changes preserve notification handles");
#endif
        }
        std::unique_lock<std::mutex> lock(mutex);
        dispatch([&,i]{if(i%3)std::this_thread::sleep_for(std::chrono::microseconds(i%3==1?50:700));signal.request(native_trace::now_us());});
        const auto deadline=native_render_wait::Clock::now()+250ms;
        while(!pending.load()&&native_render_wait::Clock::now()<deadline){
            native_render_wait::Wait(lock,cv,pending,deadline,std::min(deadline,native_render_wait::Clock::now()+2ms));
            check(lock.owns_lock(),"racing wait reacquires drain mutex");
        }
        check(pending.load(),"publication cannot be lost across unlock/park");
        lock.unlock();joined();check(consume().pending,"racing request consumed");
    }
    // Deliberate post-notification mutex ownership exercises the separately measured lock stage.
    dispatch([&]{signal.request(native_trace::now_us());std::lock_guard<std::mutex> l(mutex);std::this_thread::sleep_for(3ms);});
    const auto deadline=native_render_wait::Clock::now()+250ms;
    while(!pending.load()&&native_render_wait::Clock::now()<deadline)wait(deadline,native_render_wait::State());
    joined();check(consume().pending,"request survives contended reacquisition");
    // Real deadlines must eventually return without requiring a producer notification.
    const auto begin=native_render_wait::Clock::now();const auto end=begin+8ms;
    while(native_render_wait::Clock::now()<end)check(!wait(end,native_render_wait::State()),"timer-only wait has no GX request");
    check(native_render_wait::Clock::now()-begin<250ms,"deadline completes without a lost timer");
    {std::lock_guard<std::mutex> l(jobs_mutex);stop=true;}jobs_cv.notify_one();producer.join();
    if(live){
        write_control("timer\r\n");native_render_wait::Poll(true);
        check(native_render_wait::RequestedTimer(),"live control accepts Windows line endings");
        std::filesystem::remove(std::filesystem::u8path(control));native_render_wait::Poll(true);
        check(native_render_wait::RequestedTimer(),"missing control retains last valid selection");
    }
    if(traced){native_trace::active=false;native_trace::stop_us=native_trace::now_us();native_trace::dump();}
    std::cout<<"RENDER_PACING mode="<<native_render_wait::Mode()<<" live="<<live<<" checks="<<checks<<" failures="<<failures<<"\n";
    return failures?1:0;
}
