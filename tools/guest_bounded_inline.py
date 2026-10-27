"""Source-bound leaf inlining before promotion; original guest entry cases remain."""
from pathlib import Path
import re,json
from types import SimpleNamespace
from guest_callee_effects import Registry,baseline,sha
PORT=Path(__file__).resolve().parents[1]
def sources(port=PORT):
 port=Path(port);excluded=set(json.loads((port/'reference/guest-regions/effect-exclusions.json').read_text())['addresses']);out={}
 for file in sorted((port/'build/crash-generated').glob('output_*.cpp')):
  if file.name=='output_74.cpp':continue
  text=file.read_text()
  for h in re.finditer(r'^void (func_([0-9A-F]{8}))\((?:NWII_CTX_PARAM|CPUContext&) ctx\) \{',text,re.M):
   if h[2] in excluded:continue
   end=text.find('\n}\n',h.end())
   if end>=0:out[h[1]]=text[h.start():end+2].replace('(CPUContext& ctx)', '(NWII_CTX_PARAM ctx)',1)
 return out
class Inliner:
 def __init__(self,source_map,max_leaf=24,max_added=96,allow_memory=False):
  self.sources=dict(source_map);self.registry=Registry(source_map);self.max_leaf=max_leaf;self.max_added=max_added;self.allow_memory=allow_memory;self.bound={}
 def leaf(self,name):
  if name in self.bound:return self.bound[name]
  if name not in self.sources:self.bound[name]=None;return None
  if self.allow_memory:
   e=SimpleNamespace(source_sha256=sha(self.sources[name]))
  else:
   e=self.registry.infer(name)
   if not e or e.memory or e.needs_fpu or e.callees:self.bound[name]=None;return None
  parsed=baseline.parse(self.sources[name]);body=parsed[-1] if parsed else []
  if self.allow_memory and re.search(r'func_[0-9A-F]{8}\(ctx\)|native_call_indirect|interpret_step|setjmp|longjmp|\bthrow\b|native_blocks_on|native_guest_regions|\bstatic\b', '\n'.join(body)):
   self.bound[name]=None;return None
  labels=re.findall(r'^loc_([0-9A-F]{8}): ;$', '\n'.join(body),re.M)
  if not labels or len(labels)>self.max_leaf:self.bound[name]=None;return None
  self.bound[name]=(e,body,labels);return self.bound[name]
 def lower(self,source):
  stats=dict(inline_calls=0,added_instructions=0,callees=[]);serial=0
  # Same statement proves entry PC; no caller entry can target its middle.
  pattern=re.compile(r'^    ctx\.pc = (0x([0-9A-F]{8})); (func_\2)\(ctx\);[^\n]*$',re.M)
  used=set(re.findall(r'loc_([0-9A-F]{8})',source));next_label=0x40000000
  def fresh():
   nonlocal next_label
   while f'{next_label:08X}' in used:next_label+=4
   value=f'{next_label:08X}';used.add(value);next_label+=4;return value
  def emit(match):
   nonlocal serial
   name=match[3];leaf=self.leaf(name)
   if not leaf:return match[0]
   e,body,labels=leaf
   if sha(self.sources[name])!=e.source_sha256 or stats['added_instructions']+len(labels)>self.max_added:return match[0]
   mapping={label:fresh() for label in labels};resume=fresh()
   text='\n'.join(body)
   # Unscoped initialized locals cannot be jumped over in the caller.
   depth=0
   for line in body:
    code=baseline.code_of(line)
    if depth==0 and re.match(r'\s*(?:const\s+)?(?:uint32_t|int32_t|uint64_t|int64_t|bool|double|float)\s+\w+\s*=',code):return match[0]
    depth+=code.count('{')-code.count('}')
   text=re.sub(r'loc_([0-9A-F]{8})',lambda m:'loc_'+mapping[m[1]],text)
   text=re.sub(r'\breturn\s*;', 'goto loc_'+resume+';',text)
   serial+=1;stats['inline_calls']+=1;stats['added_instructions']+=len(labels);stats['callees'].append(name)
   # Trace remains an explicit observation barrier, with its original entry PC.
   return '    ctx.pc = '+match[1]+'; // NATIVE_BOUNDED_INLINE_V1 '+name+' '+e.source_sha256+'\n'+text+'\nloc_'+resume+': ;'
  return pattern.sub(emit,source),stats
