"""Qualify captured selected PPC paths and lower integer segments to region SSA.

This is a structural experiment, not an integrated runtime or a CPU benchmark.
It preserves observer boundaries, keeps memory effects guarded and ordered, and
does not infer private RAM from observed values. Outputs must be outside repo.
"""
from pathlib import Path
import argparse
import collections
import hashlib
import json
import re
import sys

PORT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PORT/'compiler'))
from region_ssa import Region, emit_cpp
from trace_guest_ir import FIELDS, Instruction, Unsupported, lower
from guest_region_sources import functions_from_source, words_from_dol
from guest_region_trace import qualified_owners


def selected_instructions(generated,dol):
    allowed={int(a,16) for a in qualified_owners()}
    sources={}
    for path in sorted(generated.glob('output_*.cpp')):
        if path.name=='output_74.cpp':continue
        found=functions_from_source(path,allowed)
        for pc,source in found.items():
            if pc in sources and sources[pc]!=source:
                raise ValueError('Ambiguous selected generated source')
            sources[pc]=source
    if sources.keys()!=allowed:
        raise ValueError('Missing qualified selected source')
    excluded={int(a,16) for a in json.loads((PORT/'reference/guest-regions/effect-exclusions.json').read_text())['addresses']}
    units={};bindings={}
    for owner,source in sources.items():
        labels=list(re.finditer(r'^loc_([0-9A-F]{8}): ;',source,re.M))
        bindings[f'{owner:08X}']=hashlib.sha256(source.encode()).hexdigest()
        for i,label in enumerate(labels):
            pc=int(label[1],16)
            if pc in excluded:raise ValueError('Selected instruction intersects a held-out entry')
            text=source[label.end():labels[i+1].start() if i+1<len(labels) else len(source)]
            match=re.search(r'// 0x([0-9A-F]{8}): ([0-9A-F]{8})',text)
            if not match or int(match[1],16)!=pc:raise ValueError('Missing selected instruction annotation')
            units[pc]=Instruction(pc,int(match[2],16),text)
    actual=words_from_dol(dol,set(units))
    if any(actual.get(pc)!=node.word for pc,node in units.items()):
        raise ValueError('Selected-DOL and source opcode mismatch')
    return units,bindings


def complete_roots(capture,allow_prefix=False):
    complete=capture['state']==3 and capture['completed']==64
    prefix=allow_prefix and capture['state']==4 and 0<capture['completed']<64
    if not (complete or prefix):
        raise ValueError('Expected a complete, non-overflowed 64-call capture')
    if len(capture['rows'])!=capture['count']:
        raise ValueError('Capture row count mismatch')
    invocations=collections.defaultdict(list)
    owner_count=len(qualified_owners())
    for row in capture['rows']:
        if row['kind'] not in range(5) or row['owner'] not in range(owner_count):
            raise ValueError('Invalid trace record tag')
        invocations[row['invocation']].append(row)
    count=capture['completed']
    if not set(range(count))<=set(invocations):
        raise ValueError('Missing root invocation records')
    selected=[];outside=0
    for invocation in range(count):
        rows=invocations[invocation]
        start=next((i for i,row in enumerate(rows) if row['kind']==1),None)
        if start is None:raise ValueError('Incomplete root invocation')
        # Version 1 kept its active flag between roots. Ignore those explicitly
        # out-of-root records, never interpreting them as caller/callee edges.
        if any(row['kind']==2 for row in rows[:start]):raise ValueError('Unexpected root exit')
        outside+=start;depth=0;end=None
        for i,row in enumerate(rows[start:],start):
            if row['kind']==1:depth+=1
            elif row['kind']==2:
                depth-=1
                if depth==0:end=i+1;break
        if end is None or end!=len(rows):raise ValueError('Incomplete root invocation')
        selected.extend(rows[start:end])
    quality=dict(original_state=capture['state'],complete_prefix=prefix,
        completed_roots=count,selected_rows=len(selected),outside_root_rows_removed=outside,
        unfinished_tail_rows_removed=sum(len(rows) for inv,rows in invocations.items() if inv>=count))
    return selected,quality


def analyze(capture,units,allow_prefix=False):
    rows,_=complete_roots(capture,allow_prefix)
    segments=[];unknown=collections.Counter();observers=collections.Counter();counts=collections.Counter()
    current=Region(FIELDS);pcs=[];calls=0
    def close(pc,reason):
        nonlocal current,pcs,calls
        if pcs:
            if not current.effects or current.effects[-1].kind!='exit':current.finish(pc)
            hot=current.hot_values()
            segments.append(dict(pcs=list(pcs),calls=calls,reason=reason,region=current,
                hot_pure_values=sum(v.op not in {'const','input','loaded'} for v in hot),stats=dict(current.stats)))
        current=Region(FIELDS);pcs=[];calls=0
    for i,row in enumerate(rows):
        pc=int(row['pc'],16);kind=row['kind']
        if kind!=0:
            close(pc,'capture_boundary_'+str(kind))
            if kind in (3,4):observers['indirect_before' if kind==3 else 'indirect_after']+=1
            continue
        if pc not in units:
            counts['unqualified_labels']+=1;close(pc,'unqualified_label');continue
        counts['selected_instructions']+=1
        successor=int(rows[i+1]['pc'],16) if i+1<len(rows) and rows[i+1]['kind']==0 else None
        try:
            result=lower(units[pc],current,successor,set(units))
        except Unsupported as error:
            unknown[str(error)]+=1;close(pc,'unsupported');continue
        pcs.append(pc);counts['integer_instructions']+=1
        if result=='call':calls+=1;counts['internal_direct_calls']+=1
        if result=='exit':close(pc,'side_exit')
        elif len(pcs)>=512:close(successor if successor is not None else pc+4,'size_limit')
    close(0,'capture_end')
    counts['segments']=len(segments)
    return segments,dict(counts),dict(unknown.most_common()),dict(observers)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('capture',type=Path)
    ap.add_argument('--generated',type=Path,required=True)
    ap.add_argument('--dol',type=Path,default=PORT.parent/'orig/main.dol')
    ap.add_argument('--work',type=Path,required=True)
    ap.add_argument('--complete-prefix',action='store_true',help='Use only independently balanced completed roots before a buffer overflow')
    args=ap.parse_args();work=args.work.resolve()
    if work==PORT or PORT in work.parents:raise ValueError('Output must be outside repo')
    work.mkdir(parents=True,exist_ok=True)
    units,bindings=selected_instructions(args.generated,args.dol)
    capture=json.loads(args.capture.read_text())
    if tuple(capture['owners'])!=qualified_owners():raise ValueError('Unexpected capture owners')
    _,quality=complete_roots(capture,args.complete_prefix)
    segments,counts,unknown,observers=analyze(capture,units,args.complete_prefix)
    best=sorted(segments,key=lambda row:(row['calls'],len(row['pcs'])),reverse=True)[:8]
    for i,row in enumerate(best):
        (work/f'integer-segment-{i}.cpp').write_text('// Research-only integer lowering. FP and counter policy are not integrated.\n'+emit_cpp(row['region'],'IntegerSegment'+str(i)))
    result=dict(capture=str(args.capture.resolve()),executable_sha256=capture['sha256'],source_sha256=bindings,
        trace_quality=quality,counts=counts,unsupported=unknown,observer_boundaries=observers,
        longest_segments=[dict(instructions=len(row['pcs']),internal_direct_calls=row['calls'],end_reason=row['reason'],stats=row['stats'],hot_pure_values=row['hot_pure_values'],pcs=[f'{pc:08X}' for pc in row['pcs']]) for row in best],
        limitations=['Structural selected-path counts, not CPU-time coverage or a measured speedup.',
          'No private-memory proof inferred; real-path load/store forwarding remains disabled.',
          'FP, 64-bit instruction-count policy and the runtime embedding remain unimplemented.',
          'Indirect dispatch before/after records are conservatively separate observation boundaries.'])
    (work/'analysis.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('longest_segments','source_sha256')},indent=2))
    print('Selected segment sizes:',[(len(row['pcs']),row['calls']) for row in best])


if __name__=='__main__':main()
