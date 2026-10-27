"""Offline, source-qualified execution-region gameplay experiment.

Retain the corrected translator's statements and counter placement. Flatten a
closed acyclic call graph into one CFG, then promote across its former edges.
Original functions remain the fallback. No production builder is changed.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import re
import shlex
import subprocess
import promote_regs as P
from guest_region_sources import functions_from_source, words_from_dol
from guest_region_poll import transform as poll_transform
from guest_region_trace import run

PORT=Path(__file__).resolve().parents[1]
ROOT='803061E0'
OWNERS=(ROOT,'802CC108','802CC1F0','802CB758','8029316C','80293154','802924E4','80292510')
# Edges only descend this graph. In particular a selected address does not
# authorize a recursive edge from another selected address.
EDGES={ROOT:('802CC108','802CC1F0','802CB758','802924E4','80292510'),
       '802CC108':('802924E4','80292510'),
       '802CC1F0':('802924E4','80292510'),
       '802CB758':('80293154','8029316C')}
BASELINE_SHA='1bd614ef2b33c789e978f04cc1dc13a4f1c4ee10642ca8229a653980c66dcc70'
QUALIFIED_SOURCES_SHA='ae2103634fbe09cfc589093876b0a2a0c12392ec625fd1bc9db93765dc9f8880'

SUPPORT=r'''
#include "runtime/cpu_context.h"
#include "native_region_poll.h"
#include "runtime/native_hud.h"
#include <cstdlib>
#include <cstddef>
#include <x86intrin.h>
static_assert(offsetof(nwii::runtime::CPUContext,inst_count)==0x20378);
static_assert(offsetof(native_hud::Slot,self_us)==16);
static_assert(sizeof(native_hud::slots)==24);
namespace nwii::runtime {void native_region_dispatch_prepared(CPUContext&,uint32_t);}
static const bool execution_region_disabled=std::getenv("NWII_EXECUTION_REGION_BASELINE")!=nullptr;
static const bool execution_region_scripted=std::getenv("NWII_WPAD_SCRIPT") || std::getenv("NWII_WPAD_LIVE");
static inline void ExecutionRegionObserve(nwii::runtime::CPUContext& ctx){
  asm volatile("" : : "g"(&ctx) : "memory");
}
static __attribute__((always_inline)) inline bool ExecutionRegionOrdinary(uint32_t address,uint32_t bytes){
 const uint32_t p=address&0x3fffffffu;
 const bool ram=(bytes<=nwii::runtime::MMU::kMem1Limit && p<=nwii::runtime::MMU::kMem1Limit-bytes)||
   (bytes<=nwii::runtime::MMU::kMem2Limit && p>=0x10000000u && p-0x10000000u<=nwii::runtime::MMU::kMem2Limit-bytes);
 return bytes && ram && !nwii::runtime::native_watch_span_may_hit(address,bytes);
}
// One game, equal wrapper/measurement overhead, alternating eight-second
// windows after F3 OFF -> ON. Zero means disarmed, one baseline, two region.
// Plain trivial locals only: an IOS longjmp must not cross a destructor.
extern "C" {
std::atomic<uint64_t> execution_region_tag{0};
uint64_t execution_region_attempts[2]{},execution_region_completed[2]{};
uint64_t execution_region_tsc[2]{},execution_region_units[2]{};
}
static uint64_t execution_region_start=0;
static bool execution_region_saw_off=false;
static unsigned ExecutionRegionSelect(){
 if(!native_hud::on.load(std::memory_order_relaxed)){
  execution_region_saw_off=true;execution_region_start=0;
  execution_region_tag.store(0,std::memory_order_relaxed);return 0;
 }
 if(!execution_region_saw_off)return 0;
 const uint64_t now=GetTickCount64();
 if(!execution_region_start)execution_region_start=now;
 const uint64_t epoch=(now-execution_region_start)/8000+1;
 const unsigned variant=unsigned((epoch-1)&1);
 execution_region_tag.store((epoch<<2)|(variant+1),std::memory_order_relaxed);
 return variant;
}
'''


def qualify_owners():
    excluded=set(json.loads((PORT/'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
    if set(OWNERS)&excluded:raise ValueError('Selected owner intersects held-out exclusions')
    def visit(owner,path):
        if owner in path:raise ValueError('Recursive admitted call graph')
        return 1+max((visit(c,path+(owner,)) for c in EDGES.get(owner,())),default=0)
    depth=visit(ROOT,())
    if depth>4:raise ValueError('Admitted graph exceeds fixed continuation capacity')
    return excluded


def region_promoter():
    """Isolate PC/LR promotion from the ordinary translator's register ABI."""
    spec=importlib.util.spec_from_file_location('_execution_region_promoter',P.__file__)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.ANY_REF=re.compile(module.ANY_REF.pattern+r'|ctx\.lr\b|ctx\.pc\b')
    bit,local,context=module.bit_of,module.local_name,module.ctx_name
    module.bit_of=lambda m:131 if m[0]=='ctx.lr' else 132 if m[0]=='ctx.pc' else bit(m)
    module.local_name=lambda b:'region_lr' if b==131 else 'region_pc' if b==132 else local(b)
    module.ctx_name=lambda b:'ctx.lr' if b==131 else 'ctx.pc' if b==132 else context(b)
    return module


def sources(base,corrected):
    excluded=qualify_owners();allowed={int(a,16) for a in OWNERS};found={};files={}
    for tree,pattern in (('crash-hot','hot_*.cpp'),('crash-split','output_*.cpp')):
        for staged in sorted((base/'native-port/build'/tree).glob(pattern)):
            path=corrected/staged.name
            if not path.is_file():path=staged
            for pc,source in functions_from_source(path,allowed).items():
                name=f'{pc:08X}'
                if name in found:raise ValueError('Duplicate selected owner')
                found[name]=source;files[name]=str(path)
    if set(found)!=set(OWNERS):raise ValueError('Missing selected production owner')
    joined='\n'.join(owner+'\n'+found[owner] for owner in sorted(found))
    if hashlib.sha256(joined.encode()).hexdigest()!=QUALIFIED_SOURCES_SHA:
        raise ValueError('Requalify changed selected production sources')
    words={}
    for source in found.values():
        for pc,word in re.findall(r'// 0x([0-9A-F]{8}): ([0-9A-F]{8})',source):
            if pc in excluded:raise ValueError('Selected instruction intersects held-out entry')
            words[int(pc,16)]=int(word,16)
    if words_from_dol(PORT.parent/'orig/main.dol',set(words))!=words:
        raise ValueError('Selected production instruction/DOL mismatch')
    return found,files


def canonical_body(source):
    """Undo only the production promoter's explicit architectural transfers.

    Start after its entry selector and live-in loads. Keep the normal-entry
    counter increment, every instruction label, FP operation and later count.
    """
    first=re.search(r'^loc_[0-9A-F]{8}: ;$',source,re.M)
    if first is None:raise ValueError('Missing instruction labels')
    before=source[:first.start()].splitlines()
    if before[-1].strip() not in ('++ic;','++ctx.inst_count;'):
        raise ValueError('Unqualified normal-entry counter prefix')
    body=before[-1]+'\n'+source[first.start():]
    end=body.find('\nexit_')
    if end>=0:
        exits=body[end:]
        if re.sub(r'exit_\d+: ;|ctx\.(?:gpr\[\d+\]|fpr\[\d+\]|ps1\[\d+\]|cr\[\d\]\.(?:lt|gt|eq|so)|ctr|xer|inst_count) = \w+;|return;|\s|}', '', exits):
            raise ValueError('Unqualified promoted exit block')
        body=body[:end]
    else:
        if not body.endswith('\n}'):raise ValueError('Missing function end')
        body=body[:-2]
    body=re.sub(r'goto exit_\d+;', 'return;',body)
    body=re.sub(r'__builtin_expect\((ctx.require_fpu\(0x[0-9A-F]{8}\)), 0\)',r'\1',body)
    mapping={P.local_name(b):P.ctx_name(b) for b in range(131)}
    # Only actual declared promotion locals may be substituted. Guest temporary
    # identifiers such as ea/pq_ are intentionally left in their original scope.
    declared=set()
    for kind,names in re.findall(r'^    (uint32_t|uint64_t|double|bool) ([^;]+);$',source[:first.start()],re.M):
        declared.update(s.strip() for s in names.split(','))
    for name in declared:
        if name not in mapping:raise ValueError('Unexpected promoted local '+name)
    if declared:
        body=re.sub(r'\b('+'|'.join(sorted(declared,key=len,reverse=True))+r')\b',lambda m:mapping[m[0]],body)
    ref=r'ctx\.(?:gpr\[\d+\]|fpr\[\d+\]|ps1\[\d+\]|cr\[\d\]\.(?:lt|gt|eq|so)|ctr|xer|inst_count)'
    body=re.sub(r'('+ref+r') = \1;\s*','',body)
    # Preserve comments, but a comment can never become executable text after
    # an inserted branch or return.
    body='\n'.join(line for line in body.splitlines() if P.code_of(line).strip())
    # A generated owner can also contain neighbouring interior entries. Only
    # its normal-entry reachable CFG belongs to an admitted normal call.
    labels=list(re.finditer(r'^loc_([0-9A-F]{8}): ;$',body,re.M))
    units={m[1]:body[m.start():labels[i+1].start() if i+1<len(labels) else len(body)] for i,m in enumerate(labels)}
    order=list(units);successors={}
    for i,name in enumerate(order):
        edges=set();falls=True;depth=0
        for line in units[name].splitlines()[1:]:
            code=P.code_of(line).strip();edges.update(P.GOTO.findall(code))
            if depth==0 and (re.fullmatch(r'goto loc_[0-9A-F]{8};',code) or re.fullmatch(r'(?:ctx.pc = [^;]+; )?return;',code)):
                falls=False;break
            depth+=code.count('{')-code.count('}')
        if falls and i+1<len(order):edges.add(order[i+1])
        successors[name]=edges
    live=set();todo=[order[0]]
    while todo:
        name=todo.pop()
        if name in live:continue
        if name not in units:raise ValueError('Normal CFG has an unqualified label')
        live.add(name);todo.extend(successors[name]-live)
    return body[:labels[0].start()]+''.join(units[name] for name in order if name in live)


def prepared_dispatch(source):
    for owner in OWNERS:
        records=re.findall(r'\{0x'+owner+r', ([^}\n]+)\}',source)
        if len(records)!=1 or not re.fullmatch(r'0x[0-9A-F]{8}, func_'+owner,records[0]):
            raise ValueError('Requalify selected dispatch override/ownership '+owner)
    head='void native_call_indirect_cached(CPUContext& ctx, uint32_t site_index) {'
    start=source.index(head);end=source.index('\n}',start)+2
    body=source[start:end]
    poll='    if (!ctx.is_running || ctx.pc == 0) return;\n    process_pending_callbacks(ctx);\n'
    if body.count(poll)!=1:raise ValueError('Requalify cached dispatch prelude')
    body=body.replace(head,head.replace('native_call_indirect_cached','native_region_dispatch_prepared'),1).replace(poll,'',1)
    return source+'\nnamespace nwii::runtime {\n'+body+'\n}\n'


def flatten(found):
    qualify_owners();fresh_id=0x5e000000;resumes={};entries={};bodies=[];stats={'indirect_sites':0,'direct_edges':0,'memory_guards':0,'polls':0,'fp_guards':0}
    def fresh():
        nonlocal fresh_id
        out=f'{fresh_id:08X}';fresh_id+=4;return out
    for owner in OWNERS:entries[owner]=fresh()
    leave=fresh()
    def label(v):return 'loc_'+v+': ;'
    def poll():
        stats['polls']+=1
        return '\n'.join((
            '    region_ticket = native_region_poll::Prepare(ctx, ctx.pc, ctx.inst_count);',
            '    if (region_ticket != native_region_poll::Ticket::Complete) {',
            '        native_region_poll::Finish(ctx, region_ticket);',
            '    }'))
    def push(target,ret,resume,indirect=False):
        resumes[resume]=ret
        # IC++ here is the original cached indirect dispatch increment. Direct
        # calls have no such increment. The callee's entry increment is separate.
        return ('    ++ctx.inst_count;\n' if indirect else '')+f'    region_expected[region_depth]=0x{ret}; region_resume[region_depth++]=0x{resume};\n    ctx.pc=0x{target};\n    goto loc_{entries[target]};'
    indirect=re.compile(r'^\s*if \(ctx.ctr\) \{ ctx.pc = ctx.ctr; nwii::runtime::native_call_indirect_cached\(ctx, (\d+)\); if \(ctx.pc != 0x([0-9A-F]{8})\) return; process_pending_callbacks\(ctx\); if \(ctx.pc != 0x\2\) return; \} else nwii::runtime::note_null_call\((0x[0-9A-F]{8}), ctx.lr\);$')
    direct=re.compile(r'^\s*ctx.pc = 0x([0-9A-F]{8}); func_\1\(ctx\);$')
    for owner in OWNERS:
        lines=canonical_body(found[owner]).splitlines();out=[]
        for index,line in enumerate(lines):
            code=P.code_of(line).strip();m=indirect.match(code)
            if m:
                stats['indirect_sites']+=1;site,ret,nullsite=m.groups();null,post,done=fresh(),fresh(),fresh()
                out.extend((f'    if (!ctx.ctr) goto loc_{null};','    ctx.pc = ctx.ctr;',f'    if (!ctx.is_running || !ctx.pc) goto loc_{post};',poll(),f'    if (ctx.pc==0 || ctx.pc==0xFFFFFFFCu) goto loc_{post};',
                    '    if ((ctx.pc & 0xF0000000u)==0xC0000000u) ctx.pc=(ctx.pc&0x0FFFFFFFu)|0x80000000u;',
                    '    else if ((ctx.pc & 0xF0000000u)==0xD0000000u) ctx.pc=(ctx.pc&0x0FFFFFFFu)|0x90000000u;',
                    '    else if (ctx.pc<0x80000000u) ctx.pc|=0x80000000u;'))
                fragments=[]
                for target in EDGES.get(owner,()):
                    enter=fresh();out.append(f'    if (!execution_region_scripted && region_depth<4 && ctx.pc==0x{target}) goto loc_{enter};')
                    # Defer these blocks until after the generic fallback.
                    fragments.append((enter,push(target,ret,post,True)))
                out.extend((f'    nwii::runtime::native_region_dispatch_prepared(ctx, {site});',f'    goto loc_{post};'))
                for enter,block in fragments:
                    out.extend((label(enter),block))
                out.extend((label(post),f'    if (ctx.pc != 0x{ret}) return;',poll(),f'    if (ctx.pc != 0x{ret}) return;',f'    goto loc_{done};',label(null),f'    nwii::runtime::note_null_call({nullsite}, ctx.lr);',label(done)))
                continue
            m=direct.match(code)
            if m and m[1] in EDGES.get(owner,()):
                target=m[1];resume=fresh();ret=None
                # The original caller's next line is the return-PC test. It
                # remains at the continuation, along with its original polls.
                match=re.search(r'if \(ctx.pc != 0x([0-9A-F]{8})\) return;',P.code_of(lines[index+1]))
                if not match:raise ValueError('Unqualified direct call continuation')
                ret=match[1];stats['direct_edges']+=1
                out.extend((push(target,ret,resume),label(resume)));continue
            if code=='ctx.pc = ctx.lr; return;':
                out.extend(('    ctx.pc = ctx.lr;',f'    goto loc_{leave};'));continue
            # The FPU helper writes PC on exception. Treat this cold branch as
            # an observation so promotion reloads PC/LR instead of overwriting
            # the exception PC with the old local value.
            if 'ctx.require_fpu(' in code:
                stats['fp_guards']+=1
                out.extend(('    if (!(ctx.msr & 0x2000u)) {',
                    '        '+code+' ExecutionRegionObserve(ctx);','    }'));continue
            if 'ctx.mmu.' in code or 'ctx.psq_' in code:
                # Ordinary RAM cannot observe architectural state. MMIO/watch
                # paths execute the original statement with published state.
                mm=re.search(r'ctx.mmu.(read|write)(8|16|32|64|_f32|_f64)\((.*)\)',code)
                pq=re.search(r'ctx.psq_(?:store_val\([^,]+,[^,]+,|load_pair\()(.*)',code)
                if mm:
                    args=P.split_args(mm[3]);address=args[0];size={'8':1,'16':2,'32':4,'64':8,'_f32':4,'_f64':8}[mm[2]]
                    guard=f'ExecutionRegionOrdinary({address}, {size})'
                elif pq:
                    address=P.split_args(pq[1].split(');')[0])[0]
                    guard=f'ExecutionRegionOrdinary({address}, 8)'
                else:raise ValueError('Unqualified memory statement '+code)
                stats['memory_guards']+=1
                out.extend((f'    if (!{guard}) {{','        ExecutionRegionObserve(ctx); '+code,'    } else {','        '+code,'    }'));continue
            # Plain, explicit polls must use the same observation protocol.
            if code=='if (process_pending_callbacks(ctx)) return;':
                out.extend((poll(),'    if (region_poll_result) return;'))
                # The return value is significant at these direct-call sites.
                out[-2]=out[-2].replace('native_region_poll::Finish(ctx, region_ticket);','region_poll_result = native_region_poll::Finish(ctx, region_ticket);')
                out.insert(len(out)-2,'    region_poll_result=false;')
                continue
            if 'native_poll_due' in code:
                raise ValueError('Unexpected backward poll in selected closed region')
            out.append(line)
        bodies.append((entries[owner],'\n'.join(out)))
    body='\n'.join(label(entry)+'\n'+source for entry,source in bodies)
    returns='\n'.join(f'        case 0x{resume}: goto loc_{resume};' for resume in resumes)
    result='\n'.join((f'void func_{ROOT}(NWII_CTX_PARAM ctx) {{',
        '    unsigned region_depth=0;', '    uint32_t region_expected[4],region_resume[4];',
        '    native_region_poll::Ticket region_ticket;', '    bool region_poll_result;',
        f'    goto loc_{entries[ROOT]};',body,label(leave),
        '    if (region_depth==0) return;', '    --region_depth;',
        '    if (ctx.pc != region_expected[region_depth]) return;',
        '    switch (region_resume[region_depth]) {',returns,'        default: return;','    }','}'))
    # Promotion tracks dirty state per labelled unit. A conditional transfer
    # preceding the generic dispatch barrier must leave that unit before the
    # barrier: otherwise its target would incorrectly inherit the *fallback's*
    # clean state. Split synthetic branches as well as guest instruction labels.
    split=[];depth=0
    for line in result.splitlines():
        split.append(line);code=P.code_of(line)
        depth+=code.count('{')-code.count('}')
        if depth==1 and re.search(r'goto loc_[0-9A-F]{8};',code):
            split.append(label(fresh()))
    return '\n'.join(split),stats


def generate(found):
    flat,stats=flatten(found);backend=region_promoter()
    old=backend.classify
    backend.classify=lambda code,fp_mode=False:'plain' if 'region_ticket = native_region_poll::Prepare(ctx,' in code else old(code,fp_mode)
    promoted,details=backend.promote(flat,True,True,None)
    if promoted is None:raise ValueError(details)
    promoted=promoted.replace('{','{\n    uint32_t region_lr,region_pc;',1)
    promoted=promoted.replace('void func_'+ROOT+'(', 'static __attribute__((noinline)) void ExecutionRegion(' ,1)
    # Prepare intentionally receives only authoritative control fields plus PC
    # and IC locals. A surrounding full-state spill would invalidate the test.
    for line in promoted.splitlines():
        if 'native_region_poll::Prepare' in line and not re.search(r'Prepare\(ctx, region_pc, ic\)',line):
            raise ValueError('Poll did not consume local PC/count')
    return promoted,flat,dict(structure=stats,promotion=details,bounded_internal_depth=3)


def build(args):
    base,corrected,work=args.base.resolve(),args.corrected.resolve(),args.work.resolve()
    if work==PORT or PORT in work.parents:raise ValueError('Artifacts must be outside repo')
    work.mkdir(parents=True,exist_ok=True)
    baseline=corrected/'CrashNative.exe'
    if hashlib.file_digest(baseline.open('rb'),'sha256').hexdigest()!=BASELINE_SHA:raise ValueError('Wrong corrected baseline')
    found,files=sources(base,corrected);generated,flat,details=generate(found)
    (work/'region-flat.cpp').write_text(flat);(work/'region-promoted.cpp').write_text(generated)
    (work/'native_region_poll.h').write_bytes((PORT/'runtime-overlays/native_region_poll.h').read_bytes())
    tc=base/'storage/toolchain';commands=subprocess.run(['ninja','-t','commands'],cwd=tc,capture_output=True,text=True,check=True).stdout.splitlines()
    link=json.loads((corrected/'link-command.json').read_text());compiler=Path(link[0]).with_name('g++.exe')
    rsp=(corrected/'link.rsp').read_text();plans=[]
    root_path=Path(files[ROOT]);root_source=root_path.read_text()
    original=found[ROOT]
    wrapper=original.replace('void func_'+ROOT+'(', 'static __attribute__((noinline)) void ExecutionRegionBaseline(',1)
    wrapper+='\nvoid func_'+ROOT+'(NWII_CTX_PARAM ctx) {\n'
    wrapper+='    if (execution_region_disabled || !native_guest_entry::Enabled() || ctx.pc!=0x'+ROOT+') {ExecutionRegionBaseline(ctx);return;}\n'
    wrapper+='    const unsigned variant=ExecutionRegionSelect();\n'
    wrapper+='    ++execution_region_attempts[variant]; const uint64_t count=ctx.inst_count;\n'
    wrapper+='    unsigned aux; _mm_lfence(); const uint64_t start=__rdtscp(&aux); _mm_lfence();\n'
    wrapper+='    if(variant)ExecutionRegion(ctx);else ExecutionRegionBaseline(ctx);\n'
    wrapper+='    _mm_lfence(); const uint64_t end=__rdtscp(&aux); _mm_lfence();\n'
    wrapper+='    execution_region_tsc[variant]+=end-start; execution_region_units[variant]+=ctx.inst_count-count; ++execution_region_completed[variant];\n}\n'
    if root_source.count(original)!=1:raise ValueError('Ambiguous root replacement')
    patched=root_source.replace(original,generated+'\n'+wrapper,1)
    patched=SUPPORT+'\n'+patched
    plans.append(('native-port/build/crash-hot/'+root_path.name,root_path,patched))
    main=base/'native-port/build/crash-generated/main_output.cpp'
    plans.append(('native-port/build/crash-generated/main_output.cpp',main,prepared_dispatch(main.read_text())))
    ios=base/'native-port/build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp'
    plans.append(('ios.cpp',ios,poll_transform(ios.read_text())))
    for suffix,path,source in plans:
        cpp=work/path.name;obj=work/(path.name+'.obj');cpp.write_text(source)
        command=next(s for s in commands if suffix in s.replace('\\','/') and ' -c ' in s)
        argv=shlex.split(command);argv[0]=str(compiler)
        for flag in ('-o','-MT','-MF'):argv[argv.index(flag)+1]=str(obj)+('.d' if flag=='-MF' else '')
        argv[argv.index('-c')+1]=str(cpp)
        signature={'command':argv,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'header_sha256':hashlib.sha256((work/'native_region_poll.h').read_bytes()).hexdigest()}
        stamp=work/(path.name+'.inputs.json')
        reuse=args.resume and obj.exists() and stamp.exists() and json.loads(stamp.read_text())==signature
        if not reuse:
            run(argv,tc,work/(path.name+'.compile.log'));stamp.write_text(json.dumps(signature,indent=2))
        if suffix=='ios.cpp':rsp=str(obj).replace('\\','/')+'\n'+rsp
        else:
            matches=[s for s in rsp.splitlines() if s.replace('\\','/').endswith('/'+path.name+'.obj')]
            if len(matches)!=1:raise ValueError('Ambiguous original object '+path.name)
            rsp=rsp.replace(matches[0],str(obj).replace('\\','/'))
    (work/'link.rsp').write_text(rsp)
    link=[s.replace(str(corrected/'link.rsp'),str(work/'link.rsp')) for s in link]
    link[link.index('-o')+1]=str(work/'CrashNative.exe')
    for n,s in enumerate(link):
        if s.startswith('-Wl,--out-implib,'):link[n]='-Wl,--out-implib,'+str(work/'libCrashNative.dll.a')
    run(link,tc,work/'link.log')
    (work/'symbols.txt').write_text(run([compiler.with_name('nm.exe'),'-n','-C',work/'CrashNative.exe'],tc,work/'symbols.log'))
    details.update(baseline_sha256=BASELINE_SHA,executable_sha256=hashlib.file_digest((work/'CrashNative.exe').open('rb'),'sha256').hexdigest(),
        source_sha256={k:hashlib.sha256(v.encode()).hexdigest() for k,v in found.items()},owners=OWNERS,production_enabled=False)
    (work/'build.json').write_text(json.dumps(details,indent=2));print(json.dumps(details,indent=2))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base',type=Path,required=True);ap.add_argument('--corrected',type=Path,required=True);ap.add_argument('--work',type=Path,required=True)
    ap.add_argument('--resume',action='store_true')
    build(ap.parse_args())
