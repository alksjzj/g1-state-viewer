# G1 State Viewer · G1 只读状态观测台

在浏览器中查看 Unitree G1 的身体姿态、双手关节、MID-360 点云、设备状态和前置视频。面向 **G1 EDU 29 自由度身体 + 左右 Dex3 各 7 个关节**，中文界面，模型和 Three.js 均在本仓库，运行时不依赖 CDN，也不需要安装 ROS。

应用只建立状态订阅，不发布运动指令，不切换模式，不打开相机或雷达服务。离线演示完全使用人工数据；真实连接失败时不会自动用演示数据代替。该项目是独立社区工具，不是宇树官方产品。

## 快速开始

### 1. 准备电脑

推荐 Ubuntu **22.04 / 24.04，原生 Linux Docker Engine**。宿主需要 `python3`、`iproute2`、`git` 和支持 **WebGL 2** 的现代浏览器。Docker Desktop、WSL、macOS、Windows 和其他 CPU 架构尚未完成兼容性验证。

```bash
sudo apt update
sudo apt install git python3 iproute2
```

按 [Docker 官方 Ubuntu 安装文档](https://docs.docker.com/engine/install/ubuntu/#install-using-the-apt-repository)安装 Docker Engine，并确认 `docker info` 能访问服务。如果提示权限不足，可以使用 `sudo ./run.sh`，或按 [Docker 官方用户组设置](https://docs.docker.com/engine/install/linux-postinstall/#manage-docker-as-a-non-root-user)配置后重新登录。不要把 Docker socket 改成所有用户可写。

### 2. 获取并预览

```bash
git clone https://github.com/alksjzj/g1-state-viewer.git
cd g1-state-viewer
./run.sh --demo
```

打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。黄色提示表示全部数据为人工示例；演示不需要 Docker 或机器人。按 `Ctrl+C` 结束演示。

### 3. 连接机器人

先用网线连接机器人和电脑，并按 [网络配置指南](docs/networking.md)设置电脑 IPv4。**插上网线不会自动得到正确的机器人网段地址**，启动器也不会修改网络。

```bash
./run.sh --interfaces
./run.sh --doctor
./run.sh
```

首次真机启动会自动构建独立镜像 `g1-state-viewer:local`，需要联网下载 Ubuntu 基础镜像和依赖；之后运行使用本地镜像。无需其他 Unitree 工程或预先安装的 SDK 镜像。

默认目标地址是 `192.168.123.161`。启动器检查有 IPv4、接口启用且有载波的物理网卡：与目标同网段且只有一个候选时自动选择；多个候选时在交互终端显示菜单；非交互运行需要明确指定网卡。自动选择只选择网卡，不代表 DDS 已连接。

如果自动选择失败，或使用其他地址 / 可路由网络：

```bash
./run.sh --interface enp3s0 --robot-ip 192.168.123.161
```

`enp3s0` 只是示例，请替换为 `--interfaces` 列出的实际网卡名；`--interface` 接收网卡名，`--robot-ip` 接收机器人 IPv4，用于网卡选择与网络检查。程序订阅选定网络上的 DDS 话题，并不按该 IP 过滤机器人；同一 DDS 网络应只暴露目标机器人的相关发布者。

浏览器仍打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。界面分别显示各通道的接收帧数、频率和数据年龄；网页打开成功不等于机器人数据已接通。

## 常用命令

```bash
./run.sh --help
./run.sh --interfaces                          # 网卡、地址与候选信息
./run.sh --doctor                              # 本机环境和网络预检
./run.sh --build                               # 构建 / 更新镜像后继续启动
./run.sh --background --interface enp3s0        # 后台运行
./run.sh --stop                                # 停止本仓库启动的后台实例
./run.sh --discover --interface enp3s0 --seconds 8  # 只读发现话题与类型
./run.sh --interface enp3s0 --lidar-best-effort  # 兼容 best-effort 点云发布端
```

容器名是 `g1-state-viewer-release`。前台运行按 `Ctrl+C` 退出；后台日志可用 `docker logs g1-state-viewer-release` 查看。默认 HTTP 端口 `8765`，可用 `--port` 修改；默认 DDS domain 为 `0`，可用 `--domain` 修改。

代码、网页和模型在构建时复制到镜像的 `/app`。修改源码或 `git pull` 后先停止旧实例，再运行 `./run.sh --build` 重新构建并启动；正在运行的容器不会自动读取新代码。

网页默认仅监听本机 `127.0.0.1`。如需让同一可信局域网的手机 / 电脑查看：

```bash
./run.sh --interface enp3s0 --host 0.0.0.0
```

在另一设备打开 `http://运行查看器的电脑IP:8765`。此服务没有登录认证，只适合可信网络，不应做公网端口转发。

## 可以看到什么

| 内容 | 数据来源与显示 |
| --- | --- |
| 身体姿态 | `rt/lowstate` 的前 29 个关节角与骨盆 IMU 四元数 |
| 左右 Dex3 | `rt/dex3/left/state`、`rt/dex3/right/state`，每手独立 7 关节 |
| IMU / 关节表 | RPY、角速度、加速度、`wxyz` 四元数；角度、速度、估计力矩、温度、电压和原始状态 |
| MID-360 | 默认 `rt/utlidar/cloud_livox_mid360`，标准 `PointCloud2` XYZ 点云 |
| 电池 | `rt/lf/bmsstate`，SOC 百分比、循环次数及 BMS 原始字段 |
| 运动模式 | `rt/sportmodestate`，FSM ID、FSM mode、任务编号及时间 |
| 温度 / 主板 | 身体与双手各自最高电机温度；`rt/lf/mainboardstate` 的原始字段 |
| 前置视频 | `rt/frontvideostream`，兼容的 H.264 360 分支解码为 640×360 图像 |

各通道独立判断新鲜度。两秒未收到有效数据后标记过期，身体 / 双手保留灰色历史姿态，点云保留淡色历史画面；视频过期时隐藏图像。无数据、已匹配、收到有效数据是不同状态，初始化或解码失败会显示错误。

## 显示边界

- 模型固定骨盆位置，不估计房间内平移、足底接触或轨迹。`mode_pr=0` 使用 PR 关节坐标；非零时停用身体角度映射，保留原始数值。
- 右手默认 `middle-first` 来自一台 G1 的用户现场校正。其他设备可试 `--right-hand-order index-first` 并对照实际手指；不自动更改角度符号或零点。
- 点云在自身 `frame_id` 中独立显示，不与身体模型融合、不建图。短时叠加没有运动补偿，移动时可能重影；可在网页切回单帧。
- SOC 显示为百分比；BMS 电压、电流、温度、SOH 及主板未核实的字段保留原值，不猜单位或换算。未知 FSM 显示“未映射模式”，不推测机器人动作。
- 视频依赖具体固件消息结构与 FFmpeg ABI，不保证所有 G1 都可用；图像只保留内存中的最近一帧，不录像、不上传云端。

话题、QoS、视频结构和已知硬件范围见 [兼容性说明](docs/compatibility.md)。连接排查见 [网络配置指南](docs/networking.md)。

## 验证与开发

推送到 GitHub 后，[Actions](https://github.com/alksjzj/g1-state-viewer/actions) 在 GitHub 提供的 Ubuntu 服务器上构建独立镜像，并执行环境检查和隔离 DDS 集成测试。这个过程不会在维护者电脑上下载镜像或留下 Docker 构建缓存。下载项目的使用者首次运行仍需在自己的电脑上构建运行环境。

数据解析单元测试及隔离 DDS → HTTP 测试用于检查程序行为，不能替代真机兼容性验证。项目开发时曾在一台 G1 EDU 29 + 双 Dex3 + MID-360 上观察到身体、双手、设备状态、点云及前置视频；这不代表所有固件或其他 G1 配置均已验证。Ubuntu 22.04 / 24.04 是目标宿主环境，尚不声称已在另一台全新电脑上完成实机验收。

```bash
python3 -B -m unittest discover -s tests -v
```

隔离集成测试的要求见 `tests/`；测试发布者只生成模拟状态，使用 loopback 和隔离 domain。应用入口与 DDS 接收在 `app.py`、`transport.py`；网络选择在 `network.py`；状态、点云和视频处理在 `state.py`、`lidar.py`、`video.py`；浏览器资源在 `static/`，模型在 `assets/`。

## 许可与来源

原创代码采用 [MIT License](LICENSE)。随附模型采用 Unitree BSD-3-Clause，Three.js / OrbitControls 采用 MIT；SDK、CycloneDDS、FFmpeg 和系统依赖分别遵循自己的许可。完整来源、版权通知及镜像依赖源码获取方式见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
