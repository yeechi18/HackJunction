# 出海工坊 · Listing Studio

面向中文跨境小卖家的 Amazon 美国站商品本地化上架服务。Next.js前端、FastAPI后端、MiMo Agent和PostgreSQL共同构成一套网页应用；Windows/macOS共用源码。本机运行不需要Docker或GPU。

目前M0–M7本机模拟闭环已完成验收：获客、研究、文案/实际图片审校、负责人放行、卖家反馈/确认、模拟发布及人工接管均已接通。首页的理线夹内容明确为虚构示例，不是已处理的真实订单或已发布商品。实际验收结果见 [开发进度](docs/PROGRESS.md)。

当前用户确认优先完成M0–M7的模拟上架闭环，暂无Amazon卖家账号与SP-API授权。真实Amazon接入暂缓，最终模拟订单页面会明确标记模拟结果。

## 环境

- Node.js 22.x（含npm）、uv、Python 3.11（可 `uv python install 3.11`）。
- 本机PostgreSQL 17服务，业务库和测试库分开；浏览器用于访问页面。
- MiMo API Key只放根目录 `.env`；Git可用于协作，运行压缩包不依赖Git。

前端代码位于 `apps/web`，后端位于 `apps/server`。不要跨电脑复制 `.venv`、`node_modules` 或数据库数据目录，应重新安装锁定依赖。

## 配置与安装

在项目根目录依次执行（Windows PowerShell和macOS终端使用相同命令）：

```text
node scripts/init-env.mjs
npm --prefix apps/web ci
uv sync --project apps/server --locked --group dev
```

初始化脚本不覆盖已存在的 `.env`。在根 `.env` 配置 `MIMO_API_KEY`、`DATABASE_URL`、`TEST_DATABASE_URL`；前端 `.env.local`只需要服务端使用的`API_ORIGIN`，不复制密钥。`.env.example`中的数据库密码仅供本机开发示例，实际连接应与创建的账号一致。

使用PostgreSQL管理员在本机创建项目账号与两个数据库（只在第一次创建；已有账号或数据库不要重复执行）：

```sql
CREATE ROLE hackjunction LOGIN PASSWORD 'local-dev-only';
CREATE DATABASE hackjunction OWNER hackjunction;
CREATE DATABASE hackjunction_test OWNER hackjunction;
```

Windows可在安装器附带的SQL Shell或pgAdmin中执行；安装的是PostgreSQL Server，不能只装pgAdmin。macOS可使用Postgres.app提供的psql。`psql`不在PATH不妨碍Python连接数据库，可以使用其完整路径。

```text
node scripts/check-env.mjs
uv run --project apps/server company-db-init
uv run --project apps/server company-seed-demo
uv run --project apps/server company-init-admin
```

`company-db-init`执行Alembic迁移和LangGraph检查点初始化，可重复执行；`company-seed-demo`当前只校验演示夹具，不制造真实订单。负责人初始账号写入本机被忽略的`var/local-admin.txt`，初始化不覆盖已有账号；首次配置后重启API加载密码摘要。请不要把开发库或真实数据用作测试库。

## 启动

三个终端分别运行：

```text
npm --prefix apps/web run dev
```

```text
uv run --project apps/server company-api
```

```text
uv run --project apps/server company-worker
```

`company-api`处理Windows异步连接所需的运行时设置。worker运行持久化获客、研究、生成、审校、交付与模拟发布；等待卖家或负责人时保存状态，不重复请求模型。

访问 [首页](http://127.0.0.1:3000)、[基础状态页](http://127.0.0.1:3000/system)、[本地接口文档](http://127.0.0.1:8000/docs)。`live`只检查API进程，`ready`还检查数据库、业务schema和LangGraph检查点表；数据库未准备好时返回503，不假装就绪。

PowerShell若拦截 `npm.ps1`，可将命令中的 `npm` 改为 `npm.cmd`。安装运行时后重新打开终端。前端或API端口被占用时先确认是否已有本项目进程，不盲目结束其他程序。

## 验证

```text
uv run --project apps/server company-smoke-mimo
uv run --project apps/server pytest apps/server/tests
uv run --project apps/server pytest apps/server/tests --run-db-tests
uv run --project apps/server ruff check apps/server/src apps/server/tests
uv run --project apps/server company-export-openapi
npm --prefix apps/web run generate:api
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
node scripts/test-stack.mjs
```

MiMo烟测会使用真实模型额度，输出4项能力结果和脱敏用量报告至`var/reports/m0-mimo.json`；其余默认测试不调用模型。`--run-db-tests`显式连接独立测试库；没有测试库时不要使用该参数。E2E需要独立测试数据库，脚本会在8100/3100启动隔离前后端和离线模型worker，生成临时负责人凭据，结束后清理自身进程；不复用开发服务、不调用真实MiMo、不读取开发负责人密码。`npm --prefix apps/web run test:e2e`也使用同一隔离入口。macOS13使用已安装系统Chrome的全新临时profile，其他平台首次需执行Playwright浏览器安装。

## 当前主机的数据库

当前macOS已原生安装Postgres.app，项目数据库集群位于被忽略的 `var/postgres`。它独立于Postgres.app的GUI默认集群；不要让二者同时争用5432端口。此段是当前开发机的管理命令，Windows使用自己的数据库服务管理方式。

```text
/Applications/Postgres.app/Contents/Versions/17/bin/pg_ctl -D var/postgres -l var/postgres.log -o "-h 127.0.0.1 -k /private/tmp -p 5432" start
/Applications/Postgres.app/Contents/Versions/17/bin/pg_ctl -D var/postgres status
/Applications/Postgres.app/Contents/Versions/17/bin/pg_ctl -D var/postgres stop
```

数据库仅监听本机。数据库数据和 `.env` 都不能加入源码分发；当前数据库初始化与开发凭据不代表生产部署方案。

设计与实施分别见 [项目概要](docs/PROJECT_OVERVIEW.md)、[架构](docs/IMPLEMENTATION_ARCHITECTURE.md)、[开发计划](docs/DEVELOPMENT_PLAN.md)、[新增参考采纳决定](docs/REFERENCE_DECISIONS.md)。

完整演示和测试边界见 [运行手册](docs/DEMO_RUNBOOK.md)，公网部署准备见 [部署说明](deploy/README.md)。当前真实MiMo模拟闭环示例可从负责人订单列表查看历史记录；只有明确标为虚构的模拟商品页才会公开。

## 临时公网演示与验收

另建 `hackjunction_public_demo` 数据库并安装cloudflared后，执行 `node scripts/public-demo.mjs`。脚本使用独立数据库、资产目录和生产前端，与本机开发版同时运行；HTTPS地址在 `var/public-demo/state.json`，入口密码在 `var/public-access.txt`。保持电脑及脚本运行，关闭后链接失效；负责人另用原账号登录。详细准备、停止及验收见 [部署说明](deploy/README.md)。

人工完整操作见 [用户验收指南](docs/USER_TEST_GUIDE.md)，文件与代码量见 [实现清单](docs/FILE_INVENTORY.md)。公网只改变访问方式，真实MiMo与模拟Amazon的功能边界保持一致。
