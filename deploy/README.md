# 部署范围

本次交付是可在本机运行的网页应用，Windows/macOS 共用源码与依赖锁。额外提供带入口密码的临时公网HTTPS演示；当前不交付 `.exe` / `.app` 安装包。Windows 和 CI 的实测状态以 [开发进度](../docs/PROGRESS.md) 为准。

本机需要四个运行单元：Next.js web、FastAPI API、独立 worker、原生 PostgreSQL。当前默认入口为 web 3000 / API 8000，数据库与后台服务只面向本机；启动、配置和演示见 [运行手册](../docs/DEMO_RUNBOOK.md)。

## 临时公网演示

先完成根目录README中的本机依赖、MiMo密钥与负责人账号初始化，并保持PostgreSQL运行。使用数据库管理员只创建一次独立演示库：

```sql
CREATE DATABASE hackjunction_public_demo OWNER hackjunction;
```

安装 [Cloudflare官方cloudflared](https://developers.cloudflare.com/tunnel/downloads/) 并加入PATH；也可放在被忽略的 `var/bin/cloudflared`（Windows为 `cloudflared.exe`），或设置环境变量 `CLOUDFLARED_BIN` 指向程序。然后在项目根目录执行：

```text
node scripts/public-demo.mjs
```

脚本自动迁移独立库、构建独立前端并启动API、worker与隧道；不会修改 `.env`。公网入口3300、web3200、API8200都只监听本机，由出站隧道提供HTTPS。端口被占用时直接停止启动，不结束已有服务。开发版3000/8000及其数据不受影响。

启动成功后，地址写入 `var/public-demo/state.json`，随机入口账号密码写入 `var/public-access.txt`；负责人仍使用 `var/local-admin.txt` 的独立账号。只把入口密码给参加演示的人，不分享负责人密码、密钥或整个运行目录。公网入口密码不代替卖家账号或负责人登录。卖家先在公网版注册，账号及订单与本机开发库独立。

保持终端与电脑运行，Ctrl+C只停止这套公网演示。重启会得到新地址、入口密码和会话密钥，旧会话需要重新登录，卖家可用原公网卖家账号重新登录，从“我的订单”打开订单。演示库和资产会保留；该入口不是长期托管服务。[Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)仅用于临时测试。

当前网络实测HTTP/2边缘连接曾中断，自动QUIC连接也有超时。若默认路线无法连接，可在Windows/macOS上统一使用 `node scripts/public-demo.mjs --us` 尝试美国边缘路线。脚本会先验证公网密码入口，再报告启动完成；仅取得域名不算公网可用。线路断开时可能显示1033，不能将本机服务仍在运行当作公网始终在线。

本模式使用真实MiMo（会消耗额度），Amazon发布仍为明确标记的模拟流程。入口保护全部页面/API/静态资源；API和数据库端口没有直接开放到公网。用以下命令验证HTTPS、生产Cookie、权限隔离、CSRF和图片上传下载：

```text
node --test scripts/lib/demo-gateway.test.mjs
node scripts/test-public-demo.mjs
```

第二条会在演示库新增一个缺尺寸的虚构验收订单，最多进入获客，不批准生产；报告为脱敏的 `var/reports/public-smoke.json`。完整人工操作见 [用户验收指南](../docs/USER_TEST_GUIDE.md)。

## 长期公网部署

托管平台、域名、费用及数据保留要求确定后，再选择单机进程管理或容器部署；当前不预设某个付费平台。临时隧道验收不代表长期托管、自动重启与备份恢复已经验收。

部署时需一起处理以下实际依赖：

- web / API 的同源 HTTPS 入口与内部 `API_ORIGIN`；`PUBLIC_APP_URL` 对应真实域名。生产会话使用安全 Cookie 与 CSRF 校验，不能直接沿用开发凭据。
- API 和 worker 使用同一个 PostgreSQL 与同一份持久化资产。当前文件实现为本地存储；若使用对象存储，需要先实现并验证对应适配器。
- 应用迁移及 LangGraph 检查点初始化单独执行，数据库与资产备份、恢复及进程重启一起验收。不要直接分发开发机的数据库数据目录。
- 模型 key、负责人密码摘要、会话密钥由运行环境提供，不能进入前端构建或源码。对外只开放卖家授权入口和明确标为虚构的公开模拟商品页。

生产环境可继续使用 `MIMO_MODE=live` 配合 `PUBLISH_MODE=simulated`，两种模式互相独立。真实 Amazon 上架需要另行取得卖家授权并实现适配器；当前 `production` / `sandbox` 发布模式会明确阻塞，不会降级伪造成功。模拟发布不代表真实成交或付款。
