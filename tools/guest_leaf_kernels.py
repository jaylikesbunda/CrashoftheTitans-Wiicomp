"""Produce register-local guest leaf kernels with conservative original fallback."""
from pathlib import Path
import re,json
import guest_baselines
PORT=Path(__file__).resolve().parents[1]
TARGETS=[('output_70.cpp','8038A700'),('output_70.cpp','8038A4F0'),('output_72.cpp','803A2A70'),('output_72.cpp','803A01EC')]
def original(file,addr):
    path=guest_baselines.path('leaf-before-'+addr+'.cpp')
    if path.exists():return path.read_text()
    s=(PORT/'build/crash-generated'/file).read_text();a=s.index('void func_'+addr+'(CPUContext& ctx) {');z=s.find('\nvoid func_',a+1)
    return s[a:z if z>=0 else len(s)]
def leaf_source(r):
    """Original translated source of family member `r`, verified against its qualified hash."""
    return guest_baselines.snapshot('leaf-before-'+r['address']+'.cpp',PORT/'build/crash-generated'/r['file'],r['address'],r['source_sha256'])
def plain_roots(body):
    """Reject pointer chasing, changing roots, nonconstant offsets and PSQ variants."""
    roots=set();frames=[]
    for line in body.splitlines():
        if 'ctx.mmu.' not in line and 'ctx.psq_' not in line:continue
        m=re.search(r'ctx\.mmu\.(\w+)\((.*)\)',line)
        if m:
            assert m[1] in ('read32','read64','read_f32','read_f64','write32','write64','write_f32','write_f64')
            address=m[2].split(',')[0].strip()
        else:
            m=re.search(r'ctx\.psq_(?:load|store)\(([^;]+)\);',line);assert m,line
            args=m[1].split(',');assert len(args)==4 and int(args[3])==0
            address=args[1].strip()
        if address=='ea':
            matches=re.findall(r'uint32_t ea = ctx\.gpr\[1\] \+ (-?\d+);',body)
            assert len(matches)==1 and -512<int(matches[0])<0
            roots.add(1);frames.append(int(matches[0]));continue
        m=re.fullmatch(r'ctx\.gpr\[(\d+)\](?: \+ (-?\d+))?',address);assert m,address
        roots.add(int(m[1]));assert abs(int(m[2] or 0))<512
    for root in roots:
        if root!=1:
            assert not re.search(r'ctx\.gpr\['+str(root)+r'\]\s*(?:[+*/-]?=|\+\+|--)',body),'Root pointer changes'
        else:
            deltas=[]
            for rhs in re.findall(r'ctx\.gpr\[1\] = ([^;]+);',body):
                assert rhs=='ea' or re.fullmatch(r'(?:\(uint32_t\)\()?ctx\.gpr\[1\] \+ -?\d+\)?',rhs),rhs
                if rhs!='ea':deltas.append(int(re.search(r'\+ (-?\d+)',rhs)[1]))
            assert sum(abs(x) for x in frames+deltas)<=1024
    assert roots
    return sorted(roots)
def candidate(body,addr,name,plain=False):
    special=PORT/'data/plain-leaf-special-fallbacks.json'
    if plain and special.exists() and addr in json.loads(special.read_text())['rejected']:
        return body.replace('void func_'+addr,'void '+name,1)
    assert 'process_pending_callbacks' not in body and not re.search(r'func_[0-9A-F]+\(ctx\)',body)
    assert set(re.findall(r'ctx\.([a-zA-Z0-9_]+)\(',body))<= {'require_fpu','psq_load','psq_store'}
    start=body.index('    if (ctx.pc != 0x'+addr+') {');brace=body.index('{',start);depth=1;end=brace+1
    while depth:
        depth+=(body[end]=='{')-(body[end]=='}');end+=1
    fast=body[:start]+body[end:]
    # One ordinary entry. The wrapper retains every original interior entry.
    fast=fast.replace('func_'+addr,name+'_kernel',1)
    banks={b:sorted(set(map(int,re.findall(r'ctx\.'+b+r'\[(\d+)\]',fast)))) for b in ('fpr','ps1','gpr')}
    for m in re.finditer(r'ctx\.psq_(?:load|store)\((\d+),([^;]+)\);',fast):
        for bank in ('fpr','ps1'):
            if int(m[1]) not in banks[bank]:banks[bank].append(int(m[1]))
    declarations=''.join(('uint32_t ' if b=='gpr' else 'double ')+b+str(i)+'=ctx.'+b+'['+str(i)+'];' for b,indices in banks.items() for i in sorted(indices))
    stores=''.join('ctx.'+b+'['+str(i)+']='+b+str(i)+';' for b,indices in banks.items() for i in sorted(indices))
    for bank in banks:fast=re.sub(r'ctx\.'+bank+r'\[(\d+)\]',lambda m:bank+m[1],fast)
    fast=fast.replace('ctx.inst_count','native_count')
    declarations+='auto native_count=ctx.inst_count;'
    stores+='ctx.inst_count=native_count;'
    fast=re.sub(r'if \(ctx\.require_fpu\(([^)]+)\)\) return;',r'if(!(ctx.msr&0x2000u)){native_flush();if(ctx.require_fpu(\1))return;}',fast)
    def psq(m):
        operation=m[1];args=m[2].split(',');assert len(args)==4
        register=int(args[0]);address=args[1].strip();W=int(args[2]);I=int(args[3]);assert W in (0,1) and I==0
        if operation=='load':
            code=f'fpr{register}=ctx.mmu.read_f32(native_ea);ps1{register}='+('ctx.mmu.read_f32(native_ea+4);' if W==0 else '1.;')
            # Flush if a read has observable MMIO effects.
        else:
            code=f'ctx.mmu.write_f32(native_ea,float(fpr{register}));'+(f'ctx.mmu.write_f32(native_ea+4,float(ps1{register}));' if W==0 else '')
        return '{uint32_t native_ea=uint32_t('+address+');'+code+'}'
    fast=re.sub(r'ctx\.psq_(load|store)\(([^;]+)\);',psq,fast)
    # Ordinary stores retain full context visibility for watch/MMIO callbacks.
    fast=fast.replace('ctx.mmu.','native_memory.')
    # A scope exit commits live registers on every normal/fault return.
    opening='(CPUContext& ctx) {'
    fast=fast.replace(opening,opening+'\n'+declarations+'\nauto native_flush=[&](){'+stores+'};\nNativeLeafCommit<decltype(native_flush)> native_commit{native_flush};NativeLeafMemory<decltype(native_flush)> native_memory{ctx.mmu,native_flush};',1)
    wrapper='void '+name+'(CPUContext& ctx){if(ctx.pc==0x'+addr+' && (ctx.msr&0x2000u) && ctx.gqr[0]==0 && !nwii::runtime::g_trace_calls)'+name+'_kernel(ctx);else func_'+addr+'(ctx);}\n'
    if plain:
        roots=plain_roots(body)
        fast=re.sub(r'if\(!\(ctx.msr&0x2000u\)\)\{native_flush\(\);if\(ctx.require_fpu\([^)]+\)\)return;\}','',fast)
        fast=fast.replace('NativeLeafMemory<decltype(native_flush)> native_memory{ctx.mmu,native_flush};','NativePlainMemory native_memory{ctx.mmu.mem1.data()};')
        guard='true /* NATIVE_LEAF_WATCH_SPAN_V1 */'
        for reg in roots:
            root='(ctx.gpr['+str(reg)+']&0x3fffffffu)'
            guard+=' && '+root+'>=2048 && '+root+'<ctx.mmu.mem1.size()-2056 && !nwii::runtime::native_watch_span_may_hit('+root+'-2048u,4104u)'
        guard+=' && !NativeLeafDisabled()'
        wrapper=wrapper.replace(' && !nwii::runtime::g_trace_calls',' && !nwii::runtime::g_trace_calls && '+guard)
        special=PORT/'data/plain-leaf-special-fallbacks.json'
        if special.exists() and addr in json.loads(special.read_text())['guarded']:
            assert 'goto loc_' not in fast,'Finite preflight requires straight-line stack evolution'
            conditions=[]
            for bank in ('fpr','ps1'):
                conditions += ['NativeLeafFiniteValue(ctx.'+bank+'['+str(i)+'])' for i in sorted(banks[bank])]
            delta=0;ea_delta=0;reads=set()
            for line in body.splitlines():
                m=re.search(r'uint32_t ea = ctx\.gpr\[1\] \+ (-?\d+);',line)
                if m:ea_delta=delta+int(m[1])
                for method,address in re.findall(r'ctx\.mmu\.(read_f32|read_f64)\(([^)]+)\)',line):
                    root=re.fullmatch(r'ctx\.gpr\[(\d+)\](?: \+ (-?\d+))?',address);assert root
                    reg=int(root[1]);offset=int(root[2] or 0)+(delta if reg==1 else 0);reads.add((reg,offset,4 if method=='read_f32' else 8))
                m=re.search(r'ctx\.psq_load\((\d+), ([^,]+), (\d), 0\)',line)
                if m:
                    root=re.fullmatch(r'ctx\.gpr\[(\d+)\](?: \+ (-?\d+))?',m[2]);assert root
                    reg=int(root[1]);offset=int(root[2] or 0)+(delta if reg==1 else 0);reads.add((reg,offset,4))
                    if m[3]=='0':reads.add((reg,offset+4,4))
                m=re.search(r'ctx\.gpr\[1\] = ([^;]+);',line)
                if m:
                    if m[1]=='ea':delta=ea_delta
                    else:delta+=int(re.search(r'\+ (-?\d+)',m[1])[1])
            for reg,offset,width in sorted(reads):
                expr='memory.read'+str(width*8)+'(ctx.gpr['+str(reg)+'] + '+str(offset)+')'
                conditions.append('('+expr+'&'+('0x7f800000u)!=0x7f800000u' if width==4 else '0x7ff0000000000000ull)!=0x7ff0000000000000ull'))
            helper='static inline bool NativeFinite_'+name+'(CPUContext& ctx){NativePlainMemory memory{ctx.mmu.mem1.data()};return '+' && '.join(conditions)+';}\n'
            wrapper=wrapper.replace(' && !NativeLeafDisabled()', ' && !NativeLeafDisabled() && NativeFinite_'+name+'(ctx)')
            fast=helper+fast
    return fast+'\n'+wrapper
PREFIX='''template<class F>struct NativeLeafCommit {F& flush;~NativeLeafCommit(){flush();}};
template<class F>struct NativeLeafMemory {
 nwii::runtime::MMU& mmu;F& flush;
 inline void observe(uint32_t a){if(__builtin_expect((a&0x3fffffffu)>=mmu.mem1.size()-8 || nwii::runtime::g_watch_addr!=0,0))flush();}
'''+''.join(' inline '+typ+' '+method+'(uint32_t a){observe(a);return mmu.'+method+'(a);}\n' for method,typ in [('read8','uint8_t'),('read16','uint16_t'),('read32','uint32_t'),('read64','uint64_t'),('read_f32','float'),('read_f64','double')])+''.join(' inline void '+method+'(uint32_t a,'+typ+' value){observe(a);mmu.'+method+'(a,value);}\n' for method,typ in [('write8','uint8_t'),('write16','uint16_t'),('write32','uint32_t'),('write64','uint64_t'),('write_f32','float'),('write_f64','double')])+'};\n'
PREFIX+='''struct NativePlainMemory {
 uint8_t* __restrict__ ram;
 inline uint32_t read32(uint32_t a){uint32_t v;memcpy(&v,ram+(a&0x3fffffffu),4);return __builtin_bswap32(v);}
 inline uint64_t read64(uint32_t a){uint64_t v;memcpy(&v,ram+(a&0x3fffffffu),8);return __builtin_bswap64(v);}
 inline float read_f32(uint32_t a){uint32_t v=read32(a);float f;memcpy(&f,&v,4);return f;}
 inline double read_f64(uint32_t a){uint64_t v=read64(a);double f;memcpy(&f,&v,8);return f;}
 inline void write32(uint32_t a,uint32_t v){v=__builtin_bswap32(v);memcpy(ram+(a&0x3fffffffu),&v,4);}
 inline void write64(uint32_t a,uint64_t v){v=__builtin_bswap64(v);memcpy(ram+(a&0x3fffffffu),&v,8);}
 inline void write_f32(uint32_t a,float f){uint32_t v;memcpy(&v,&f,4);write32(a,v);}
 inline void write_f64(uint32_t a,double f){uint64_t v;memcpy(&v,&f,8);write64(a,v);}
};
static inline bool NativeLeafDisabled(){static const bool disabled=std::getenv("NWII_NO_LEAF_KERNELS")!=nullptr;return disabled;}
static inline bool NativeLeafFiniteValue(double x){uint64_t bits;memcpy(&bits,&x,8);return (bits&0x7ff0000000000000ull)!=0x7ff0000000000000ull;}
'''
