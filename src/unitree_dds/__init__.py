"""Explicitly loaded C++ HG DDS endpoint; importing this module opens no DDS."""
from __future__ import annotations

import ctypes as ct
from pathlib import Path
import threading

import numpy as np


class DdsState(ct.Structure):
  _fields_ = [("received_ns", ct.c_uint64), ("sequence", ct.c_uint64),
    ("source_ns", ct.c_int64), ("tick", ct.c_uint32), ("mode_machine", ct.c_uint8),
    ("q", ct.c_float * 35), ("dq", ct.c_float * 35), ("tau", ct.c_float * 35),
    ("quat", ct.c_float * 4), ("gyro", ct.c_float * 3)]


class DdsCommand(ct.Structure):
  _fields_ = [("received_ns", ct.c_uint64), ("sequence", ct.c_uint64),
    ("source_ns", ct.c_int64), ("mode_machine", ct.c_uint8), ("mode_pr", ct.c_uint8),
    ("enabled", ct.c_uint8 * 35),
    *[(name, ct.c_float * 35) for name in ("q", "dq", "kp", "kd", "tau")]]


def array(value) -> np.ndarray:
  return np.ctypeslib.as_array(value)


_LIBRARY_PATH = Path(__file__).resolve().parent / "_native/libunitree_dds.so"


class DdsEndpoint:
  """Package-local library/ABI loading, followed by role-scoped startup."""

  def __init__(self, *, role: str, domain_id: int = 1,
    interface: str | None = None, state_topic: str = 'rt/lowstate',
    command_topic: str = 'rt/lowcmd', enable_write: bool = False, offline: bool = False) -> None:
    if role not in {'policy', 'simulator'}:
      raise ValueError('DDS role must be policy or simulator')
    if isinstance(domain_id, bool) or not 0 <= domain_id <= 232:
      raise ValueError('DDS domain_id must be between 0 and 232')
    try:
      path = _LIBRARY_PATH.resolve(strict=True)
    except OSError as exc:
      raise RuntimeError(f"DDS library is missing from the installed package: {_LIBRARY_PATH}; "
        "install the matching platform wheel, or build DDS for this source checkout") from exc
    try:
      self.lib = ct.CDLL(str(path))  # CDLL releases the GIL during native calls.
    except OSError as exc:
      raise RuntimeError(f"cannot load DDS library {path}: {exc}") from exc
    signatures = {
      'rpd_abi_version': (ct.c_uint32, []),
      'rpd_state_size': (ct.c_size_t, []), 'rpd_command_size': (ct.c_size_t, []),
      'rpd_error': (ct.c_char_p, []),
      'rpd_create': (ct.c_void_p, [ct.c_int, ct.c_int, ct.c_int, ct.c_uint32, ct.c_char_p, ct.c_char_p, ct.c_char_p]),
      'rpd_close': (None, [ct.c_void_p]),
      'rpd_read_state': (ct.c_int, [ct.c_void_p, ct.POINTER(DdsState)]),
      'rpd_read_command': (ct.c_int, [ct.c_void_p, ct.POINTER(DdsCommand)]),
      'rpd_write_state': (ct.c_int, [ct.c_void_p, ct.POINTER(DdsState)]),
      'rpd_write_command': (ct.c_int, [ct.c_void_p, ct.POINTER(DdsCommand)]),
      'rpd_encode_state': (ct.c_int, [ct.POINTER(DdsState), ct.c_void_p, ct.c_size_t]),
      'rpd_encode_command': (ct.c_int, [ct.POINTER(DdsCommand), ct.c_void_p, ct.c_size_t]),
      'rpd_feed': (ct.c_int, [ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.c_int64]),
    }
    try:
      for name, (result, arguments) in signatures.items():
        function = getattr(self.lib, name)
        function.restype, function.argtypes = result, arguments
    except AttributeError as exc:
      raise RuntimeError('DDS ABI symbols are missing; rebuild this library') from exc
    if (self.lib.rpd_abi_version(), self.lib.rpd_state_size(), self.lib.rpd_command_size()) != (1, ct.sizeof(DdsState), ct.sizeof(DdsCommand)):
      raise RuntimeError("DDS ABI mismatch; rebuild this library")

    self._create_args = (int(role == 'simulator'), int(offline), int(enable_write),
      domain_id, (interface or '').encode(), state_topic.encode(), command_topic.encode())
    self._lock = threading.Lock()
    self._handle = None
    self._closed = False

  def start(self) -> None:
    """Open DDS only after the caller's startup permission checks complete."""
    with self._lock:
      if self._closed:
        raise RuntimeError('DDS endpoint is closed')
      if self._handle is None:
        self._handle = self.lib.rpd_create(*self._create_args)
        if not self._handle:
          self._check(-1)

  def _check(self, result: int) -> int:
    if result < 0:
      raise RuntimeError(self.lib.rpd_error().decode('utf-8', errors='replace'))
    return result

  def encode(self, value: DdsState | DdsCommand) -> bytes:
    buffer = ct.create_string_buffer(4096)
    function = self.lib.rpd_encode_state if isinstance(value, DdsState) else self.lib.rpd_encode_command
    size = self._check(function(ct.byref(value), buffer, len(buffer)))
    return buffer.raw[:size]

  def _call(self, name: str, *args) -> int:
    with self._lock:
      if self._closed:
        raise RuntimeError('DDS endpoint is closed')
      if self._handle is None:
        raise RuntimeError('DDS endpoint has not started')
      return self._check(getattr(self.lib, name)(self._handle, *args))

  def read_state(self) -> DdsState | None:
    state = DdsState()
    return state if self._call('rpd_read_state', ct.byref(state)) else None

  def read_command(self) -> DdsCommand | None:
    command = DdsCommand()
    return command if self._call('rpd_read_command', ct.byref(command)) else None

  def write_state(self, state: DdsState) -> None:
    self._call('rpd_write_state', ct.byref(state))

  def write_command(self, command: DdsCommand) -> None:
    self._call('rpd_write_command', ct.byref(command))

  def feed(self, packet: bytes, source_ns: int = 0) -> None:
    """Inject an offline packet; real DDS endpoints reject this operation."""
    self._call('rpd_feed', packet, len(packet), source_ns)

  def close(self) -> None:
    with self._lock:
      if self._handle is not None:
        self.lib.rpd_close(self._handle)
        self._handle = None
      self._closed = True
