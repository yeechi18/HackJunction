# 上架有方 Shelfwise · Listing Studio

面向中文跨境小卖家的 Amazon 美国站商品本地化上架服务。Next.js 前端、FastAPI 后端、MiMo Agent 和 PostgreSQL 共同构成一套网页应用；Windows/macOS 共用源码。本机运行不需要 Docker 或 GPU。

目前已接通获客、研究、文案与图片制作、审校、负责人放行、卖家反馈/确认、模拟发布及人工接管闭环。PR #4 的运营后台、出海进阶包与图片生产整合已合入 `main`；图片生成和竞品搜索按配置启用。阶段验收记录见 [开发进度](docs/PROGRESS.md)，最新合并与修复记录见 [变更日志](docs/CHANGELOG.md)。

当前发布模式为 Amazon 模拟上架。

## 当前功能

| 功能                  | 当前实现                                                                                                                                                  |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 卖家账号与订单归属    | 邮箱验证码注册、密码登录/退出、账号会话与“我的订单”；订单只向所属卖家和负责人开放。                                                                     |
| 聊天与资料卡建单      | 自由描述商品，或导入不超过 2 MiB 的 CSV/XLSX 数据包，由顾问整理草稿；资料卡可手动修改，支持参考图选择、粘贴、上传重试及符合条件的删除。                   |
| 两种服务入口          | 首页提供“一站式上架包”和“出海进阶包”；进阶包在同一建单流程中增加海关备案自助指南，登录后保留所选入口。                                                |
| 商品初审与接单        | 获客 Agent 分析本单参考图，尝试读取公开国内链接，整理资料一致性、矛盾、追问及文案方向；负责人单独批准或拒绝接单。                                         |
| 按版本保存英文初稿    | 两端可查看标题、五点、详情和 FAQ，成功结果复用，每版最多五次尝试；初稿与正式交付分开，更新资料后展示对应新版本。                                          |
| 品牌与商品事实        | 内置百岁山/Ganten 的有限品牌与类别资料，保留官方来源；卖家可确认本件包装数量、净含量与单位、用途。补充资料形成新版本，旧快照和交付保留。                  |
| 研究与竞品线索        | 分析商品事实、买家问题、市场资料与定价依据；负责人可在接单前导入有来源的授权材料。可选 MiMo、Ark 或 Brave 搜索，来源线索与已确认商品事实分开保存。        |
| 商品图片制作          | 支持外部工具人工制作、回传与核对；也可启用 MiMo 编写构图提示词、Seedream 生成图片。任务记录排队、制作、回传、核对、失败及历史，绑定商品资料版本和参考图。 |
| 审校与负责人交付      | 正式文案生成、文字/实际图片审校、候选版本对比、放行/否决和 ZIP 交付；支持负责人查看本版问题后填写理由，明确决定人工放行。                                 |
| 卖家反馈与模拟发布    | 查看交付、提出返修、按所见版本确认成品及价格，再进入 Amazon 模拟发布；保存版本对应的交付与模拟刊登回执。                                                  |
| 运营后台与 Agent 监控 | 运营总览、订单搜索、Agent 状态、运行趋势与日志、审批中心及人工客服；可查看角色、模型、耗时、token 用量、错误和结构化产物。                                |
| 故障处理与用量授权    | 定位失败订单、记录忽略原因、发送卖家建议、追加本单模型预算并单独授权恢复；人工接管/恢复自动回复，保留原失败和审批记录。                                   |

接单审批与成品放行是两个独立步骤。默认流程须完成图文审校和适用的商品资料确认；人工放行会展示当前问题并保存负责人理由，不把原 AI 结论改成“通过”。候选仍须绑定当前商品、研究资料及完整图片，资料或审校记录变化后必须重新核对。概念图和演示素材不能作为正式商品图交付。

## 环境

- Node.js 22.x（含npm）、uv、Python 3.11（可 `uv python install 3.11`）。
- 本机PostgreSQL 17服务，业务库和测试库分开；浏览器用于访问页面。
- 使用新版Chrome/Edge或Safari 16.4及以上；本机原Safari 16.3低于Next.js支持范围，可能只显示页面而无法交互。建单页会提示升级，建议先用已安装的Chrome验收。
- MiMo 及可选 Ark、Seedream、Brave 的密钥只放根目录 `.env`，不传到浏览器；Git 可用于协作，运行压缩包不依赖 Git。

前端代码位于 `apps/web`，后端位于 `apps/server`。不要跨电脑复制 `.venv`、`node_modules` 或数据库数据目录，应重新安装锁定依赖。

## 配置与安装

在项目根目录依次执行（Windows PowerShell和macOS终端使用相同命令）：

```text
node scripts/init-env.mjs
npm --prefix apps/web ci
uv sync --project apps/server --locked --group dev
```

初始化脚本不覆盖已存在的 `.env`。在根 `.env` 配置 `MIMO_API_KEY`、`DATABASE_URL`、`TEST_DATABASE_URL`；前端 `.env.local`只需要服务端使用的`API_ORIGIN`，不复制密钥。`.env.example`中的数据库密码仅供本机开发示例，实际连接应与创建的账号一致。

更新已有项目时，对照 [.env.example](.env.example) 手动补入需要的新配置；无需启用所有可选服务。开发环境保持 `APP_ENV=development`、`MIMO_MODE=live`、`PUBLISH_MODE=simulated`；离线模拟模型只允许用于 `APP_ENV=test`。

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

`company-db-init`执行Alembic迁移和LangGraph检查点初始化，可重复执行；`company-seed-demo`当前只校验演示夹具，不制造真实订单。负责人初始账号和密码写入本机被忽略的`var/local-admin.txt`，初始化不覆盖已有账号；首次配置后重启API加载密码摘要。请不要把开发库或真实数据用作测试库。

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

`company-api`处理Windows异步连接所需的运行时设置。worker运行持久化获客、研究、生成、审校、交付与模拟发布；启用 Seedream 后也处理图片制作队列。等待卖家或负责人时保存状态，不重复请求模型。

访问 [首页](http://127.0.0.1:3000)、[基础状态页](http://127.0.0.1:3000/system)、[本地接口文档](http://127.0.0.1:8000/docs)。`live`只检查API进程，`ready`还检查数据库、业务schema和LangGraph检查点表；数据库未准备好时返回503，不假装就绪。

PowerShell若拦截 `npm.ps1`，可将命令中的 `npm` 改为 `npm.cmd`。安装运行时后重新打开终端。前端或API端口被占用时先确认是否已有本项目进程，不盲目结束其他程序。

## 卖家账号与订单

先在 [卖家注册](http://127.0.0.1:3000/seller/register) 输入邮箱并收取验证码，创建账号（密码8–18位，字母/数字/特殊符号至少两类），之后 [登录](http://127.0.0.1:3000/seller/login) 提交商品。订单自动绑定当前账号，在 [我的订单](http://127.0.0.1:3000/seller/orders) 查看；换浏览器或重启后重新登录即可找回，不依赖私有链接。其他卖家即使拿到完整链接也无法访问。

更新代码若包含数据库迁移，先停止 API 和 worker，执行 `uv run --project apps/server company-db-init`，再启动服务；每次重启电脑无需重复迁移。当前数据库迁移版本为 `0012_intake_cleanup`，已有账号、订单及图片保留。PR #4 未新增迁移，更新应用后需重启 API/worker，生产前端需重新构建；其后的 CI 测试配置修复无需重启。此前的一次性旧数据清理见 changelog，常规迁移不会清空数据。

卖家密码使用 Argon2 摘要，登录保存在 HttpOnly 会话 cookie 中；默认7天过期，可主动退出。注册已要求邮箱验证码，当前尚无忘记密码流程，请记住注册邮箱和密码。本机库与公网演示库独立，两边分别注册，账号和订单不互通。

QQ 发件邮箱与 SMTP 授权码配置见 [邮箱配置说明](docs/EMAIL_SETUP.md)。邮箱功能由 `0010_registration_email` 引入；其他机器更新后先 `uv sync --project apps/server --locked --group dev`，再执行迁移并重启。

建单页填写商品名、明细、可选国内链接、参考图、补充说明五项；商品名与明细必填。可先聊天或导入商品数据包，再核对资料卡提交；导入不自动建单或将资料标记为已核实。商家身份从登录账号关联，建单前聊天需要登录和 CSRF 校验，模型不可用时可继续手动填写。

获客顾问分析参考图、尝试读取公开链接并给出资料一致性意见，不强制所有类别补齐材质、尺寸、数量或逐项核实。初审后负责人决定接单，正式研究/生成/审校再自动推进。负责人不能通过卖家参考图接口上传或删除素材，可通过独立图片制作任务回传制作结果。详见 [Agent 输入策略](docs/AGENT_INPUT_STRATEGY.md)。

[出海进阶包](http://127.0.0.1:3000/orders/new?package=overseas) 在建单页提供可展开的海关备案自助指南和官方办理入口；当前属于资料指引，未接入在线备案代办。普通建单入口仍为 `/orders/new`。

订单内可查看按资料版本生成的英文初稿，补充事实并查看正式交付。当前内置品牌规则只覆盖百岁山/Ganten 的有限信息，不会按品牌猜本件容量、材质、矿物指标、认证或授权；该类别的默认正式交付流程会检查包装数量、正数净含量与 mL/L 单位、用途。卖家补资料后需对新版本重新初审和接单。人工接管期间自动回复暂停，但初审仍可记录供负责人查看。

负责人登录入口为 `/internal/login`，账号固定 `owner`。打开 `var/local-admin.txt`，密码框只填写 `password=` 右侧的完整值；不填写前缀、不把文件两行一起粘贴，也不使用卖家密码或 SMTP 授权码。

## 负责人运营后台

登录后可从侧栏进入以下页面，数据来自后端的真实订单与运行记录：

| 页面       | 本地路径                           | 主要操作                                                         |
| ---------- | ---------------------------------- | ---------------------------------------------------------------- |
| 运营总览   | `/internal/operations/overview`  | 查看待办订单与最近 7/14 天 Agent 运行趋势。                      |
| 订单工作台 | `/internal/orders`               | 按商品、卖家、订单编号查找订单，进入单单处理。                   |
| Agent 监控 | `/internal/operations/agents`    | 查看获客、研究、生成、审校、上架角色及最近结果。                 |
| 运行日志   | `/internal/operations/runs`      | 按订单、角色、状态筛选模型、用量、耗时和错误，定位订单处理问题。 |
| 审批中心   | `/internal/operations/approvals` | 集中处理接单和交付审批；接单决定可直接在列表展开。               |
| 人工客服   | `/internal/operations/support`   | 查看工单、处理人工协助，配合订单内人工接管和恢复。               |

订单详情集中展示初审、英文初稿、研究与制作记录、图片任务、成品审校和交付审批。审批导航会展开并定位对应面板；卖家确认成品始终由卖家账号执行。

失败任务可查看原因、记录忽略说明，或发送建议到卖家沟通记录。“忽略”保留失败状态，也不恢复任务。模型额度用尽时，负责人可按当前可追加额度授权本单增加 20,000 token 上限；额度追加与“授权恢复一次”分别操作，不会自动重试或自动批准交付。

## 可选研究与图片服务

默认由 MiMo 完成获客、研究、文案与审校，竞品搜索和 Seedream 自动制图均关闭。以下配置位于根 `.env`，完整字段与模型默认值见 [.env.example](.env.example)。修改后重启 API 和 worker。

| 能力           | 配置                                                                               | 生效范围                                                                                                                                                                                             |
| -------------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ark 研究分析   | `RESEARCH_PROVIDER=ark`、`ARK_API_KEY`                                         | 在正常运行环境将正式研究分析切至火山 Responses API；接待、文案和审校仍用 MiMo。可用 `ARK_BASE_URL`、`ARK_VISION_MODEL`、`ARK_RESEARCH_MODEL`、`ARK_RESEARCH_EFFORT` 选择研究模型与推理配置。 |
| 竞品搜索       | `COMPETITOR_SEARCH_ENABLED=true`、`COMPETITOR_SEARCH_PROVIDER`                 | 提供方可选 `mimo`、`ark`、`brave`；配置对应的 `MIMO_API_KEY`、`ARK_API_KEY` 或 `BRAVE_SEARCH_API_KEY`。是否可用还取决于提供方模型、联网权限与额度。                                      |
| 后台自动制图   | `SEEDREAM_ENABLED=true`、`SEEDREAM_API_KEY`                                    | 负责人可在图片任务中启动自动制作；MiMo 编写商品专属构图提示词，Seedream 实际绘图并保存候选结果，需运行 worker。                                                                                      |
| 上传后自动制图 | `SEEDREAM_AUTO_ON_UPLOAD=true`                                                   | 与 Seedream 服务同时启用后，卖家上传参考图会自动排队制作任务。                                                                                                                                       |
| 图片调用控制   | `SEEDREAM_MODEL`、`SEEDREAM_TIMEOUT_SECONDS`、`SEEDREAM_ORDER_ATTEMPT_LIMIT` | 选择绘图模型、请求超时及本单尝试上限；默认超时 180 秒、本单上限 12 次。                                                                                                                              |

负责人可在接单前从订单的“图片制作与研究资料”导入有来源的竞品、客服等授权原文；正式研究开始后不能直接修改已冻结的资料。搜索每单最多五次尝试，保存的 Amazon 商品链接与摘要只作为竞品线索，不等于真实评论、销量或已验证商品事实。没有外部来源时，研究只给出基于现有资料的分析和待验证建议。

图片制作有两条路径：启用 Seedream 后在后台自动生成；未启用时保存/复制任务，在 Codex 或其他外部工具实际制作，再记录调用并上传结果。网站未接入 Codex 内置图片工具的 API，保存或复制任务本身不会绘图。手工回传支持 PNG/JPEG/WebP，须为至少 1024×1024 的正方形图片。

两条路径都保留原始参考图、资料版本、来源哈希和核对记录。回传后先人工核对，当前资料下核对通过的实物参考编辑图可提交为新候选，再进行完整图文审校与负责人放行；旧版本结果不能自动用于新资料。缺少实物依据时仅生成内部概念预览，不能进入正式交付。生成图片不证明商品性能，也不保证原商品像素不变。

自动图片调用若中断且结果不确定，会标为待核查，不静默重放；负责人核查提供方结果与计费后再决定重试。搜索、绘图和文本模型可能分别计费，费用面板不能完整代表外部工具或聊天内绘图成本。

## 验证

```text
uv run --project apps/server company-smoke-mimo
uv run --project apps/server pytest apps/server/tests
uv run --project apps/server pytest apps/server/tests --run-db-tests
uv run --project apps/server ruff check apps/server/src apps/server/tests apps/server/alembic
uv run --project apps/server company-export-openapi
npm --prefix apps/web run generate:api
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
node scripts/test-stack.mjs
```

MiMo烟测会使用真实模型额度，输出4项能力结果和脱敏用量报告至`var/reports/m0-mimo.json`；其余默认测试不调用模型。`--run-db-tests`显式连接独立测试库；没有测试库时不要使用该参数。E2E需要独立测试数据库，脚本会在8100/3100启动隔离前后端和离线模型worker，生成临时负责人凭据，结束后清理自身进程；不复用开发服务、不调用真实MiMo、不读取开发负责人密码。`npm --prefix apps/web run test:e2e`也使用同一隔离入口。macOS13使用已安装系统Chrome的全新临时profile，其他平台首次需执行Playwright浏览器安装。

截至 2026-11-08，`41e636d` 已通过 [GitHub Actions 三平台检查](https://github.com/yeechi18/HackJunction/actions/runs/42459690067)：Windows/macOS 离线检查与 Ubuntu PostgreSQL 17 集成检查。本机使用 CI 环境变量验证离线测试 132 项通过、114 项集成测试跳过，独立 PostgreSQL 完整测试 246 项通过；Ruff、前端 lint/typecheck 和隔离生产构建通过。该轮修复未重新运行浏览器全流程验收，历史页面验收见变更日志。

## 当前主机的数据库

当前macOS已原生安装Postgres.app，项目数据库集群位于被忽略的 `var/postgres`。它独立于Postgres.app的GUI默认集群；不要让二者同时争用5432端口。此段是当前开发机的管理命令，Windows使用自己的数据库服务管理方式。

```text
/Applications/Postgres.app/Contents/Versions/17/bin/pg_ctl -D var/postgres -l var/postgres.log -o "-h 127.0.0.1 -k /private/tmp -p 5432" start
/Applications/Postgres.app/Contents/Versions/17/bin/pg_ctl -D var/postgres status
/Applications/Postgres.app/Contents/Versions/17/bin/pg_ctl -D var/postgres stop
```

数据库仅监听本机。数据库数据和 `.env` 都不能加入源码分发；当前数据库初始化与开发凭据不代表生产部署方案。

设计与实施分别见 [项目概要](docs/PROJECT_OVERVIEW.md)、[架构](docs/IMPLEMENTATION_ARCHITECTURE.md)、[开发计划](docs/DEVELOPMENT_PLAN.md)、[新增参考采纳决定](docs/REFERENCE_DECISIONS.md)。

完整演示和测试边界见 [运行手册](docs/DEMO_RUNBOOK.md)，公网部署准备见 [部署说明](deploy/README.md)。交付和模拟刊登记录仅在有权限的订单中查看，不提供匿名公开商品页。

## 临时公网演示与验收

另建 `hackjunction_public_demo` 数据库并安装cloudflared后，执行 `node scripts/public-demo.mjs`。脚本使用独立数据库、资产目录和生产前端，与本机开发版同时运行；HTTPS地址在 `var/public-demo/state.json`，入口密码在 `var/public-access.txt`。保持电脑及脚本运行，关闭后链接失效；负责人另用原账号登录。详细准备、停止及验收见 [部署说明](deploy/README.md)。

人工完整操作见 [用户验收指南](docs/USER_TEST_GUIDE.md)，文件与代码量见 [实现清单](docs/FILE_INVENTORY.md)。公网只改变访问方式，真实MiMo与模拟Amazon的功能边界保持一致。

每次提交解决的问题、修改内容、验证结果和运行影响统一记录在 [变更日志](docs/CHANGELOG.md)。开发验收过程保留在 `docs/PROGRESS.md`，两者分别用于追踪 Git 提交与开发阶段。
