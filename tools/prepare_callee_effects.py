"""Build effects from final staged source, with portable exclusion evidence."""
from pathlib import Path
import re,json,hashlib
from guest_callee_effects import Registry,experimental_promoter,PREFIX
def prepare(port):
 port=Path(port);excluded=set(json.loads((port/'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
 sources={}
 for file in sorted((port/'build/crash-generated').glob('output_*.cpp')):
  if file.name=='output_74.cpp':continue
  text=file.read_text()
  for head in re.finditer(r'^void (func_([0-9A-F]{8}))\(NWII_CTX_PARAM ctx\) \{',text,re.M):
   if head[2] in excluded:continue
   end=text.find('\n}\n',head.end())
   if end>=0:sources[head[1]]=text[head.start():end+2]
 registry=Registry(sources)
 for name in sources:registry.infer(name)
 header=PREFIX.replace('inline bool enabled=std::getenv("NWII_CALLEE_SUMMARY")!=nullptr;', '''inline const bool enabled=!std::getenv("NWII_NO_CALLEE_EFFECTS") && !std::getenv("NWII_SAMPLE") && !std::getenv("NWII_STARTUP_PROFILE") && !std::getenv("NWII_PEEK") && !std::getenv("NWII_LOOPTRACE");''')
 header=header.replace('inline void record(bool fast,unsigned spills,unsigned reloads){if(instrument){', 'inline void record(bool fast,unsigned spills,unsigned reloads){if(native_hud::on.load(std::memory_order_relaxed)){(fast?native_pipeline::effect_fast:native_pipeline::effect_fallback).fetch_add(1,std::memory_order_relaxed);native_pipeline::saved_stores.fetch_add(spills,std::memory_order_relaxed);native_pipeline::saved_loads.fetch_add(reloads,std::memory_order_relaxed);}if(instrument){')
 header='#pragma once\n#include <cstdlib>\n#include <cstdint>\n#include "runtime/native_hud.h"\n#include "runtime/native_pipeline_stats.h"\n'+header
 dst=port/'build/nwii-full-source/nWiiRuntime/include/runtime/native_callee_effects.h'
 if not dst.exists() or dst.read_text()!=header:dst.write_text(header)
 report=dict(schema=1,eligible_callees=len(registry.effects),source_functions=len(sources),source_sha256={n:e.source_sha256 for n,e in registry.effects.items()},guarded_ram_callees=sum(bool(e.memory) for e in registry.effects.values()),fpu_callees=sum(e.needs_fpu for e in registry.effects.values()),exclusions=len(excluded),codegen_sha256=hashlib.sha256(header.encode()).hexdigest(),game_launched=False)
 (port/'reports/callee-effect-registry.json').write_text(json.dumps(report,indent=2)+'\n')
 return experimental_promoter(registry)
