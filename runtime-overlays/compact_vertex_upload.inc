// NATIVE_COMPACT_VERTEX_UPLOAD_V1: inactive texgens are all-zero inputs.
static void NativeUploadBatch() {
  const unsigned generators=std::min(8u,std::max(1u,unsigned(g_state.bp[0]&15u)));
  const size_t stride=offsetof(BatchVertex,texgen)+generators*12;
  static std::vector<uint8_t> packed;
  const void* data=s_batch.data();
  if(stride!=sizeof(BatchVertex)) {
    if(packed.size()<stride*s_batch.size())packed.resize(stride*s_batch.size());
    for(size_t i=0;i<s_batch.size();++i)std::memcpy(packed.data()+i*stride,&s_batch[i],stride);
    data=packed.data();
  }
  static size_t previous_stride=0;
  if(stride!=previous_stride) {
    previous_stride=stride;
    glVertexAttribPointer(0,3,GL_FLOAT,GL_FALSE,GLsizei(stride),(void*)offsetof(BatchVertex,x));
    glVertexAttribPointer(1,4,GL_FLOAT,GL_FALSE,GLsizei(stride),(void*)offsetof(BatchVertex,r));
    glVertexAttribPointer(2,2,GL_FLOAT,GL_FALSE,GLsizei(stride),(void*)offsetof(BatchVertex,u));
    glVertexAttribPointer(3,3,GL_FLOAT,GL_FALSE,GLsizei(stride),(void*)offsetof(BatchVertex,nx));
    glVertexAttribPointer(4,4,GL_FLOAT,GL_FALSE,GLsizei(stride),(void*)offsetof(BatchVertex,color1));
    for(unsigned t=0;t<8;++t) {
      if(t<generators) {
        glEnableVertexAttribArray(5+t);
        glVertexAttribPointer(5+t,3,GL_FLOAT,GL_FALSE,GLsizei(stride),(void*)(offsetof(BatchVertex,texgen)+t*12));
      } else {glDisableVertexAttribArray(5+t);glVertexAttrib3f(5+t,0,0,0);}
    }
  }
  glBufferData(GL_ARRAY_BUFFER,GLsizeiptr(stride*s_batch.size()),data,GL_STREAM_DRAW);
  native_trace::mark("upload.bytes",stride*s_batch.size(),sizeof(BatchVertex)*s_batch.size());
}
