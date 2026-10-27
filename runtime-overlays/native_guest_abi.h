#pragma once
// Translated guest functions spill guest state at host call boundaries. Host
// callee-saved registers add work without preserving any additional guest state.
#if defined(__x86_64__) && defined(__has_attribute)
#if __has_attribute(preserve_none)
#define NWII_GUEST_ABI __attribute__((preserve_none))
#endif
#endif
#ifndef NWII_GUEST_ABI
#define NWII_GUEST_ABI
#endif
namespace nwii::runtime { struct CPUContext; }
typedef NWII_GUEST_ABI void (*NativeGuestFnPtr)(::nwii::runtime::CPUContext&);
