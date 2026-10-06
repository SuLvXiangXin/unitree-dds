# unitree-dds

独立的 Python/C++ Unitree HG 通信包。官方 SDK 保持原样；本包只使用 HG
消息定义和 CycloneDDS，不包含机器人 profile、policy、默认增益或 motion release。

## 安装

源码仓库：[SuLvXiangXin/unitree-dds](https://github.com/SuLvXiangXin/unitree-dds)。
日常安装使用 [v0.1.0 Release](https://github.com/SuLvXiangXin/unitree-dds/releases/tag/v0.1.0)
的预编译 wheel，不需要 clone SDK 或编译 C++：

```bash
uv pip install https://github.com/SuLvXiangXin/unitree-dds/releases/download/v0.1.0/unitree_dds-0.1.0-py3-none-linux_x86_64.whl
```

aarch64 将文件名中的 `x86_64` 换为 `aarch64`。两个 wheel 的 SHA-256
随 Release 的 `SHA256SUMS` 发布；安装工程应锁定版本 URL 和 wheel 哈希。
本包未发布到 PyPI，不要依赖同名包或使用源码 Git URL 代替预编译 wheel。

## 源码构建

只有开发通信库或新增部署平台时需要构建。先 clone 本仓库，再执行下面步骤。

先按 `sdk.json` 下载固定版本的官方 SDK 到 `third_party/unitree_sdk2`：

```bash
git clone https://github.com/unitreerobotics/unitree_sdk2.git third_party/unitree_sdk2
git -C third_party/unitree_sdk2 checkout 63096d0ac0c5d2dec9d6e0c22cd5233410ca2f36
```

Linux x86_64/aarch64 原生构建需要 CMake、C++17 编译器和 Boost 头文件：

```bash
uv run --no-project python scripts/build_native.py --sdk-root third_party/unitree_sdk2
uv sync --group dev
uv run pytest -q
uv build
```

wheel 包含通信库、DDS 动态依赖、许可证和构建哈希；安装不编译、不启动 DDS。
ctypes 使用 C ABI，Python 3.8+ 共用同架构 wheel；glibc/ROS 库兼容仍需目标验收，
不声称 manylinux 兼容。构建信息记录 SDK commit；仅升级并验证 SDK 后才更新 pin。

## 接口

```python
from unitree_dds import DdsEndpoint, DdsState, DdsCommand, array

endpoint = DdsEndpoint(role="policy", domain_id=0, interface="eth0", enable_write=False)
endpoint.start()
try:
  state = endpoint.read_state()
finally:
  endpoint.close()
```

- `policy` 读取 LowState，可显式允许写 LowCmd；`simulator` 读取 LowCmd，
  可显式允许写 LowState。默认禁止写，禁止写时不创建对应 DDS writer。
- `start()` 才创建 DDS；导入以及构造/ABI 检查不创建 participant。
- `read_state/read_command` 无数据返回 None；否则返回复制的最新快照。
  sequence 和 received_ns 表示本地接收进展，tick 是硬件来源 tick，
  source_ns 是 DDS 来源时间。关节、电机 ID 与过期策略由调用者决定。
- 命令数组是 HG 的 35 个电机槽位；enabled 为每槽的电机 mode。
  write_command 每次调用发送一次，不启动定时重发线程，不做 action 转换。
- 所有读写错误向调用者传播。调用者必须在 finally 中 close。
- simulator 只用于授权的独立仿真；不得把仿真 state 发布到真机 domain/interface。
- `offline=True` 完全不创建 DDS；encode/feed 支持离线协议和最新帧测试，
  离线端点拒绝写入。自动化测试只能使用离线端点。

ROS 环境可能有其他版本的 libddsc。ROS/DDS 混合进程在启动 Python 前，
应将本包的 `unitree_dds/_native/lib` 放在 LD_LIBRARY_PATH 最前面；
仅修改需要 DDS 的 state 进程，不污染 camera 环境。

本包基于 robot-policy-deploy 中的既有薄 endpoint；官方 SDK 与第三方许可证
随 wheel 放在 `unitree_dds/_native/licenses`。不 fork 或修改官方 SDK。
