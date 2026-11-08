# 出海工坊 · Listing Studio

面向中文跨境小卖家的 Amazon 美国站商品本地化上架服务。Next.js前端、FastAPI后端、MiMo Agent和PostgreSQL共同构成一套网页应用；Windows/macOS共用源码。本机运行不需要Docker或GPU。

目前M0–M7本机模拟闭环已完成验收：获客、研究、文案/实际图片审校、负责人放行、卖家反馈/确认、模拟发布及人工接管均已接通。首页的理线夹内容明确为虚构示例，不是已处理的真实订单或已发布商品。实际验收结果见 [开发进度](docs/PROGRESS.md)。

当前用户确认优先完成M0–M7的模拟上架闭环，暂无Amazon卖家账号与SP-API授权。真实Amazon接入暂缓，最终模拟订单页面会明确标记模拟结果。

## 环境

- Node.js 22.x（含npm）、uv、Python 3.11（可 `uv python install 3.11`）。
- 本机PostgreSQL 17服务，业务库和测试库分开；浏览器用于访问页面。
- 使用新版Chrome/Edge或Safari 16.4及以上；本机原Safari 16.3低于Next.js支持范围，可能只显示页面而无法交互。建单页会提示升级，建议先用已安装的Chrome验收。
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

`company-api`处理Windows异步连接所需的运行时设置。worker运行持久化获客、研究、生成、审校、交付与模拟发布；等待卖家或负责人时保存状态，不重复请求模型。

访问 [首页](http://127.0.0.1:3000)、[基础状态页](http://127.0.0.1:3000/system)、[本地接口文档](http://127.0.0.1:8000/docs)。`live`只检查API进程，`ready`还检查数据库、业务schema和LangGraph检查点表；数据库未准备好时返回503，不假装就绪。

PowerShell若拦截 `npm.ps1`，可将命令中的 `npm` 改为 `npm.cmd`。安装运行时后重新打开终端。前端或API端口被占用时先确认是否已有本项目进程，不盲目结束其他程序。

## 卖家账号与订单

先在 [卖家注册](http://127.0.0.1:3000/seller/register) 输入邮箱并收取验证码，创建账号（密码8–18位，字母/数字/特殊符号至少两类），之后 [登录](http://127.0.0.1:3000/seller/login) 提交商品。订单自动绑定当前账号，在 [我的订单](http://127.0.0.1:3000/seller/orders) 查看；换浏览器或重启后重新登录即可找回，不依赖私有链接。其他卖家即使拿到完整链接也无法访问。

更新代码若包含数据库迁移，先停止 API 和 worker，执行 `uv run --project apps/server company-db-init`，再启动服务；每次重启电脑无需重复迁移。本机当前已升级到 `0012_intake_cleanup`，已有账号、订单及图片保留。此前按要求进行的一次性旧数据清理见 changelog，常规迁移不会清空数据。

卖家密码使用 Argon2 摘要，登录保存在 HttpOnly 会话 cookie 中；默认7天过期，可主动退出。注册已要求邮箱验证码，当前尚无忘记密码流程，请记住注册邮箱和密码。本机库与公网演示库独立，两边分别注册，账号和订单不互通。

QQ 发件邮箱与 SMTP 授权码配置见 [邮箱配置说明](docs/EMAIL_SETUP.md)。邮箱功能由 `0010_registration_email` 引入；其他机器更新后先 `uv sync --project apps/server --locked --group dev`，再执行迁移并重启。

建单页填写商品名、明细、可选国内链接、参考图、补充说明五项。获客顾问会实际分析本单参考图，并尝试读取公开链接，给出资料一致性意见及文案初稿方向；不再强制补齐材质、尺寸、数量、适用范围或逐项核实。初审后负责人决定接单，正式研究/生成/审校自动推进，负责人端素材只读。详见[Agent输入策略](docs/AGENT_INPUT_STRATEGY.md)。`0012_intake_cleanup`清理旧订单模式、重复身份列和工时表，须停止API/worker后先迁移；本次初审策略更新无需新增迁移。



新版界面采用上架有方 Shelfwise 的首页、聊天与资料卡布局。建单前可直接填写资料卡，也可上传不超过 2 MiB 的 CSV/XLSX 商品数据包或用文字请顾问整理；导入只形成待核对草稿，不自动创建订单或将资料标记为已核实。商家信息仍从登录账号关联，最终提交沿用上述五项建单与参考图上传流程。建单前聊天同样需要登录和 CSRF 校验；模型不可用时可继续手动填写。

负责人登录入口为 `/internal/login`，账号固定 `owner`。打开 `var/local-admin.txt`，密码框只填写 `password=` 右侧的完整值；不填写前缀、不把文件两行一起粘贴，也不使用卖家密码或 SMTP 授权码。

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

完整演示和测试边界见 [运行手册](docs/DEMO_RUNBOOK.md)，公网部署准备见 [部署说明](deploy/README.md)。交付和模拟刊登记录仅在有权限的订单中查看，不提供匿名公开商品页。

## 临时公网演示与验收

另建 `hackjunction_public_demo` 数据库并安装cloudflared后，执行 `node scripts/public-demo.mjs`。脚本使用独立数据库、资产目录和生产前端，与本机开发版同时运行；HTTPS地址在 `var/public-demo/state.json`，入口密码在 `var/public-access.txt`。保持电脑及脚本运行，关闭后链接失效；负责人另用原账号登录。详细准备、停止及验收见 [部署说明](deploy/README.md)。

人工完整操作见 [用户验收指南](docs/USER_TEST_GUIDE.md)，文件与代码量见 [实现清单](docs/FILE_INVENTORY.md)。公网只改变访问方式，真实MiMo与模拟Amazon的功能边界保持一致。

每次提交解决的问题、修改内容、验证结果和运行影响统一记录在 [变更日志](docs/CHANGELOG.md)。开发验收过程保留在 `docs/PROGRESS.md`，两者分别用于追踪 Git 提交与开发阶段。

## 最新版集成试验（feat/latest-image-research-drafts）

在 Shelfwise 原有的资料卡、真实图片初审和审批产线上补充以下能力，未接入真实 Amazon 发布或视频生成：

- 两端可查看按资料版本保存的英文初稿（标题、五点、详情、FAQ）。初稿与正式 DeliverableVersion 分开，未知参数不写入正文。打开订单自动生成本版初稿，成功结果重用；每版至多五次尝试，调用记入模型预算。更改资料后旧初稿不再作为当前版本显示。
- 已核对品牌目录目前只有百岁山/Ganten：官网支持品牌、天然矿泉水类别、饮用用途，不支持据此猜本件净含量、材质、矿物指标、认证或销售授权。新单补入缺失基础项，旧单可点击补全，已有字段不覆盖。官方来源在页面和生成记录中保留。
- 百岁山瓶装水正式交付前须确认包装数量、正数净含量及 mL/L 单位、用途；草稿和接单不受此限制。卖家用“正式交付前确认”保存必要规格，新版本重新初审和批准。交付审核阶段允许卖家补资料，旧输入与成品保持不变。其他类别沿用作者现有流程，本次没有宣称实现通用合规引擎。
- 放行的读模型、接单决定接口及交付服务均检查必要事实与计划/实际图片数量；完整图文审校也检查图片数量。生成计划限制为当前制作器实际支持的两张图，历史三张计划不静默改写。
- 负责人可在接单前导入有权限的资料：`POST /internal/orders/{id}/research-sources`。导入材料只作部分证据，不冒充已验证事实；原来的 evidence POST 和手动生产路由仍保持停用。已有研究快照不被悄悄改写。
- `RESEARCH_PROVIDER=ark` 使用火山 Responses API 分析提供的事实和来源，接待、文案和审校仍用 MiMo。真实模型与用量分别记录，不把第三方输出冒充 MiMo。搜索可选配置但默认关闭；无外部来源时展示市场分析初稿、待验证建议，不生成虚构评论、价格和销量。
- Image2/Codex 为人工交接：负责人复制本版任务，在 Codex 制作，核对后上传。网站不能调用本聊天内部工具。未核实图存为内部 concept，不进入参考图选择或交付；合格参考编辑图另存 reference_edit，保留原图、源哈希、资料版本与人工核对记录，在接单前或等待图片阶段回传。旧资料版本的编辑图不会用于新版本。生成图不证明商品性能，后续仍须完整审校、负责人放行。

本地生成测试产物 `var/reports/ganten-image2-integration-preview.png`：使用内置 Image2，要求仅调整原瓶背景、保留红色标识，不新增容量、成分或认证。生成结果有重绘细节，作为未核实内部预览，不能当成实拍或正式上架图。模型绘图本身不保证原商品像素不变。

密钥只配置在后端忽略的 `.env`；示例文件不含密钥。绘图和搜索的额外费用不视为零，当前费用面板不包含聊天内绘图成本。
