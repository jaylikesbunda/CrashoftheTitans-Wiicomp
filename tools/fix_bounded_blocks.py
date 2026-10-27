"""Apply only individually qualified register regions, preserving all labels."""
from pathlib import Path
import hashlib,json,re,shutil
import block_codegen as bc
import region
p=bc.PORT;report=bc.load_report('bounded-block-qualification.json')
scan=bc.load_report('bounded-block-candidates.json')
assert report['regions']==scan['count'],'Complete the full-image qualification before adoption'
selected=[r for r in report['results'] if r['adopted'] and not r['failed'] and not r['fp'] and all(bank=='gpr' for bank,n in r['used'])]
selected=[r for r in selected if region.active().reference or region.active().exact(int(r['function'],16))] # another release keeps only functions proven identical
byfile={}
for r in selected:
 r['file']=region.chunk_file(r['function'],r['file']) # another release splits its functions into different chunks
 byfile.setdefault(r['file'],[]).append(r)

def function_label_index(text):
 functions={}
 for address,start,end in bc.function_ranges(text):
  labels={};ordered=[]
  for m in bc.LABEL.finditer(text,start,end):
   labels.setdefault(m[1].upper(),[]).append(m.start())
   ordered.append((m.start(),m[1].upper()))
  functions[address]=(start,end,labels,ordered)
 return functions

storage=(p/'build/crash-generated').resolve().parent/'bounded-block-before';storage.mkdir(parents=True,exist_ok=True)
support=p/'runtime-overlays/bounded-blocks';support.mkdir(exist_ok=True)
common='#pragma once\n#include <cstdlib>\n// NATIVE_BOUNDED_BLOCKS_V1\ninline const bool native_blocks_on=std::getenv("NWII_NO_BOUNDED_BLOCKS")==nullptr;\n'
commonfile=support/'common.h'
if not commonfile.exists() or commonfile.read_text()!=common:commonfile.write_text(common)
for file,regions in byfile.items():
 f=p/'build/crash-generated'/file;s=f.read_text();header=support/(f.stem+'.h')
 contents='#pragma once\n#include "'+commonfile.as_posix()+'"\n'
 staged='NATIVE_BOUNDED_BLOCKS_V1' in s
 if staged and not (storage/file).exists():
  raise SystemExit('Bounded-block baseline for '+file+' is missing; start from a clean build tree')
 if not staged:(storage/file).write_text(s)
 original=(storage/file).read_text()
 source_index=function_label_index(original)
 current_index=function_label_index(s)
 marker_positions={(m[1],m[2]):m.start() for m in re.finditer(r'^\s*// NATIVE_BOUNDED_BLOCKS_V1 ([0-9A-F]{8}) ([0-9A-F]{8})\s*$',s,re.M)}
 resolved=[]
 for r in regions:
  fn=source_index.get(r['function']);assert fn,r['function']
  labels=fn[2];positions=labels.get(r['address'],[]);assert len(positions)==1,(r['function'],r['address'],len(positions))
  ordered=fn[3];i=next(i for i,x in enumerate(ordered) if x[1]==r['address'])
  end_i=i+r['instructions'];assert end_i<len(ordered),r['address']
  start=positions[0];end=ordered[end_i][0];resume_address=ordered[end_i][1]
  resolved.append((start,end,resume_address,r))
 for start,end,resume_address,r in sorted(resolved,key=lambda x:x[0],reverse=True):
  if staged:
   # Staged chunks: recover the original region from the chunk itself (fast path removed) and prove it with the recorded hash,
   # so the check needs neither the offsets nor a snapshot of an earlier text state.
   m0='// NATIVE_BOUNDED_BLOCKS_V1 '+r['function']+' '+r['address'];pos0=marker_positions.get((r['function'],r['address']));assert pos0 is not None,'Earlier stage invalidated qualified block '+r['address']
   lab0='loc_'+r['address']+': ;';begin0=current_index[r['function']][2][r['address']][0]
   gm=re.search(r'goto loc_([0-9A-F]{8});',s[pos0:pos0+800]);assert gm,r['address']
   resume_pos=current_index[r['function']][2][gm[1]][0];cur0=s[begin0:resume_pos]
   inj0=lab0+'\n    '+m0+'\n    if('+('native_blocks_on && (ctx.msr&0x2000)' if r['fp'] else 'native_blocks_on')+'){native_block_'+r['function']+'_'+r['address']+'(ctx);goto loc_'+gm[1]+';}\n'
   assert inj0 in cur0,'Qualified fast path changed '+r['address']
   raw=cur0.replace(inj0,lab0,1);assert region.active().trusted(r['sha256'],hashlib.sha256(raw.encode()).hexdigest(),int(r['function'],16)),'Qualified original path changed: requalify '+r['address']
   resume_address=gm[1]
  else:
   raw=original[start:end];assert region.active().trusted(r['sha256'],hashlib.sha256(raw.encode()).hexdigest(),int(r['function'],16)),r['address']
  clean=re.sub(r'//[^\n]*','',raw)
  callees=set(re.findall(r'([A-Za-z_][A-Za-z_0-9:]*)\(',clean))
  assert callees<=set(('std::rotl','std::countl_zero')),('Impure register region',r['address'],callees)
  assert not re.search(r'ctx\.gpr\[(?!\d+\])',clean),'Dynamic register index '+r['address']
  name='native_block_'+r['function']+'_'+r['address'];data=dict(r,body=raw)
  contents+=bc.helper(data,name)
  marker='// NATIVE_BOUNDED_BLOCKS_V1 '+r['function']+' '+r['address']
  label='loc_'+r['address']+': ;'
  guard='native_blocks_on'+(' && (ctx.msr&0x2000)' if r['fp'] else '')
  injection=label+'\n    '+marker+'\n    if('+guard+'){'+name+'(ctx);goto loc_'+resume_address+';}\n'
  if staged:
   assert marker in s,'Earlier stage invalidated qualified block '+r['address']
   pos=s.index(marker);begin=s.rfind(label,0,pos);end=s.index('loc_'+resume_address+': ;',pos)
   current=s[begin:end];assert injection in current,'Qualified fast path changed '+r['address']
   assert current.replace(injection,label,1)==raw,'Qualified original path changed: requalify '+r['address']
   continue
  assert s[start:end]==raw,'Generated block changed: requalify '+r['address']
  replacement=raw.replace(label,injection,1)
  s=s[:start]+replacement+s[end:]
 if not header.exists() or header.read_text()!=contents:header.write_text(contents)
 include='#include "'+header.as_posix()+'"'
 if include not in s:s=s.replace('#include "functions.h"','#include "functions.h"\n'+include,1)
 if f.read_text()!=s:f.write_text(s)
manifest=dict(regions=len(selected),instructions=sum(r['instructions'] for r in selected),functions=len({r['function'] for r in selected}),chunks=len(byfile),selected_checks=sum(r['checks'] for r in selected),rejected_state_regions=report['failed_regions'],fp_regions_deferred='All FP regions remain original: local lowering can alter NaN payload selection; sampling is insufficient proof',guard='disabled flag; integer-only regions',fallback='all original interior labels, operations and faults retained',game_launched=False,regions_metadata=selected)
bc.record_report('bounded-block-adoption.json',manifest)
print({k:v for k,v in manifest.items() if k!='regions_metadata'})
