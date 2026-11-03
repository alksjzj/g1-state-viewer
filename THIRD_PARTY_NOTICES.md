# Third-party notices / 第三方来源与许可

本仓库的原创应用代码使用根目录 [LICENSE](LICENSE) 中的 MIT License。该许可不替代以下资产和依赖的许可，也不意味着 Unitree、Three.js、HomeBody 或其他上游为本项目背书。

## 1. 仓库中随附的文件

### Unitree G1 模型

- 路径：`assets/g1_viz/`（URDF 与可视化 STL 网格）。
- 直接来源：[Stanford-TML/homebody](https://github.com/Stanford-TML/homebody)，引入这些资产的版本为 `0db5c8de1b148fdf6c4fd0f7a4ea881ea7e50fb8`，原路径 `assets/robot/g1_viz/`。
- HomeBody 的 [第三方通知](https://github.com/Stanford-TML/homebody/blob/0db5c8de1b148fdf6c4fd0f7a4ea881ea7e50fb8/THIRD_PARTY_NOTICES.md)将这组 G1 29-DoF / Dex3 模型单独标为 **BSD-3-Clause**，完整随附条款保留在 [assets/LICENSE-Unitree](assets/LICENSE-Unitree)。该模型不改授为本项目的 MIT。
- 官方原始模型参考：[unitreerobotics/unitree_ros 的 G1 description](https://github.com/unitreerobotics/unitree_ros/tree/master/robots/g1_description)，含 `g1_29dof_with_hand_rev_1_0.urdf`。本仓库使用的是 HomeBody 的可视化版本，不声称与当前官方资产逐字节相同。
- 随直接来源保留的版权：`Copyright (c) 2016-2024, Unitree Robotics. All rights reserved.`
- 官方 `unitree_ros` 根许可证另载版权：`Copyright (c) 2016-2022 HangZhou YuShu TECHNOLOGY CO.,LTD. ("Unitree Robotics"). All rights reserved.` 该通知也予保留；见[官方 BSD-3-Clause 条款](https://github.com/unitreerobotics/unitree_ros/blob/master/LICENSE)。

BSD-3-Clause 允许按其条件再分发，并要求保留版权、许可条款及免责声明；不得借版权方名称为衍生产品背书。模型中的名称、标识和商标仍属于各自所有者。

### Three.js 与 OrbitControls

- 路径：`static/vendor/three.js`、`three-core.js`、`orbit-controls.js`、`orbit-controls-core.js`。
- 直接来源：HomeBody 的 `docs/scripts/`，保留本地模块拆分形式；库版本为 Three.js r180。
- 上游：[mrdoob/three.js](https://github.com/mrdoob/three.js/tree/r180)。
- 许可：MIT；完整文本保留在 [static/vendor/LICENSE-Three.txt](static/vendor/LICENSE-Three.txt)。
- 版权：`Copyright © 2010-2025 three.js authors`。

本仓库只复制上述模型和 JavaScript 资源，没有随附 HomeBody 的控制器、策略权重、重建数据或其他第三方闭包。

## 2. Docker 构建时获取的依赖

这些依赖不作为源码 vendoring 放入本仓库；Dockerfile / requirements.txt 在本机构建时获取和安装。版本及下载校验以这两个文件为准。本项目发布源码，不提供预构建 Docker 二进制镜像归档。

| 组件 | 上游 / 许可 | 来源与通知 |
| --- | --- | --- |
| Unitree Python SDK | [unitree_sdk2_python](https://github.com/unitreerobotics/unitree_sdk2_python)，BSD-3-Clause | Dockerfile 固定提交；完整[SDK LICENSE](https://github.com/unitreerobotics/unitree_sdk2_python/blob/814556d15970dd2ecf1c9984e845ca02ab07e206/LICENSE) |
| CycloneDDS C 库 | [eclipse-cyclonedds/cyclonedds 0.10.2](https://github.com/eclipse-cyclonedds/cyclonedds/tree/0.10.2)，EPL-2.0 或 EDL-1.0 | Dockerfile 源码构建并校验下载；保留上游 LICENSE 和安装生成的通知 |
| CycloneDDS Python binding | [eclipse-cyclonedds/cyclonedds-python](https://github.com/eclipse-cyclonedds/cyclonedds-python)，EPL-2.0 或 EDL-1.0 | `requirements.txt` 固定版本；许可证见其安装包与上游源码 |
| NumPy | [numpy/numpy](https://github.com/numpy/numpy)，BSD-3-Clause，附带组件可能另有条款 | 以安装包中的 LICENSE / bundled-library notices 为准 |
| OpenCV Python headless | [opencv/opencv-python](https://github.com/opencv/opencv-python)，包装代码 MIT，OpenCV 本体 Apache-2.0，wheel 内含其他组件 | 安装包中的 `LICENSE.txt` 和 `LICENSE-3RD-PARTY.txt`；wheel 自带的 FFmpeg 也须遵守其通知 |
| FFmpeg 系统共享库 | [FFmpeg](https://ffmpeg.org/)，LGPL / GPL 等，取决于实际构建选项及组件 | Ubuntu 的 `libavcodec58`、`libavutil56`、`libswscale5`；以 `/usr/share/doc/*/copyright` 和对应 Ubuntu 源包为准，不能仅按库名断言全部为 LGPL |
| Rich / rich-click / Click / markdown-it-py / mdurl | 各安装包列明的 MIT 或 BSD 许可 | 具体版本见 `requirements.txt`，完整条款见包的 `.dist-info/licenses/` 或许可证文件 |
| Pygments / typing_extensions | BSD / PSF 等各自许可 | 以安装包许可证为准 |
| Ubuntu 基础系统、Python 及 apt 依赖 | 各软件包自己的许可 | 镜像内 `/usr/share/doc/<包名>/copyright`，以及对应 Ubuntu 源包 |

### Unitree SDK 版权通知

```text
Copyright (c) 2016-2024 HangZhou YuShu TECHNOLOGY CO.,LTD. ("Unitree Robotics")
All rights reserved.
```

SDK 及本项目参考的 Unitree 消息接口仍遵循 BSD-3-Clause；消息布局参考还包括 [unitree_sdk2](https://github.com/unitreerobotics/unitree_sdk2)。本项目自写的接收器不代表上游保证所有固件使用相同布局。

## 3. 获取对应源码和依赖许可

CycloneDDS C 的完整对应源码可从 [0.10.2 源码归档](https://github.com/eclipse-cyclonedds/cyclonedds/archive/refs/tags/0.10.2.tar.gz)获取，其上游[完整许可](https://github.com/eclipse-cyclonedds/cyclonedds/blob/0.10.2/LICENSE)包含 EPL-2.0 / EDL-1.0。Python SDK 的完整源码由 Dockerfile 中固定提交的 GitHub 归档提供。Python 依赖可以按 `requirements.txt` 中的精确版本从 PyPI 获取源码发行包与项目链接。

Ubuntu 的 FFmpeg 包可能启用 GPL 功能，其条款不能笼统写成 LGPL。查看实际镜像包版本和许可证：

```bash
docker run --rm --entrypoint sh g1-state-viewer:local -c \
  'dpkg-query -W libavcodec58 libavutil56 libswscale5; cat /usr/share/doc/libavcodec58/copyright'
```

FFmpeg 对应源码为 Ubuntu 的 `ffmpeg` 源包，需匹配镜像内安装的精确版本及 Ubuntu 补丁。可通过 [Ubuntu Launchpad ffmpeg 源包](https://launchpad.net/ubuntu/+source/ffmpeg)获取，或在启用匹配发行版 `deb-src` 的 Ubuntu 环境中使用 `apt source ffmpeg`。相关共享库通过动态加载使用；原始源码、修改补丁与编译选项应按实际再分发物保留。Python OpenCV wheel 的附带 FFmpeg 与系统 FFmpeg 是两个来源，分别检查各自许可证。

若再分发自行构建的二进制镜像，应同时交付适用于其中所有组件的通知、许可证和对应源码获取方式，并履行实际 LGPL / GPL / EPL 或其他条款的要求。本仓库的 MIT License 不免除这些义务。

## 4. 数据接口参考

- [ROS 2 PointCloud2 定义](https://github.com/ros2/common_interfaces/blob/rolling/sensor_msgs/msg/PointCloud2.msg)：点云布局参考。
- [Unitree G1 低层示例](https://github.com/unitreerobotics/unitree_sdk2/blob/main/example/g1/low_level/g1_ankle_swing_example.cpp)：关节索引与 PR / AB 语义参考，不作为控制程序运行。
- [Unitree Dex3 枚举](https://github.com/unitreerobotics/xr_teleoperate/blob/main/teleop/robot_control/robot_hand_unitree.py)：左右手映射参考；本项目右手默认顺序另有实机校正，见[兼容性说明](docs/compatibility.md)。
