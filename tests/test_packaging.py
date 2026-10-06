"""Build-hook checks use inert ELF headers, never an executable DDS library."""

import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import tarfile
import zipfile

import pytest


def _project(tmp_path):
  root = Path(__file__).resolve().parents[1]
  shutil.copyfile(root / "hatch_build.py", tmp_path / "hatch_build.py")
  (tmp_path / "pyproject.toml").write_text('''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[project]
name = "unitree-dds"
version = "0.1.0"
requires-python = ">=3.8"
[tool.hatch.build]
artifacts = ["src/unitree_dds/_native/**"]
[tool.hatch.build.targets.wheel]
packages = ["src/unitree_dds"]
[tool.hatch.build.hooks.custom]
path = "hatch_build.py"
''')
  package = tmp_path / "src/unitree_dds"
  package.mkdir(parents=True)
  (package / "__init__.py").touch()
  bundle = package / "_native"
  (bundle / "lib").mkdir(parents=True)
  (bundle / "licenses").mkdir()
  header = bytearray(64)
  header[:6] = b"\x7fELF\x02\x01"
  struct.pack_into("<H", header, 18, {"x86_64": 62, "aarch64": 183}[platform.machine()])
  for name in ("libunitree_dds.so", "lib/libddsc.so.0", "lib/libddscxx.so.0"):
    (bundle / name).write_bytes(header)
  (bundle / "build_info.json").write_text(json.dumps({"architecture": platform.machine()}))
  (bundle / "licenses/UNITREE_SDK2_LICENSE").write_text("inert test fixture")
  return bundle


def test_wheel_has_platform_tag_and_carries_bundle_through_sdist(tmp_path):
  _project(tmp_path)
  result = subprocess.run(["uv", "build"], cwd=tmp_path, capture_output=True, text=True)
  assert result.returncode == 0, result.stderr
  wheel, = (tmp_path / "dist").glob("*.whl")
  assert wheel.name.endswith(f"-py3-none-linux_{platform.machine()}.whl")
  with zipfile.ZipFile(wheel) as archive:
    names = archive.namelist()
    metadata = archive.read("unitree_dds-0.1.0.dist-info/WHEEL").decode()
    assert "Root-Is-Purelib: false" in metadata
    assert f"Tag: py3-none-linux_{platform.machine()}" in metadata
    for name in ("libunitree_dds.so", "lib/libddsc.so.0", "lib/libddscxx.so.0",
      "build_info.json", "licenses/UNITREE_SDK2_LICENSE"):
      assert "unitree_dds/_native/" + name in names
  sdist, = (tmp_path / "dist").glob("*.tar.gz")
  with tarfile.open(sdist) as archive:
    assert any(name.endswith("src/unitree_dds/_native/libunitree_dds.so")
      for name in archive.getnames())


@pytest.mark.parametrize("failure", ["missing_library", "metadata_arch", "binary_arch", "dependency", "license"])
def test_build_rejects_missing_or_mismatched_bundle(tmp_path, failure):
  bundle = _project(tmp_path)
  if failure == "missing_library":
    (bundle / "libunitree_dds.so").unlink()
  elif failure == "metadata_arch":
    (bundle / "build_info.json").write_text('{"architecture": "wrong"}')
  elif failure == "binary_arch":
    path = bundle / "lib/libddsc.so.0"
    header = bytearray(path.read_bytes())
    struct.pack_into("<H", header, 18, 0)
    path.write_bytes(header)
  elif failure == "dependency":
    # Other .so files cannot substitute for the dependency's required SONAME.
    shutil.copyfile(bundle / "lib/libddsc.so.0", bundle / "lib/libddsc.so")
    shutil.copyfile(bundle / "lib/libddscxx.so.0", bundle / "lib/libddscxx.so")
    (bundle / "lib/libddscxx.so.0").unlink()
  else:
    (bundle / "licenses/UNITREE_SDK2_LICENSE").unlink()
  result = subprocess.run(["uv", "build", "--wheel"], cwd=tmp_path, capture_output=True, text=True)
  assert result.returncode != 0
  assert "Build DDS first:" in result.stderr
  assert not list((tmp_path / "dist").glob("*.whl"))
