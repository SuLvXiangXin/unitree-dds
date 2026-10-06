import importlib
import ctypes
import threading

import numpy as np
import pytest

import unitree_dds
from unitree_dds import DdsCommand, DdsEndpoint, DdsState, array


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
  original = DdsEndpoint.__init__
  def initialize(self, *args, **kwargs):
    assert kwargs.get("offline") is True, "automated tests must not open DDS"
    original(self, *args, **kwargs)
  monkeypatch.setattr(DdsEndpoint, "__init__", initialize)


@pytest.mark.parametrize("role", ["policy", "simulator"])
def test_offline_latest_snapshot_and_permissions(role):
  endpoint = DdsEndpoint(role=role, offline=True, enable_write=False)
  endpoint.start()
  try:
    read = endpoint.read_state if role == "policy" else endpoint.read_command
    assert read() is None
    value = DdsState() if role == "policy" else DdsCommand()
    for sequence in range(1, 41):
      array(value.q)[:] = sequence
      endpoint.feed(endpoint.encode(value), source_ns=sequence)
    sample = read()
    assert sample.sequence == 40 and sample.source_ns == 40
    assert sample.received_ns > 0
    np.testing.assert_array_equal(array(sample.q), np.full(35, 40))
    array(sample.q)[:] = 0
    assert array(read().q)[0] == 40
    with pytest.raises(RuntimeError, match="not enabled"):
      endpoint.write_command(DdsCommand())
    with pytest.raises(RuntimeError, match="not enabled"):
      endpoint.write_state(DdsState())
    with pytest.raises(RuntimeError):
      endpoint.feed(b"invalid")
  finally:
    endpoint.close()
  endpoint.close()
  with pytest.raises(RuntimeError, match="closed"):
    read()


def test_state_tick_imu_and_concurrent_snapshot():
  endpoint = DdsEndpoint(role="policy", offline=True)
  endpoint.start()
  state = DdsState()
  state.tick = 77
  array(state.quat)[:] = (1, 0, 0, 0)
  array(state.gyro)[:] = (1, 2, 3)
  packet = endpoint.encode(state)
  def receive():
    for sequence in range(100):
      endpoint.feed(packet, source_ns=sequence)
  worker = threading.Thread(target=receive)
  try:
    worker.start()
    worker.join()
    sample = endpoint.read_state()
    assert sample.tick == 77 and sample.sequence == 100
    np.testing.assert_array_equal(array(sample.gyro), (1, 2, 3))
  finally:
    worker.join()
    endpoint.close()


def test_import_does_not_load_native_library(monkeypatch):
  def forbidden(*args):
    pytest.fail("import loaded native library")
  monkeypatch.setattr(ctypes, "CDLL", forbidden)
  importlib.reload(unitree_dds)


def test_missing_library_fails_without_start(monkeypatch, tmp_path):
  monkeypatch.setattr(unitree_dds, "_LIBRARY_PATH", tmp_path / "missing.so")
  with pytest.raises(RuntimeError, match="library is missing"):
    DdsEndpoint(role="policy", offline=True)
