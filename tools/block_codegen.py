"""Bounded pure-register lowering; retain original instruction-entry paths."""
from pathlib import Path
import hashlib,json,re,os
PORT=Path(__file__).resolve().parents[1]
from port_paths import default_storage
REPORTS=default_storage()/'backend-reports'
def record_report(name,value):
    REPORTS.mkdir(parents=True,exist_ok=True);out=REPORTS/name
    out.write_text(json.dumps(value,indent=2)+'\n')
    summary={k:v for k,v in value.items() if k not in ('blocks','results','regions_metadata')}
    summary['artifact']=str(out);summary['sha256']=hashlib.sha256(out.read_bytes()).hexdigest()
    (PORT/'data'/name).write_text(json.dumps(summary,indent=2)+'\n')
def load_report(name):
    summary=json.loads((PORT/'data'/name).read_text());portable=PORT/'reference/backend-reports'/name
    packed=portable.with_name(name+'.gz')   # the repository ships the evidence tables gzip-compressed
    if packed.is_file():
        import gzip;data=gzip.decompress(packed.read_bytes());out=packed
    else:
        out=portable if portable.is_file() else REPORTS/name
        if not portable.is_file():assert summary['artifact']==str(out),name+' artifact moved'
        data=out.read_bytes()
    import region
    if region.active().reference:assert summary['sha256']==hashlib.sha256(data).hexdigest(),name+' artifact changed'
    return json.loads(data.decode())
REG=re.compile(r'ctx\.(gpr|fpr|ps1)\[(\d+)\]')
FAULT=re.compile(r'    if \(ctx\.require_fpu\(0x[0-9A-Fa-f]+\)\) return;[^\n]*\n')
LABEL=re.compile(r'^loc_([0-9A-Fa-f]{8}): ;\n',re.M)

def function_ranges(text):
    # Erase comments/strings without changing offsets, then count real braces.
    clean=re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"',lambda m:' '*len(m[0]),text)
    for m in re.finditer(r'^void func_([0-9A-Fa-f]{8})\(CPUContext& ctx\) \{',clean,re.M):
        pos=m.end();depth=1
        while depth and pos<len(clean):
            depth+=(clean[pos]=='{')-(clean[pos]=='}');pos+=1
        if depth:raise ValueError('Incomplete generated function '+m[1])
        yield m[1].upper(),m.start(),pos

def eligible(body):
    fast=FAULT.sub('',body)
    if any(x in fast for x in ('goto ','return','ctx.mmu','ctx.ps_','func_','process_pending','interpret_','trace_','native_','std::exit','longjmp','Config::','std::getenv')):return False
    fields=set(re.findall(r'ctx\.([a-zA-Z0-9_]+)',fast))
    if not fields<=set(('gpr','fpr','ps1','cr','xer','ctr','lr','inst_count','msr')):return False
    if re.search(r'ctx\.(?:msr|lr)\s*=(?!=)',fast):return False
    # Only literal register indices; all loops, calls and memory stay original.
    if any(x in fast for x in ('for (','for(','while (','while(','switch (','switch(')):return False
    return True

def blocks(text,address,start,end):
    body=text[start:end];labels=list(LABEL.finditer(body));runs=[];current=[]
    def finish():
        nonlocal current
        if len(current)>=4:
            a=current[0][0];b=current[-1][1];raw=body[a:b]
            used=sorted(set((bank,int(n)) for bank,n in REG.findall(raw)))
            if used and len(used)<=20:
                dirty=sorted({(m[1],int(m[2])) for m in re.finditer(r'ctx\.(gpr|fpr|ps1)\[(\d+)\]\s*(?:=(?!=)|\+=|-=|\*=|/=|&=|\|=|\^=|<<=|>>=|\+\+|--)',raw)})
                if dirty:runs.append(dict(function=address,address=current[0][2],start=start+a,end=start+b,body=raw,instructions=len(current),used=used,dirty=dirty,fp=bool(FAULT.search(raw)),sha256=hashlib.sha256(raw.encode()).hexdigest()))
        current=[]
    for i,m in enumerate(labels[:-1]):
        a=m.start();b=labels[i+1].start();raw=body[a:b]
        if eligible(raw):
            trial=''.join(x[3] for x in current)+raw
            if len(current)>=16 or len(set(REG.findall(trial)))>20:finish()
            current.append((a,b,m[1].upper(),raw))
        else:finish()
    finish();return runs

def discover():
    found=[]
    for file in sorted((PORT/'build/crash-generated').glob('output_*.cpp')):
        s=file.read_text()
        if 'NATIVE_BOUNDED_BLOCKS_V1' in s:raise ValueError('Discovery requires unstaged blocks: '+file.name)
        for addr,a,b in function_ranges(s):
            for r in blocks(s,addr,a,b):r['file']=file.name;found.append(r)
    return found

def helper(r,name=None):
    name=name or 'native_block_'+r['address']
    body=LABEL.sub('',FAULT.sub('',r['body']))
    declarations=''.join(('uint32_t ' if bank=='gpr' else 'double ')+bank+str(n)+'=ctx.'+bank+'['+str(n)+'];' for bank,n in r['used'])
    stores=''.join('ctx.'+bank+'['+str(n)+']='+bank+str(n)+';' for bank,n in r['dirty'])
    body=REG.sub(lambda m:m[1]+m[2],body).replace('ctx.inst_count','native_count')
    return 'static inline void '+name+'(CPUContext& ctx){\n'+declarations+'auto native_count=ctx.inst_count;\n'+body+'\n'+stores+'ctx.inst_count=native_count;\n}\n'

def metadata(r):return {k:v for k,v in r.items() if k!='body'}

if __name__=='__main__':
    rows=discover()
    record_report('bounded-block-candidates.json',dict(blocks=[metadata(r) for r in rows],count=len(rows),instructions=sum(r['instructions'] for r in rows),functions=len({r['function'] for r in rows}),game_launched=False))
    print('Bounded pure-register regions:',len(rows),'across',len({r['function'] for r in rows}),'functions;',sum(r['instructions'] for r in rows),'guest instructions')
