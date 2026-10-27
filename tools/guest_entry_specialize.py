"""Normal-entry register promotion; cold continuation remains original code."""
import re,os,json,hashlib
from pathlib import Path
from functools import lru_cache
import promote_regs as P

@lru_cache(maxsize=1)
def verify_backend():
 header=(Path(__file__).resolve().parents[1]/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h').read_text()
 assert 'if (msr & 0x2000u) return false;' in header, 'Requalify lazy-FPU semantics'
 a=header.index('struct MMU {');b=header.index('struct CPUContext',a)
 assert not re.search(r'\bmsr\b|g_ctx_ptr',header[a:b]), 'Requalify MMU/CPU state separation'

def entry_source(source,continuations=()):
 parsed=P.parse(source)
 if not parsed:return None
 name,entry,trace,cases,special,body=parsed
 prefix=source.splitlines()[0]+'\n'+((trace+'\n') if trace else '')
 selected=[(addr,label) for addr,label in cases if addr in continuations]
 if selected:
  prefix+='    if (ctx.pc != 0x%08X) {\n        switch (ctx.pc) {\n'%entry
  prefix+=''.join('            case %s: goto loc_%s;\n'%(addr,label) for addr,label in selected)
  prefix+='            default: return;\n        }\n    }\n'
  # The stock parser only accepts its original interpreter fallback spelling.
  prefix=prefix.replace('            default: return;','            default: nwii::runtime::interpret_step(ctx); return;')
 return prefix+'\n'.join(body)+'\n}'

def fpu_guard(line,level):
 return level==0 and bool(re.fullmatch(r'if \(ctx\.require_fpu\(0x[0-9A-F]{8}\)\) (?:return;|\{ ctx\.inst_count -= \d+; return; \})',line.code.strip()))

def invalidates_fpu(line):
 if line.kind in ('barrier','cbarrier','poll','blockcall') or re.search(r'ctx\.msr\s*(?:=(?!=)|[-+*/&|^]=)',line.code):return True
 pure={'if','while','switch','uint32_t','int32_t','uint64_t','int64_t','double','float','bool','std::fma','std::sqrt','std::fabs','std::rotl','std::memcpy','ctx.require_fpu','ctx.read_timebase','ctx.read_dec','ctx.write_dec','ctx.dec_expired'}
 for name in re.findall(r'\b([A-Za-z_]\w*(?:(?:::|\.)[A-Za-z_]\w*)*)\s*\(',line.code):
  if name not in pure and not name.startswith(('ctx.mmu.','ctx.ps_','ctx.psq_')):return True
 return False

def prune_fpu(source):
 parsed=P.parse(source)
 if not parsed:return source,0
 body=parsed[-1];units=[];cur=[];labels={};depth=0;rows=[]
 for index,line in enumerate(body):
  m=P.LABEL.match(line)
  if m:
   if depth:return source,0
   units.append(cur);cur=[];labels[m[1]]=len(units)
  else:
   ln=P.analyse_line(line,depth);cur.append((index,ln,depth));depth+=ln.code.count('{')-ln.code.count('}')
 units.append(cur)
 if depth:return source,0
 preds=[[] for u in units];succ=[]
 for k,unit in enumerate(units):
  destinations=set();last=None
  for index,ln,level in unit:
   for target in ln.gotos:
    if target not in labels:return source,0
    destinations.add(labels[target])
   if ln.code.strip():last=ln.code.strip()
  falls=not(last and (re.match(r'^goto loc_[0-9A-F]{8};$',last) or re.match(r'^(?:ctx\.pc = [^;]+; )?return;$',last)))
  if falls and k+1<len(units):destinations.add(k+1)
  for target in destinations:preds[target].append(k)
  succ.append(destinations)
 entries={0}|{labels[label] for _,label in parsed[3]}
 incoming=[False]*len(units);outgoing=[False]*len(units)
 def transfer(unit,known):
  for index,ln,level in unit:
   if fpu_guard(ln,level):known=True
   elif invalidates_fpu(ln):known=False
  return known
 changed=True
 while changed:
  changed=False
  for k,unit in enumerate(units):
   start=k not in entries and bool(preds[k]) and all(outgoing[p] for p in preds[k])
   end=transfer(unit,start)
   if incoming[k]!=start or outgoing[k]!=end:incoming[k]=start;outgoing[k]=end;changed=True
 remove=set()
 for k,unit in enumerate(units):
  known=incoming[k]
  for index,ln,level in unit:
   guard=fpu_guard(ln,level)
   if guard:
    if known:remove.add(index)
    known=True
   elif invalidates_fpu(ln):known=False
 if not remove:return source,0
 suffix='\n'.join(body)+'\n}';assert source.endswith(suffix)
 return source[:-len(suffix)]+'\n'.join(line for i,line in enumerate(body) if i not in remove)+'\n}',len(remove)

def promote_entry(source,nest=None,prune=True,continuations=(),typed_leaves=None):
 verify_backend()
 entry=entry_source(source,continuations)
 if entry is None:return None,'unrecognized entry'
 typed_ids=[];typed_sites=0
 if typed_leaves:
  from guest_typed_leaves import apply
  entry,_,typed_sites=apply(entry,typed_leaves)
  typed_ids=sorted(address for address in typed_leaves if 'native_leaf_value_'+address+'(' in entry)
 reduced,removed=prune_fpu(entry) if prune else (entry,0)
 new,stats=P.promote(reduced,True,True,nest)
 if new is not None:
  stats['fpu_checks_removed']=removed;stats['entry_only']=True
  stats['typed_leaf_ids']=typed_ids;stats['typed_leaf_sites']=typed_sites
  if os.environ.get('NWII_BUILD_LOOP_FASTPATH','1')=='1' and (P.parse(source)[0] in loop_targets() or os.environ.get('NWII_BUILD_GUEST_BATCH','1')!='0'):
   from guest_loop_guards import apply
   new,stats['loop_fastpaths']=apply(new,clone=True,fuse=True,records=os.environ.get('NWII_BUILD_GUEST_BATCH','1')!='0')
 return new,stats

def promote_continuations(source,continuations,nest=None):
 entry=entry_source(source,continuations)
 parsed=P.parse(entry);body=parsed[-1]
 # No ordinary entry into this helper. Case gotos jump past this return;
 # backedges into original labels still execute the original instructions.
 suffix='\n'.join(body)+'\n}';assert entry.endswith(suffix)
 entry=entry[:-len(suffix)]+'    return;\n'+suffix
 reduced,removed=prune_fpu(entry)
 new,stats=P.promote(reduced,True,True,nest)
 if new is not None:
  stats['continuation_fpu_checks_removed']=removed
  if os.environ.get('NWII_BUILD_LOOP_FASTPATH','1')=='1' and P.parse(source)[0] in loop_targets():
   from guest_loop_guards import apply
   new,stats['loop_fastpaths']=apply(new,clone=True,fuse=True)
 return new,stats

@lru_cache(maxsize=1)
def loop_targets():
 root=Path(__file__).resolve().parents[1]
 manifest=json.loads((root/'reference/guest-regions/loop-fastpaths.json').read_text())
 dol=root.parent/'orig/main.dol'
 if not dol.is_file() or hashlib.sha256(dol.read_bytes()).hexdigest()!=manifest['dol_sha256']:return set()
 return set(manifest['functions'])

@lru_cache(maxsize=1)
def continuation_targets():
 root=Path(__file__).resolve().parents[1]
 manifest=json.loads((root/'reference/guest-regions/hot-continuations.json').read_text())
 dol=root.parent/'orig/main.dol'
 if not dol.is_file() or hashlib.sha256(dol.read_bytes()).hexdigest()!=manifest['dol_sha256']:return set()
 return {r['name'] for r in manifest['functions']}


def specialize(source,nest=None,continuation_allowlist=None,typed_leaves=None):
 original,old_stats=P.promote(source,True,True,nest)
 if original is None:return None,old_stats
 parsed=P.parse(source)
 if os.environ.get('NWII_BUILD_LOOP_FASTPATH','1')=='1' and parsed[0] in loop_targets():
  from guest_loop_guards import apply
  original,old_stats['loop_fastpaths']=apply(original,clone=True,fuse=True)
 returns=set(re.findall(r'ctx\.lr = (0x[0-9A-F]{8});',source))
 continuations=[addr for addr,label in parsed[3] if addr in returns]
 # NATIVE_POLL_CONTINUATIONS_V1: poll-site resume PCs as well (interrupts re-enter at the poll); bounded in
 # guest_poll_continuations.select. NWII_BUILD_POLL_CONTINUATIONS=0 keeps the LR-only rule.
 import guest_poll_continuations as PC
 if PC.enabled():continuations,_=PC.select(source,parsed[3])
 # NATIVE_CONTINUATIONS_ALL_V1 (opt-in): the measured fallback entries are
 # mostly jump-table interior targets, not LR literals. Allow every interior
 # entry (and bypass the 64/allowlist bounds) so it can be A/B measured.
 all_entries=os.environ.get('NWII_BUILD_CONTINUATIONS_ALL','0')=='1'
 if all_entries:continuations=[addr for addr,label in parsed[3]]
 # Return continuations are useful execution entries. FPU-fault and unknown
 # interior entries retain the complete original fallback. Bound code growth.
 targets=continuation_targets() if continuation_allowlist is None else continuation_allowlist
 if continuation_allowlist is None and PC.targets_override() is not None:targets=PC.targets_override()
 if all_entries:targets={parsed[0]}
 if os.environ.get('NWII_BUILD_FAST_CONTINUATIONS','1')=='0' or (not all_entries and not PC.enabled() and len(continuations)>64) or parsed[0] not in targets:continuations=[]
 fast,stats=promote_entry(source,nest,typed_leaves=typed_leaves)
 if fast is None:return original,old_stats
 name,entry=parsed[:2];cases=len(parsed[3])
 # Avoid extra entry gates on tiny routines with no meaningful re-entry cost.
 saved=old_stats.get('spill_bits',0)+old_stats.get('reload_bits',0)-stats.get('spill_bits',0)-stats.get('reload_bits',0)
 from guest_linked_region import qualified_normal
 linked,linked_stats=qualified_normal(source,nest)
 if linked is None and not stats.get('typed_leaf_sites') and (cases<4 or (saved<16 and stats.get('fpu_checks_removed',0)<4)):return original,old_stats
 cold_name='native_guest_resume_'+name[5:]
 cold=original.replace('void '+name+'(', 'static __attribute__((noinline,cold,optimize("O2"))) void '+cold_name+'(',1)
 helper='';helper_name='native_guest_continuation_'+name[5:]
 if continuations:
  resume,resume_stats=promote_continuations(source,continuations,nest)
  if resume is None:continuations=[]
  else:
   helper=resume.replace('void '+name+'(', 'static __attribute__((noinline,optimize("O2"))) void '+helper_name+'(',1)
   stats['continuation_fpu_checks_removed']=resume_stats['continuation_fpu_checks_removed']
 head=fast.splitlines()[0]
 linked_helper=''
 linked_name='native_guest_linked_'+name[5:]
 if linked is not None:
  linked_helper=linked.replace(linked.splitlines()[0],'static __attribute__((noinline,optimize("O2"))) '+head.replace(name,linked_name),1)
  stats['linked_roots']=1
  stats['linked_sites']=len(linked_stats['linked_region']['sites'])
 gate='\n    if(ctx.pc!=0x'+name[5:]+' || !native_guest_entry::Enabled()){\n'
 if continuations:
  gate+='        if(native_guest_entry::Enabled() && native_guest_entry::ContinuationsEnabled()){switch(ctx.pc){\n'
  gate+=''.join('        case '+addr+':\n' for addr in continuations)
  gate+='            native_guest_entry::RecordContinuation();'+helper_name+'(ctx);return;\n        default: break;\n        }}\n'
 gate+='        native_guest_entry::Record(false);'+cold_name+'(ctx);return;\n    }\n    native_guest_entry::Record(true);'
 opt=os.environ.get('NWII_GUEST_ENTRY_OPT','O2');assert opt in ('Os','O1','O2','O3','inherit'),opt
 attribute='' if opt=='inherit' else '__attribute__((optimize("'+opt+'")))\n'
 if continuations:
  # Keep route selection out of the register-heavy body's prologue. The tiny
  # public wrapper tail-calls a normal or resume helper without saving the
  # normal body's host register set on the resume path.
  primary_name='native_guest_primary_'+name[5:]
  primary=fast.replace(head,'static __attribute__((noinline,optimize("'+opt+'"))) '+head.replace(name,primary_name),1) if opt!='inherit' else fast.replace(head,'static __attribute__((noinline)) '+head.replace(name,primary_name),1)
  wrapper=attribute+head+'\n    if(!native_guest_entry::Enabled()){native_guest_entry::Record(false);'+cold_name+'(ctx);return;}\n'
  normal_call=(('if(native_linked_region::Enabled()){'+linked_name+'(ctx);return;}') if linked_helper else '')+primary_name+'(ctx);return;'
  wrapper+='    if(ctx.pc==0x'+name[5:]+'){native_guest_entry::Record(true);'+normal_call+'}\n'
  wrapper+='    if(native_guest_entry::ContinuationsEnabled()){switch(ctx.pc){\n'+''.join('    case '+addr+':\n' for addr in continuations)
  wrapper+='        native_guest_entry::RecordContinuation();'+helper_name+'(ctx);return;\n    default: break;\n    }}\n    native_guest_entry::Record(false);'+cold_name+'(ctx);\n}'
  fast=primary+'\n\n'+linked_helper+'\n\n'+wrapper
 elif linked_helper:
  primary_name='native_guest_primary_'+name[5:]
  primary=fast.replace(head,'static __attribute__((noinline,optimize("O2"))) '+head.replace(name,primary_name),1)
  fast=primary+'\n\n'+linked_helper+'\n\n'+attribute+head+gate+'\n    if(native_linked_region::Enabled()){'+linked_name+'(ctx);return;}\n    '+primary_name+'(ctx);\n}'
 else:fast=fast.replace(head,attribute+head+gate,1)
 stats.update(entry_specialized=True,normal_entry_opt=opt,continuation_cases=cases,fast_continuations=len(continuations),static_sync_bits_removed=saved,normal_entry_source_bytes=len(fast),cold_source_bytes=len(cold))
 return cold+'\n\n'+helper+'\n\n'+fast,stats
