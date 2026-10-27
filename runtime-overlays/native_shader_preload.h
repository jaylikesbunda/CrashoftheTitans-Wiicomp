#pragma once
// NATIVE_SHADER_PRELOAD_V1: GL-free reader for the program binary cache (see tools/fix_shader_preload.py).
// The worker thread reads and validates <cache>/<16 hex digits>.bin files and hands over owned bytes only.
// It never calls GL. glProgramBinary and the link check stay on the render thread.
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

namespace native_shader_preload {
// Layout written by NativeProgramCache::store in renderer_gl.cpp: magic[8], key u64, format u32, length u32, binary.
inline constexpr char kMagic[8] = {'N', 'W', 'P', 'B', 'C', '0', '0', '1'};
inline constexpr size_t kHeaderBytes = 24;
inline constexpr uint32_t kMaxBinaryBytes = 64u << 20; // same bound as the on-demand load

struct Entry {
  uint64_t key = 0;
  uint32_t format = 0;
  std::vector<char> data;
};

struct Counts {
  size_t read = 0;     // valid files handed to the render thread queue (taken or still queued)
  size_t rejected = 0; // unreadable, truncated, mis-named or oversized files
  size_t skipped = 0;  // valid files left on disk because a bound was reached
};

inline bool ReadEntry(const std::filesystem::path &path, Entry &entry) {
  std::ifstream in(path, std::ios::binary);
  if (!in) return false;
  char header[kHeaderBytes];
  in.read(header, kHeaderBytes);
  if (!in || std::memcmp(header, kMagic, sizeof kMagic) != 0) return false;
  uint64_t key = 0;
  uint32_t format = 0, length = 0;
  std::memcpy(&key, header + 8, 8);
  std::memcpy(&format, header + 16, 4);
  std::memcpy(&length, header + 20, 4);
  if (length == 0 || length > kMaxBinaryBytes) return false;
  // The file name is the key it was stored under, so a renamed or copied file cannot satisfy another shader.
  char name[32];
  std::snprintf(name, sizeof name, "%016llx", static_cast<unsigned long long>(key));
  if (path.stem().string() != name) return false;
  std::error_code size_error;
  const auto file_size = std::filesystem::file_size(path, size_error);
  if (size_error || file_size != kHeaderBytes + length) return false;
  entry.key = key;
  entry.format = format;
  entry.data.resize(length);
  in.read(entry.data.data(), std::streamsize(length));
  return bool(in);
}

class Loader {
  std::mutex mutex;
  std::deque<Entry> ready;
  size_t queued_bytes = 0;
  bool finished = false;
  bool stopping = false;
  size_t max_entries;
  size_t max_queued_bytes;
  Counts counts;
  std::thread thread;

  void run(std::filesystem::path directory) {
    std::vector<std::pair<std::filesystem::file_time_type, std::filesystem::path>> files;
    std::error_code error;
    for (std::filesystem::directory_iterator it(directory, error), end; !error && it != end; it.increment(error)) {
      const auto &path = it->path();
      if (path.extension() != ".bin") continue;
      std::error_code time_error;
      const auto stamp = std::filesystem::last_write_time(path, time_error);
      if (!time_error) files.emplace_back(stamp, path);
    }
    // Newest first: the binaries written most recently are the ones the last session compiled last.
    std::sort(files.begin(), files.end(), [](const auto &a, const auto &b) { return a.first > b.first; });
    for (const auto &file : files) {
      {
        std::lock_guard<std::mutex> lock(mutex);
        if (stopping || counts.read >= max_entries) break;
      }
      Entry entry;
      const bool valid = ReadEntry(file.second, entry);
      std::lock_guard<std::mutex> lock(mutex);
      if (stopping) break;
      if (!valid) {
        ++counts.rejected;
        continue;
      }
      if (queued_bytes + entry.data.size() > max_queued_bytes) {
        ++counts.skipped;
        continue;
      }
      queued_bytes += entry.data.size();
      ready.push_back(std::move(entry));
      ++counts.read;
    }
    std::lock_guard<std::mutex> lock(mutex);
    finished = true;
  }

public:
  // Bounds: entries read in total, and bytes held in the queue waiting for the render thread.
  explicit Loader(size_t entries = 256, size_t queued_bytes_max = size_t(64) << 20)
      : max_entries(entries), max_queued_bytes(queued_bytes_max) {}
  Loader(const Loader &) = delete;
  Loader &operator=(const Loader &) = delete;

  void start(std::filesystem::path directory) {
    if (thread.joinable()) return;
    try {
      thread = std::thread([this, directory] { run(directory); });
    } catch (const std::system_error &) {
      std::lock_guard<std::mutex> lock(mutex);
      finished = true; // no preload; every shader takes the on-demand path
    }
  }

  bool take(Entry &out) {
    std::lock_guard<std::mutex> lock(mutex);
    if (ready.empty()) return false;
    out = std::move(ready.front());
    ready.pop_front();
    queued_bytes -= out.data.size();
    return true;
  }

  // True once the directory scan has ended (the queue may still hold entries).
  bool scanned() {
    std::lock_guard<std::mutex> lock(mutex);
    return finished;
  }

  // True once the scan has ended and every queued entry has been taken.
  bool drained() {
    std::lock_guard<std::mutex> lock(mutex);
    return finished && ready.empty();
  }

  Counts snapshot() {
    std::lock_guard<std::mutex> lock(mutex);
    return counts;
  }

  ~Loader() {
    {
      std::lock_guard<std::mutex> lock(mutex);
      stopping = true;
    }
    if (thread.joinable()) thread.join();
  }
};
} // namespace native_shader_preload
