# Dolphin AX reference and adapted backend

Upstream revision and file hashes are pinned in provenance.json. The downloaded
source and free coefficient table come from Dolphin Emulator. Copyright notices
are retained; SPDX license GPL-2.0-or-later, complete license in COPYING.

prepare_ax_backend.py derives the isolated runtime-overlays/native_ax source
from these retained files. The adapter provides bounded big-endian RAM transfers,
Wii MEM1/MEM2 accelerator memory, a command-list validator and hardware mailbox
integration. The translated Crash CPU SDK code remains intact.

AXWii/AXVoice/DSPAccelerator mixing, decoding, filtering, resampling, depop studio
initialisation, compression, aux transfers and PCM output are adapted from the
upstream implementation. bridge.h and protocol.h provide native integration.

This is DSP HLE, not a claim of instruction-exact DSP emulation. Dolphin's
initial-time-delay and DPL2 mixing limitations remain; those paths report
limitations. Unknown microcode, commands, formats or invalid RAM ranges fail
without successful completion. DSP task DRAM downloads are unsupported.
The current title's captured microcode is 347112ba, 8192 bytes at 80579120.
