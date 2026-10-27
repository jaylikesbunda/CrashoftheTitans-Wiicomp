"""Audio policy v3: stop discarding audio when the guest delivers it in bursts.

When the guest runs slower than real time the DSP/ADMA events catch up in
bursts, the backlog crosses the 150 ms limit and the jitter buffer dropped the
oldest audio down to 60 ms: whole sound effects (and cutscene audio) vanished,
leaving only sounds that happened to arrive between bursts. Now the backlog
limit is 600 ms and catching up is done by playing up to 8% faster instead of
dropping. [AUDIO] counters (callbacks, underruns, drops, max backlog) are printed
every 10 s so the behaviour can be checked from the log.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
p = port / 'build/nwii-full-source/nWiiRuntime/src/hw/hw_dsp.cpp'
t = p.read_text()
marker = 'NATIVE_AUDIO_POLICY_V3'
if marker not in t:
    def rep(old, new):
        global t
        assert t.count(old) == 1, old
        t = t.replace(old, new)
    rep('    static const size_t kHigh = 4800;       // 150 ms\n',
        '    static const size_t kHigh = 19200;      // 600 ms NATIVE_AUDIO_POLICY_V3\n')
    rep('double ratio = 1.0 + std::max(-0.01, std::min(0.01, err * 0.01));',
        'double ratio = 1.0 + std::max(-0.01, std::min(0.08, err * 0.02));')
    rep('    static bool primed = false;\n    size_t have = adma_ring.size() / 2;\n',
        '''    static bool primed = false;
    static uint64_t st_calls = 0, st_under = 0, st_drop = 0;
    static size_t st_max = 0;
    static auto st_t0 = std::chrono::steady_clock::now();
    size_t have = adma_ring.size() / 2;
    ++st_calls;
    st_max = std::max(st_max, have);
''')
    rep('        size_t drop = have - size_t(kTarget);\n', '        size_t drop = have - size_t(kTarget);\n        ++st_drop;\n')
    rep('''        std::memset(out + i * 2, 0, (frames - i) * 2 * sizeof(int16_t));
        primed = false;
    }
    return frames;
}''', '''        std::memset(out + i * 2, 0, (frames - i) * 2 * sizeof(int16_t));
        primed = false;
        ++st_under;
    }
    {
        const auto now = std::chrono::steady_clock::now();
        if (std::chrono::duration_cast<std::chrono::seconds>(now - st_t0).count() >= 10) {
            st_t0 = now;
            std::printf("[AUDIO] callbacks=%llu underruns=%llu drops=%llu max_backlog_ms=%zu backlog_ms=%zu\\n",
                        (unsigned long long)st_calls, (unsigned long long)st_under, (unsigned long long)st_drop,
                        st_max / 32, adma_ring.size() / 64);
            std::fflush(stdout);
            st_calls = st_under = st_drop = 0;
            st_max = 0;
        }
    }
    return frames;
}''')
    for inc in ['<chrono>', '<cstdio>', '<algorithm>']:
        if '#include ' + inc not in t:
            t = '#include ' + inc + '\n' + t
    p.write_text(t)
print('Audio policy v3 installed.')
