// NATIVE_VECTOR_DECODE_PLAN_V1: specialize invariant VAT choices once per draw.
struct NativeVectorPlan {
    using Reader = bool (*)(const NativeVectorPlan&, const std::vector<uint8_t>&, size_t&, size_t, float*);
    Reader reader;
    uint32_t base, stride;
    const uint8_t* span=nullptr; // NATIVE_INDEX_ARRAY_SPAN_V1
    void resolve_span(VtxAttrMask mask,int components,int type) {
        if(!nwii::runtime::g_mmu || (int(mask)!=2 && int(mask)!=3))return;
        const uint64_t physical=base&0x3fffffffu;
        const uint64_t last=uint64_t(int(mask)==2?255:65535)*stride;
        const uint64_t bytes=uint64_t(components)*(type<2?1:type<4?2:4);
        auto& m=*nwii::runtime::g_mmu;
        auto valid=[&](uint64_t start,const auto& bank){
            return start+last<bank.size()-8 && start+last+bytes<=bank.size();
        };
        if(physical<m.mem1.size()-8 && valid(physical,m.mem1))span=m.mem1.data()+physical;
        else if(physical>=0x10000000 && physical<0x10000000+m.mem2.size()-8 && valid(physical-0x10000000,m.mem2))span=m.mem2.data()+physical-0x10000000;
    }
    float scale;
    template<int Mask, int Type, int Components>
    static bool Read(const NativeVectorPlan& p, const std::vector<uint8_t>& fifo,
                     size_t& offset, size_t size, float* out) {
        if constexpr(Mask==0){for(int i=0;i<Components;++i)out[i]=0.0f;return true;}
        constexpr int width=Type<2?1:Type<4?2:4;
        const uint8_t* src=nullptr;
        if constexpr(Mask==1) {
            if(offset+width*Components>size)return false;
            src=fifo.data()+offset;offset+=width*Components;
        } else {
            constexpr unsigned bytes=Mask==2?1:2;
            if(offset+bytes>size)return false;
            const uint32_t index=bytes==1?fifo[offset]:((fifo[offset]<<8)|fifo[offset+1]);
            offset+=bytes;
            if(p.span)src=p.span+size_t(index)*p.stride;
            else if(nwii::runtime::g_mmu)src=nwii::runtime::g_mmu->get_ptr(p.base+index*p.stride);
            if(!src){for(int i=0;i<Components;++i)out[i]=0.0f;return true;}
        }
        for(int i=0;i<Components;++i) {
            const uint8_t* q=src+width*i;
            if constexpr(Type==0)out[i]=float(q[0])*p.scale;
            else if constexpr(Type==1)out[i]=float(int8_t(q[0]))*p.scale;
            else if constexpr(Type==2)out[i]=float(uint16_t((q[0]<<8)|q[1]))*p.scale;
            else if constexpr(Type==3)out[i]=float(int16_t((q[0]<<8)|q[1]))*p.scale;
            else {uint32_t w=(uint32_t(q[0])<<24)|(uint32_t(q[1])<<16)|(uint32_t(q[2])<<8)|q[3];std::memcpy(out+i,&w,4);}
        }
        return true;
    }
    template<int Mask,int Components>static Reader ChooseType(int type) {
        switch(type) {
        case 0:return Read<Mask,0,Components>;case 1:return Read<Mask,1,Components>;
        case 2:return Read<Mask,2,Components>;case 3:return Read<Mask,3,Components>;
        default:return Read<Mask,4,Components>;
        }
    }
    template<int Components>static Reader Choose(VtxAttrMask mask,VtxAttrType type) {
        switch(int(mask)) {
        case 0:return Read<0,4,Components>;case 1:return ChooseType<1,Components>(int(type));
        case 2:return ChooseType<2,Components>(int(type));default:return ChooseType<3,Components>(int(type));
        }
    }
    NativeVectorPlan():reader(Read<0,4,2>),base(0),stride(0),scale(1){}
    NativeVectorPlan(VtxAttrMask mask,VtxAttrType type,uint8_t shift,uint32_t b,uint32_t s,int n):base(b),stride(s),scale(1.0f/float(int32_t(uint32_t(1)<<shift))) {
        resolve_span(mask,n,int(type));
        switch(n) {case 1:reader=Choose<1>(mask,type);break;case 2:reader=Choose<2>(mask,type);break;
        case 3:reader=Choose<3>(mask,type);break;default:reader=Choose<9>(mask,type);break;}
    }
    bool read(const std::vector<uint8_t>& b,size_t& o,size_t size,float* out)const{return reader(*this,b,o,size,out);}
};
