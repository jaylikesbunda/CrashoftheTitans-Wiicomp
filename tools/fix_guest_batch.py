"""Reversible, bounded guest lowering after the existing runtime stages.

Typed leaves run earlier, before register promotion, in fix_hot_split.py.
This stage handles only eligible hot definitions; cold resume bodies are intact.
"""
from pathlib import Path
from collections import Counter
import argparse,base64,json,os,re,shutil
import guest_batch_lowering as lowering
import guest_loop_guards

MARK='NATIVE_GUEST_BATCH_V1'
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/',re.S)
HEAD=re.compile(r'^(?:static )?(?:NWII_GUEST_ABI )?(?:__attribute__\(\([^\n]*\)\)\s+)*void (func_|native_guest_primary_)([0-9A-F]{8})\(NWII_CTX_PARAM ctx\) \{',re.M)
INCLUDE='#include "runtime/native_guest_batch.h" // '+MARK+'\n'
LOOP_INCLUDE='#include "runtime/native_loop_span.h" // '+MARK+'\n'

def wrap(old,new):
    return '/* '+MARK+' '+base64.b64encode(old.encode('utf-8','surrogateescape')).decode()+' */\n'+new+'\n/* END_'+MARK+' */'

def restore(text):
    text=BLOCK.sub(lambda m:base64.b64decode(m[1]).decode('utf-8','surrogateescape'),text)
    return text.replace(INCLUDE,'').replace(LOOP_INCLUDE,'')

def scope_end(text,at):
    depth=1;i=at+1
    while depth and i<len(text):
        if text.startswith('//',i):
            i=text.find('\n',i)
            if i<0:raise ValueError('Unclosed scope')
            continue
        if text.startswith('/*',i):
            i=text.index('*/',i)+2;continue
        if text[i] in '\"\'':
            quote=text[i];i+=1
            while i<len(text):
                if text[i]=='\\':i+=2;continue
                if text[i]==quote:i+=1;break
                i+=1
            continue
        depth+=(text[i]=='{')-(text[i]=='}');i+=1
    if depth:raise ValueError('Unclosed scope')
    return i

def cpu_source(text):
    text=restore(text);methods=[]
    for name,ret in [('load','NativePsqPair'),('store','void')]:
        signature='  __attribute__((always_inline)) inline '+ret+' psq_'+('load_pair' if name=='load' else 'store_val')+'('
        a=text.index(signature);brace=text.index('{',a);end=scope_end(text,brace)
        method=text[a:end];gate='if (__builtin_expect('+('ld_type' if name=='load' else 'st_type')+' == 0, 1)) {'
        gate_at=method.index(gate)+len(gate)-1;body=method[gate_at+1:scope_end(method,gate_at)-1]
        head=method[:method.index('{')].replace('psq_load_pair','native_batch_psq_load_zero').replace('psq_store_val','native_batch_psq_store_zero')
        # Extract the production type-zero branch, including its RAM/FIFO paths.
        # No hand-maintained duplicate memory/publication implementation.
        methods.append(head+'{'+body+'\n  }')
    anchor='  // NATIVE_PSQ_QUANT_INLINE_V1: see tools/fix_psq_value_helpers.py'
    assert text.count(anchor)==1,'Requalify PSQ helper layout'
    return text.replace(anchor,wrap(anchor,'\n'.join(methods)+'\n'+anchor),1)

def guest_source(text,excluded=()):
    text=restore(text);stats=Counter();edits=[]
    for head in HEAD.finditer(text):
        if head[2] in excluded:continue
        end=scope_end(text,head.end()-1);old=text[head.start():end]
        # Public routing wrappers do not contain the normal execution body.
        if 'loc_'+head[2]+': ;' not in old:continue
        new=old
        # Existing cloned loops own their labels and proof; do not clone twice.
        if 'native_span_' not in new:
            new,n=guest_loop_guards.apply(new,clone=True,fuse=True,records=True)
            stats['record_loops']+=n
        # The guarded packed fixture regressed locally; keep its implementation
        # opt-in until its classification/packing overhead is reduced. There
        # are currently no matching arithmetic chains in the hot source set.
        new,counts=lowering.lower(new,packed=os.environ.get('NWII_BUILD_GUEST_PACKED_PS','0')=='1');stats.update(counts)
        if new!=old:edits.append((head.start(),end,wrap(old,new)))
    for a,b,new in reversed(edits):text=text[:a]+new+text[b:]
    if edits:text=INCLUDE+LOOP_INCLUDE+text
    stats['functions']=len(edits)
    return text,dict(stats)

def renderer_source(text):
    text=restore(text)
    old='const bool ordinary=reg==0 ||'
    new='const bool ordinary=reg==0 || reg==0x0f || reg==0xe8 ||'
    assert text.count(old)==1,'Requalify duplicate BP predicate'
    return text.replace(old,wrap(old,new),1)

def apply(port,undo=False):
    rt=port/'build/nwii-full-source/nWiiRuntime';changes=[];stats=Counter()
    enabled=os.environ.get('NWII_BUILD_GUEST_BATCH','1')!='0' and not undo
    excluded=set(json.loads((port/'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
    for path in sorted((port/'build/crash-hot').glob('hot_*.cpp')):
        old=path.read_text(errors='surrogateescape')
        if enabled:new,counts=guest_source(old,excluded);stats.update(counts)
        else:new=restore(old)
        changes.append((path,old,new))
    for rel,fn in [('include/runtime/cpu_context.h',cpu_source),('src/hle/gx/renderer_gl.cpp',renderer_source)]:
        path=rt/rel
        if not path.exists():continue
        old=path.read_text(errors='surrogateescape');new=fn(old) if enabled else restore(old)
        changes.append((path,old,new))
    if enabled:
        shutil.copy2(port/'runtime-overlays/native_guest_batch.h',rt/'include/runtime/native_guest_batch.h')
        shutil.copy2(port/'runtime-overlays/native_loop_span.h',rt/'include/runtime/native_loop_span.h')
    for path,old,new in changes:
        if old!=new:path.write_text(new,errors='surrogateescape')
    print('Guest batch '+('staged '+json.dumps(dict(stats),sort_keys=True) if enabled else 'restored/disabled'))
    return dict(stats)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--restore',action='store_true');a=ap.parse_args();apply(a.port,a.restore)
