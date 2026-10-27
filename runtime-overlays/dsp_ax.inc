// NATIVE_AX_DSP_V1: all DSP command effects occur on the guest CPU thread.
static NativeAX::Protocol& native_ax() {
    static NativeAX::Protocol processor([](uint32_t address,size_t size)->uint8_t* {
        if(!g_mmu)return nullptr;
        auto p=address&0x3fffffffu;
        auto& bank=p>=0x10000000u?g_mmu->mem2:g_mmu->mem1;
        size_t offset=p>=0x10000000u?p-0x10000000u:p;
        size_t limit=bank.size()-8;
        return offset<=limit&&size<=limit-offset?bank.data()+offset:nullptr;
    });
    return processor;
}
static std::vector<uint32_t> native_dsp_mails;
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
        auto& processor=native_ax();processor.Send(mail);
        auto& replies=processor.dsp.mail.pending;
        native_dsp_mails.insert(native_dsp_mails.end(),replies.begin(),replies.end());replies.clear();
        if(std::getenv("NWII_AX_PROBE")&&processor.command_frames && (processor.command_frames<=5||processor.command_frames%1000==0))
            std::cout<<"[AX] frames="<<processor.command_frames<<" last_mail="<<std::hex<<mail<<std::dec<<"\n";
        native_dsp_schedule_mail();
    }catch(const std::exception& error){
        std::cerr<<"[AX] DSP processing failed: mail="<<std::hex<<mail<<std::dec<<" "<<error.what()<<std::endl;
        // Unsupported work is never acknowledged as successful.
        std::exit(1);
    }
}
static void native_dsp_reset() {
    native_ax().Reset();native_dsp_mails.clear();
    dsp_mbox_dsp_hi=0x8071;dsp_mbox_dsp_lo=0xfeed; // ROM ready, polled by DSPBootTask.
}
