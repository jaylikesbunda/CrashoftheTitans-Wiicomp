// NATIVE_NAND_FS_V1
// Host-backed Wii NAND (ISFS) for /dev/fs. Replaces the former in-memory file
// list that answered every ISFS ioctl with success and an empty reply, which
// made the NAND check report no space/no save directory. Files persist under
// $NWII_NAND_DIR (default ./nand beside the runtime state), so saves survive
// restarts. Guest paths are confined to that root.
#include "runtime/devices.h"
#include <algorithm>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

namespace nwii::runtime::devices {

namespace fs = std::filesystem;

namespace {

constexpr int32_t FS_EINVAL = -101;
constexpr int32_t FS_EEXIST = -105;
constexpr int32_t FS_ENOENT = -106;
constexpr int32_t FS_EFDEXHAUSTED = -109;
constexpr int32_t FS_EIO = -114;
constexpr uint32_t CLUSTER = 0x4000;
constexpr int FD_BASE = 20;
constexpr int FD_MAX = 16;

struct Handle {
    bool open = false;
    fs::path host;
    std::unique_ptr<std::fstream> stream;
    uint32_t pos = 0;
};

Handle g_handles[FD_MAX];
fs::path g_root;
bool g_ready = false;

uint32_t virt(uint32_t addr) {
    if (addr < 0x01800000 || (addr >= 0x10000000 && addr < 0x14000000))
        return addr | 0x80000000;
    return addr;
}

std::string guest_string(CPUContext& ctx, uint32_t addr, size_t max) {
    std::string s;
    for (size_t i = 0; i < max; ++i) {
        char c = (char)ctx.mmu.read8(virt(addr) + (uint32_t)i);
        if (!c) break;
        s += c;
    }
    return s;
}

void write_string(CPUContext& ctx, uint32_t addr, const std::string& s, size_t width) {
    for (size_t i = 0; i < width; ++i)
        ctx.mmu.write8(virt(addr) + (uint32_t)i, i < s.size() ? (uint8_t)s[i] : 0);
}

// Maps a guest path onto the host root; empty on anything that escapes it.
fs::path host_path(const std::string& guest) {
    if (guest.empty() || guest[0] != '/') return {};
    fs::path rel;
    size_t i = 1;
    while (i <= guest.size()) {
        size_t j = guest.find('/', i);
        if (j == std::string::npos) j = guest.size();
        std::string part = guest.substr(i, j - i);
        i = j + 1;
        if (part.empty() || part == ".") continue;
        if (part == ".." || part.find('\\') != std::string::npos ||
            part.find(':') != std::string::npos) return {};
        rel /= part;
    }
    return g_root / rel;
}


// A real console has /title/<hi>/<lo>[/data] from installation; this synthetic
// NAND starts empty, so those directories are created when first queried.
fs::path host_path_title(const std::string& guest) {
    fs::path p = host_path(guest);
    if (p.empty() || guest.rfind("/title/", 0) != 0) return p;
    int depth = 0;
    for (char c : guest) if (c == '/') ++depth;
    if (guest.size() > 1 && guest.back() == '/') --depth;
    if (depth <= 4) { std::error_code ec; fs::create_directories(p, ec); }
    return p;
}

void seed(const std::string& guest, const std::vector<uint8_t>& data) {
    fs::path p = host_path(guest);
    if (p.empty() || fs::exists(p)) return;
    std::error_code ec;
    fs::create_directories(p.parent_path(), ec);
    std::ofstream f(p, std::ios::binary);
    f.write((const char*)data.data(), (std::streamsize)data.size());
}

std::vector<uint8_t> make_sysconf() {
    std::vector<uint8_t> sc(0x4000, 0xFF);
    sc[0]='S'; sc[1]='C'; sc[2]='v'; sc[3]='1'; sc[4]='5';
    sc[5] = 1;
    sc[6] = 0; sc[7] = 1;
    const uint8_t key[] = "IPL.AR";
    const uint8_t val[] = { 0, 4, 'N','T','S','C' };
    int off = 8;
    sc[off++] = 0x02;
    sc[off++] = (uint8_t)(sizeof(key) - 1);
    for (size_t i = 0; i + 1 < sizeof(key); ++i) sc[off++] = key[i];
    for (auto b : val) sc[off++] = b;
    return sc;
}

std::vector<uint8_t> make_setting_txt() {
    const char* txt =
        "AREA=USA\r\n" "MODEL=RVL-001\r\n" "DVD=0\r\n" "MPCH=0x7FFE\r\n"
        "CODE=0\r\n" "SERNO=123456789\r\n" "VIDEO=NTSC\r\n" "GAME=US\r\n";
    return std::vector<uint8_t>(txt, txt + strlen(txt));
}

void init() {
    if (g_ready) return;
    g_ready = true;
    const char* env = std::getenv("NWII_NAND_DIR");
    g_root = fs::absolute(env && *env ? fs::path(env) : fs::path("nand"));
    std::error_code ec;
    fs::create_directories(g_root, ec);
    // IOS wipes /tmp on boot; a stale temp file would make the game's temp-file create fail.
    fs::remove_all(g_root / "tmp", ec);
    fs::create_directories(g_root / "tmp", ec);
    seed("/shared2/sys/SYSCONF", make_sysconf());
    seed("/title/00000001/00000002/data/setting.txt", make_setting_txt());
    seed("/title/00000001/00000002/data/play_rec.dat", std::vector<uint8_t>(0x20, 0));
    seed("/shared2/test2/dvderror.dat", std::vector<uint8_t>(4, 0));
    std::cout << "[NAND] host root " << g_root.string() << std::endl;
}

// Recursive cluster/inode usage below a directory (or a single file).
void usage(const fs::path& p, uint32_t& clusters, uint32_t& inodes) {
    std::error_code ec;
    if (fs::is_directory(p, ec)) {
        for (auto& e : fs::directory_iterator(p, ec)) {
            ++inodes;
            usage(e.path(), clusters, inodes);
        }
    } else if (fs::is_regular_file(p, ec)) {
        uint64_t n = fs::file_size(p, ec);
        clusters += (uint32_t)((n + CLUSTER - 1) / CLUSTER);
    }
}

} // namespace

class FSDevice : public IDevice {
public:
    const char* get_name() const override { return "/dev/fs"; }

    bool matches_path(const std::string& path) const override {
        if (path == "/dev/fs") return true;
        if (path.find("/dev/") == 0) return false;
        return path.find("/") == 0;
    }

    int32_t open(CPUContext&, const std::string& path, uint32_t mode) override {
        init();
        if (path == "/dev/fs") return IPC_OK;
        fs::path p = host_path(path);
        std::error_code ec;
        if (p.empty() || !fs::is_regular_file(p, ec)) {
            std::cout << "[FS] open: no such file " << path << " mode " << mode << std::endl;
            return FS_ENOENT;
        }
        for (int s = 0; s < FD_MAX; ++s) {
            if (g_handles[s].open) continue;
            auto st = std::make_unique<std::fstream>(
                p, std::ios::in | std::ios::out | std::ios::binary);
            if (!st->is_open()) {
                st = std::make_unique<std::fstream>(p, std::ios::in | std::ios::binary);
                if (!st->is_open()) return FS_EIO;
            }
            g_handles[s].open = true;
            g_handles[s].host = p;
            g_handles[s].stream = std::move(st);
            g_handles[s].pos = 0;
            std::cout << "[FS] open " << path << " mode " << mode << " -> " << (FD_BASE + s) << std::endl;
            return FD_BASE + s;
        }
        return FS_EFDEXHAUSTED;
    }

    int32_t close(CPUContext&, uint32_t fd) override {
        Handle* h = handle(fd);
        if (h) { h->stream.reset(); h->open = false; }
        return IPC_OK;
    }

    int32_t read(CPUContext& ctx, uint32_t fd, uint32_t buf, uint32_t len) override {
        Handle* h = handle(fd);
        if (!h) return 0;
        std::error_code ec;
        uint64_t size = fs::file_size(h->host, ec);
        if (h->pos >= size) return 0;
        uint32_t n = (uint32_t)std::min<uint64_t>(len, size - h->pos);
        std::vector<char> tmp(n);
        h->stream->clear();
        h->stream->seekg(h->pos);
        h->stream->read(tmp.data(), n);
        n = (uint32_t)h->stream->gcount();
        for (uint32_t i = 0; i < n; ++i) ctx.mmu.write8(virt(buf) + i, (uint8_t)tmp[i]);
        h->pos += n;
        return (int32_t)n;
    }

    int32_t write(CPUContext& ctx, uint32_t fd, uint32_t buf, uint32_t len) override {
        Handle* h = handle(fd);
        if (!h) return 0;
        std::vector<char> tmp(len);
        for (uint32_t i = 0; i < len; ++i) tmp[i] = (char)ctx.mmu.read8(virt(buf) + i);
        h->stream->clear();
        h->stream->seekp(h->pos);
        h->stream->write(tmp.data(), len);
        h->stream->flush();
        if (!*h->stream) return FS_EIO;
        h->pos += len;
        return (int32_t)len;
    }

    int32_t seek(CPUContext&, uint32_t fd, int32_t offset, int32_t whence) override {
        Handle* h = handle(fd);
        if (!h) return IPC_OK;
        std::error_code ec;
        int64_t size = (int64_t)fs::file_size(h->host, ec);
        int64_t target = whence == 0 ? offset : whence == 1 ? (int64_t)h->pos + offset
                                                            : size + offset;
        if (target < 0 || target > size) return FS_EINVAL;
        h->pos = (uint32_t)target;
        return (int32_t)h->pos;
    }

    int32_t ioctl(CPUContext& ctx, const IpcRequest& req) override {
        int32_t r = ioctl_impl(ctx, req);
        if (req.ioctl_cmd != 2) // GetStats is polled constantly
            std::cout << "[FS] ioctl " << req.ioctl_cmd << " '" << guest_string(ctx, virt(req.in_buf) + 6, 64) << "' '" << guest_string(ctx, virt(req.in_buf), 64) << "' -> " << r << std::endl;
        return r;
    }
    int32_t ioctl_impl(CPUContext& ctx, const IpcRequest& req) {
        init();
        const uint32_t in = virt(req.in_buf), out = virt(req.out_buf);
        std::error_code ec;
        switch (req.ioctl_cmd) {
        case 1: return IPC_OK;                      // Format
        case 2: {                                    // GetStats (NANDStat)
            const uint32_t stat[8] = {CLUSTER, 0x5DEC, 0x1DD4, CLUSTER, 0x10, 0x2, 0x146B, 0x394};
            for (uint32_t i = 0; i < 8 && i * 4 + 4 <= req.out_size; ++i)
                ctx.mmu.write32(out + i * 4, stat[i]);
            return IPC_OK;
        }
        case 3: case 9: {                            // CreateDir / CreateFile
            fs::path p = host_path(guest_string(ctx, in + 6, 64));
            if (p.empty()) return FS_EINVAL;
            if (fs::exists(p, ec)) return FS_EEXIST;
            fs::create_directories(p.parent_path(), ec);
            if (req.ioctl_cmd == 3) { fs::create_directory(p, ec); return ec ? FS_EIO : IPC_OK; }
            std::ofstream f(p, std::ios::binary);
            return f ? IPC_OK : FS_EIO;
        }
        case 5: {                                    // SetAttr
            fs::path p = host_path(guest_string(ctx, in + 6, 64));
            return !p.empty() && fs::exists(p, ec) ? IPC_OK : FS_ENOENT;
        }
        case 6: {                                    // GetAttr
            std::string g = guest_string(ctx, in, 64);
            fs::path p = host_path_title(g);
            if (p.empty() || !fs::exists(p, ec)) return FS_ENOENT;
            for (uint32_t i = 0; i < 0x4A && i < req.out_size; ++i) ctx.mmu.write8(out + i, 0);
            write_string(ctx, out + 6, g, 64);
            ctx.mmu.write8(out + 0x46, 0);
            ctx.mmu.write8(out + 0x47, 3); ctx.mmu.write8(out + 0x48, 3); ctx.mmu.write8(out + 0x49, 3);
            return IPC_OK;
        }
        case 7: {                                    // Delete
            fs::path p = host_path(guest_string(ctx, in, 64));
            if (p.empty() || !fs::exists(p, ec)) return FS_ENOENT;
            fs::remove_all(p, ec);
            return ec ? FS_EIO : IPC_OK;
        }
        case 8: {                                    // Rename
            fs::path from = host_path(guest_string(ctx, in, 64));
            fs::path to = host_path(guest_string(ctx, in + 64, 64));
            if (from.empty() || to.empty() || !fs::exists(from, ec)) return FS_ENOENT;
            // ISFS replaces an existing destination (the NAND library saves via temp file + rename).
            if (fs::exists(to, ec)) fs::remove_all(to, ec);
            fs::create_directories(to.parent_path(), ec);
            fs::rename(from, to, ec);
            return ec ? FS_EIO : IPC_OK;
        }
        case 0xA: case 0xD: return IPC_OK;           // SetFileVerCtrl / Shutdown
        case 0xB: {                                  // GetFileStats
            Handle* h = handle((uint32_t)req.internal_fd);
            if (!h) return FS_EINVAL;
            ctx.mmu.write32(out, (uint32_t)fs::file_size(h->host, ec));
            ctx.mmu.write32(out + 4, h->pos);
            return IPC_OK;
        }
        default:
            std::cout << "[FS] unhandled ioctl 0x" << std::hex << req.ioctl_cmd << std::dec << std::endl;
            return IPC_OK;
        }
    }

    int32_t ioctlv(CPUContext& ctx, const IpcRequest& req) override {
        int32_t r = ioctlv_impl(ctx, req);
        std::cout << "[FS] ioctlv " << req.ioctl_cmd << " '" << (req.ioctlv_vecs.empty() ? std::string() : guest_string(ctx, req.ioctlv_vecs[0].addr, 64)) << "' -> " << r << std::endl;
        return r;
    }
    int32_t ioctlv_impl(CPUContext& ctx, const IpcRequest& req) {
        init();
        const auto& v = req.ioctlv_vecs;
        std::error_code ec;
        if (req.ioctl_cmd == 4 && v.size() >= 2) {   // ReadDir
            fs::path p = host_path_title(guest_string(ctx, v[0].addr, 64));
            if (p.empty()) return FS_EINVAL;
            // A missing path is NotFound; an existing file is Invalid (the game probes existence this way).
            if (!fs::is_directory(p, ec)) return fs::exists(p, ec) ? FS_EINVAL : FS_ENOENT;
            std::vector<std::string> names;
            for (auto& e : fs::directory_iterator(p, ec)) {
                std::string n = e.path().filename().string();
                if (n.size() <= 12) names.push_back(n);
            }
            std::sort(names.begin(), names.end());
            if (req.arg_cnt_in == 2 && v.size() >= 4) {
                uint32_t max = ctx.mmu.read32(virt(v[1].addr));
                uint32_t n = (uint32_t)std::min<size_t>(names.size(), max);
                for (uint32_t i = 0; i < n; ++i) write_string(ctx, v[2].addr + i * 13, names[i], 13);
                ctx.mmu.write32(virt(v[3].addr), n);
            } else {
                ctx.mmu.write32(virt(v[1].addr), (uint32_t)names.size());
            }
            return IPC_OK;
        }
        if (req.ioctl_cmd == 0xC && v.size() >= 3) { // GetUsage
            fs::path p = host_path_title(guest_string(ctx, v[0].addr, 64));
            if (p.empty()) return FS_EINVAL;
            if (!fs::exists(p, ec)) return FS_ENOENT;
            uint32_t clusters = 0, inodes = 0;
            usage(p, clusters, inodes);
            ctx.mmu.write32(virt(v[1].addr), clusters);
            ctx.mmu.write32(virt(v[2].addr), inodes);
            return IPC_OK;
        }
        std::cout << "[FS] unhandled ioctlv 0x" << std::hex << req.ioctl_cmd << std::dec << std::endl;
        return IPC_OK;
    }

private:
    static Handle* handle(uint32_t fd) {
        int slot = (int)fd - FD_BASE;
        if (slot >= 0 && slot < FD_MAX && g_handles[slot].open) return &g_handles[slot];
        return nullptr;
    }
};

std::unique_ptr<IDevice> create_fs_device() {
    return std::make_unique<FSDevice>();
}

} // namespace nwii::runtime::devices
