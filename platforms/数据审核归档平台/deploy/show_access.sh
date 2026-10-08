#!/bin/bash
# 访问记录（服务端日志部分）。由 deploy/view_access.bat 调用。
#
# 为什么单独一个 .sh：日志路径含中文（~/.local/share/审核归档平台/服务运行日志.log），
# 而 .bat 必须保持纯 ASCII —— cmd 按 GBK 代码页读脚本，文件里的 UTF-8 中文会让
# 它的行解析错位，`rem` 前缀被吃掉、后半句被当命令执行（实测过）。
# 所以凡是带中文的命令一律放进 .sh，.bat 只负责调用。
LOG="$HOME/.local/share/审核归档平台/服务运行日志.log"

if [ ! -f "$LOG" ]; then
  echo "找不到服务日志：$LOG"
  echo "服务可能从未启动过，或日志被清理了。查状态：systemctl --user status audit-archive"
  exit 1
fi

echo "--- 最近 15 条请求 ---"
grep -E 'HTTP/1' "$LOG" | tail -n 15

echo
echo "--- 最近 15 次登录尝试（401 = 口令错）---"
grep -E 'POST /api/login' "$LOG" | tail -n 15 || echo "（暂无登录记录）"

echo
echo "--- 登录失败次数统计 ---"
FAILED=$(grep -c 'POST /api/login HTTP/1.1" 401' "$LOG" 2>/dev/null || echo 0)
echo "累计失败 $FAILED 次"
[ "$FAILED" -gt 20 ] && echo "⚠ 失败次数偏多，可能有人在猜口令——建议改口令（内网部署说明.md 第五节）"
exit 0
