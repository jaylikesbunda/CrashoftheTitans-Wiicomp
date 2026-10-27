// NATIVE_HOT_PGO_V1 (generate mode only): flush gcov counters from a running instrumented build.
//
// The instrumented game normally writes its .gcda files from the gcov exit handler. If the process
// ends without running it (window kill, crash, ExitProcess), the profile would be lost. A watcher
// thread therefore dumps the counters whenever the trigger file appears:
//
//   NWII_PGO_TRIGGER=<path>   trigger file (default: pgo-dump.trigger next to the executable)
//
// Create the file (e.g. `type nul > pgo-dump.trigger`) while playing. The watcher deletes it, calls
// __gcov_dump() then __gcov_reset(), and writes <trigger>.done next to it. Reset keeps the final
// exit-time dump from counting the same executions twice: gcov merges each dump into the existing file.
//
// Only the guest thread runs the instrumented hot units, so counters use -fprofile-update=single.
// The watcher only reads/zeroes them; a few lost increments around a dump do not matter for a profile.
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>
#include <windows.h>

extern "C" void __gcov_dump(void);
extern "C" void __gcov_reset(void);

namespace {
std::string trigger_path() {
    if (const char* env = std::getenv("NWII_PGO_TRIGGER"); env && *env) return env;
    char module[MAX_PATH]{};
    const DWORD n = GetModuleFileNameA(nullptr, module, MAX_PATH);
    std::string dir(module, n);
    const auto slash = dir.find_last_of("\\/");
    dir.resize(slash == std::string::npos ? 0 : slash + 1);
    return dir + "pgo-dump.trigger";
}

struct Watcher {
    Watcher() {
        std::thread([] {
            const std::string trigger = trigger_path();
            std::fprintf(stderr, "[PGO] instrumented build; create %s to flush the profile\n", trigger.c_str());
            for (;;) {
                std::this_thread::sleep_for(std::chrono::milliseconds(250));
                if (GetFileAttributesA(trigger.c_str()) == INVALID_FILE_ATTRIBUTES) continue;
                DeleteFileA(trigger.c_str());
                __gcov_dump();
                __gcov_reset();
                std::fprintf(stderr, "[PGO] profile flushed\n");
                if (FILE* done = std::fopen((trigger + ".done").c_str(), "wb")) std::fclose(done);
            }
        }).detach();
    }
} watcher;
}  // namespace
