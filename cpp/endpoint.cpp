// Native latest-sample HG transport. No DDS entity is created at library load.
#include <dds/dds.hpp>
#include <dds/dds.h>
#include <unitree/idl/hg/LowCmd_.hpp>
#include <unitree/idl/hg/LowState_.hpp>
#include <org/eclipse/cyclonedds/core/cdr/basic_cdr_ser.hpp>
#include <array>
#include <cstring>
#include <functional>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <time.h>

using LowState = unitree_hg::msg::dds_::LowState_;
using LowCmd = unitree_hg::msg::dds_::LowCmd_;
namespace cdr = org::eclipse::cyclonedds::core::cdr;

extern "C" {
struct State {
  uint64_t received_ns, sequence;
  int64_t source_ns;
  uint32_t tick;
  uint8_t mode_machine;
  float q[35], dq[35], tau[35], quat[4], gyro[3];
};
struct Command {
  uint64_t received_ns, sequence;
  int64_t source_ns;
  uint8_t mode_machine, mode_pr, enabled[35];
  float q[35], dq[35], kp[35], kd[35], tau[35];
};
}
namespace {
thread_local std::string error;
uint64_t now_ns() {
  timespec t{};
  clock_gettime(CLOCK_MONOTONIC, &t);
  return uint64_t(t.tv_sec) * 1000000000ULL + uint64_t(t.tv_nsec);
}
uint32_t crc32_words(const uint8_t* bytes, size_t size) {
  if (size % 4) throw std::runtime_error("HG CRC requires aligned words");
  uint32_t crc = 0xffffffffu;
  for (size_t i = 0; i < size; i += 4) {
    uint32_t word = uint32_t(bytes[i]) | uint32_t(bytes[i+1]) << 8 |
                    uint32_t(bytes[i+2]) << 16 | uint32_t(bytes[i+3]) << 24;
    crc ^= word;
    for (int bit = 0; bit < 32; ++bit)
      crc = (crc << 1) ^ ((crc & 0x80000000u) ? 0x04c11db7u : 0u);
  }
  return crc;
}
template<class T> size_t encode(const T& value, uint8_t* out, size_t capacity) {
  if (!out || capacity < 4) throw std::runtime_error("CDR buffer too short");
  std::memset(out, 0, capacity);
  out[1] = 1; // XCDR1 little endian encapsulation; padding starts after header.
  cdr::basic_cdr_stream stream(cdr::endianness::little_endian);
  stream.set_buffer(out + 4, capacity - 4);
  if (!cdr::write(stream, value, false)) throw std::runtime_error("CDR encode failed");
  return stream.position() + 4;
}
template<class T> T decode(const uint8_t* data, size_t size) {
  if (!data || size < 4 || data[0] != 0 || data[1] > 1)
    throw std::runtime_error("expected XCDR1 HG packet");
  T value;
  cdr::basic_cdr_stream stream(data[1] ? cdr::endianness::little_endian : cdr::endianness::big_endian);
  stream.set_buffer(const_cast<uint8_t*>(data + 4), size - 4);
  if (!cdr::read(stream, value, false) || stream.position() != size - 4)
    throw std::runtime_error("invalid HG packet length or contents");
  return value;
}
uint32_t float_bits(float value) {
  uint32_t bits;
  std::memcpy(&bits, &value, sizeof(bits));
  return bits;
}
template<size_t N> uint32_t crc_words(const std::array<uint32_t, N>& words) {
  std::array<uint8_t, N * 4> bytes{};
  for (size_t i = 0; i < N; ++i)
    for (size_t b = 0; b < 4; ++b) bytes[4*i+b] = uint8_t(words[i] >> (8*b));
  return crc32_words(bytes.data(), bytes.size());
}
void set_crc(LowCmd& value) {
  // Unitree CRC covers the padded memory layout, NOT the CDR wire layout.
  std::array<uint32_t, 250> words{};
  words[0] = uint32_t(value.mode_pr()) | uint32_t(value.mode_machine()) << 8;
  for (size_t i = 0; i < 35; ++i) {
    const auto& m = value.motor_cmd()[i];
    const size_t j = 1 + i * 7;
    words[j] = m.mode(); words[j+1] = float_bits(m.q()); words[j+2] = float_bits(m.dq());
    words[j+3] = float_bits(m.tau()); words[j+4] = float_bits(m.kp()); words[j+5] = float_bits(m.kd());
    words[j+6] = m.reserve();
  }
  std::copy(value.reserve().begin(), value.reserve().end(), words.begin() + 246);
  value.crc(crc_words(words));
}
void set_crc(LowState& value) {
  // Simulator state uses zero auxiliary fields; explicitly pack the SDK ABI.
  std::array<uint32_t, 522> words{};
  words[0] = value.version()[0]; words[1] = value.version()[1];
  words[2] = uint32_t(value.mode_pr()) | uint32_t(value.mode_machine()) << 8;
  words[3] = value.tick();
  for (size_t i = 0; i < 4; ++i) words[4+i] = float_bits(value.imu_state().quaternion()[i]);
  for (size_t i = 0; i < 3; ++i) words[8+i] = float_bits(value.imu_state().gyroscope()[i]);
  for (size_t i = 0; i < 35; ++i) {
    const auto& m = value.motor_state()[i];
    const size_t j = 18 + i * 14;
    words[j] = m.mode(); words[j+1] = float_bits(m.q()); words[j+2] = float_bits(m.dq());
    words[j+4] = float_bits(m.tau_est());
  }
  value.crc(crc_words(words));
}
LowCmd lowcmd(const Command& in) {
  LowCmd out;
  out.mode_machine(in.mode_machine);
  out.mode_pr(in.mode_pr);
  for (size_t i = 0; i < 35; ++i) {
    auto& m = out.motor_cmd()[i];
    m.mode(in.enabled[i]); m.q(in.q[i]); m.dq(in.dq[i]);
    m.kp(in.kp[i]); m.kd(in.kd[i]); m.tau(in.tau[i]);
  }
  set_crc(out);
  return out;
}
LowState lowstate(const State& in) {
  LowState out;
  out.mode_machine(in.mode_machine); out.tick(in.tick);
  for (size_t i = 0; i < 35; ++i) {
    auto& m = out.motor_state()[i];
    m.q(in.q[i]); m.dq(in.dq[i]); m.tau_est(in.tau[i]);
  }
  std::copy(in.quat, in.quat + 4, out.imu_state().quaternion().begin());
  std::copy(in.gyro, in.gyro + 3, out.imu_state().gyroscope().begin());
  set_crc(out);
  return out;
}
State state_from(const LowState& in) {
  State out{};
  out.tick = in.tick(); out.mode_machine = in.mode_machine();
  for (size_t i = 0; i < 35; ++i) {
    out.q[i] = in.motor_state()[i].q(); out.dq[i] = in.motor_state()[i].dq();
    out.tau[i] = in.motor_state()[i].tau_est();
  }
  std::copy(in.imu_state().quaternion().begin(), in.imu_state().quaternion().end(), out.quat);
  std::copy(in.imu_state().gyroscope().begin(), in.imu_state().gyroscope().end(), out.gyro);
  return out;
}
Command command_from(const LowCmd& in) {
  Command out{};
  out.mode_machine = in.mode_machine(); out.mode_pr = in.mode_pr();
  for (size_t i = 0; i < 35; ++i) {
    const auto& m = in.motor_cmd()[i];
    out.enabled[i] = m.mode(); out.q[i] = m.q(); out.dq[i] = m.dq();
    out.kp[i] = m.kp(); out.kd[i] = m.kd(); out.tau[i] = m.tau();
  }
  return out;
}
struct Endpoint;
template<class T> struct Listener : dds::sub::NoOpDataReaderListener<T> {
  std::function<void(const T&, int64_t)> receive;
  std::function<void(std::string)> failed;
  void on_data_available(dds::sub::DataReader<T>& reader) override {
    try {
      auto samples = reader.take();
      for (const auto& sample : samples) {
        if (sample.info().valid()) {
          const auto stamp = sample.info().timestamp();
          receive(sample.data(), stamp.sec() * 1000000000LL + stamp.nanosec());
        }
      }
    } catch (const std::exception& e) { failed(e.what()); }
      catch (...) { failed("unknown native receive error"); }
  }
};
struct Endpoint {
  int role;
  bool offline, enable_write;
  std::mutex mutex;
  State state{};
  Command command{};
  std::string receive_error;
  dds_entity_t domain = 0;
  dds::domain::DomainParticipant participant{dds::core::null};
  dds::sub::DataReader<LowState> state_reader{dds::core::null};
  dds::sub::DataReader<LowCmd> command_reader{dds::core::null};
  dds::pub::DataWriter<LowState> state_writer{dds::core::null};
  dds::pub::DataWriter<LowCmd> command_writer{dds::core::null};
  Listener<LowState> state_listener;
  Listener<LowCmd> command_listener;

  Endpoint(int role_, bool offline_, bool enable_) : role(role_), offline(offline_), enable_write(enable_) {
    if (role != 0 && role != 1) throw std::runtime_error("invalid endpoint role");
  }
  void receive(const LowState& value, int64_t source) {
    State next = state_from(value);
    next.received_ns = now_ns(); next.source_ns = source;
    std::lock_guard<std::mutex> lock(mutex);
    next.sequence = state.sequence + 1; state = next;
  }
  void receive(const LowCmd& value, int64_t source) {
    Command next = command_from(value);
    next.received_ns = now_ns(); next.source_ns = source;
    std::lock_guard<std::mutex> lock(mutex);
    next.sequence = command.sequence + 1; command = next;
  }
  void start(uint32_t domain_id, const std::string& interface,
             const char* state_topic, const char* command_topic) {
    if (offline) return;
    if (!interface.empty()) {
      if (interface.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-") != std::string::npos)
        throw std::runtime_error("invalid network interface name");
      std::string xml = "<CycloneDDS><Domain><General><Interfaces><NetworkInterface name=\"" + interface +
        "\"/></Interfaces></General></Domain></CycloneDDS>";
      domain = dds_create_domain(domain_id, xml.c_str());
      if (domain < 0) throw std::runtime_error("cannot create configured DDS domain");
    }
    participant = dds::domain::DomainParticipant(domain_id);
    dds::sub::Subscriber sub(participant);
    dds::pub::Publisher pub(participant);
    dds::topic::Topic<LowState> state_t(participant, state_topic);
    dds::topic::Topic<LowCmd> cmd_t(participant, command_topic);
    // Keep the latest available complete sample; no Python callback or FIFO.
    auto rq = sub.default_datareader_qos();
    rq << dds::core::policy::History::KeepLast(1);
    auto wq = pub.default_datawriter_qos();
    wq << dds::core::policy::History::KeepLast(1)
       << dds::core::policy::Reliability::Reliable(dds::core::Duration::from_millisecs(1));
    auto failed = [this](std::string message) {
      std::lock_guard<std::mutex> lock(mutex); receive_error = std::move(message);
    };
    if (role == 0) {
      state_listener.receive = [this](const LowState& s, int64_t t) { receive(s, t); };
      state_listener.failed = failed;
      state_reader = dds::sub::DataReader<LowState>(sub, state_t, rq, &state_listener, dds::core::status::StatusMask::data_available());
      if (enable_write) command_writer = dds::pub::DataWriter<LowCmd>(pub, cmd_t, wq);
    } else {
      command_listener.receive = [this](const LowCmd& s, int64_t t) { receive(s, t); };
      command_listener.failed = failed;
      command_reader = dds::sub::DataReader<LowCmd>(sub, cmd_t, rq, &command_listener, dds::core::status::StatusMask::data_available());
      if (enable_write) state_writer = dds::pub::DataWriter<LowState>(pub, state_t, wq);
    }
  }
  ~Endpoint() {
    // Removing listeners waits for outstanding callbacks before their storage dies.
    auto close_reader = [](auto& reader) {
      if (reader == dds::core::null) return;
      try { reader.listener(nullptr, dds::core::status::StatusMask::none()); } catch (...) { }
      try { reader.close(); } catch (...) { }
    };
    auto close = [](auto& entity) {
      try { if (entity != dds::core::null) entity.close(); } catch (...) { }
    };
    close_reader(state_reader);
    close_reader(command_reader);
    close(state_writer);
    close(command_writer);
    close(participant);
    if (domain > 0) dds_delete(domain);
  }
};
template<class F> int checked(F&& function) {
  try { error.clear(); return function(); }
  catch (const std::exception& e) { error = e.what(); return -1; }
  catch (...) { error = "unknown native DDS error"; return -1; }
}
}
extern "C" {
uint32_t rpd_abi_version() { return 1; }
size_t rpd_state_size() { return sizeof(State); }
size_t rpd_command_size() { return sizeof(Command); }
const char* rpd_error() { return error.c_str(); }
void* rpd_create(int role, int offline, int enable_write, uint32_t domain_id,
                 const char* interface, const char* state_topic, const char* command_topic) {
  Endpoint* result = nullptr;
  checked([&] {
    auto endpoint = std::make_unique<Endpoint>(role, offline != 0, enable_write != 0);
    endpoint->start(domain_id, interface ? interface : "", state_topic, command_topic);
    result = endpoint.release(); return 0;
  });
  return result;
}
void rpd_close(void* handle) { delete static_cast<Endpoint*>(handle); }
int rpd_read_state(void* handle, State* out) {
  return checked([&] {
    auto& e = *static_cast<Endpoint*>(handle);
    if (e.role != 0) throw std::runtime_error("state read requires policy endpoint");
    std::lock_guard<std::mutex> lock(e.mutex);
    if (!e.receive_error.empty()) throw std::runtime_error(e.receive_error);
    *out = e.state; return out->sequence ? 1 : 0;
  });
}
int rpd_read_command(void* handle, Command* out) {
  return checked([&] {
    auto& e = *static_cast<Endpoint*>(handle);
    if (e.role != 1) throw std::runtime_error("command read requires simulator endpoint");
    std::lock_guard<std::mutex> lock(e.mutex);
    if (!e.receive_error.empty()) throw std::runtime_error(e.receive_error);
    *out = e.command; return out->sequence ? 1 : 0;
  });
}
int rpd_write_command(void* handle, const Command* command) {
  return checked([&] {
    auto& e = *static_cast<Endpoint*>(handle);
    if (!e.enable_write || e.offline || e.role != 0) throw std::runtime_error("command writer is not enabled");
    e.command_writer.write(lowcmd(*command)); return 0;
  });
}
int rpd_write_state(void* handle, const State* state) {
  return checked([&] {
    auto& e = *static_cast<Endpoint*>(handle);
    if (!e.enable_write || e.offline || e.role != 1) throw std::runtime_error("state writer is not enabled");
    e.state_writer.write(lowstate(*state)); return 0;
  });
}
int rpd_encode_command(const Command* in, uint8_t* out, size_t size) {
  return checked([&] { return int(encode(lowcmd(*in), out, size)); });
}
int rpd_encode_state(const State* in, uint8_t* out, size_t size) {
  return checked([&] { return int(encode(lowstate(*in), out, size)); });
}
int rpd_feed(void* handle, const uint8_t* packet, size_t size, int64_t source_ns) {
  return checked([&] {
    auto& e = *static_cast<Endpoint*>(handle);
    if (!e.offline) throw std::runtime_error("packet injection is offline-only");
    if (e.role == 0) e.receive(decode<LowState>(packet, size), source_ns);
    else e.receive(decode<LowCmd>(packet, size), source_ns);
    return 0;
  });
}
}
