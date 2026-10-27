"""AX command lists run on a worker thread instead of inside the guest's DSP-mailbox write.

Capture frame-cost-20261006-221021: the guest thread spent 78 of its 2,913 samples (about 0.9 ms per frame) inside the DSP mailbox write handler,
NativeAX::Protocol::Send -> AXWiiUCode::HandleCommandList -> ProcessPBList (PCM mixing).

The DSP is a separate processor on real hardware: the CPU writes the command-list address, the DSP mixes, then answers with a YIELD mail and an
interrupt. This emulation already delivers those replies through the guest's virtual-time event scheduler, 1 ms of guest time after the write.
So the mixing can run concurrently with the guest for that millisecond and be joined by the delivery event; the guest observes the same mails at
the same guest times:
  * Protocol::Send posts `Execute` + the YIELD mail as a job (state machine and everything else stay on the guest thread);
  * the job is joined (and its mails queued, in the order a synchronous run produced them) by the delivery event 1 ms later, by the next
    mailbox write, and by a DSP reset, whichever comes first;
  * the worker only touches guest RAM the same way the synchronous code did (parameter blocks, sample and output buffers); the guest cannot
    legitimately read those before the YIELD mail, exactly as on hardware.
NWII_NO_AUDIO_THREAD=1 restores the synchronous behaviour. A failed job is reported at its join and exits like the synchronous failure did.
Idempotent.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
rt = p / 'build/nwii-full-source/nWiiRuntime'
MARK = 'NATIVE_AX_WORKER_V1'


def edit(path, steps):
    s = path.read_text(errors='surrogateescape')
    if MARK in s:
        return
    for old, new in steps:
        assert s.count(old) == 1, (path.name, old[:60], s.count(old))
        s = s.replace(old, new)
    path.write_text(s, errors='surrogateescape')


edit(rt / 'include/runtime/native_ax/protocol.h', [
    ("  u32 crc=0;\n  unsigned command_frames=0;\n",
     "  u32 crc=0;\n  unsigned command_frames=0;\n"
     "  std::function<void(std::function<void()>)> async_exec; // NATIVE_AX_WORKER_V1: runs a command list off the guest thread\n"
     "  bool job_posted=false;\n"),
    ("      ax->Execute(mail,command_size); // Produces SYNC only after actual PCM output.\n"
     "      ++command_frames;\n"
     "      dsp.mail.PushMail(0xdcd10002,true); // YIELD after sample/history writeback.\n"
     "      state=State::Next;return;\n",
     "      {\n"
     "        const u32 list=mail;const u16 size=command_size;\n"
     "        auto work=[this,list,size]{\n"
     "          ax->Execute(list,size); // Produces SYNC only after actual PCM output.\n"
     "          ++command_frames;\n"
     "          dsp.mail.PushMail(0xdcd10002,true); // YIELD after sample/history writeback.\n"
     "        };\n"
     "        if(async_exec){async_exec(std::move(work));job_posted=true;}else work();\n"
     "      }\n"
     "      state=State::Next;return;\n"),
])

old_block = """static std::vector<uint32_t> native_dsp_mails;
static bool native_mail_scheduled=false;
static void native_dsp_schedule_mail() {
    if(native_mail_scheduled||native_dsp_mails.empty()||(dsp_mbox_dsp_hi&0x8000))return;
    native_mail_scheduled=true;
    EventScheduler::get().schedule_after(std::max<uint64_t>(1,g_ctx_ptr->tb_freq/1000),[](CPUContext&,uint64_t){
        native_mail_scheduled=false;
        if(native_dsp_mails.empty()||(dsp_mbox_dsp_hi&0x8000))return;
        auto mail=native_dsp_mails.front();native_dsp_mails.erase(native_dsp_mails.begin());
        dsp_mbox_dsp_hi=uint16_t(mail>>16);dsp_mbox_dsp_lo=uint16_t(mail);
        dsp_control|=0x0080;dsp_update_pi();
    });
}
static void native_dsp_send(uint32_t mail) {
    try {
        native_trace::Scope trace_audio("audio.ax_mail");
        auto& processor=native_ax();processor.Send(mail);
        auto& replies=processor.dsp.mail.pending;
        native_dsp_mails.insert(native_dsp_mails.end(),replies.begin(),replies.end());replies.clear();
        if(std::getenv("NWII_AX_PROBE")&&processor.command_frames && (processor.command_frames<=5||processor.command_frames%1000==0))
            std::cout<<"[AX] frames="<<processor.command_frames<<" last_mail="<<std::hex<<mail<<std::dec<<"\\n";
        native_dsp_schedule_mail();
    }catch(const std::exception& error){
        std::cerr<<"[AX] DSP processing failed: mail="<<std::hex<<mail<<std::dec<<" "<<error.what()<<std::endl;
        // Unsupported work is never acknowledged as successful.
        std::exit(1);
    }
}
static void native_dsp_reset() {
    native_ax().Reset();native_dsp_mails.clear();
"""

new_block = """static std::vector<uint32_t> native_dsp_mails;
static bool native_mail_scheduled=false;
// NATIVE_AX_WORKER_V1: see tools/fix_audio_worker.py
struct NativeAudioWorker {
    std::mutex mutex;
    std::condition_variable wake, idle;
    std::function<void()> job;
    bool has_job=false, busy=false, started=false;
    std::string error;
    void post(std::function<void()> work) {
        std::unique_lock<std::mutex> lock(mutex);
        idle.wait(lock,[&]{return !busy;});
        if(!started){started=true;std::thread([this]{loop();}).detach();}
        job=std::move(work);has_job=true;busy=true;
        wake.notify_one();
    }
    void wait() {
        std::unique_lock<std::mutex> lock(mutex);
        idle.wait(lock,[&]{return !busy;});
    }
    std::string failure() {std::lock_guard<std::mutex> lock(mutex);return error;}
    void loop() {
        for(;;){
            std::function<void()> work;
            {std::unique_lock<std::mutex> lock(mutex);wake.wait(lock,[&]{return has_job;});work=std::move(job);has_job=false;}
            std::string failed;
            try{work();}catch(const std::exception& e){failed=e.what();}catch(...){failed="unknown exception";}
            {std::lock_guard<std::mutex> lock(mutex);if(!failed.empty())error=failed;busy=false;}
            idle.notify_all();
        }
    }
};
static NativeAudioWorker& native_audio_worker() {static NativeAudioWorker* worker=new NativeAudioWorker;return *worker;}
static bool native_audio_inflight=false;
static void native_dsp_deliver_one() {
    if(native_dsp_mails.empty()||(dsp_mbox_dsp_hi&0x8000))return;
    auto mail=native_dsp_mails.front();native_dsp_mails.erase(native_dsp_mails.begin());
    dsp_mbox_dsp_hi=uint16_t(mail>>16);dsp_mbox_dsp_lo=uint16_t(mail);
    dsp_control|=0x0080;dsp_update_pi();
}
static void native_dsp_schedule_mail() {
    if(native_mail_scheduled||native_dsp_mails.empty()||(dsp_mbox_dsp_hi&0x8000))return;
    native_mail_scheduled=true;
    EventScheduler::get().schedule_after(std::max<uint64_t>(1,g_ctx_ptr->tb_freq/1000),[](CPUContext&,uint64_t){
        native_mail_scheduled=false;
        native_dsp_deliver_one();
    });
}
// Finish the posted command list and queue the mails it produced, in the order a synchronous run produced them.
static void native_dsp_join_audio() {
    if(!native_audio_inflight)return;
    auto& worker=native_audio_worker();
    worker.wait();
    native_audio_inflight=false;
    const std::string failed=worker.failure();
    if(!failed.empty()){
        std::cerr<<"[AX] DSP processing failed: "<<failed<<std::endl;
        std::exit(1);
    }
    auto& replies=native_ax().dsp.mail.pending;
    native_dsp_mails.insert(native_dsp_mails.end(),replies.begin(),replies.end());replies.clear();
}
static void native_dsp_prepare_send() {
    auto& processor=native_ax();
    static const bool threaded=std::getenv("NWII_NO_AUDIO_THREAD")==nullptr;
    if(threaded&&!processor.async_exec)processor.async_exec=[](std::function<void()> work){native_audio_worker().post(std::move(work));};
    native_dsp_join_audio();   // the previous command list must be finished (and its mails queued) before the protocol runs again
    processor.job_posted=false;
}
static void native_dsp_send(uint32_t mail) {
    try {
        native_dsp_prepare_send();
        native_trace::Scope trace_audio("audio.ax_mail");
        auto& processor=native_ax();processor.Send(mail);
        if(processor.job_posted){
            native_audio_inflight=true;
            // The guest sees the replies where the synchronous version delivered them: one millisecond of guest time after the write.
            EventScheduler::get().schedule_after(std::max<uint64_t>(1,g_ctx_ptr->tb_freq/1000),[](CPUContext&,uint64_t){
                native_dsp_join_audio();
                if(!native_mail_scheduled)native_dsp_deliver_one();
            });
            return;
        }
        auto& replies=processor.dsp.mail.pending;
        native_dsp_mails.insert(native_dsp_mails.end(),replies.begin(),replies.end());replies.clear();
        static const bool ax_probe=std::getenv("NWII_AX_PROBE")!=nullptr;
        if(ax_probe&&processor.command_frames && (processor.command_frames<=5||processor.command_frames%1000==0))
            std::cout<<"[AX] frames="<<processor.command_frames<<" last_mail="<<std::hex<<mail<<std::dec<<"\\n";
        native_dsp_schedule_mail();
    }catch(const std::exception& error){
        std::cerr<<"[AX] DSP processing failed: mail="<<std::hex<<mail<<std::dec<<" "<<error.what()<<std::endl;
        // Unsupported work is never acknowledged as successful.
        std::exit(1);
    }
}
static void native_dsp_reset() {
    native_dsp_join_audio();
    native_ax().Reset();native_dsp_mails.clear();
"""

edit(rt / 'src/hw/hw_dsp.cpp', [
    ('#include "runtime/event_scheduler.h"\n',
     '#include "runtime/event_scheduler.h"\n#include <thread>\n#include <condition_variable>\n#include <functional>\n#include <string>\n'),
    (old_block, new_block),
])
print('audio worker installed')
