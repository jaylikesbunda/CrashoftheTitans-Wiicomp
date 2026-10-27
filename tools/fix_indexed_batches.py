"""Use a VAO-owned element buffer; preserve triangle stream and archive format."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]
path=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
s=path.read_text()
if 'NATIVE_INDEXED_BATCH_V1' not in s:
    a=s.index('static void EmitPrimitive(const GXCommand &cmd) {');b=s.index('\n}',a)+2
    (p/'runtime-overlays/indexed_batch_baseline.inc').write_text(s[a:b])
    s=s[:a]+(p/'runtime-overlays/indexed_primitive.inc').read_text().rstrip()+s[b:]
    def replace(old,new):
        global s
        assert s.count(old)==1,old
        s=s.replace(old,new,1)
    replace('static std::vector<BatchVertex> s_batch;', 'static std::vector<BatchVertex> s_batch;\nstatic std::vector<uint32_t> s_indices;')
    replace('static GLuint s_vao = 0, s_vbo = 0;', 'static GLuint s_vao = 0, s_vbo = 0, s_ebo = 0;')
    replace('    glGenBuffers(1, &s_vbo);','    glGenBuffers(1, &s_vbo);\n    glGenBuffers(1, &s_ebo);')
    replace('  glDrawArrays(GL_TRIANGLES, 0, s_batch.size());', '''  if (s_indices.empty()) {
    glDrawArrays(GL_TRIANGLES, 0, s_batch.size());
  } else {
  glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, s_ebo);
  glBufferData(GL_ELEMENT_ARRAY_BUFFER, s_indices.size()*sizeof(uint32_t),
               s_indices.data(), GL_STREAM_DRAW);
  glDrawElements(GL_TRIANGLES, GLsizei(s_indices.size()), GL_UNSIGNED_INT, nullptr);
  }''')
    replace('  s_batch.clear();','  s_batch.clear();\n  s_indices.clear();')
    replace('word(uint32_t(s_batch.size()));','word(uint32_t(s_indices.empty()?s_batch.size():s_indices.size()));')
    replace('std::fwrite(s_batch.data(),sizeof(BatchVertex),s_batch.size(),file);',
            'if(s_indices.empty()) std::fwrite(s_batch.data(),sizeof(BatchVertex),s_batch.size(),file);\n  else for (uint32_t index : s_indices) std::fwrite(&s_batch[index],sizeof(BatchVertex),1,file);')
    path.write_text(s)
else:
    a=s.index('static void EmitPrimitive(const GXCommand &cmd) {');b=s.index('\n}',a)+2
    s=s[:a]+(p/'runtime-overlays/indexed_primitive.inc').read_text().rstrip()+s[b:]
    if 'if (s_indices.empty()) {' not in s:
        s=s.replace('  glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, s_ebo);','  if (s_indices.empty()) {\n    glDrawArrays(GL_TRIANGLES, 0, s_batch.size());\n  } else {\n  glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, s_ebo);',1)
        s=s.replace('  glDrawElements(GL_TRIANGLES, GLsizei(s_indices.size()), GL_UNSIGNED_INT, nullptr);','  glDrawElements(GL_TRIANGLES, GLsizei(s_indices.size()), GL_UNSIGNED_INT, nullptr);\n  }',1)
        s=s.replace('word(uint32_t(s_indices.size()));','word(uint32_t(s_indices.empty()?s_batch.size():s_indices.size()));',1)
        s=s.replace('for (uint32_t index : s_indices) std::fwrite(&s_batch[index],sizeof(BatchVertex),1,file);','if(s_indices.empty()) std::fwrite(s_batch.data(),sizeof(BatchVertex),s_batch.size(),file);\n  else for (uint32_t index : s_indices) std::fwrite(&s_batch[index],sizeof(BatchVertex),1,file);',1)
    if path.read_text()!=s:path.write_text(s)
# EFB-probe refresh stages replace archive helpers on every canonical build.
path=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp';s=path.read_text()
s=s.replace('word(uint32_t(s_batch.size()));','word(uint32_t(s_indices.empty()?s_batch.size():s_indices.size()));',1)
old='std::fwrite(s_batch.data(),sizeof(BatchVertex),s_batch.size(),file);'
if 'if(s_indices.empty()) '+old not in s:
    s=s.replace(old,'if(s_indices.empty()) '+old+'\n  else for (uint32_t index : s_indices) std::fwrite(&s_batch[index],sizeof(BatchVertex),1,file);',1)
if path.read_text()!=s:path.write_text(s)
path=p/'tools/build_native.py';s=path.read_text()
if "run('native-indexed-batches-stage'" not in s:
    anchor="run('native-game-build',";assert s.count(anchor)==1
    path.write_text(s.replace(anchor,"run('native-indexed-batches-stage',[sys.executable,PORT/'tools/fix_indexed_batches.py'])\n"+anchor,1))
print('Indexed triangle batches and expanded archive compatibility installed.')
