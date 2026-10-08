# 平台局域网部署与维护手册

> 适用场景：把「自动化数据处理平台」部署到公司局域网，供内部员工通过内网访问。
> 目标拓扑：**一台常开的 Windows 机器（本机）**，后端跑在 WSL（Ubuntu）内，代码与数据在
> `D:\自动化数据处理`（WSL 通过 `/mnt/d` 访问同一份文件）。员工通过 `http://<内网IP>:8688` 访问。

---

## 一、部署环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows 10/11（带 WSL2 + Ubuntu） | 后端实际运行在 WSL 内 |
| Python | WSL 内 python3（3.10+） | 依赖：`fastapi uvicorn python-multipart pypdf img2pdf Pillow pikepdf cairosvg requests openpyxl` |
| 端口 | **8688/TCP**（可改） | 只放行这一个入站端口 |
| 磁盘 | D:\ 盘剩余空间充足 | 数据目录约 26 万文件，勿放系统盘 |
| 网络 | 固定内网 IP 或 DHCP 保留地址 | 建议在路由器/公司 DHCP 里给本机 MAC 绑定固定 IP |

**已随本次部署完成的代码改动**（无需再改）：
- `run.py`：默认监听 `0.0.0.0`，支持 `HOST` / `PORT` 环境变量；未配置认证时打印安全警告
- `backend/auth.py`（新增）：HTTP Basic Auth 中间件，凭据读环境变量 `DEPLOY_USER` / `DEPLOY_PASS`
- `backend/main.py`：挂载认证中间件（未配置凭据时自动放行，不影响开发）

---

## 二、部署步骤（按顺序执行）

### 第 1 步：确认 WSL 环境与依赖

在 WSL（Ubuntu）终端执行：

```bash
python3 --version                                  # 需 3.10+
python3 -c "import fastapi, uvicorn" && echo OK     # 依赖已装则 OK；否则：
# 缺依赖时安装：
python3 -m pip install fastapi "uvicorn[standard]" python-multipart \
    pypdf img2pdf Pillow pikepdf cairosvg requests openpyxl
```

### 第 2 步：重启后端服务（让认证与监听改动生效）

**小白模式**：双击 `deploy/restart_deploy.bat` 即可（自动停旧服务 → 用 `deploy/start.sh` 带认证重启 → 打印启动日志）。

**手动模式**（WSL 终端）：

```bash
# 1) 停掉旧进程（先看 PID，别用 pkill -f 的宽模式）
PID=$(ps -eo pid,cmd | awk '$2=="python3" && $3=="run.py" {print $1}')
[ -n "$PID" ] && kill "$PID" && sleep 1

# 2) 启动（凭据已固化在 deploy/start.sh，无需手动 export）
cd /mnt/d/自动化数据处理/数据处理平台
bash deploy/start.sh

# 3) 确认起来且带了认证
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8688/api/queue
#   无凭据应返回 401（认证生效）；返回 200 说明凭据没设上，检查 deploy/start.sh
```

> 登录凭据（用户名/密码）统一写在 **`deploy/start.sh`** 顶部（`DEPLOY_USER` / `DEPLOY_PASS`），
> 想改密码就改那一行，然后重启服务。**部署时已生成一个随机初始密码，见部署交付说明。**

### 第 3 步：打通 Windows → WSL 的端口转发（关键！）

WSL2 的 localhost 转发只覆盖 Windows 本机，**局域网其他机器访问不到**。

**小白模式**：右键 `deploy/deploy_setup.bat` →「以管理员身份运行」→ 点 UAC 的"是"，
脚本自动完成：取 WSL IP → 建端口转发（Windows 0.0.0.0:8688 → WSL:8688）→ 配防火墙白名单。

**手动模式**（管理员 PowerShell，等价操作）：

```powershell
wsl -e hostname -I          # 拿到 WSL IP，如 172.26.x.x
netsh interface portproxy add v4tov4 listenport=8688 listenaddress=0.0.0.0 connectport=8688 connectaddress=<WSL_IP>
netsh advfirewall firewall add rule name="DataPlatform-8688" dir=in action=allow protocol=TCP localport=8688 remoteip=192.168.2.0/24
```

> ⚠ 每次 WSL 重启后 IP 可能变化，转发会失效 → 重新双击 `deploy_setup.bat` 即可。
> 想一劳永逸可给 WSL 固定 IP（`.wslconfig` 里 `[wsl2] ipAddress=172.26.0.50`，重启 WSL 生效）。

**方式 B：WSL2 镜像网络模式**（Windows 11 22H2+）

```powershell
# %UserProfile%\.wslconfig 添加：
#   [wsl2]
#   networkingMode=mirrored
# 然后 wsl --shutdown 重启 WSL。此后 WSL 服务直接共享 Windows 网卡，无需 portproxy。
```

### 第 4 步：防火墙放行 + 内网白名单（安全第一道闸）

**只放行 8688，且限定内网网段**（先 `ipconfig` 确认本机所在网段；2026-10-08 本机为
`192.168.2.106`，网段即 `192.168.2.0/24`）：

> ⚠ 这个 IP 是路由器 DHCP 发的，**会变**：2026-10-08 就从 `192.168.2.102` 漂成了
> `192.168.2.106`，而旧地址随即被发给了别的设备——同事按旧地址访问只会连到那台机器
> 上，表现就是"平台明明启动了却打不开"。当前地址随时可以这样查：跑
> `deploy\deploy_check.bat`，第 [3] 步会打印本机当前内网 IP（脚本已改成动态探测，
> 不再写死）。想让地址不再漂：在路由器管理页给本机网卡 MAC 做 **DHCP 地址保留**
> （或改静态 IP）。

```powershell
# 管理员 PowerShell。remoteip 可写多个网段，逗号分隔（如含 VPN 网段 10.10.0.0/16）
netsh advfirewall firewall add rule name="DataPlatform-8688" dir=in action=allow `
    protocol=TCP localport=8688 remoteip=192.168.2.0/24
```

> 不推荐不加 remoteip 的全放行——那等于任何能路由到本机的来源（含公司其他隔离网段）
> 都能打开。宁可多列几个内网网段，也不开全放行。

### 第 5 步：开机自启（可选但推荐）

**任务计划程序**（图形界面，管理员）：
1. 开始菜单搜「任务计划程序」→ 创建任务
2. 触发器：**登录时**（若机器常开不断电，也可选「系统启动时」）
3. 操作：启动程序 → `wsl.exe`，参数：
   `-d Ubuntu -e bash -lc "cd /mnt/d/自动化数据处理/数据处理平台 && bash deploy/start.sh"`
4. 条件：取消「只有在计算机使用交流电源时才启动」

> 机器需要长时间开机才适合做服务器。若不能常开，可跳过本步，登录后手动双击
> `deploy/restart_deploy.bat` 即可。

---

## 三、访问控制与安全策略（汇总）

| 层 | 措施 | 目的 |
|---|---|---|
| 网络层 | 防火墙仅放行 8688，`remoteip` 限内网网段 | 外网/隔离网段到不了端口 |
| 应用层 | Basic Auth（`DEPLOY_USER`/`DEPLOY_PASS`） | 到了端口也要凭据才进 |
| 平台层 | 原有配置：任务队列串行、写盘任务需显式确认 | 防止误操作 |
| 约定 | **不做**端口映射/DMZ/公网暴露；不把本机接入公司访客 WiFi 同网段 | 禁止对外 |

**密码强度建议**：至少 12 位、含大小写字母+数字+符号；Basic Auth 走明文 base64，
只用于可信内网（防火墙已限网段），不用于公网场景。

---

## 四、启动 / 停止

### WSL 内启动

```bash
cd /mnt/d/自动化数据处理/数据处理平台
bash deploy/start.sh        # 自动停旧服务 → 带认证启动（凭据固化在脚本顶部）
```

### 停止

```bash
PID=$(ps -eo pid,cmd | awk '$2=="python3" && $3=="run.py" {print $1}')
[ -n "$PID" ] && kill "$PID" && echo "已停止 $PID" || echo "未在运行"
```

### 查看状态 / 日志

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8688/api/queue   # 200=活
tail -f /tmp/platform.log                                                 # 启动日志
ls -t /mnt/d/自动化数据处理/数据处理平台/data/jobs/ | head                # 任务日志
```

---

## 五、部署完成后的验证清单

| # | 验证项 | 方法 | 预期 |
|---|---|---|---|
| 1 | 服务存活 | WSL 内 `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8688/api/queue` | 401（认证已开） |
| 2 | 认证生效 | 带凭据：`curl -u admin:'密码' -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8688/api/queue` | 200 |
| 3 | 错误凭据拒绝 | `curl -u admin:wrong -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8688/api/queue` | 401 |
| 4 | 一键自检 | 双击 `deploy/deploy_check.bat` | 依次输出服务状态、登录测试、局域网地址 |
| 5 | 端口转发 | Windows 本机浏览器打开 `http://127.0.0.1:8688` | 弹登录框→输密码→进总览 |
| 6 | 局域网可达 | **换一台员工电脑**（同一内网）浏览器打开 `http://<本机内网IP>:8688` | 弹登录框→输密码→正常 |
| 7 | 功能抽查 | 员工电脑上走一遍：总览、待办、统计、分类体系、设置各页；点一次导出 | 各页正常、导出任务可下载 |
| 8 | 外网隔离（抽查） | 手机开**蜂窝数据**（不走公司 WiFi）访问 `http://<本机内网IP>:8688` | 无法打开（超时/拒绝） |

---

## 六、常见问题排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 本机 127.0.0.1 能开，员工电脑打不开 | ① portproxy 没配/失效（WSL IP 变了）② 防火墙没放行 8688 | 重新双击 `deploy/deploy_setup.bat`；`netsh interface portproxy show all` 核对 |
| 不弹登录框直接 401 白页 | 认证已开但浏览器没弹框（少见） | 换浏览器/隐私窗口再试；确认访问的是 http 而非 https |
| 输对密码仍 401 | 凭据没设上（start.sh 没生效） | 检查 `deploy/start.sh` 顶部 DEPLOY_PASS；重启后 curl -u 验证 |
| 员工电脑连不上但手机 WiFi 能上 | 防火墙 remoteip 白名单网段没包含该员工所在网段 | 查员工机 IP 网段，加到 remoteip 再更新规则 |
| 服务起来了但页面数据为空/接口 500 | 后端启动日志有 traceback；可能 /mnt/d 路径或数据目录被占用 | 看 `/tmp/platform.log`；`ls /mnt/d/自动化数据处理` 确认可读 |
| 端口被占用 | 别的服务占了 8688 | 改 `PORT=8689` 启动，同步改 portproxy 与防火墙的 localport |
| WSL 重启后局域网又断了 | WSL IP 变了，portproxy 指向旧 IP | 给 WSL 固定 IP（见第 3 步提示）或重跑 portproxy |
| 员工访问极慢 | 首次加载大目录/后台任务在跑 | 正常现象；总览有缓存，秒开；让员工稍后刷新 |

---

## 七、维护要点

- **备份**：数据目录（`公司产品数据*` 各阶段）与 `数据处理平台/data/`（记忆/缓存/任务模型）
  是生产数据，备份用复制（服务运行时复制可能有文件占用，最好先停服务再整目录复制）。
- **升级代码**：改完前端 JS/HTML 无需重启（no-cache 已配置，员工刷新即生效）；
  改后端 Python 必须重启（见「四、停止/启动」）。
- **密码轮换**：改 `deploy/start.sh` 里的 DEPLOY_PASS → 重启服务（双击 `restart_deploy.bat`）→ 通知员工用新密码。
- **日志清理**：`data/jobs/` 自动保留最近 200 个任务，无需人工清理。
