#!/bin/bash
# 自动化数据处理平台 —— WSL 启动脚本
# 用法（在 WSL 里或通过 Windows 侧 restart_deploy.bat）：bash deploy/start.sh
# 本文件放在平台目录的 deploy/ 子目录下，固定向上退一级到平台目录。
cd "$(dirname "$0")/.." || exit 1

# ── 登录凭据（Basic Auth）────────────────────────────────────────────
# 员工访问平台时弹窗输入这个用户名和密码；想改密码就改这里，然后重启服务。
export DEPLOY_USER=admin
export DEPLOY_PASS='123456'
# ─────────────────────────────────────────────────────────────────────

# 已在运行则先停掉旧进程（可能有多个，逐个杀）
#
# ⚠⚠ **必须按工作目录筛，不能只匹配 `python3 run.py`。**
# 这台机器上不止一个项目用 `run.py` 当入口（实测 /mnt/d/公司数据自动化检验平台 也是），
# 只按命令行匹配就会把**别人的服务**一起杀掉；对方的启动脚本同样如此，两边互相残杀。
# 2026-08-05 实测：09:53:07 我们的服务被杀、任务跑到一半中断，09:53:15 对方的服务起来 ——
# 相差 8 秒。所以这里用 /proc/<pid>/cwd 认准自己，别人的一概不碰。
SELF=$(cd "$(dirname "$0")/.." && pwd -P)
PIDS=$(ps -eo pid,cmd | awk '$2=="python3" && $3=="run.py" {print $1}')
for PID in $PIDS; do
  CWD=$(readlink "/proc/$PID/cwd" 2>/dev/null)
  if [ "$CWD" = "$SELF" ]; then
    kill "$PID" 2>/dev/null && echo "已停止本平台的旧进程 $PID"
    KILLED=1
  else
    echo "跳过别的项目的 run.py（PID $PID，目录 ${CWD:-未知}）—— 不是本平台的，不动它"
  fi
done
[ -n "$KILLED" ] && sleep 2          # 等端口释放

# PYTHONUNBUFFERED=1：让 python 输出实时写日志（默认块缓冲会让日志暂时为空，误判成没启动）
# setsid：把服务放进**独立会话**。只用 nohup 挡得住 SIGHUP，挡不住 SIGTERM/SIGINT ——
# 调用方（终端、bat、自动化工具）一旦被中断或超时，信号会发给整个进程组，
# 把刚起来的服务一起带走。实测踩过：脚本超时被杀，日志里紧接着就是 "Shutting down"。
PYTHONUNBUFFERED=1 setsid nohup python3 run.py > /tmp/platform.log 2>&1 &
# 等待就绪：服务 import 一堆模块要几秒，别只等 2 秒就误判失败。最多等 25 秒。
#
# ⚠ 两个 curl 参数缺一不可：
#   --noproxy '*'   本机装了 http_proxy（172.28.96.1:7897）。不绕过的话，探测 127.0.0.1
#                   也会被送去代理，每次要等到代理自己超时 —— 实测这个循环因此挂了两分多钟，
#                   看起来像"服务起不来"，其实服务早就好了。
#   --max-time 3    单次探测封顶，别让任何一次卡死整个循环。
CODE=000
for i in $(seq 1 25); do
  CODE=$(curl -s --noproxy '*' --max-time 3 -o /dev/null \
              -w "%{http_code}" http://127.0.0.1:8688/api/queue 2>/dev/null)
  [ "$CODE" = "401" ] || [ "$CODE" = "200" ] && break
  sleep 1
done
if [ "$CODE" = "401" ] || [ "$CODE" = "200" ]; then
  echo "已启动：http://127.0.0.1:8688   PID: $!"
  [ "$CODE" = "401" ] && echo "✓ 密码锁已生效（401 = 无凭据访问被拒，正常）"
  echo "登录：$DEPLOY_USER / 密码见本文件 DEPLOY_PASS 一行"
else
  echo "⚠ 25 秒内未就绪（HTTP $CODE），请把 /tmp/platform.log 的最后几行截图反馈："
  tail -n 6 /tmp/platform.log
fi
