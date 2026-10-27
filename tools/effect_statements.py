"""Token-aware statement boundaries for generated guest code (not a C++ rewrite).

Strings/comments and balanced call/index parentheses are preserved. Only body
statement terminators and block braces become boundaries. Unknown constructs
are still rejected by the effect verifier; their original code remains valid.
"""
import re
def split_body(lines):
 out=[]
 for line in lines:
  if re.match(r'^\s*(?:loc_[0-9A-F]+:|#)',line):out.append(line);continue
  parens=brackets=0;quote=None;escape=False;start=0;i=0
  while i<len(line):
   ch=line[i]
   if quote:
    if escape:escape=False
    elif ch=='\\':escape=True
    elif ch==quote:quote=None
   elif line[i:i+2]=='//':break
   elif ch in ('"',"'"):quote=ch
   elif ch=='(':parens+=1
   elif ch==')':parens-=1
   elif ch=='[':brackets+=1
   elif ch==']':brackets-=1
   elif not parens and not brackets and ch in ';{}':
    piece=line[start:i+1].strip()
    if piece:out.append('    '+piece)
    start=i+1
   i+=1
  tail=line[start:].strip()
  if tail:out.append('    '+tail)
 return out
def normalize(source,promoter):
 if re.search(r'else[^\n]*\bctx\b(?!\.)',source):return source
 parsed=promoter.parse(source)
 if parsed is None:return source
 body=parsed[-1];begin=source.splitlines().index(body[0]) if body else len(source.splitlines())-1
 # Find the exact body suffix; do not format the dispatcher/trace preamble.
 suffix='\n'.join(body)+'\n}'
 if not source.endswith(suffix):return source
 return source[:-len(suffix)]+'\n'.join(split_body(body))+'\n}'
