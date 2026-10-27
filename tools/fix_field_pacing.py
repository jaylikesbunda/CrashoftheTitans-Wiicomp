"""Decouple guest field cadence from unreliable host swap throttling."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/core/main.cpp'
s=p.read_text()
if 'NATIVE_FIELD_PACING_V1' in s:
    print('Regional field pacing already staged.');raise SystemExit(0)
needle='  std::thread cpu_thread(cpu_thread_func, ctx.get());'
if s.count(needle)!=1:raise SystemExit('Main thread initialization changed')
helper='''  // NATIVE_FIELD_PACING_V1: swap interval can be ignored by the host driver.
  // Supply nominal regional fields, rather than one interrupt per presentation.
  const auto &field_id = nwii::runtime::Config::get().game_id;
  const char field_region = field_id.size() >= 4 ? field_id[3] : 'E';
  const bool field_pal = field_region == 'P' || field_region == 'D' ||
      field_region == 'F' || field_region == 'I' || field_region == 'S' ||
      field_region == 'H' || field_region == 'U' || field_region == 'X' ||
      field_region == 'Y' || field_region == 'Z';
  const auto field_period = std::chrono::nanoseconds(field_pal ? 20000000 : 16683333);
  auto field_deadline = std::chrono::steady_clock::now();
  auto pace_video_field = [&]() {
    field_deadline += field_period;
    const auto now = std::chrono::steady_clock::now();
    if (field_deadline < now) field_deadline = now;
    std::this_thread::sleep_until(field_deadline);
  };
  std::cout << "[VI cadence] nominal field_ns=" << field_period.count() << std::endl;

'''
s=s.replace(needle,helper+needle)
needle='std::this_thread::sleep_for(std::chrono::milliseconds(16));'
if s.count(needle)!=2:raise SystemExit('Expected two existing loop sleeps')
s=s.replace(needle,'pace_video_field();',1)
s=s.replace('if (headless) '+needle,'pace_video_field();')
p.write_text(s)
print('Nominal regional field pacing staged for windowed and headless startup.')
