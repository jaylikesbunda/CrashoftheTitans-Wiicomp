"""Effect-aware register promotion with conservative final-source summaries.

Summaries cover a closed grammar and bounded ordinary-RAM accesses.
Escaping context pointers, opaque helpers and recursive calls decline.
The stock promoter is cloned in memory with checked hook points, so its baseline
dataflow/emission remains the independently executable comparison.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib,re,sys,types

TOOLS=Path(__file__).resolve().parent
sys.path.insert(0,str(TOOLS))
import promote_regs as baseline
from effect_statements import normalize,split_body

@dataclass(frozen=True)
class Effect:
 name:str
 source_sha256:str
 reads:int
 writes:int
 callees:tuple=()
 memory:tuple=()
 needs_fpu:bool=False

def sha(source):return hashlib.sha256(source.encode()).hexdigest()

class Registry:
 def __init__(self,sources):
  self.sources=dict(sources);self.effects={};self.rejected={};self.active=set()
 def bind(self,name,source):
  effect=self.infer(name)
  return effect if effect and effect.source_sha256==sha(source) else None
 def infer(self,name):
  if name in self.effects:return self.effects[name]
  if name in self.rejected:return None
  if name in self.active:self.rejected[name]='recursive call graph';return None
  source=self.sources.get(name)
  if source is None:self.rejected[name]='missing/excluded callee';return None
  parsed=baseline.parse(normalize(source,baseline))
  if parsed is None:self.rejected[name]='unrecognised staged preamble';return None
  self.active.add(name)
  try:
   reads=writes=0;callees=[];locals_=set();previous='';memory={};needs_fpu=False;written_roots=set();backward=False
   labels=[int(x,16) for x in re.findall(r'loc_([0-9A-F]{8}):',source)]
   current=0
   for line in parsed[-1]:
    code=baseline.code_of(line).strip()
    if not code:continue
    label=re.fullmatch(r'loc_([0-9A-F]{8}): ;',code)
    if label:current=int(label[1],16);previous='';continue
    for target in re.findall(r'goto loc_([0-9A-F]{8})',code):
     if int(target,16)<=current:backward=True
    if re.fullmatch(r'if \(ctx\.require_fpu\(0x[0-9A-F]+\)\) return;',code):needs_fpu=True;previous=code;continue
    call=re.fullmatch(r'(func_[0-9A-F]{8})\(ctx\);',code)
    if call:
     target=call[1]
     if not re.fullmatch(r'ctx\.pc\s*=\s*0x'+target[5:]+r';',previous):raise ValueError('unproven nested entry')
     child=self.infer(target)
     if child is None:raise ValueError('opaque nested callee')
     if child.memory:raise ValueError('nested RAM guard requires call-boundary proof')
     reads|=child.reads;writes|=child.writes;needs_fpu|=child.needs_fpu;callees.append(target);previous=code;continue
    original_code=code
    while True:
     access=re.search(r'ctx\.mmu\.(read8|read16|read32|read_f32|read_f64|write8|write16|write32|write_f32|write_f64)\(',code)
     if not access:break
     begin=access.end();depth=1;end=begin;comma=None
     while end<len(code) and depth:
      if code[end]=='(':depth+=1
      elif code[end]==')':depth-=1
      elif code[end]==',' and depth==1 and comma is None:comma=end
      end+=1
     if depth:raise ValueError('malformed RAM access')
     address=code[begin:comma if comma is not None else end-1].strip()
     addr=re.fullmatch(r'ctx\.gpr\[(\d+)\]\s*(?:([+\-])\s*(-?(?:0x[0-9A-Fa-f]+|\d+)))?',address)
     if not addr:raise ValueError('non-affine RAM address')
     root=int(addr[1]);offset=int(addr[3],0) if addr[3] else 0
     if addr[2]=='-':offset=-offset
     if root in written_roots:raise ValueError('RAM root changed before access')
     method=access[1];writing=method.startswith('write');width=8 if method.endswith('64') else 4 if method.endswith(('32','f32')) else 2 if method.endswith('16') else 1
     old=memory.get(root,(offset,offset+width,False));memory[root]=(min(old[0],offset),max(old[1],offset+width),old[2] or writing)
     if memory[root][1]-memory[root][0]>65536:raise ValueError('RAM guard too broad')
     value=code[comma+1:end-1] if comma is not None else '0'
     # Effects of value/address operands remain visible to the register analysis.
     code=code[:access.start()]+'('+address+' + ('+value+'))'+code[end:]
    if re.search(r'\bctx\b(?!\.)|&\s*ctx\.|ctx\.\w+\s*\(',code):raise ValueError('escaping context/helper')
    if re.search(r'ctx\.(?:msr|is_running)\s*(?:=(?!=)|[|&^+\-]=)',code):raise ValueError('execution-control write')
    for field,index in re.findall(r'ctx\.(gpr|fpr|ps1|cr)\[(\d+)\]',code):
     if int(index)>=(8 if field=='cr' else 32):raise ValueError('out-of-range register')
    if re.search(r'ctx\.(gpr|fpr|ps1|cr)\[(?!\d+\])',code):raise ValueError('dynamic register index')
    # Match complete field spellings, not a permissive ctx identifier prefix.
    fields=re.sub(r'ctx\.(?:gpr|fpr|ps1)\[\d+\]|ctx\.cr\[\d\]\.(?:lt|gt|eq|so)|ctx\.(?:ctr|inst_count|pc|lr|xer|fpscr|msr|is_running)\b','0',code)
    if 'ctx.' in fields:raise ValueError('unknown state field')
    local=re.match(r'(?:const\s+)?(?:uint32_t|int32_t|uint64_t|int64_t|bool|double|float)\s+(\w+)\s*=',fields)
    if local:locals_.add(local[1])
    allowed={'uint32_t','int32_t','uint64_t','int64_t','bool','double','float','const','if','else','goto','return','true','false','std','rotl'}|locals_
    names=set(re.findall(r'\b[A-Za-z_]\w*',fields))
    if any(n not in allowed and not re.fullmatch(r'loc_[0-9A-F]{8}',n) for n in names):raise ValueError('unknown expression/effect')
    if re.search(r'\b(?:for|while|switch|do|try|throw)\b|["\'#]',fields):raise ValueError('unsupported structure')
    ln=baseline.analyse_line('    '+code,0)
    if ln.kind!='plain':raise ValueError('opaque line')
    reads|=ln.uses;writes|=ln.writes;written_roots|={i for i in range(32) if ln.writes & (1<<i)};previous=original_code
   if backward and any(writes & (1<<root) for root in memory):raise ValueError('changing loop RAM root')
   plans=tuple((root,*span) for root,span in sorted(memory.items()))
   result=Effect(name,sha(source),reads,writes,tuple(callees),plans,needs_fpu);self.effects[name]=result;return result
  except ValueError as error:self.rejected[name]=str(error);return None
  finally:self.active.remove(name)

def experimental_promoter(registry,bound_sources=None):
 """Bind summaries only to the exact callee bodies compiled into this variant."""
 bindings={n:registry.bind(n,s) for n,s in (registry.sources if bound_sources is None else bound_sources).items()}
 text=(TOOLS/'promote_regs.py').read_text()
 def replace(old,new):
  nonlocal text
  if text.count(old)!=1:raise RuntimeError('Stock promoter hook changed: '+old[:90])
  text=text.replace(old,new,1)
 replace("'ic_sync', 'fp_guard')", "'ic_sync', 'fp_guard', 'effect')")
 replace('    ln.kind = classify(ln.code)','''    ln.kind = classify(ln.code)
    match = re.fullmatch(r'\\s*(func_[0-9A-F]{8})\\(ctx\\);\\s*',ln.code)
    ln.effect = EFFECT_BINDINGS.get(match[1]) if match else None''')
 replace("            if ln.kind == 'barrier':\n                d = 0",'''            if ln.kind == 'barrier':
                d = d & ~(ln.effect.reads|ln.effect.writes) if ln.effect else 0''')
 replace("                live = ln.dbefore\n",'''                live = ((live & ~ln.effect.writes) | ln.dbefore) if ln.effect else ln.dbefore
''')
 replace('    # control flow\n','''    # Do not optimize a compound instruction unit whose early edge bypasses
    # synchronization. Production instruction labels delimit CFG units.
    for unit in units:
        early_edge=False
        for line in unit.lines:
            if line.effect and early_edge:
                return None, 'early exit before summarized call in one unit'
            early_edge=early_edge or bool(line.gotos) or line.has_return
    # control flow
''')
 replace("            if ln.kind in ('barrier', 'cbarrier'):\n                sp = sanitise_mask(ln.dbefore)\n                rl = sanitise_mask(ln.lafter)\n",'''            if ln.kind in ('barrier', 'cbarrier'):
                sp = sanitise_mask(ln.dbefore)
                rl = sanitise_mask(ln.lafter)
                if ln.effect:
                    e=ln.effect; selective=sp & (e.reads|e.writes); carry=sp & ~selective
                    reload=(rl | (sp if ln.kind=='cbarrier' else 0)) & e.writes
                    conditions=['native_callee_effects::enabled','!nwii::runtime::g_trace_calls','ctx.pc==0x'+e.name[5:]]
                    if e.needs_fpu:conditions.append('(ctx.msr&0x2000u)!=0')
                    for root,low,high,writing in e.memory:
                        value=local_name(root) if sp & (1<<root) else 'ctx.gpr['+str(root)+']'
                        conditions.append('native_callee_effects::Ordinary(ctx.mmu,'+value+'+uint32_t('+str(low)+'),'+str(high-low)+','+str(writing).lower()+')')
                    out.append('    if('+' && '.join(conditions)+'){')
                    out.append('    native_callee_effects::record(true,'+str((sp^selective).bit_count())+','+str((rl & ~reload).bit_count())+');')
                    if selective:out.append('    '+spill_stmt(selective))
                    out.append(ln.text)
                    if reload:out.append('    '+reload_stmt(reload))
                    out.append('    }else{')
                    out.append('    native_callee_effects::record(false,0,0);')
                    if sp:out.append('    '+spill_stmt(sp))
                    out.append(ln.text)
                    # Fallback may overwrite any carried register, including
                    # values needed only by a later dirty-state commit.
                    fallback_reload=rl | (sp if ln.kind=='cbarrier' else carry)
                    if fallback_reload:out.append('    '+reload_stmt(fallback_reload))
                    out.append('    }')
                    stats['effect_calls']=stats.get('effect_calls',0)+1
                    stats['effect_spill_removed']=stats.get('effect_spill_removed',0)+(sp^selective).bit_count()
                    stats['effect_reload_removed']=stats.get('effect_reload_removed',0)+(rl & ~reload).bit_count()
                    stats['spill_bits']+=selective.bit_count();stats['reload_bits']+=reload.bit_count()
                    continue
''')
 module=types.ModuleType('experimental_promote_regs');module.__file__=str(TOOLS/'promote_regs.py');module.EFFECT_BINDINGS=bindings
 exec(compile(text,module.__file__,'exec'),module.__dict__)
 original_promote=module.promote
 def promote(source,*args,**kwargs):return original_promote(normalize(source,baseline),*args,**kwargs)
 module.promote=promote
 return module

PREFIX='''namespace native_callee_effects {
inline bool enabled=std::getenv("NWII_CALLEE_SUMMARY")!=nullptr;
template<class Memory> inline bool Ordinary(Memory& memory,uint32_t address,uint32_t bytes,bool writing){
 const uint32_t physical=address&0x3fffffffu;if(!bytes || bytes-1>0x3fffffffu-physical)return false;
 const intptr_t bias=memory.page_bias[physical>>16];if(bias<=1)return false;
 for(uint32_t page=physical>>16;page<=((physical+bytes-1)>>16);++page)if(memory.page_bias[page]!=bias)return false;
 return !writing || !nwii::runtime::native_watch_span_may_hit(physical,bytes);
}
inline bool instrument=false;
struct Counters {uint64_t fast=0,fallback=0,spills_removed=0,reloads_removed=0;};
inline Counters counters;
inline void record(bool fast,unsigned spills,unsigned reloads){if(instrument){if(fast)++counters.fast;else ++counters.fallback;counters.spills_removed+=spills;counters.reloads_removed+=reloads;}}
}
'''
