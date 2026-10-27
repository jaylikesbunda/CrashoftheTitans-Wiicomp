// offsetof probe: prints the CPUContext byte offsets the IR emitter needs.
// Compiled with the same headers/config as the harness (layout is stdlib dependent).
#include "runtime/cpu_context.h"
#include <cstdio>
#include <cstddef>
using namespace nwii::runtime;
int main() {
  printf("{\n");
  printf("  \"gpr\": %zu,\n", offsetof(CPUContext, gpr));
  printf("  \"cr\": %zu,\n", offsetof(CPUContext, cr));
  printf("  \"pc\": %zu,\n", offsetof(CPUContext, pc));
  printf("  \"lr\": %zu,\n", offsetof(CPUContext, lr));
  printf("  \"xer\": %zu,\n", offsetof(CPUContext, xer));
  printf("  \"inst_count\": %zu,\n", offsetof(CPUContext, inst_count));
  printf("  \"guest_bytes\": %zu,\n", offsetof(CPUContext, backup_stack));
  printf("  \"mem1_base\": %zu,\n",
         offsetof(CPUContext, mmu) + offsetof(MMU, mem1) + offsetof(NativeWatchedRam, base_));
  printf("  \"mem2_base\": %zu,\n",
         offsetof(CPUContext, mmu) + offsetof(MMU, mem2) + offsetof(NativeWatchedRam, base_));
  printf("  \"ctx_size\": %zu,\n", sizeof(CPUContext));
  printf("  \"mem1_data\": %zu,\n", offsetof(MMU, mem1) + offsetof(NativeWatchedRam, base_));
  printf("  \"sizeof_nwr\": %zu\n", sizeof(NativeWatchedRam));
  printf("}\n");
  return 0;
}
