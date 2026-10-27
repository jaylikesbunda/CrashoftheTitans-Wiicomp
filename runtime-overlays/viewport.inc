// NATIVE_GX_VIEWPORT_V1: XF viewport floats are raw IEEE words.
struct NativeViewport {
  GLint x=0,y=0,width=640,height=kNativeEfbHeight;
  double near_depth=0,far_depth=1;
};
static NativeViewport NativeDecodeViewport(const uint32_t* xf) {
  NativeViewport out;
  float v[6];
  for(unsigned i=0;i<6;++i) std::memcpy(v+i,xf+0x1a+i,4);
  // An untouched XF register file has no usable viewport yet. GX's ordinary
  // viewport uses positive X scale and negative Y scale (top-origin screen).
  for(float value:v) if(!std::isfinite(value)) return out;
  if(v[0]<=0 || v[1]>=0) return out;
  const double left=double(v[3])-342.0-v[0];
  const double bottom=kNativeEfbHeight-(double(v[4])-342.0-v[1]);
  const double width=2.0*v[0],height=-2.0*v[1];
  if(std::fabs(left)>32768 || std::fabs(bottom)>32768 || width<1 || height<1 || width>32768 || height>32768) return out;
  out.x=GLint(std::lround(left));out.y=GLint(std::lround(bottom));
  out.width=GLint(std::lround(width));out.height=GLint(std::lround(height));
  constexpr double max_depth=16777215.0;
  // Existing projection conversion maps GX clip -w..0 to GL -w..w:
  // near = farZ - zRange, far = farZ. glDepthRange supports reversed ranges.
  out.near_depth=(double(v[5])-v[2])/max_depth;
  out.far_depth=double(v[5])/max_depth;
  return out;
}
static NativeViewport NativeApplyViewport(const uint32_t* xf,int scale=1) {
  const auto v=NativeDecodeViewport(xf);
  glViewport(v.x*scale,v.y*scale,v.width*scale,v.height*scale);
  glDepthRange(v.near_depth,v.far_depth);
  return v;
}
