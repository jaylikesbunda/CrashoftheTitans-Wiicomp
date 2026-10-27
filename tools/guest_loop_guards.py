"""Hoist checks from proven, count-controlled RAM-to-FIFO loops.

Run after register promotion. Keep the original loop for every failed proof and
every interior entry. A fast lane has proven advancing input pointers, bounded
fixed-address scalar FIFO stores and no observer except its existing poll.
The poll materializes guest state exactly as before and leaves the fast lane;
resumed execution rechecks the memory range and machine state.
"""
import re
def apply(source,clone=False,fuse=False,records=False):
 # This operates on one already-promoted body, never the original fallback.
 labels={m[1]:m.start() for m in re.finditer(r'^loc_([0-9A-F]{8}): ;$',source,re.M)}
 candidates=[]
 for match in re.finditer(r'if \(ctrv != 0\) goto loc_([0-9A-F]{8});',source):
  label=match[1]
  if label not in labels or labels[label]>=match.start():continue
  start=labels[label];end=match.end();body=source[start:end]
  if body.count('goto loc_'+label+';')!=1:continue
  if any(start<previous_end and end>previous_start for previous_start,previous_end,_,_ in candidates):continue
  if len(body)>(18000 if records else 6000):continue
  reads=list(re.finditer(r'ctx\.mmu\.read_f32\((g\d+) \+ (-?\d+)\)',body))
  if len(reads)<2:continue
  roots=list(dict.fromkeys(m[1] for m in reads))
  if len(roots)>4 or (len(roots)!=1 and not (clone and records)):continue
  streams={};valid=True
  for root in roots:
   offsets=[int(m[2]) for m in reads if m[1]==root]
   increments=re.findall(r'\b'+root+r' = '+root+r' \+ (\d+);',body)
   if min(offsets)<0 or len(increments)!=1 or not 0<int(increments[0])<=256:valid=False;break
   inc=body.index(root+' = '+root+' + ')
   if any(m.end()>inc for m in reads if m[1]==root):valid=False;break
   streams[root]=(int(increments[0]),min(offsets),max(offsets),offsets)
  if not valid:continue
  reg=roots[0];step,first,last,offsets=streams[reg]
  # All reads precede the one pointer advance. Reject other pointer writes,
  # side calls and interior gotos; these invalidate the induction proof.
  poll_start=body.find('if (nwii::runtime::native_poll_due()) {')
  if poll_start<0:continue
  if any(len(re.findall(r'\b'+root+r'\s*(?:=(?!=)|\+\+|--|[-+*/&|^]=)',body[:poll_start]))!=1 for root in roots):continue
  if re.search(r'func_[0-9A-F]{8}\(|native_call_indirect\(|native_block_',body):continue
  if 'ctrv--;' not in body or 'native_poll_due()' not in body:continue
  interior=set(re.findall(r'^loc_([0-9A-F]{8}): ;$',body,re.M))-{label}
  # Cloning preserves every original label and all incoming interior entries.
  # Only the original head and the cloned backedge can enter the fast lane.
  if not clone and any(re.search(r'goto loc_'+x+r';',source) for x in interior):continue
  # A poll is the only allowed host observer in these shapes. Memory stores
  # stay unchanged, including FIFO redirection, watch and diagnostics handling.
  calls=re.findall(r'\b([A-Za-z_][\w:.]*)\s*\(',body)
  allowed={'if','float','int32_t','uint32_t','__builtin_expect','std::fma','std::fabs','std::sqrt','ctx.require_fpu','nwii::runtime::native_poll_due','process_pending_callbacks'}
  if any(name not in allowed and not name.startswith('ctx.mmu.') for name in calls):continue
  token='native_span_'+label;active=token+'_active'
  if clone:
   if re.search(r'ctx\.msr\s*(?:=(?!=)|[-+*/&|^]=)',body[:poll_start]):continue
   if body.count('native_poll_due()')!=1:continue
   stores=list(re.finditer(r'ctx\.mmu\.write_f32\((g\d+) \+ (-?\d+), \(float\)(f\d+)\)',body))
   if not 2<=len(stores)<=(8 if records else 2) or len({(m[1],m[2]) for m in stores})!=1:continue
   if len(re.findall(r'ctx\.mmu\.[A-Za-z_]\w*\(',body[:poll_start]))!=len(reads)+len(stores):continue
   dest,delta=stores[0][1],stores[0][2]
   if re.search(r'\b'+dest+r'\s*(?:=(?!=)|\+\+|--|[-+*/&|^]=)',body[:poll_start]):continue
   if re.search(r'\bctrv\s*(?:=(?!=)|\+\+|--|[-+*/&|^]=)',body[:poll_start].replace('ctrv--;','')) or body.count('ctrv--;')!=1:continue
   # Extract the complete nested poll block with its original spills/reloads.
   open_at=body.index('{',poll_start);depth=1;close_at=open_at+1
   while depth:
    depth+=int(body[close_at]=='{')-int(body[close_at]=='}');close_at+=1
   poll=body[poll_start:close_at]
   afterpoll='native_after_poll_'+label;fastlabel='native_fast_'+label;after='native_after_loop_'+label;legacy='native_legacy_loop_'+label
   pointers={root:active+'_'+root for root in roots}
   proofs='\n    '.join(pointers[root]+'=native_guest_loop::Preflight(ctx.mmu,'+root+',ctrv,'+str(v[0])+','+str(v[1])+','+str(v[2])+');' for root,v in streams.items())
   valid_pointers=' && '.join(pointers.values())
   invalidate=''.join(p+'=nullptr;' for p in pointers.values())
   original=body.replace('loc_'+label+': ;','loc_'+label+': ;\n    '+proofs+'\n    if('+valid_pointers+' && (ctx.msr&0x2000u) && nwii::runtime::g_native_fifo_ok && (('+dest+'+uint32_t('+delta+'))&0x3ffff000u)==0x0c008000u && !nwii::runtime::native_watch_span_may_hit('+dest+'+uint32_t('+delta+'),4)) goto '+fastlabel+';',1)
   original=original.replace(poll,poll[:poll.index('{')+1]+invalidate+poll[poll.index('{')+1:]+'\n'+afterpoll+': ;',1)
   gate_end=original.index('goto '+fastlabel+';')+len('goto '+fastlabel+';')
   original=original[:gate_end]+'\n'+legacy+': ;'+original[gate_end:]
   fast=body
   fast=re.sub(r'^loc_([0-9A-F]{8}): ;$',lambda m:(fastlabel if m[1]==label else 'native_fast_'+label+'_'+m[1])+': ;',fast,flags=re.M)
   exceptional=' || '.join('native_guest_loop::Exceptional('+pointers[root]+'+'+str(offset-v[1])+')' for root,v in streams.items() for offset in sorted(set(v[3])))
   fast=fast.replace(fastlabel+': ;',fastlabel+': ;\n    if('+exceptional+') goto '+legacy+';',1)
   fast=re.sub(r'ctx\.mmu\.read_f32\((g\d+) \+ (-?\d+)\)',lambda m:'native_guest_loop::F32('+pointers[m[1]]+'+'+str(int(m[2])-streams[m[1]][1])+')',fast)
   fast=re.sub(r'ctx\.mmu\.write_f32\('+dest+r' \+ '+re.escape(delta)+r', \(float\)(f\d+)\)',r'NWII_CTX_BARRIER(); nwii::runtime::GX_WGPIPE_WriteF32((float)\1)',fast)
   # MSR cannot change in this lane: ordinary RAM reads, ordinary FIFO writes,
   # no calls except the original poll, which immediately rejoins the legacy lane.
   fast=re.sub(r'^\s*if \(__builtin_expect\(ctx\.require_fpu\([^\n]+\n','\n',fast,flags=re.M)
   if fuse:
    writes=list(re.finditer(r'NWII_CTX_BARRIER\(\); nwii::runtime::GX_WGPIPE_WriteF32\(\(float\)(f\d+)\);',fast))
    replacements=[];k=0
    while k+1<len(writes):
     left,right=writes[k:k+2];between=fast[left.end():right.start()]
     # Only labels, comments and instruction bookkeeping can move relative
     # to the paired emission. Polls and guest register operations cannot.
     clean=re.sub(r'//[^\n]*|native_fast_\w+: ;|\+\+(?:ctx\.inst_count|ic);|(?:ctx\.inst_count|ic) \+= \d+;','',between)
     if clean.strip():k+=1;continue
     replacements.append((left.start(),right.end(),'native_guest_loop::WritePair((float)'+left[1]+',(float)'+right[1]+');'+between));k+=2
    if not records and not replacements:continue
    for a,b,value in reversed(replacements):fast=fast[:a]+value+fast[b:]
   fast=fast.replace(poll,poll[:-1]+invalidate+'goto '+afterpoll+';}',1)
   advances=''.join(pointers[root]+'+='+str(v[0])+';' for root,v in streams.items())
   fast=fast.replace('if (ctrv != 0) goto loc_'+label+';','if (ctrv != 0) {'+advances+'goto '+fastlabel+';}')
   changed=original+'\n    goto '+after+';\n'+fast+'\n'+after+': ;'
   candidates.append((start,end,changed,'    const uint8_t* '+', *'.join(pointers.values())+';\n'))
   continue
  changed=body.replace('loc_'+label+': ;','loc_'+label+': ;\n    '+active+'='+token+'.get(ctx.mmu,'+reg+',ctrv,'+str(step)+','+str(first)+','+str(last)+');',1)
  changed=re.sub(r'ctx\.mmu\.read_f32\('+reg+r' \+ (-?\d+)\)',lambda m:'('+active+'?native_guest_loop::F32('+active+'+'+str(int(m[1])-first)+'):ctx.mmu.read_f32('+reg+' + '+m[1]+'))',changed)
  changed=changed.replace('if (nwii::runtime::native_poll_due()) {','if (nwii::runtime::native_poll_due()) {'+token+'.invalidate();'+active+'=nullptr;',1)
  candidates.append((start,end,changed,'    native_guest_loop::Span '+token+'; const uint8_t* '+active+'=nullptr;\n'))
 for start,end,body,decl in reversed(candidates):source=source[:start]+body+source[end:]
 if candidates:
  at=source.index('{')+1;source=source[:at]+'\n'+''.join(c[3] for c in candidates)+source[at:]
 return source,len(candidates)
