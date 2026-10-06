"""Build and stage the packaged HG DDS endpoint from an explicit SDK2 checkout."""
from pathlib import Path
import argparse
import hashlib
import json
import platform
import subprocess
import shutil

def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--sdk-root', type=Path, required=True)
  parser.add_argument('--output', type=Path, default=Path('.build'))
  args = parser.parse_args()
  sdk = args.sdk_root.resolve(strict=True)
  output = args.output.resolve()
  source = Path(__file__).resolve().parents[1] / 'cpp'
  commit = subprocess.check_output(['git', '-C', str(sdk), 'rev-parse', 'HEAD'], text=True).strip()
  pin = json.loads((source.parent / 'sdk.json').read_text())
  if commit != pin['commit']:
    raise RuntimeError('SDK commit differs from sdk.json; verify the SDK before updating its pin')
  subprocess.run(['cmake', '-S', str(source), '-B', str(output), '-DCMAKE_BUILD_TYPE=Release', f'-DUNITREE_SDK2_ROOT={sdk}'], check=True)
  subprocess.run(['cmake', '--build', str(output), '--parallel', '2'], check=True)
  inputs = [source / 'endpoint.cpp', source / 'CMakeLists.txt',
    sdk / 'include/unitree/idl/hg/LowCmd_.hpp', sdk / 'include/unitree/idl/hg/LowState_.hpp',
    sdk / f'lib/{platform.machine()}/libunitree_sdk2.a',
    sdk / f'thirdparty/lib/{platform.machine()}/libddsc.so',
    sdk / f'thirdparty/lib/{platform.machine()}/libddscxx.so']
  (output / 'build_info.json').write_text(json.dumps({
    'sdk_commit': commit,
    'architecture': platform.machine(),
    'sha256': {
      ('cpp/' + path.name if path.parent == source else 'sdk/' + str(path.relative_to(sdk))):
        hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs},
  }, indent=2) + '\n')
  bundle = source.parent / 'src/unitree_dds/_native'
  bundle.mkdir(parents=True, exist_ok=True)
  for name in ('libunitree_dds.so', 'build_info.json'):
    shutil.copyfile(output / name, bundle / name)
  for name in ('lib', 'licenses'):
    if (bundle / name).exists():
      shutil.rmtree(bundle / name)
    shutil.copytree(output / name, bundle / name)
  print(bundle / 'libunitree_dds.so')

if __name__ == '__main__':
  main()
