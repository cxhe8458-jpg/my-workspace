#!/bin/bash
# 数据审核归档平台 —— WSL 启动脚本
# 用法：bash deploy/start.sh   （或从 Windows 侧双击 deploy\restart_deploy.bat）
cd "$(dirname "$0")/.." || exit 1

# ── 进程管理走 systemd，不自己 nohup ─────────────────────────────────
# 这台机器上不止一个项目拿 `run.py` 当入口（/mnt/d/自动化数据处理/数据处理平台、
# /mnt/d/公司数据自动化检验平台 都是）。按命令行匹配去 kill 会误杀别人的服务——
# 邻居项目的 deploy/start.sh 里记着 2026-08-05 两边互相残杀的实测。
# systemd 只认自己启动的那个 PID，压根不会碰到别人，比按 cwd 筛更稳。
SERVICE=audit-archive

systemctl --user daemon-reload 2>/dev/null
if systemctl --user restart "$SERVICE" 2>/dev/null; then
  echo "已通过 systemd 重启服务（$SERVICE）"
else
  echo "⚠ systemd 用户服务不可用，请检查：systemctl --user status $SERVICE"
  exit 1
fi

# ── 等就绪 ───────────────────────────────────────────────────────────
# 两个 curl 参数缺一不可（同样是这台机器上的实测教训）：
#   --noproxy '*'   本机装了 http_proxy（172.28.96.1:7897）。不绕过的话探测 127.0.0.1
#                   也会被送去代理，要等代理自己超时，看起来像"服务起不来"。
#   --max-time 3    单次探测封顶，别让任何一次卡死整个循环。
# 期望 401：/api/version 需要登录，无凭据被拒 = 服务活着且鉴权生效。
PORT=$(python3 -c "from app.infra import paths; print(paths.load_config().get('port', 8010))" 2>/dev/null || echo 8010)
CODE=000
for _ in $(seq 1 25); do
  CODE=$(curl -s --noproxy '*' --max-time 3 -o /dev/null \
              -w "%{http_code}" "http://127.0.0.1:$PORT/api/version" 2>/dev/null)
  [ "$CODE" = "401" ] || [ "$CODE" = "200" ] && break
  sleep 1
done

if [ "$CODE" = "401" ]; then
  echo "✓ 已启动，密码锁生效（401 = 无凭据访问被拒，正常）"
  # 内网地址动态取：DHCP 会把 Windows 的 IP 换掉（2026-10-08 从 192.168.2.102 变成
  # 192.168.2.106），写死的话这行提示就把人指到别人机器上。取不到再退回已知地址。
  LANIP=$(powershell.exe -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp)[0].IPAddress" 2>/dev/null | tr -d '\r\n')
  [ -z "$LANIP" ] && LANIP=192.168.2.106
  echo "  本机： http://127.0.0.1:$PORT     内网： http://$LANIP:$PORT"
  echo "  账号 admin，口令见 内网部署说明.md 第五节（改口令也在那）"
elif [ "$CODE" = "200" ]; then
  echo "⚠ 服务在跑，但 /api/version 无凭据也返回 200 —— 鉴权没生效，别对外开放！"
  echo "  检查 app/main.py 的 require_login 中间件与 app/infra/auth.py"
else
  echo "⚠ 25 秒内未就绪（HTTP $CODE），最后几行日志："
  tail -n 8 "$HOME/.local/share/审核归档平台/服务运行日志.log" 2>/dev/null
fi
