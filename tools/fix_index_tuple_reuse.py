"""Reuse identical indexed vertex records within one draw, never across draws."""
from pathlib import Path
p=Path(__file__).resolve().parents[1];rt=p/'build/nwii-full-source/nWiiRuntime'
header=rt/'include/runtime/gx_command.h';s=header.read_text()
if 'NATIVE_INDEX_TUPLE_REUSE_V1' not in s:
    anchor='    std::vector<VertexData> vertices;'
    assert s.count(anchor)==1
    s=s.replace(anchor,anchor+'''
    // NATIVE_INDEX_TUPLE_REUSE_V1: logical vertex sequence -> unique vertices.
    // Empty means the ordinary expanded representation. Draw-local only.
    std::vector<uint32_t> native_vertex_indices;
    size_t NativeVertexCount() const {return native_vertex_indices.empty()?vertices.size():native_vertex_indices.size();}
    const VertexData& NativeVertex(size_t i) const {return vertices[native_vertex_indices.empty()?i:native_vertex_indices[i]];}
''')
    header.write_text(s)
parser=rt/'src/hle/gx/fifo_parser.cpp';s=parser.read_text()
if '// NATIVE_INDEX_TUPLE_CACHE_V1' not in s:
    (p/'runtime-overlays/index_tuple_parser_baseline.cpp').write_text(s)
    a=s.index('template<int P,int N,int T> static void NativeIndexedDecodeDraw(');z=s.index('// NATIVE_INDEXED_DRAW_DECODE_V1',a)
    body=s[a:z]
    anchor='    c.vertices.resize(r.count);'
    assert body.count(anchor)==1
    body=body.replace(anchor,'''
    // NATIVE_INDEX_TUPLE_CACHE_V1. Full byte-record comparisons resolve hash
    // collisions. RAM is read only during this draw; no persistent cache.
    static const bool disable_reuse=std::getenv("NWII_NO_INDEX_REUSE") ||
      std::getenv("NWII_NORMAL_DRAW_PROBE") || std::getenv("NWII_BLEND_DIAG") ||
      std::getenv("NWII_DRAWLOG") || std::getenv("NWII_VTXTRACE");
    const size_t record_bytes=VertexSize(r.vat);
    bool reuse=!disable_reuse && !g_skindbg && r.count>=32 && record_bytes>0 && record_bytes<=64 &&
      (c.prim_type==0x80 || c.prim_type==0x90 || c.prim_type==0x98 || c.prim_type==0xA0);
    // Don't pay a hash table for unshared geometry. Sample full records at
    // neighboring positions (common triangle lists repeat corners nearby).
    if(reuse) {
      unsigned repeated=0;const unsigned sample=std::min<unsigned>(r.count,96);
      for(unsigned i=1;i<sample;++i)for(unsigned d=1;d<=8 && d<=i;++d)
        if(!std::memcmp(buf.data()+size_t(i)*record_bytes,buf.data()+size_t(i-d)*record_bytes,record_bytes)){++repeated;break;}
      reuse=repeated>=sample/8;
    }
    std::vector<uint32_t> slots,first;
    size_t table_mask=0;uint32_t unique=0;
    c.native_vertex_indices.clear();
    if(reuse) {
      size_t capacity=1;while(capacity<size_t(r.count)*2)capacity*=2;
      slots.assign(capacity,UINT32_MAX);first.reserve(r.count);table_mask=capacity-1;
      c.native_vertex_indices.resize(r.count);
    }
    if(reuse)c.vertices.reserve(r.count/2+1);else c.vertices.resize(r.count);
''')
    anchor='        VertexData& vtx=c.vertices[i];'
    assert body.count(anchor)==1
    body=body.replace(anchor,'''
        if(reuse) {
          const uint8_t* record=buf.data()+off;
          uint64_t h=1469598103934665603ull;
          for(size_t j=0;j<record_bytes;++j)h=(h^record[j])*1099511628211ull;
          size_t slot=size_t(h)&table_mask;
          while(slots[slot]!=UINT32_MAX && std::memcmp(record,buf.data()+size_t(first[slots[slot]])*record_bytes,record_bytes))slot=(slot+1)&table_mask;
          if(slots[slot]!=UINT32_MAX) {
            c.native_vertex_indices[i]=slots[slot];off+=record_bytes;continue;
          }
          slots[slot]=unique;c.native_vertex_indices[i]=unique++;
          first.push_back(i);
          c.vertices.emplace_back();
        }
        VertexData& vtx=c.vertices[reuse?unique-1:i];''')
    body=body.replace('  // NATIVE_NORMAL_DRAW_PROBE_BEGIN','    if(reuse)c.vertices.resize(unique);\n  // NATIVE_NORMAL_DRAW_PROBE_BEGIN',1)
    s=s[:a]+body+s[z:];parser.write_text(s)
else:
    a=s.index('template<int P,int N,int T> static void NativeIndexedDecodeDraw(');z=s.index('// NATIVE_INDEXED_DRAW_DECODE_V1',a);body=s[a:z]
    if 'if(reuse)c.vertices.reserve' not in body:
        body=body.replace('    c.vertices.resize(r.count);\n','',1)
        body=body.replace('    const NativeVectorPlan pos(', '    if(reuse)c.vertices.reserve(r.count/2+1);else c.vertices.resize(r.count);\n    const NativeVectorPlan pos(',1)
        body=body.replace('          first.push_back(i);','          first.push_back(i);\n          c.vertices.emplace_back();',1)
        s=s[:a]+body+s[z:];parser.write_text(s)
renderer=rt/'src/hle/gx/renderer_gl.cpp';s=renderer.read_text()
if '// NATIVE_INDEX_TUPLE_EMIT_V1' not in s:
    (p/'runtime-overlays/index_tuple_renderer_baseline.cpp').write_text(s)
    a=s.index('static void EmitPrimitive(const GXCommand &cmd) {');z=s.index('\n}',a)+2
    body=s[a:z].replace('const size_t n=v.size();','const size_t n=cmd.NativeVertexCount();')
    anchor='  for(size_t i=0;i<count;++i) EmitVertex(v[i]);'
    assert body.count(anchor)==1
    body=body.replace(anchor,'''  if(!count)return;
  // NATIVE_INDEX_TUPLE_EMIT_V1: convert each decoded tuple once.
  const bool reused=!cmd.native_vertex_indices.empty();
  if(reused)for(const auto& vertex:v)EmitVertex(vertex);
  else for(size_t i=0;i<count;++i)EmitVertex(v[i]);
  auto index=[&](size_t i){return base+uint32_t(reused?cmd.native_vertex_indices[i]:i);};''')
    body=body.replace('base+i-((i&1)?1:2)','index(i-((i&1)?1:2))').replace('base+i-((i&1)?2:1)','index(i-((i&1)?2:1))')
    body=body.replace('base+i+3','index(i+3)').replace('base+i+2','index(i+2)').replace('base+i+1','index(i+1)').replace('base+i-1','index(i-1)').replace('base+i','index(i)')
    body=body.replace('s_indices.push_back(base);','s_indices.push_back(index(0));')
    body=body.replace('  case 0x90:\n    break;', '''  case 0x90:
    if(reused || !s_indices.empty()) {
      // A batch can contain expanded draws before/after a reused draw.
      if(s_indices.empty())for(uint32_t i=0;i<base;++i)s_indices.push_back(i);
      for(size_t i=0;i<count;++i)s_indices.push_back(index(i));
    }
    break;''')
    s=s[:a]+body+s[z:]
    # Keep draw and diagnostic counts logical, not the unique storage count.
    a=s.index('static void DrawPrimitive(const GXCommand &cmd) {');z=s.index('\n// NATIVE_UNIFORM_CACHE',a)
    s=s[:a]+s[a:z].replace('const size_t n = v.size();','const size_t n = cmd.NativeVertexCount();')+s[z:]
    s=s.replace('cmd.raw ? cmd.raw->count : cmd.vertices.size()','cmd.raw ? cmd.raw->count : cmd.NativeVertexCount()')
    renderer.write_text(s)
else:
    # Upgrade the first qualification candidate without changing its baseline.
    s=s.replace('  // NATIVE_INDEX_TUPLE_EMIT_V1:','  if(!count)return;\n  // NATIVE_INDEX_TUPLE_EMIT_V1:',1) if '  if(!count)return;\n  // NATIVE_INDEX_TUPLE_EMIT_V1:' not in s else s
    s=s.replace('    if(reused)for(size_t i=0;i<count;++i)s_indices.push_back(index(i));','''    if(reused || !s_indices.empty()) {
      if(s_indices.empty())for(uint32_t i=0;i<base;++i)s_indices.push_back(i);
      for(size_t i=0;i<count;++i)s_indices.push_back(index(i));
    }''')
    renderer.write_text(s)
print('Draw-local indexed tuple reuse staged; expanded path retained and opt-out available.')
