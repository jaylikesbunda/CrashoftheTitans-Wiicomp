"""Give the audio output a real jitter buffer and drive it from the SDL audio thread.

Measured at the title screen: the ADMA ring held ~419 ms of audio (latency, so
sound lags the picture), was cut off at its 1 s cap (dropping audio -> clicks and
a looping/stuttering stream), and was only drained from the video main loop in
1024-frame chunks, so any slow frame starved the device.

Now the SDL audio callback pulls directly. dsp_audio_pull keeps ~60 ms queued:
it trims the playback rate by at most +-1% to steer toward that target, drops the
oldest audio if the backlog exceeds 150 ms, and re-primes after an underrun.
Guest-visible behaviour (ADMA timing, interrupts) is unchanged.
"""
from pathlib import Path
port = Path(__file__).resolve().parents[1]
src = port / 'build/nwii-full-source/nWiiRuntime/src'
marker = 'NATIVE_AUDIO_JITTER_BUFFER_V1'

dsp = src / 'hw/hw_dsp.cpp'
t = dsp.read_text()
if marker not in t:
    start = t.index('size_t dsp_audio_pull(int16_t* out, size_t frames) {')
    end = t.index('static void adma_event_cb')
    new = '''size_t dsp_audio_pull(int16_t* out, size_t frames) { // NATIVE_AUDIO_JITTER_BUFFER_V1
    std::lock_guard<std::mutex> lock(adma_mutex);
    static const double kTarget = 1920.0;   // 60 ms at 32 kHz
    static const size_t kHigh = 4800;       // 150 ms
    static double position = 0.0;
    static bool primed = false;
    size_t have = adma_ring.size() / 2;
    if (!primed) {
        if (have < size_t(kTarget)) {
            std::memset(out, 0, frames * 2 * sizeof(int16_t));
            return frames;
        }
        primed = true;
        position = 0.0;
    }
    if (have > kHigh) {
        size_t drop = have - size_t(kTarget);
        adma_ring.erase(adma_ring.begin(), adma_ring.begin() + drop * 2);
        have -= drop;
        position = 0.0;
    }
    double err = (double(have) - kTarget) / kTarget;
    double ratio = 1.0 + std::max(-0.01, std::min(0.01, err * 0.01));
    size_t i = 0;
    for (; i < frames; ++i) {
        size_t idx = size_t(position);
        if (idx + 1 >= have) break;
        double frac = position - double(idx);
        for (int c = 0; c < 2; ++c) {
            double a = adma_ring[idx * 2 + c], b = adma_ring[(idx + 1) * 2 + c];
            out[i * 2 + c] = int16_t(a + (b - a) * frac);
        }
        position += ratio;
    }
    size_t consumed = std::min(size_t(position), have);
    if (consumed) {
        adma_ring.erase(adma_ring.begin(), adma_ring.begin() + consumed * 2);
        position -= double(consumed);
    }
    if (i < frames) {
        std::memset(out + i * 2, 0, (frames - i) * 2 * sizeof(int16_t));
        primed = false;
    }
    return frames;
}

'''
    t = t[:start] + new + t[end:]
    if '#include <algorithm>' not in t:
        t = '#include <algorithm>\n' + t
    dsp.write_text(t)

mp = src / 'core/main.cpp'
m = mp.read_text()
if marker not in m:
    old = '    want.samples = 1024;\n    audio_dev = SDL_OpenAudioDevice(NULL, 0, &want, &have, 0);'
    assert m.count(old) == 1
    new = ('    want.samples = 512; // NATIVE_AUDIO_JITTER_BUFFER_V1\n'
           '    want.callback = [](void*, Uint8* stream, int len) {\n'
           '      nwii::runtime::hw::dsp_audio_pull(reinterpret_cast<int16_t*>(stream), size_t(len) / 4);\n'
           '    };\n'
           '    audio_dev = SDL_OpenAudioDevice(NULL, 0, &want, &have, 0);')
    m = m.replace(old, new)
    a = m.index('      if (audio_dev > 0) {\n        if (SDL_GetQueuedAudioSize')
    b = m.index('      ctx->vblank_pending = true;', a)
    m = m[:a] + '      // audio is pulled by the SDL callback (NATIVE_AUDIO_JITTER_BUFFER_V1)\n\n' + m[b:]
    mp.write_text(m)
print('Audio: SDL callback + adaptive 60 ms jitter buffer installed.')
