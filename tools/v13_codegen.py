"""Reversible guest code optimizations from the V12/V13 runtime pipeline."""
import base64,re

MARK='NATIVE_V13_CODEGEN_V1'
LINE=re.compile(r'^/\* '+MARK+r' ([A-Za-z0-9+/=]+|-) \*/[^\n]*(?:\n|$)',re.M)
HEAD=re.compile(r'^[^;{}\n]*\bvoid (?:func_[0-9A-F]{8}\w*|native_guest_(?:primary|resume|continuation)_[0-9A-F]{8}\w*)\([^\n]*\) \{\s*$')
READS=re.compile(r'(\w+) = ctx\.(gpr|fpr|ps1|cr|ctr|xer|inst_count)(?:\[(\d+)\])?(?:\.(lt|gt|eq|so))?;')
CALL=re.compile(r'ctx\.pc = 0x([0-9A-F]{8}); func_([0-9A-F]{8})\(ctx\);')
DECL=re.compile(r'^\s*(?:uint32_t|uint64_t|int32_t|double|float|bool) ([^;]+);\s*$')
NONVOL={f'r{i}' for i in range(14,32)}|{f'f{i}' for i in range(14,32)}|{'cr2','cr3','cr4'}
GUARD=re.compile(r'^\s*if \((?:__builtin_expect\()?ctx\.require_fpu\(0x[0-9A-F]+\)(?:, 0\))?\) (?:return;|goto exit_\d+;|\{ ic -= 1; goto exit_\d+; \}) // MSR\.FP exception\s*$')
LABEL=re.compile(r'^(\w+): ;')
BARRIER=re.compile(r'[A-Za-z_][\w:]*\(ctx\b|msr|interpret|syscall|\brfi\b')
STORE=re.compile(r'ctx\.mmu\.(write8|write16|write32|write64|write_f32|write_f64)\((g1|ctx\.gpr\[1\]) ([+-])')
EA=re.compile(r'^\s*uint32_t ea = (g1|ctx\.gpr\[1\]) [+-] [-0-9x]+;\s*$')
EA_STORE=re.compile(r'ctx\.mmu\.(write8|write16|write32|write64|write_f32|write_f64)\(ea,')
TAIL=re.compile(r'^(\s*)ctx\.pc = (0x[0-9A-F]{8}|ctx\.ctr|ctrv); (return;|goto (exit_\d+);)\s*$')
COMMENT=re.compile(r'^\s*// 0x([0-9A-F]{8}): ([0-9A-F]{8})')

def restore(text):
    while LINE.search(text):
        text=LINE.sub(lambda m:'' if m[1]=='-' else base64.b64decode(m[1]).decode('utf-8',errors='surrogateescape'),text)
    return text

def record(old,new):
    encoded=base64.b64encode(old.encode('utf-8',errors='surrogateescape')).decode() if old else '-'
    return '/* '+MARK+' '+encoded+' */'+new.rstrip('\r\n')+'\n'

def resource(kind,index):
    return {'gpr':'r','fpr':'f','ps1':'f','cr':'cr'}.get(kind,kind)+(index if kind in ('gpr','fpr','ps1','cr') else '')

def trim_body(body,summaries,stats):
    declared=set();d=1
    while d<len(body) and (DECL.match(body[d]) or body[d].lstrip().startswith('NWII_TRACE_CALL') or not body[d].strip()):
        match=DECL.match(body[d])
        if match:
            for part in match[1].split(','):
                var=re.match(r'\s*(\w+)',part)
                if var:declared.add(var[1])
        d+=1
    inits={}
    for k in range(1,len(body)-2):
        call=CALL.search(body[k])
        if not call:continue
        target=int(call[2],16)
        if target not in summaries:continue
        line=body[k+1];parts=list(READS.finditer(line))
        if not parts or READS.sub('',line).strip():continue
        ret=re.fullmatch(r'\s*if \(ctx\.pc != (0x[0-9A-F]+)\) return;\s*',body[k+2])
        if not ret:continue
        keep=[];drop=[]
        for part in parts:
            var,kind,index,bit=part.groups();res=resource(kind,index or '')
            if var in declared and res in NONVOL and res not in summaries[target]:drop.append(part)
            else:keep.append(part[0])
        if not drop:continue
        checks=[]
        for part in drop:
            var=part[1];inits[var]=part[0]
            context=part[0].split('= ',1)[1][:-1]
            checks.append('native_guest_fast::Check('+var+','+context+');')
        new='    '+' '.join(keep)+' if(native_guest_fast::verify_trim && ctx.pc=='+ret[1]+'){'+''.join(checks)+'}'
        body[k+1]=record(line,new);stats['reloads']+=len(drop)
    if inits:body.insert(d,record('','    '+' '.join(inits.values())))
    return body

def fp_body(body,stats):
    referenced=set(re.findall(r'goto (\w+)', ''.join(body)))
    known=False
    for n,line in enumerate(body):
        label=LABEL.match(line)
        if label and label[1] in referenced:known=False;continue
        if GUARD.match(line):
            if known:body[n]=record(line,'    // Dominated MSR.FP guard.');stats['fp_guards']+=1
            else:known=True
            continue
        # MMU accessors cannot change MSR; callback/guest calls can.
        observed=re.sub(r'native_fastmem::read\w+\(ctx\.mmu,','read(',line)
        if BARRIER.search(observed) and not observed.lstrip().startswith('//'):known=False
    return body

def branches(text,starts,overrides,wpad,stats):
    lines=text.splitlines(keepends=True)
    for n,line in enumerate(lines):
        match=TAIL.match(line)
        instruction=COMMENT.match(lines[n-1]) if n else None
        if not match or not instruction:continue
        pc,word=int(instruction[1],16),int(instruction[2],16)
        indirect=word==0x4e800420 and match[2] in ('ctx.ctr','ctrv')
        if indirect:call='nwii::runtime::native_dispatch_nested(ctx);'
        else:
            if word>>26!=18 or word&1 or not match[2].startswith('0x'):continue
            delta=word&0x03fffffc
            if delta&0x02000000:delta-=0x04000000
            target=(delta if word&2 else pc+delta)&0xffffffff
            if target!=int(match[2],16) or target not in starts or target in overrides or (wpad and wpad[0]<=target<=wpad[1]):continue
            call='++ctx.inst_count; func_'+format(target,'08X')+'(ctx);'
        writeback=''
        if match[4]:
            end=next((j for j in range(n+1,len(lines)) if lines[j].strip()==match[4]+': ;'),None)
            if end is None or end+1>=len(lines) or not lines[end+1].rstrip().endswith('return;'):continue
            writeback=lines[end+1].strip()[:-len('return;')].strip()
            if 'goto ' in writeback or 'return' in writeback:continue
        new=match[1]+'ctx.pc = '+match[2]+'; { '+writeback+' if(native_guest_fast::TailEnter()){'+call+' native_guest_fast::TailLeave();} return; }'
        lines[n]=record(line,new);stats['nested' if indirect else 'tails']+=1
    return ''.join(lines)

def transform(text,summaries,starts,overrides,wpad,hot=False,stack=False):
    text=restore(text)
    stats=dict(reloads=0,fp_guards=0,tails=0,nested=0,stack=0)
    lines=text.splitlines(keepends=True);out=[];n=0
    while n<len(lines):
        if not HEAD.match(lines[n]):out.append(lines[n]);n+=1;continue
        end=n+1
        while end<len(lines) and not lines[end].startswith('}'):end+=1
        body=fp_body(lines[n:end+1],stats)
        if hot:body=trim_body(body,summaries,stats)
        out.extend(body);n=end+1
    text=branches(''.join(out),starts,overrides,wpad,stats)
    if stack:
        lines=text.splitlines(keepends=True)
        for n,line in enumerate(lines):
            new,count=STORE.subn(lambda m:'native_guest_fast::'+m[1]+'(ctx.mmu,'+m[2]+' '+m[3],line)
            if not count and n and EA.match(lines[n-1]):new,count=EA_STORE.subn(lambda m:'native_guest_fast::'+m[1]+'(ctx.mmu,ea,',line)
            if count:lines[n]=record(line,new);stats['stack']+=count
        text=''.join(lines)
    return text,stats

def function_span(text,name):
    start=text.index('void '+name+'(')
    end=text.index('\n}\n}',start)+2
    return start,end,text[start:end]

def dispatch_source(text,fast,poll,replace):
    # Keep normalization, region-specific WPAD dispatch, HLE and interpreter
    # behavior in the generated dispatcher rather than duplicating fixed IDs.
    _,_,legacy=function_span(text,'native_call_indirect')
    nested=legacy.replace('void native_call_indirect(','void native_dispatch_nested(',1)
    assert nested.count('    process_pending_callbacks(ctx);')==1
    nested=nested.replace('    process_pending_callbacks(ctx);\n','',1)
    anchor='// NATIVE_INDIRECT_SITE_CACHE_V1 begin'
    text=replace(text,anchor,'namespace nwii::runtime {\n'+nested+'\n}\n'+anchor)
    loop='    while (ctx.is_running) {\n'
    text=replace(text,loop,loop+'      native_guest_fast::tail_depth=0; // recovery/dispatcher boundary\n')
    if fast:
        struct=re.search(r'^struct NativeIndirectSite \{.*?^\};\n',text,re.M|re.S)
        assert struct,'Indirect cache structure moved'
        text=replace(text,struct[0],'')
        array=re.search(r'^static NativeIndirectSite g_indirect_sites\[\d+\];',text,re.M)
        assert array,'Indirect cache array moved'
        text=replace(text,array[0],array[0].replace('static ', '',1))
        _,_,cached=function_span(text,'native_call_indirect_cached')
        due=cached.replace('void native_call_indirect_cached(','void native_call_indirect_due(',1)
        assert due.count(poll)==1
        due=due.replace(poll,'process_pending_callbacks(ctx);')
        text=replace(text,cached,cached+'\n'+due)
    return text
