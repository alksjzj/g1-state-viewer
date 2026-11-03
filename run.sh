#!/usr/bin/env bash
set -euo pipefail
viewer_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
container_name="g1-state-viewer-release"
image_name="${G1_VIEWER_IMAGE:-g1-state-viewer:local}"
interface="auto"
robot_ip="192.168.123.161"
mode="live"
background=false
build=false
lidar_reliable=true
app_args=()
usage() {
    cat <<'HELP'
用法: ./run.sh [启动选项] [查看器选项]

默认：Linux 原生 Docker 前台运行真机只读查看器，浏览器访问 http://127.0.0.1:8765。
  --interface NAME       明确选择机器人所在网卡；默认 auto。
  --robot-ip IP          仅辅助自动选择同网段网卡，默认 192.168.123.161。
                         默认地址不保证适用于你的机器人；不修改 IP/路由，不设置 DDS peer。
  --background           后台运行；多个候选网卡时必须明确指定 --interface。
  --demo                 宿主 python3 离线演示；无需 Docker/SDK/机器人。
  --build                构建镜像后继续启动（源码更新后使用）；首次构建需联网下载依赖。
  --interfaces           只读列出宿主网卡、链路和 IPv4，不启动容器。
  --doctor               只读检查宿主网络、Python、Docker、镜像和路由，不启动容器。
  --discover             有限时长只读发现 DDS 发布话题；支持 --seconds 8、--domain、--peer。
  --stop                 仅停止本目录标记的 g1-state-viewer-release 容器。
  --lidar-best-effort     禁用默认的 --lidar-reliable 点云订阅要求。
  --lidar-reliable        使用 reliable 点云订阅要求（默认）。
  --help, -h             显示本帮助。

查看器选项直接传给 app.py，例如 --host 127.0.0.1 --port 8765 --domain 0
  --peer IP 可重复；--lidar-topic TOPIC；--max-points 50000；--max-range 50。
  --right-hand-order middle-first|index-first（具体指序需按实机核对）。

自动选择要求：UP、有 carrier、有 IPv4、与目标同网段的唯一物理网卡。
多个候选时前台终端询问；后台/非终端报出候选和指定命令。
虚拟网卡不会自动选择；确认用途后可 --interface lo 等明确指定。
无同网段候选时请自行核对网线、机器人实际地址和主机网络配置。
镜像不存在时自动构建；默认镜像 g1-state-viewer:local，可用 G1_VIEWER_IMAGE 覆盖。
Docker 使用宿主网络；需要 Ubuntu/Linux 原生 Docker Engine 和当前用户访问权限。
HELP
}
set_mode() {
    if [[ "$mode" != live && "$mode" != "$1" ]]; then
        echo "模式选项不能组合：$mode / $1" >&2
        exit 2
    fi
    mode="$1"
}
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --background) background=true; shift ;;
        --build) build=true; shift ;;
        --demo) set_mode demo; shift ;;
        --discover) set_mode discover; shift ;;
        --interfaces) set_mode interfaces; shift ;;
        --doctor) set_mode doctor; shift ;;
        --stop) set_mode stop; shift ;;
        --interface|--robot-ip)
            if (($# < 2)) || [[ "$2" == --* || -z "$2" ]]; then
                echo "$1 需要一个值。" >&2; exit 2
            fi
            if [[ "$1" == --interface ]]; then interface="$2"; else robot_ip="$2"; fi
            shift 2 ;;
        --interface=*) interface="${1#*=}"; shift ;;
        --robot-ip=*) robot_ip="${1#*=}"; shift ;;
        --lidar-best-effort) lidar_reliable=false; shift ;;
        --lidar-reliable) lidar_reliable=true; shift ;;
        --) shift; app_args+=("$@"); break ;;
        *) app_args+=("$1"); shift ;;
    esac
done
if [[ "$background" == true && "$mode" != live ]]; then
    echo "--background 仅用于真机查看器；--demo 通过宿主前台运行。" >&2; exit 2
fi
if [[ "$build" == true && "$mode" != live && "$mode" != discover ]]; then
    echo "--build 仅用于真机查看器或 --discover。" >&2; exit 2
fi
if [[ "$mode" == demo ]]; then
    exec python3 -B "$viewer_dir/app.py" --demo "${app_args[@]}"
fi
if [[ "$mode" == interfaces || "$mode" == doctor ]]; then
    exec python3 -B "$viewer_dir/network.py" "--$mode" --interface "$interface" \
        --robot-ip "$robot_ip" --image "$image_name" "${app_args[@]}"
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "未找到 docker；真机模式需要 Linux Docker Engine。离线预览使用 ./run.sh --demo。" >&2; exit 1
fi
if [[ "$mode" == stop ]]; then
    owner="$(docker inspect --format '{{ index .Config.Labels "g1.viewer.root" }}' "$container_name" 2>/dev/null || true)"
    if [[ "$owner" != "$viewer_dir" ]]; then
        echo "未找到属于本目录的容器 $container_name；不会停止其他目录的容器。" >&2; exit 1
    fi
    exec docker stop --time 5 "$container_name"
fi
if [[ "$(uname -s)" != Linux ]]; then
    echo "真机 DDS 模式要求 Linux 原生 Docker host 网络。离线预览使用 ./run.sh --demo。" >&2; exit 1
fi
network_args=(--interface "$interface" --robot-ip "$robot_ip")
if [[ "$background" == true ]]; then network_args+=(--non-interactive); fi
# Preserve stdin for interactive disambiguation; network.py writes prompts to stderr.
selected_interface="$(python3 -B "$viewer_dir/network.py" "${network_args[@]}")"
echo "宿主网卡：$selected_interface（目标 $robot_ip 仅用于子网选择）" >&2
if ! docker info >/dev/null 2>&1; then
    echo "无法访问 Docker daemon；请检查 Docker 服务与当前用户权限（可运行 ./run.sh --doctor）。" >&2; exit 1
fi
if [[ "$build" == true ]] || ! docker image inspect "$image_name" >/dev/null 2>&1; then
    echo "构建 $image_name；首次构建需联网下载基础镜像与依赖。" >&2
    docker build --tag "$image_name" "$viewer_dir"
fi
if docker inspect "$container_name" >/dev/null 2>&1; then
    echo "容器名 $container_name 已存在。确认归属后运行 ./run.sh --stop；不会覆盖现有容器。" >&2; exit 1
fi
docker_mode=()
if [[ "$background" == true ]]; then docker_mode+=(--detach); fi
entrypoint_file="app.py"
extra_args=()
if [[ "$mode" == discover ]]; then
    entrypoint_file="diagnose.py"
elif [[ "$lidar_reliable" == true ]]; then
    extra_args+=(--lidar-reliable)
fi
exec docker run --rm --init --network host --read-only \
    "${docker_mode[@]}" --name "$container_name" --label "g1.viewer.root=$viewer_dir" \
    --tmpfs /tmp:rw,nosuid,nodev,size=64m \
    --cap-drop ALL --security-opt no-new-privileges \
    --user "$(id -u):$(id -g)" \
    -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python3 \
    "$image_name" -B "/app/$entrypoint_file" --interface "$selected_interface" \
    "${extra_args[@]}" "${app_args[@]}"
