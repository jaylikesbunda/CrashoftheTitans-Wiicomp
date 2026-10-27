// NATIVE_PIXEL_CENTER_V1: GX non-AA samples at 7/12; GL samples at 1/2.
// Shift clip coordinates so GL interpolates at the guest sample location.
// Column-major, including perspective w. EFB y runs opposite GL window y.
static void NativeCorrectPixelCenter(float* projection, float width, float height) {
  const float dx=1.0f/(6.0f*width), dy=1.0f/(6.0f*height);
  for(unsigned column=0;column<4;++column) {
    projection[column*4]-=dx*projection[column*4+3];
    projection[column*4+1]+=dy*projection[column*4+3];
  }
}
