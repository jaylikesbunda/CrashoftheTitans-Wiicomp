"""Bounded post-promotion lowering. Every observer ends the local fact region."""
import re
from collections import Counter

REG=r'(?:f|p)\d+'
LABEL=re.compile(r'^\s*([A-Za-z_]\w*):\s*;?\s*$')
BOOK=re.compile(r'^\s*(?:(?:\+\+(?:ic|ctx\.inst_count)|(?:ic|ctx\.inst_count)\s*\+=\s*\d+);)?\s*(?://.*)?$')
READ=re.compile(r'^(\s*)((?:[fp]\d+ = )+)((?:ctx\.mmu\.read_f32\(|native_fastmem::read_f32\(ctx\.mmu,)[^;]+);(.*)$')
PAIR=re.compile(r'^\s*\{\s*(f\d+) = (f\d+) ([+*\-]) (f\d+);\s*(p\d+) = (p\d+) \3 (p\d+);\s*\}\s*(?://.*)?$')
MOVE=re.compile(r'^\s*\{\s*(f\d+) = ([fp]\d+);\s*(p\d+) = ([fp]\d+);\s*\}\s*(?://.*)?$')

def fp_transport(text,selected=None):
    lines=text.splitlines();out=[];pending={};decl=[];count=0;serial=0
    targets=set(re.findall(r'\bgoto\s+([A-Za-z_]\w*)\s*;',text));needed=set()
    def flush(names=None):
        for name in list(pending):
            if names is None or name in names:out.append('    '+name+' = double('+pending.pop(name)[0]+');')
    for line in lines:
        label=LABEL.match(line)
        if label and label[1] not in targets:out.append(line);continue
        if label or re.search(r'\b(?:return|goto|if|switch|while|for)\b',line) or '{' in line or '}' in line:
            flush();out.append(line);continue
        load=READ.fullmatch(line)
        if load:
            destinations=re.findall(r'[fp]\d+',load[2]);used=set(re.findall(REG,load[3]))
            flush(used);serial+=1;shadow='native_f32_'+str(serial)
            if selected is not None and shadow not in selected:
                for name in destinations:pending.pop(name,None)
                out.append(line);continue
            safe='native_f32_safe_'+str(serial);decl.extend(['    float '+shadow+';','    bool '+safe+';'])
            out.append(load[1]+shadow+' = '+load[3]+';'+load[4])
            out.append('    '+safe+' = native_guest_batch::StoreSafe('+shadow+');')
            out.append('    if(!'+safe+'){'+''.join(name+' = double('+shadow+');' for name in destinations)+'}')
            for name in destinations:pending[name]=(shadow,safe)
            continue
        # Only substitute a complete F32-store value, never an arbitrary FP expression.
        replaced=[]
        if re.search(r'(?:ctx\.mmu\.|native_guest_fast::)write_f32\(',line):
            def store(m):
                nonlocal count
                name=m[1] or m[2]
                if name not in pending:return m[0]
                shadow,safe=pending[name];count+=1
                needed.add(shadow)
                value='('+safe+' ? '+shadow+' : float('+name+'))'
                replaced.append(value)
                return value
            line=re.sub(r'\(float\)\s*([fp]\d+)\b|\bfloat\(([fp]\d+)\)',store,line)
        use_line=line
        for value in replaced:use_line=use_line.replace(value,'0')
        uses=set(re.findall(REG,use_line))
        assignment=re.match(r'^\s*([fp]\d+) = ([fp]\d+);\s*(?://.*)?$',line)
        if assignment and assignment[2] in pending:
            shadow,safe=pending[assignment[2]]
            out.append('    if(!'+safe+') '+assignment[1]+' = '+assignment[2]+';')
            pending[assignment[1]]=(shadow,safe);continue
        # A simple overwrite discards the old deferred value; all other uses materialize.
        lhs=re.match(r'^\s*([fp]\d+)\s*=(?!=)',line)
        if lhs and lhs[1] not in re.findall(REG,line[lhs.end():]):
            pending.pop(lhs[1],None);uses.discard(lhs[1])
        flush(uses)
        calls=re.findall(r'([\w:.]+)\s*\(',line)
        pure={'float','double','uint32_t','int32_t','uint64_t','int64_t','std::fma','std::fabs','std::sqrt','std::rotl','native_guest_batch::StoreValue'}
        if re.search(r'\bctx\b(?!\.(?:mmu|inst_count)\b)',line) or any(c not in pure and not c.startswith(('ctx.mmu.','native_guest_fast::write')) for c in calls):flush()
        out.append(line)
    flush()
    if not count:return text,0
    # Do not add guards or shadows to unrelated lfs instructions in a function
    # that happens to contain one eligible transport elsewhere.
    if selected is None:return fp_transport(text,needed)
    result='\n'.join(out);at=result.index('{')+1
    return result[:at]+'\n'+'\n'.join(decl)+result[at:],count

def packed_pairs(text):
    """Keep several independent-lane arithmetic operations packed as two doubles."""
    lines=text.splitlines();targets=set(re.findall(r'\bgoto\s+([A-Za-z_]\w*)\s*;',text));out=[];i=0;changed=0
    while i<len(lines):
        if not (PAIR.match(lines[i]) or MOVE.match(lines[i])):out.append(lines[i]);i+=1;continue
        end=i;ops=[]
        while end<len(lines) and len(ops)<6:
            m=PAIR.match(lines[end]);move=MOVE.match(lines[end]);label=LABEL.match(lines[end])
            if m:
                # Preserve lane identities; do not combine cross-lane sequential dependencies.
                if any(m[a][1:]!=m[b][1:] for a,b in ((1,5),(2,6),(4,7))):break
                ops.append(m);end+=1
            elif move and move[1][1:]==move[3][1:]:ops.append(move);end+=1
            elif BOOK.match(lines[end]) or (label and label[1] not in targets):end+=1
            else:break
        if len(ops)<4:out.append(lines[i]);i+=1;continue
        values={};valid={};dirty=set();generated=[];inputs=set();number=0
        def get(reg,mask=3):
            nonlocal number
            if reg not in values:
                number+=1;v='native_v'+str(number);values[reg]=v;valid[reg]=0
                generated.append('    native_pair '+v+' = {0., 0.};')
            for lane in (0,1):
                if mask&(1<<lane) and not valid[reg]&(1<<lane):
                    scalar=('f' if lane==0 else 'p')+reg
                    generated.append('    '+values[reg]+'['+str(lane)+'] = '+scalar+';')
                    inputs.add((reg,lane));valid[reg]|=1<<lane
            return values[reg]
        for line in lines[i:end]:
            if LABEL.match(line):line='    // '+line.strip()
            m=PAIR.match(line)
            if not m:
                move=MOVE.match(line)
                if not move:generated.append(line);continue
                d=move[1][1:];dest=get(d,0);left=get(move[2][1:],1<<int(move[2][0]=='p'))
                generated.append('    '+dest+'[0] = '+left+'['+str(int(move[2][0]=='p'))+'];')
                valid[d]|=1
                # Resolve the second lane after the first write. Merge helpers
                # are sequential when D aliases a cross-lane source.
                right=get(move[4][1:],1<<int(move[4][0]=='p'));generated.append('    '+dest+'[1] = '+right+'['+str(int(move[4][0]=='p'))+'];');valid[d]|=2;dirty.add(d)
                continue
            d,a,b=m[1][1:],m[2][1:],m[4][1:];left,right=get(a),get(b)
            if d not in values:number+=1;values[d]='native_v'+str(number);generated.append('    native_pair '+values[d]+';')
            generated.append('    '+values[d]+' = '+left+' '+m[3]+' '+right+';');valid[d]=3;dirty.add(d)
        # Bound every intermediate away from overflow/invalid operations. Six
        # repeated squarings are the worst growth in a six-operation region.
        exponent=900//(1<<len(ops))-1
        guard=' && '.join('native_guest_batch::PackedInput('+('f' if lane==0 else 'p')+index+','+str(exponent)+')' for index,lane in sorted(inputs,key=lambda x:(int(x[0]),x[1])))
        out.append('    if('+guard+'){\n    using native_pair = double __attribute__((vector_size(16)));')
        out.extend(generated)
        for index in sorted(dirty,key=int):out.append('    f'+index+' = '+values[index]+'[0]; p'+index+' = '+values[index]+'[1];')
        out.append('    }else{');out.extend('    // '+line.strip() if LABEL.match(line) else line for line in lines[i:end]);out.append('    }')
        changed+=len(ops);i=end
    return '\n'.join(out),changed

def split_args(value):
    result=[];start=depth=0
    for i,c in enumerate(value):
        if c in '([{':depth+=1
        elif c in ')]}':depth-=1
        elif c==',' and not depth:result.append(value[start:i].strip());start=i+1
    result.append(value[start:].strip());return result

def psq_regions(text):
    """Clone straight-line PSQ regions; format tests occur outside the fast lane."""
    rows=text.splitlines();out=[];region=[];count=0;targets=set(re.findall(r'\bgoto\s+([A-Za-z_]\w*)\s*;',text))
    call=re.compile(r'ctx\.(psq_load_pair|psq_store_val)\(([^;]+)\)')
    def emit():
        nonlocal count
        raw='\n'.join('    // '+line.strip() if LABEL.match(line) else line for line in region);matches=list(call.finditer(raw));formats={};plans=[]
        if len(matches)<2 or len(matches)>16 or len(raw)>16000:out.extend(region);region.clear();return
        for m in matches:
            args=split_args(m[2]);load=m[1]=='psq_load_pair'
            if len(args)!=(3 if load else 5) or not re.fullmatch('[01]',args[-2]) or not re.fullmatch('[0-7]',args[-1]):out.extend(region);region.clear();return
            if any(n not in {'uint32_t','int32_t','uint64_t','int64_t','float','double'} for n in re.findall(r'([\w:.]+)\s*\(',m[2])):out.extend(region);region.clear();return
            index=int(args[-1]);formats[index]=formats.get(index,0)|(0x70000 if load else 7)
            replacement=('ctx.native_batch_psq_load_zero' if load else 'ctx.native_batch_psq_store_zero')+'('+m[2]+')'
            plans.append((m.start(),m.end(),replacement))
        fast=raw
        for a,b,replacement in reversed(plans):fast=fast[:a]+replacement+fast[b:]
        guard=' && '.join('(ctx.gqr['+str(index)+']&0x'+format(mask,'x')+'u)==0' for index,mask in sorted(formats.items()))
        out.append('    if('+guard+'){');out.extend(fast.splitlines());out.append('    }else{');out.extend(raw.splitlines());out.append('    }')
        count+=len(matches);region.clear()
    for line in rows:
        # Generated PSQ calls use a self-contained scope. No declaration escapes it.
        allowed_call=bool(call.search(line))
        ordinary_read=bool(re.fullmatch(r'\s*(?:g\d+|[fp]\d+|ctrv|ic) = (?:(?:ctx\.mmu\.read(?:8|16|32|64|_f32|_f64)\([^;]+)|(?:native_fastmem::read(?:8|16|32|64|_f32|_f64)\(ctx\.mmu,[^;]+));\s*(?://.*)?',line))
        if ordinary_read:
            # These existing MMU scalar readers do not change GQR. Arbitrary
            # calls, writes, CPU-register accesses and declarations still end
            # the format region, just as the FPU must-analysis requires.
            names=re.findall(r'([\w:.]+)\s*\(',line.split('//',1)[0])
            ordinary_read=all(n.startswith(('ctx.mmu.read','native_fastmem::read')) or n in {'uint32_t','int32_t'} for n in names)
            expression=line.split('=',1)[1].split('//',1)[0].replace('ctx.mmu.','').replace('ctx.mmu,','')
            ordinary_read=ordinary_read and not re.search(r'\bctx\b|\+\+|--|(?<![=!<>])=(?!=)',expression)
        if allowed_call:
            rest=call.sub('0',line)
            if re.search(r'\bctx\b|\b(?:if|goto|return|switch|while|for)\b',rest):emit();out.append(line);continue
        label=LABEL.match(line)
        if label and label[1] not in targets:region.append(line);continue
        boundary=LABEL.match(line) or re.search(r'\b(?:if|goto|return|switch|while|for|func_\w+|process_\w+)\b',line)
        names=re.findall(r'([\w:.]+)\s*\(',line)
        opaque=any(n not in {'uint32_t','int32_t','uint64_t','int64_t','float','double'} for n in names)
        if not allowed_call and not ordinary_read and (boundary or opaque or '{' in line or '}' in line or re.search(r'\bctx\b|\b(?:auto|bool|double|float|uint\w+|int\w+)\s+\w+',line)):
            emit();out.append(line)
        else:region.append(line)
    emit();return '\n'.join(out),count

def carry_regions(text):
    """Keep XER.CA independent in straight-line arithmetic runs, then commit once."""
    rows=text.splitlines();out=[];run=[];changed=0;serial=0;targets=set(re.findall(r'\bgoto\s+([A-Za-z_]\w*)\s*;',text))
    update=re.compile(r'\bxer = \(xer & ~\(1u? << 29\)\) \| ([^;]+);')
    extract=re.compile(r'\(\(xer\s*>>\s*29\)\s*&\s*1(?:u)?\)|\(xer\s*>>\s*29\)\s*&\s*1(?:u)?')
    def emit():
        nonlocal changed,serial
        raw='\n'.join(run)
        writes=list(update.finditer(raw))
        if len(writes)<2:out.extend(run);run.clear();return
        # Accept masked carry extraction or explicit 0/CA alternatives only.
        def proved(value):
            if '<< 29' not in value:return False
            if re.fullmatch(r'\(\(\(\w+ >> \d+\) & 1u?\) << 29\)',value):return True
            if re.fullmatch(r'\(\([^;()]+\) \? \(1u << 29\) : 0u\)',value):return True
            local=re.fullmatch(r'\((\w+) << 29\)',value)
            if not local:return False
            symbol=re.escape(local[1]);assignments=re.findall(r'\b'+symbol+r'\s*=(?!=)\s*([^;]+);',raw)
            return bool(assignments and all(re.search(r'\? 1 : 0$',v) for v in assignments) and not re.search(r'\b'+symbol+r'\s*(?:\+\+|--|[-+*/&|^]=)',raw))
        if any(not proved(m[1]) for m in writes):out.extend(run);run.clear();return
        other=update.sub('',extract.sub('0',raw))
        if re.search(r'\bxer\b',other):out.extend(run);run.clear();return
        serial+=1;name='native_ca_'+str(serial)
        transformed=extract.sub(name,raw)
        transformed=update.sub(lambda m:name+' = (uint32_t('+m[1]+') >> 29)&1u;',transformed)
        out.extend(['    '+name+' = (xer>>29)&1u;',transformed,'    xer = (xer & ~(1u<<29)) | (('+name+'&1u)<<29);'])
        changed+=len(writes);run.clear()
    i=0
    while i<len(rows):
        chunk=[rows[i]];code=rows[i].split('//',1)[0];depth=code.count('{')-code.count('}');i+=1
        # Generated arithmetic instruction scopes may span several lines.
        if depth==1 and code.strip()=='{':
            while depth and i<len(rows):
                chunk.append(rows[i]);part=rows[i].split('//',1)[0];depth+=part.count('{')-part.count('}');i+=1
            code='\n'.join(line.split('//',1)[0] for line in chunk)
        line='\n'.join(chunk);label=LABEL.match(line)
        calls=re.findall(r'([\w:.]+)\s*\(',code)
        opaque=any(n not in {'uint32_t','int32_t','uint64_t','int64_t','bool'} for n in calls)
        # Each accepted instruction scope is self-contained. Never
        # move a commit outside a lexical scope, branch, call or function.
        if (label and label[1] in targets) or opaque or code.count('{')!=code.count('}') or re.search(r'\b(?:void|goto|return|if|switch|while|for|ctx|func_\w+)\b',code):emit();out.append(line)
        else:run.append(line)
    emit();result='\n'.join(out)
    if changed:
        at=result.index('{')+1;result=result[:at]+'\n    uint32_t '+', '.join('native_ca_'+str(n) for n in range(1,serial+1))+';'+result[at:]
    return result,changed

def condition_demand(text):
    """Delete only pure CR-bit definitions overwritten before any local reader."""
    rows=text.splitlines();out=[];run=[];changed=0
    cref=re.compile(r'\bc[0-7](?:lt|gt|eq|so)\b')
    assign=re.compile(r'\b(c[0-7](?:lt|gt|eq|so)) = ([^;]+);')
    targets=set(re.findall(r'\bgoto\s+([A-Za-z_]\w*)\s*;',text))
    def emit():
        nonlocal changed
        live=set(cref.findall('\n'.join(run)));result=[]
        for line in reversed(run):
            matches=list(assign.finditer(line));tail=len(line);parts=[]
            for m in reversed(matches):
                # Comparisons over integer locals only, with no arithmetic
                # division, call, memory access or volatile/FP observation.
                value=m[2];tokens=re.findall(r'\b[A-Za-z_]\w*\b',value)
                pure=not re.search(r'[.*/%\[\]]|\+\+|--|(?<![=!<>])=(?!=)',value) and all(re.fullmatch(r'(?:g\d+|ctrv|xer|c[0-7](?:lt|gt|eq|so)|int32_t|uint32_t|true|false|[uUlL]|x[0-9a-fA-F]+)',n) for n in tokens)
                calls=re.findall(r'(\w+)\s*\(',value)
                pure=pure and all(n in {'int32_t','uint32_t'} for n in calls)
                live.update(cref.findall(line[m.end():tail]));parts.append(line[m.end():tail])
                if pure and m[1] not in live:changed+=1
                else:
                    parts.append(m[0]);live.discard(m[1]);live.update(cref.findall(value))
                tail=m.start()
            live.update(cref.findall(line[:tail]));parts.append(line[:tail]);result.append(''.join(reversed(parts)))
        out.extend(reversed(result));run.clear()
    for line in rows:
        label=LABEL.match(line);code=line.split('//',1)[0]
        if (label and label[1] in targets) or code.count('{')!=code.count('}') or re.search(r'\b(?:ctx|if|goto|return|switch|while|for)\b',code):emit();out.append(line)
        else:run.append(line)
    emit();return '\n'.join(out),changed

def lower(text,packed=True):
    stats=Counter()
    # Region cloning runs before FP transport so raw shadows do not cross cloned scopes.
    for name,fn in [('psq_sites',psq_regions),('paired_ops',packed_pairs),('carry_updates',carry_regions),('condition_definitions',condition_demand),('fp_transports',fp_transport)]:
        if name=='paired_ops' and not packed:stats[name]=0;continue
        text,n=fn(text);stats[name]+=n
    return text,dict(stats)
