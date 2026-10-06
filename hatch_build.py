"""Package the explicitly built DDS endpoint; never load or initialize DDS."""

import json
from pathlib import Path
import platform
import struct
import sys

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
  def initialize(self, version, build_data):
    machine = platform.machine()
    if sys.platform != "linux" or machine not in {"x86_64", "aarch64"}:
      raise RuntimeError("DDS wheels require Linux x86_64 or aarch64")
    bundle = Path(self.root) / "src/unitree_dds/_native"
    try:
      metadata = json.loads((bundle / "build_info.json").read_text())
      binaries = [bundle / "libunitree_dds.so", *sorted((bundle / "lib").glob("*.so*"))]
      if not all((bundle / name).is_file() for name in
        ("lib/libddsc.so.0", "lib/libddscxx.so.0", "licenses/UNITREE_SDK2_LICENSE")):
        raise ValueError("incomplete DDS dependency/license bundle")
      if metadata["architecture"] != machine:
        raise ValueError("DDS build architecture differs from this host")
      for binary in binaries:
        with binary.open("rb") as stream:
          header = stream.read(20)
        if (header[:6] != b"\x7fELF\x02\x01" or len(header) < 20
          or struct.unpack_from("<H", header, 18)[0] != {"x86_64": 62, "aarch64": 183}[machine]):
          raise ValueError(f"DDS binary architecture mismatch: {binary.name}")
    except (OSError, KeyError, ValueError) as exc:
      raise RuntimeError("Build DDS first: uv run --no-project python scripts/build_native.py "
        "--sdk-root /absolute/path/unitree_sdk2; " + str(exc)) from exc
    # Editable wheels only contain a .pth pointing at this prepared source tree.
    if self.target_name == "wheel" and version == "editable":
      return
    if self.target_name == "wheel":
      build_data["pure_python"] = False
      build_data["tag"] = f"py3-none-linux_{machine}"
