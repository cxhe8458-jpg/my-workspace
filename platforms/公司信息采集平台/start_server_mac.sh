#!/usr/bin/env bash
# macOS / Linux 启动脚本（代替 Windows 的 start_server.bat）。
#
# 首次在你自己的电脑上运行时，会在当前目录建一个 .venv 虚拟环境并装依赖；
# 之后再运行就直接启动服务，不用重装。
#
# 用法：
#   ./start_server_mac.sh
#   或  bash start_server_mac.sh
#
# 启动后浏览器打开： http://localhost:8000
#   登录账号 admin / 密码 admin123（首次运行未生成 server_config.json 时用这个默认值；
#   之后改了密码就按你改的来；连 MySQL / 填 AI 接口都在网页「设置」页，不用重启服务）
#
# 想换端口：改下面第 44 行 server.app 后面的 --port 8000。
# 改过后端代码（server/*.py）需要重启；只改前端（server/static/*）刷新页面即可。
set -e
cd "$(dirname "$0")"

PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "✘ 没找到 python3。macOS 上请先安装：xcode-select --install 或从 https://www.python.org 装。"
  exit 1
fi

# 没有虚拟环境就现建现装
if [ ! -x ".venv/bin/python" ]; then
  echo "首次运行：创建虚拟环境 .venv 并安装依赖（约一两分钟）…"
  "$PY" -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip -q
  ./.venv/bin/pip install -r requirements_server.txt
fi

echo "启动服务：http://localhost:8000   （停止：Ctrl+C）"
# 0.0.0.0 = 局域网内其它电脑也能访问；只本机访问可改成 127.0.0.1
exec ./.venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8000
