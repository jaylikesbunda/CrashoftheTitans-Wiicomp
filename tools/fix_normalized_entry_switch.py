"""Opt-in aligned word-index entry switches, preserving C++ case bodies/defaults."""
from pathlib import Path
import os,re,json,argparse

BEGIN='// NATIVE_NORMALIZED_ENTRY_SWITCH_V1 '
END='// END_NATIVE_NORMALIZED_ENTRY_SWITCH_V1'
CASE=re.compile(r'\bcase\s+(0x[0-9A-F]{1,8})\s*:')
HEAD=re.compile(r'\bvoid\s+(?:func_|native_guest_(?:resume|continuation|primary)_)([0-9A-F]{8})\(NWII_CTX_PARAM ctx\)\s*\{')

def closing(text,opening):
    depth=1;at=opening+1
    while depth:
        depth+=(text[at]=='{')-(text[at]=='}');at+=1
    return at

def restore(text):
    while BEGIN in text:
        start=text.index(BEGIN);meta_end=text.index('\n',start)
        meta=json.loads(text[start+len(BEGIN):meta_end])
        switch=text.index('switch (',meta_end);opening=text.index('{',switch);end=closing(text,opening)
        body=text[opening+1:end-1]
        def original(m):
            tag=int(m[1]);pc=meta['extra'].get(str(tag))
            return 'case '+(pc if pc else '0x%08X'%(meta['base']+tag*4))+':'
        body=re.sub(r'\bcase\s+(\d+)u\s*:',original,body)
        tail=text.index(END,end)+len(END)
        text=text[:start]+'switch (ctx.pc) {'+body+'}'+text[tail:]
    return text

def patch_text(text,excluded=(),min_cases=24,max_words=4096,min_density=.40):
    canonical=restore(text);heads=list(HEAD.finditer(canonical));edits=[];stats=[]
    for index,head in enumerate(heads):
        if head[1] in excluded:continue
        limit=closing(canonical,head.end()-1)
        for match in re.finditer(r'switch \(ctx\.pc\) \{',canonical[head.end():limit]):
            start=head.end()+match.start();opening=canonical.index('{',start);end=closing(canonical,opening)
            # Only entry selectors inside the original non-normal-PC guard.
            before=canonical[max(head.end(),start-240):start]
            if not re.search(r'if\s*\(ctx\.pc != 0x[0-9A-F]{8}\)\s*\{',before):continue
            body=canonical[opening+1:end-1];cases=list(CASE.finditer(body))
            # Reject unsupported decimal/expression case forms rather than
            # letting an unconverted case collide with the new word indices.
            if len(re.findall(r'\bcase\b',body))!=len(cases):continue
            values=[int(m[1],16) for m in cases];normal=[v for v in values if v]
            if len(cases)<min_cases or not normal or len(set(values))!=len(values):continue
            if any(v&3 for v in normal) or any(len(m[1])!=10 and int(m[1],16) for m in cases):continue
            base=min(normal);last=(max(normal)-base)//4;words=last+1
            if words>max_words or len(normal)/words<min_density:continue
            extra={};tags={v:(v-base)//4 for v in normal}
            for v in values:
                if not v:
                    tag=words+len(extra);tags[v]=tag
                    extra[str(tag)]=next(m[1] for m in cases if int(m[1],16)==v)
            invalid='0xffffffffu'
            for tag,pc in reversed(list(extra.items())):invalid='(ctx.pc=='+pc+'?'+tag+'u:'+invalid+')'
            expression='(((uint32_t(ctx.pc-0x%08Xu)&3u)==0 && uint32_t(ctx.pc-0x%08Xu)<=%du)?(uint32_t(ctx.pc-0x%08Xu)>>2):%s)'%(base,base,last*4,base,invalid)
            changed=CASE.sub(lambda m:'case '+str(tags[int(m[1],16)])+'u:',body)
            already='native_perf_census::GuestSwitch(ctx.pc,' in before
            metadata=dict(base=base,extra=extra,hook_added=not already)
            hook='' if already else 'native_perf_census::GuestSwitch(ctx.pc,'+str(len(cases))+');\n        '
            replacement=BEGIN+json.dumps(metadata,separators=(',',':'))+'\n        '+hook+'switch ('+expression+') {'+changed+'}\n        '+END
            edits.append((start,end,replacement));stats.append(dict(owner=head[1],cases=len(cases),words=words,density=len(normal)/words))
    for start,end,replacement in reversed(edits):canonical=canonical[:start]+replacement+canonical[end:]
    if edits and '#include "runtime/native_perf_census.h"' not in canonical:canonical='#include "runtime/native_perf_census.h"\n'+canonical
    return canonical,stats

def main():
    port=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser();parser.add_argument('--restore',action='store_true');args=parser.parse_args()
    enabled=not args.restore and os.environ.get('NWII_BUILD_NORMALIZED_ENTRY_SWITCH','0')=='1'
    excluded=set(json.loads((port/'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
    total=0
    for tree in ('crash-generated','crash-hot','crash-split'):
        for path in sorted((port/'build'/tree).glob('*.cpp')):
            if path.name in ('main_output.cpp','output_74.cpp'):continue
            source=path.read_text(errors='surrogateescape')
            result,rows=patch_text(source,excluded) if enabled else (restore(source),[])
            if result!=source:path.write_text(result,errors='surrogateescape')
            total+=len(rows)
    print('Normalized entry switches:',total,'build-opt-in:',enabled)

if __name__=='__main__':main()
