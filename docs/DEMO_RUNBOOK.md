# 本机运行与演示手册

本项目以本机网页应用交付：卖家端、负责人工作台共用一个后端和 PostgreSQL，Windows/macOS 使用同一套源码。本机不要求 Docker 或 GPU。当前发布目标是明确标记的本地模拟刊登，未接入真实 Amazon 账号或 SP-API。

实际已通过的阶段与报告见 [PROGRESS.md](PROGRESS.md)。Windows 和 GitHub CI 尚未执行验证；提供兼容代码与 CI 文件，不等于这些平台已经实测通过。

## 首次准备与启动

准备 Node.js 22、uv、Python 3.11 和原生 PostgreSQL 17 Server。在项目根目录执行：

```text
uv python install 3.11
node scripts/init-env.mjs
npm --prefix apps/web ci
uv sync --project apps/server --locked --group dev --python 3.11
```

通过 PostgreSQL 管理员创建业务库与独立测试库，账号示例和当前 macOS 数据库管理方式见 [README.md](../README.md)。并行进行集成测试与浏览器测试时，另建 `hackjunction_e2e_test`，由项目账号拥有；不要让两组测试共用正在初始化的数据。应用账号无建库权限时由数据库管理员创建，不给应用账号增加管理员权限。

根目录私有 `.env` 配置数据库、MiMo key、`MIMO_MODE=live`、`PUBLISH_MODE=simulated`；`apps/web/.env.local` 仅配置后端 `API_ORIGIN`。初始化不会覆盖已有私有配置。

```text
node scripts/check-env.mjs
uv run --project apps/server company-db-init
uv run --project apps/server company-init-admin
```

负责人账号保存在本机 `var/local-admin.txt`；初始化只打印路径，不打印密码。三个终端分别执行：

```text
npm --prefix apps/web run dev
uv run --project apps/server company-api
uv run --project apps/server company-worker
```

每条命令独占一个终端。访问 `http://127.0.0.1:3000`、`/internal/login`、`/system`；接口文档在 `http://127.0.0.1:8000/docs`。API 或 worker 配置变更后重启对应进程。Windows 使用 `company-api` 入口处理异步数据库事件循环；PowerShell 拦截 `npm.ps1` 时使用 `npm.cmd`。不要复制其他电脑的 `.venv`、`node_modules` 或 PostgreSQL 数据目录。

## 三分钟业务演示

1. 建立标为虚构的桌面理线夹订单，先缺少尺寸。查看获客追问，再补齐并确认材质、单件尺寸及单位、包装数量、适用范围。要演示“6 个装改 4 个装，6 mm 不变”，需另外提供明确的 6 mm 线径事实。
2. 负责人批准接单，展示研究来源、候选版本、审校问题、实际图片与用量。未放行版本只能内部查看；实际图文审校通过后由负责人放行，再从卖家入口复制文案、预览图片和下载 ZIP。
3. 卖家提出修改，提交完整、明确确认的事实快照。新版重新生成并审校，旧版保留；核对数量、尺寸文字层和商品外观。新增防水、认证、通用适配承诺缺证据时应被追问或阻止。
4. 卖家确认指定版本、`12.99 USD` 与 SKU，展示 `SIMULATED` 回执和模拟商品页。模拟刊登不会产生真实 Amazon 发布、支付或成交。

审校不保证每次现场都退回；可以展示已有、可核对的历史退回与版本记录，不人为伪造失败。另演示拒绝接单、负责人否决、请求人工客服及受控重试。人工接管暂停自动获客回复，不自动停产。负责人界面不再提供人工计时，保留模型用量与失败恢复。

## 自动验收的边界

```text
uv run --project apps/server pytest apps/server/tests -q
uv run --project apps/server pytest apps/server/tests -q --run-db-tests
uv run --project apps/server ruff check apps/server/src apps/server/tests apps/server/alembic
npm --prefix apps/web run lint
npm --prefix apps/web run build
npm --prefix apps/web run typecheck
```

默认后端测试不调用远端模型，真 PostgreSQL 用例需显式 `--run-db-tests`，且拒绝开发库。真实 MiMo 能力验证另运行 `company-smoke-mimo`，会使用额度；不能用 fake 结果替代真实模型验收。

隔离浏览器测试先安装项目 Playwright 浏览器，再执行：

```text
node apps/web/node_modules/@playwright/test/cli.js install chromium
node scripts/test-stack.mjs --check
node scripts/test-stack.mjs
```

`TEST_DATABASE_URL` 必须指向专用测试库，并与开发库名称不同、以 `_test` 结尾；需要并行验收时可通过当前进程环境指向 `hackjunction_e2e_test`，无需改开发连接。脚本使用 API 8100 / web 3100、临时负责人凭据和会话密钥、fake worker，以及独立临时前端目录和资产目录，不复用正在运行的开发服务，不读取本机管理员密码文件。结束时仅停止自身进程树、删除自身临时目录，不清空数据库。

本机 macOS 13 的项目 Chromium 不可用时，脚本会检测系统 Chrome 并使用全新临时 profile；也可通过 `PLAYWRIGHT_CHANNEL=chrome` 显式选择。其他平台按项目 Playwright 安装方式准备浏览器。离线 provider 标为 `offline-fixture`，已知远端 token 为 0；真实响应缺失用量仍是未知，不能填 0。

`.github/workflows/check.yml` 包含 Windows/macOS 离线检查与 Ubuntu PostgreSQL 17 集成检查，尚未实际运行。公网部署是后续独立事项，见 [deploy/README.md](../deploy/README.md)。

真实模型闭环验收脚本为 `scripts/smoke-closed-loop.py`，分 `--stage m5`、`--stage feedback`、`--stage publish` 执行，会创建虚构订单并消耗MiMo额度；已有本机验收记录时优先查看报告和负责人历史订单，不需为演示反复付费重跑。`var/local-acceptance.json`保存用于本机验收恢复的私有访问链接，不得对外分享。
