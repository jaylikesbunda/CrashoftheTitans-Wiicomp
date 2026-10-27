"""Match host pacing, guest VI cadence and the guest update gate to custom FPS."""
from pathlib import Path
import json
import re

UPDATE_OWNER = '802198C0'
UPDATE_PC = '802198E4'
UPDATE_WORD = 'C002A8B8'
GUEST_MARKER = 'NATIVE_GUEST_FRAME_LIMIT_V1'
INCLUDE = '#include "runtime/native_frame_limit.h"\n'


def patch_guest_update(source):
    """Patch only the qualified threshold load, including promoted/resume copies."""
    labels=list(re.finditer(r'^loc_802198E4: ;$', source, re.M))
    if not labels:return source,0
    edits=[]
    for label in labels:
        next_label=re.search(r'^loc_[0-9A-F]{8}: ;$',source[label.end():],re.M)
        end=label.end()+next_label.start() if next_label else len(source)
        unit=source[label.end():end]
        if not re.search(r'// 0x'+UPDATE_PC+': '+UPDATE_WORD+r'\b',unit):
            raise ValueError('Guest frame gate instruction changed; requalify it')
        load=re.compile(r'ctx\.mmu\.read_f32\((?:ctx\.gpr\[2\]|g2) \+ -22344\)')
        matches=list(load.finditer(unit))
        if len(matches)!=1:raise ValueError('Guest frame gate load shape changed')
        if GUEST_MARKER in unit:
            if unit.count(GUEST_MARKER)!=1 or 'native_frame_limit::MinimumUpdateSeconds('+matches[0][0]+')' not in unit:
                raise ValueError('Malformed existing guest frame gate patch')
            continue
        if 'native_frame_limit::MinimumUpdateSeconds' in unit:
            raise ValueError('Unqualified partial guest frame gate patch')
        match=matches[0]
        new='/* '+GUEST_MARKER+' */ native_frame_limit::MinimumUpdateSeconds('+match[0]+')'
        edits.append((label.end()+match.start(),label.end()+match.end(),new))
    for start,end,new in reversed(edits):source=source[:start]+new+source[end:]
    if INCLUDE not in source:source=INCLUDE+source
    return source,len(labels)


def apply(port):
    excluded=set(json.loads((Path(port)/'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
    if {UPDATE_OWNER,UPDATE_PC}&excluded:
        raise ValueError('Guest frame gate intersects held-out exclusions')
    p=Path(port);rt=p/'build/nwii-full-source/nWiiRuntime'
    header=rt/'include/runtime/native_frame_limit.h';value=(p/'runtime-overlays/native_frame_limit.h').read_text()
    if not header.exists() or header.read_text()!=value:header.write_text(value)
    f=rt/'src/core/main.cpp';s=f.read_text();marker='NATIVE_CUSTOM_FRAME_LIMIT_V1'
    if marker not in s:
     old='  const auto field_period = std::chrono::nanoseconds(int64_t((field_pal ? 20000000 : 16683333) / turbo_factor));'
     new='  const auto field_period = native_frame_limit::Period(field_pal,turbo_factor,std::getenv("NWII_FPS_LIMIT")); // '+marker
     assert s.count(old)==1;s='#include "runtime/native_frame_limit.h"\n'+s.replace(old,new,1);f.write_text(s)
    print('Custom FPS overrides regional field pacing; unchanged default and turbo fallback.')

    # Match the guest VI scheduler to a custom cap when its single source is used.
    f=rt/'src/hle/ios.cpp';text=f.read_text();tag='NATIVE_CUSTOM_VI_PERIOD_V1'
    if tag not in text:
     anchor='              vi_period = vi_pal ? ctx.tb_freq / 50 : ctx.tb_freq * 1001 / 60000;'
     assert text.count(anchor)==1
     text='#include "runtime/native_frame_limit.h"\n'+text.replace(anchor,anchor+'\n              if(std::getenv("NWII_FPS_LIMIT"))vi_period=native_frame_limit::GuestPeriodTicks(vi_pal,ctx.tb_freq,std::getenv("NWII_FPS_LIMIT")); // '+tag,1);f.write_text(text)
    # NATIVE_CUSTOM_VI_PERIOD_OUTER_V1: the FPS cap must apply even when the guest VI
    # source is not NWII_SINGLE_VI (the override above is nested in that block, so the
    # guest otherwise stays at the hard tb_freq/60 while the host loop follows the cap).
    tag2='NATIVE_CUSTOM_VI_PERIOD_OUTER_V1'
    if tag2 not in text:
     anchor2='          if (vi_period == 0) vi_period = 500000;'
     assert text.count(anchor2)==1,('vi_period zero anchor',text.count(anchor2))
     outer=('          // '+tag2+': the FPS cap applies regardless of the VI source, matching the host loop.\n'
            '          if(std::getenv("NWII_FPS_LIMIT")){\n'
            '            const auto &vi_cap_id=nwii::runtime::Config::get().game_id;\n'
            "            const char vi_cap_region=vi_cap_id.size()>=4?vi_cap_id[3]:'E';\n"
            "            const bool vi_cap_pal=vi_cap_region=='P'||vi_cap_region=='D'||vi_cap_region=='F'||vi_cap_region=='I'||vi_cap_region=='S'||vi_cap_region=='H'||vi_cap_region=='U'||vi_cap_region=='X'||vi_cap_region=='Y'||vi_cap_region=='Z';\n"
            '            vi_period=native_frame_limit::GuestPeriodTicks(vi_cap_pal,ctx.tb_freq,std::getenv("NWII_FPS_LIMIT"));\n'
            '          }\n')
     text=text.replace(anchor2,outer+anchor2,1);f.write_text(text)
    # Observe the actual normal/paused virtual update, after its elapsed-time dt
    # reaches f1 and only when a non-null target is invoked.
    for file in (p/'build/crash-generated').glob('output_*.cpp'):
     text=file.read_text()
     if 'loc_80218330: ;' not in text:continue
     text=text.replace('    native_frame_limit::RecordTimestep(ctx.fpr[1]); // NATIVE_GAME_TIMESTEP_METRIC_V1\n','')
     if 'NATIVE_GAME_TIMESTEP_METRIC_V2' not in text:
      for pc in ('80218330','80218364'):
       anchor='    ctx.lr = 0x'+f'{int(pc,16)+4:08X}'+'; // save return address'
       assert text.count(anchor)==1
       text=text.replace(anchor,anchor+'\n    if(ctx.ctr)native_frame_limit::RecordTimestep(ctx.fpr[1]); // NATIVE_GAME_TIMESTEP_METRIC_V2',1)
      if '#include "runtime/native_frame_limit.h"' not in text:text='#include "runtime/native_frame_limit.h"\n'+text
      file.write_text(text)
     break
    else:raise SystemExit('CUberManager timestep observation anchor changed')

    for file in (p/'build/crash-generated').glob('output_*.cpp'):
        text=file.read_text()
        new,count=patch_guest_update(text)
        if not count:continue
        if new!=text:file.write_text(new)
        print(f'Guest update minimum follows custom FPS at {count} qualified threshold load(s).')
        break
    else:raise SystemExit('Guest frame gate instruction missing; requalify it')


if __name__=='__main__':apply(Path(__file__).resolve().parents[1])
