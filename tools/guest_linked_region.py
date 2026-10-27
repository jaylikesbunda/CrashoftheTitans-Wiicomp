"""Experimental single-copy linking of a root and direct leaf guest callees.

Preserve context commits at old call/return boundaries. Host architectural
locals flow through a CFG instead of crossing a C++ ABI. Each leaf has one
copy; the remembered return selector must match actual PC before re-entering
the caller. Faults and unexpected returns leave the region.
"""
import re,types,json,hashlib,os
from pathlib import Path
from functools import lru_cache
import promote_regs as P
from guest_entry_specialize import entry_source,prune_fpu

def combine(root,callees):
 root=entry_source(root);root,_=prune_fpu(root)
 parsed=P.parse(root);assert parsed
 body='\n'.join(parsed[-1]);used=set(re.findall(r'loc_([0-9A-F]{8})',root))
 for source in callees.values():used.update(re.findall(r'loc_([0-9A-F]{8})',source))
 next_label=0x5f000000
 def fresh():
  nonlocal next_label
  while '%08X'%next_label in used:next_label+=4
  label='%08X'%next_label;used.add(label);next_label+=4;return label
 entries={name:fresh() for name in callees};leave=fresh();resumes={};sites=[]
 pattern=re.compile(r'^    ctx\.pc = 0x([0-9A-F]{8}); (func_\1)\(ctx\);\n    if \(ctx\.pc != (0x[0-9A-F]{8})\) return;',re.M)
 def call(match):
  if match[2] not in callees:return match[0]
  expected=match[3];resume=fresh()
  assert expected not in resumes,'Duplicate return selector'
  resumes[expected]=resume;sites.append(dict(callee=match[2],return_pc=expected))
  return ('    native_link_return='+expected+';\n    NATIVE_REGION_COMMIT();\n    ctx.pc=0x'+match[1]+';goto loc_'+entries[match[2]]+';\nloc_'+resume+': ;\n    if (ctx.pc != '+expected+') return;')
 body=pattern.sub(call,body)
 if not sites:return None,dict(reason='no proven direct calls')
 fragments=[]
 for name,source in callees.items():
  normal=entry_source(source);normal,_=prune_fpu(normal);cal=P.parse(normal)
  text='\n'.join(cal[-1])
  if re.search(r'func_[0-9A-F]{8}\(ctx\)|native_call_indirect|ctx\.lr\s*=(?!=)',text):return None,dict(reason='not a leaf: '+name)
  if 'ctx.pc = ctx.lr; return;' not in text:return None,dict(reason='unrecognized return: '+name)
  text=text.replace('ctx.pc = ctx.lr; return;','ctx.pc = ctx.lr; goto loc_'+leave+';')
  lines=[]
  for line in text.splitlines():
   if line.lstrip().startswith(P.POLL_PREFIX):
    old='if (process_pending_callbacks(ctx)) return;'
    if old not in line:return None,dict(reason='poll shape changed')
    line=line.replace(old,'if (process_pending_callbacks(ctx)) { if(ctx.pc!=native_link_return)return; native_link_callback_return=true; }')
    line=line.replace('if ((ctx.inst_count % 10000000) == 0)','if (!native_link_callback_return && (ctx.inst_count % 10000000) == 0)')
    lines+=[line,'    if(native_link_callback_return) goto loc_'+leave+';']
   else:lines.append(line)
  fragments.append('loc_'+entries[name]+': ;\n    native_link_callback_return=false;\n'+((cal[2]+'\n') if cal[2] else '')+'\n'.join(lines))
 dispatch='loc_'+leave+': ;\n    NATIVE_REGION_COMMIT();\n    if(ctx.pc!=native_link_return)return;\n    switch(native_link_return){\n'+''.join('    case '+pc+': goto loc_'+label+';\n' for pc,label in resumes.items())+'    default: return;\n    }'
 result=parsed[0];head=root.splitlines()[0]
 source=head+'\n    uint32_t native_link_return;\n    bool native_link_callback_return;\n'+((parsed[2]+'\n') if parsed[2] else '')+body+'\n'+'\n'.join(fragments)+'\n'+dispatch+'\n}'
 return source,dict(root=result,callees=list(callees),sites=sites,single_copy=True)

def promoter(commit_mask=(1<<130)-1):
 """Clone only dirty-state commit hooks; keep stock instruction lowering."""
 text=open(P.__file__,encoding='utf-8').read()
 def replace(old,new):
  nonlocal text
  assert text.count(old)==1,old[:80];text=text.replace(old,new,1)
 replace('    s = code.lstrip()','    s = code.lstrip()\n    if s == "NATIVE_REGION_COMMIT();":return "commit"')
 replace("            if ln.kind == 'barrier':\n                d = 0", "            if ln.kind == 'commit':\n                d &= ~COMMIT_MASK\n            elif ln.kind == 'barrier':\n                d = 0")
 replace("            if ln.kind == 'barrier':\n                ln.lafter = live\n                live = ln.dbefore", "            if ln.kind == 'commit':\n                ln.lafter = live\n                live |= ln.dbefore & COMMIT_MASK\n            elif ln.kind == 'barrier':\n                ln.lafter = live\n                live = ln.dbefore")
 replace("            if ln.kind in ('barrier', 'cbarrier'):\n                sp = sanitise_mask(ln.dbefore)", "            if ln.kind == 'commit':\n                mask=sanitise_mask(ln.dbefore & COMMIT_MASK)\n                if mask:out.append('    '+spill_stmt(mask))\n                continue\n            if ln.kind in ('barrier', 'cbarrier'):\n                sp = sanitise_mask(ln.dbefore)")
 module=types.ModuleType('linked_region_promoter');module.__file__=P.__file__;module.COMMIT_MASK=commit_mask;exec(compile(text,P.__file__,'exec'),module.__dict__)
 return module

@lru_cache(maxsize=1)
def qualified_sources():
 """Closed, DOL/source-bound rollout; mismatch retains the ordinary compiler."""
 from guest_bounded_inline import sources
 port=Path(__file__).resolve().parents[1]
 manifest=json.loads((port/'reference/guest-regions/linked-regions.json').read_text())
 dol=port.parent/'orig/main.dol'
 if not dol.is_file() or hashlib.sha256(dol.read_bytes()).hexdigest()!=manifest['dol_sha256']:return {}
 source_map=sources();out={}
 for row in manifest['roots']:
  names=[row['name']]+list(row['callees'])
  if all(name in source_map and hashlib.sha256(source_map[name].encode()).hexdigest()==row['source_sha256'][name] for name in names):out[row['name']]=(row,source_map)
 return out

def qualified_normal(source,nest=None):
 """Return a normal-entry region or None; the caller supplies its proven fallback.

 Only the full architectural commit policy is permitted in production. The
 experimental GP1/GP13/IC policy deliberately cannot enter this path.
 """
 # The integrated production-equivalent paired probe was neutral/regressing
 # on three roots. Keep this source-bound experiment opt-in until a real
 # many-call workload demonstrates a useful end-to-end gain.
 if os.environ.get('NWII_BUILD_LINKED_REGIONS','0')=='0':return None,None
 parsed=P.parse(source)
 if not parsed:return None,None
 bound=qualified_sources().get(parsed[0])
 if not bound:return None,None
 row,source_map=bound
 if hashlib.sha256(source.encode()).hexdigest()!=row['source_sha256'][parsed[0]]:return None,None
 linked,metadata=combine(source,{name:source_map[name] for name in row['callees']})
 if linked is None:return None,metadata
 body,stats=promoter().promote(linked,True,True,nest)
 if body is not None:stats['linked_region']=metadata;stats['linked_commit_policy']='all'
 return body,stats
