// One compiled variant of the real generated C++ guest function.
// Compile with -DREFNS=ref_o2 / ref_os and the desired -O level; the namespace
// lets several optimisation levels coexist in one binary.
#include "runtime/cpu_context.h"
namespace REFNS {
#include "func_8023B080.ref.cpp"
}
