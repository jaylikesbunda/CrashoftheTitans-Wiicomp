"""Replay hash-qualified semantic register chains from the selected disc."""
from pathlib import Path
import hashlib,json,os,re,sys
import region
from guest_region_sources import restore_regions,words_from_dol

PORT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PORT/'compiler'))
from guest_leaf_ir import SCHEMA,lift_chain,emit,PREFIX,Unsupported

manifest=json.loads((PORT/'reference/guest-regions/register-chains.json').read_text())
assert manifest['schema']==SCHEMA and manifest['qualification']['passed']
release=region.active()
if not release.reference:
 # The manifest stores reference addresses as decimal integers; carry them over, dropping chains with no counterpart.
 kept=[]
 for r in manifest['regions']:
  entry=release.address(r['entry']);chain=[release.address(a) for a in r['chain_entries']]
  if entry is None or None in chain:continue
  kept.append(dict(r,entry=entry,chain_entries=chain))
 manifest['regions']=kept
expected={r['entry']:r for r in manifest['regions']}
allowed={pc for r in expected.values() for pc in r['chain_entries']}
file=PORT/'build/crash-generated'/region.chunk_file('%08X'%next(iter(expected)),'output_71.cpp');source=restore_regions(file.read_text())
# Inspect the restored text without a temporary copy or touching the reference.
heads=list(re.finditer(r'^void func_([0-9A-F]{8})\(NWII_CTX_PARAM ctx\) \{',source,re.M));sources={};spans={}
for head in heads:
 address=int(head[1],16)
 if address not in allowed:continue
 z=source.index('\n}\n',head.end())+2;sources[address]=source[head.start():z];spans[address]=(head.start(),z)
instructions={pc for text in sources.values() for pc in [int(x,16) for x in re.findall(r'// 0x([0-9A-F]{8}):',text)]}
words=words_from_dol(Path(os.environ.get('NWII_INPUT_DOL',PORT.parent/'orig/main.dol')),instructions)
regions=[];rejected=[]
for address,proof in expected.items():
 try:
  ir=lift_chain(sources,words,address)
  digest=hashlib.sha256(b''.join(n.word.to_bytes(4,'big') for n in ir['ops'])).hexdigest()
  policy=hashlib.sha256(json.dumps([[n.pc,n.before,n.after] for n in ir['ops']],separators=(',',':')).encode()).hexdigest()
  same=ir['source_sha256']==proof['source_sha256'] and digest==proof['instructions_sha256'] and policy==proof['counter_policy_sha256']
  # Another release has other relocated words and addresses in these hashes; its chain is the same code when every function is exact.
  if not (same if release.reference else all(release.exact(a) for a in proof['chain_entries'])):raise Unsupported('Qualification identity changed')
  regions.append(ir)
 except (Unsupported,KeyError) as error:rejected.append(dict(entry=address,reason=str(error)))
rt=PORT/'build/nwii-full-source/nWiiRuntime'
header=PREFIX+'\n'.join(emit(r) for r in regions)+'\n}\n'
for name,value in [('native_guest_regions.h',header),('native_guest_region_stats.h',(PORT/'runtime-overlays/native_guest_region_stats.h').read_text())]:
 target=rt/'include/runtime'/name
 if not target.exists() or target.read_text()!=value:target.write_text(value)
enabled={r['entry'] for r in regions}
original_entries={pc for r in regions for pc in r['chain_entries']}
for ir in sorted(regions,key=lambda r:spans[r['entry']][0],reverse=True):
 address=ir['entry'];tag=f'{address:08X}';a,z=spans[address];original=sources[address].replace('void func_'+tag+'(','void native_region_original_'+tag+'(',1)
 for target in original_entries:original=original.replace(f'func_{target:08X}(ctx);',f'native_region_original_{target:08X}(ctx);')
 args=['ctx.mmu','ctx.inst_count','ctx.lr','ctx.xer']+[f'ctx.gpr[{reg}]' for reg in ir['inputs']]
 wrapper=f'void func_{tag}(NWII_CTX_PARAM ctx) {{\n if(ctx.pc==0x{tag} && native_guest_regions::Enabled() && !nwii::runtime::g_trace_calls){{\n  const auto result=native_guest_regions::Try_{tag}('+','.join(args)+');\n  if(result.valid){\n'
 wrapper+=''.join(f'   ctx.gpr[{reg}]=result.r{reg};\n' for reg in ir['dirty'])
 wrapper+='   ctx.inst_count=result.count;ctx.pc=result.pc;\n   if(native_hud::on.load(std::memory_order_relaxed))native_guest_regions::fast_calls.fetch_add(1,std::memory_order_relaxed);\n   return;\n  }\n }\n if(native_hud::on.load(std::memory_order_relaxed))native_guest_regions::legacy_calls.fetch_add(1,std::memory_order_relaxed);\n native_region_original_'+tag+'(ctx);\n}'
 source=source[:a]+f'// BEGIN_NATIVE_GUEST_REGION_{tag}\n'+original+'\n'+wrapper+f'\n// END_NATIVE_GUEST_REGION_{tag}'+source[z:]
if regions:
 declarations='// BEGIN_NATIVE_GUEST_REGION_DECLS\n'+''.join(f'void native_region_original_{pc:08X}(NWII_CTX_PARAM ctx);\n' for pc in sorted(original_entries))
 extras='// BEGIN_NATIVE_GUEST_REGION_EXTRAS\n'
 for pc in sorted(original_entries-enabled):
  original=sources[pc].replace(f'void func_{pc:08X}(',f'void native_region_original_{pc:08X}(',1)
  for target in original_entries:original=original.replace(f'func_{target:08X}(ctx);',f'native_region_original_{target:08X}(ctx);')
  extras+=original+'\n'
 extras+='// END_NATIVE_GUEST_REGION_EXTRAS\n'
 declarations+='// END_NATIVE_GUEST_REGION_DECLS\n'
 end_marker=f'// END_NATIVE_GUEST_REGION_{max(enabled):08X}';position=source.index(end_marker)+len(end_marker)
 source=source[:position]+'\n'+extras+source[position:]
 first=source.index('void func_');source=source[:first]+declarations+source[first:]
 source='#include "runtime/native_guest_regions.h" // NATIVE_GUEST_REGIONS_V1\n'+source
if file.read_text()!=source:file.write_text(source)
report=dict(schema=SCHEMA,adopted=len(regions),entries=sorted(enabled),rejected=rejected,selected_dol=True,guard='Exact entry, tracing/diagnostics disabled, complete ordinary span before any writes; original chain fallback',game_launched=False)
(PORT/'reports/guest-region-adoption.json').write_text(json.dumps(report,indent=2)+'\n')
print('Qualified semantic guest register chains:',len(regions),'rejected:',len(rejected))
