> 最新修订：获客改为基于图片、公开链接与现有描述的初审/内容顾问，不追索固定四类参数；负责人初审后可接单，素材只读。下方保留阶段计划，当前行为见AGENT_INPUT_STRATEGY.md。

> 当前输入、候选提取与需求传递策略见[AGENT_INPUT_STRATEGY.md](AGENT_INPUT_STRATEGY.md)。旧人工录入证据、手动生产启动与工时记录已从前后端和数据库中清理。以下阶段为原始开发计划。

> 2026-11-07 实施调整：卖家先经邮箱验证码注册并登录，密码8–18位且至少两类字符；新订单自动绑定账号并从“我的订单”找回；旧订单不认领，已按要求单次清空。以下阶段记录中的匿名私有链接建单方案由此替代，负责人账号仍独立。 新建单页已调整为五项商品输入，商家身份由账号自动提供；新增与清理字段须先执行0012迁移，实际交付状态以README和验收记录为准。

# 开发执行计划

日期：2026-11-07。依据 `PROJECT_OVERVIEW.md` 与 `IMPLEMENTATION_ARCHITECTURE.md`。本文把架构转换成实施步骤；涉及的程序、命令入口和测试均为待实现内容，本文为实施前计划，实际完成状态和测试证据以 `PROGRESS.md` 与当前 `README.md` 为准。

**执行中的用户范围更新：**团队目前没有Amazon卖家账号/SP-API授权，已明确要求先完成标注清楚的模拟上架闭环。当前交付目标为M0–M7的模拟闭环，R真实接入暂缓，保留适配接口但不列为本轮完成条件；以下原真实接入说明作为未来里程碑保留。模拟结果始终不代表真实上架。

## 1. 实施方式与首版约定

采用常见业务系统的分层方式：页面 → API → 应用服务 → 领域规则/数据访问；Agent、模型、图片和发布平台通过明确接口接入。按“一条业务链跑通后再扩展”开发，每个阶段都提供可运行成果，不先生成全部空目录或建设通用平台。

新增整合方案与调研资料的采纳范围见 [参考材料采纳决定](REFERENCE_DECISIONS.md)。保留五Agent和真实上架目标，增强事实冲突、可执行研究结论、数量/尺寸变更联动及小样本公平验收，不重做现有基础工程。

保留原目录命名：`apps/web` 是前端，`apps/server` 是后端。同仓库管理，两者分别构建；内部端和卖家端是同一前端的不同页面，不再新增两个前端项目。

跨平台目标按“同一套源码在 Windows/macOS 安装依赖后启动，通过浏览器使用”实施，业务代码不按操作系统分叉。原生 `.exe`/`.app` 安装包暂不属于已确定的网页交付形式，若需要另行设计打包，不将源码可运行等同于已提供桌面安装包。用户已具备 MiMo API Key，后续从后端本地配置读取并验证，无需在文档中记录密钥。

| 决策 | 本次开发采用的方案 |
|---|---|
| 数据库 | 本地原生安装PostgreSQL，开发库与测试库隔离；业务保持PostgreSQL语义，不切换SQLite |
| 跨平台与容器 | Windows/macOS共用代码与启动命令；本机开发不要求Docker，容器仅保留为将来可选部署方式 |
| 依赖管理 | 前端 npm + `package-lock.json`；后端 uv + `uv.lock`；初始化时验证并锁定版本 |
| 执行方式 | FastAPI 接收命令；独立 worker 运行 LangGraph；首版一个 worker，不并行执行同一订单 |
| 前后端通信 | 浏览器只访问本站 `/api/v1`，Next.js rewrite 转发到 FastAPI；业务规则统一放 Python 后端 |
| 进度刷新 | 首版短轮询，进行中约 2 秒、等待中约 5 秒；页面显示网络错误与最后更新时间 |
| 人工交付闸门 | 建议采用“本版不否决并交付”按钮，绑定具体版本；不实现自动倒计时放行 |
| 图片 | 先用核实过的商品参考图与确定性排版，输出实际图片文件；外部生图后续接同一接口 |
| 发布 | 用户已明确本轮完成标记清楚的模拟闭环；真实Amazon接入保留为后续里程碑R |
| 测试 | 离线用 FakeMiMo 验证流程；真实 MiMo 另做能力验收；两者不互相替代 |

原架构中的实体是业务概念，不要求一个概念建一张表。初期按阶段建必要表；研究包、审校报告等结构化产物可用带 Pydantic 契约的 JSONB 存储，不建设通用 repository、通用队列平台或事件溯源系统。

## 2. 当前环境与启动目标

计划制定时检查：目录仅有需求和架构文档。当前已有工程及原生PostgreSQL，详情见README与PROGRESS。现有 Node.js `22.14.0`、npm `10.9.2`、Python `3.11.9`、uv `0.11.28`。此前 PATH 未发现 `psql`、`postgres`。用户已选择本机原生安装PostgreSQL；此为计划制定时的环境记录，不代表当前状态。

前端、API、worker及PostgreSQL均以本机原生运行作为开发基线，通过数据库连接配置访问服务。PostgreSQL应安装并启动server，不能只安装pgAdmin；创建业务库与独立测试库，记录端口、账号及密码，配置初始化权限。Docker/Compose不列入本地必需环境，未来若采用容器部署可另行提供。提供三种检查：`/health/live` 检查进程，`/health/ready` 检查数据库与初始化状态，内部页展示 worker 心跳；健康检查不调用付费模型。

跨平台实现要求：路径用Python `pathlib` / Node路径API，配置模板由 `scripts/init-env.mjs` 复制且不覆盖已有配置；启动与测试进程管理不依赖Bash、`fork`、`fcntl`或Unix权限。用同一锁文件在各系统重新安装依赖，不复制 `node_modules` 或 `.venv`。依赖选择需核验对应系统/CPU支持，并分别验证Windows/macOS的启动、worker恢复、上传下载和基本端到端流程；未实际测试的平台明确记为待验。

Windows开发/运行机的环境清单如下；仅访问线上网站的卖家和负责人不需要安装这些开发工具：

| 环境 | 是否必需 | 安装或交付约定 |
|---|---|---|
| Node.js与npm | 必需 | 与项目固定的Node主版本保持一致；npm随Node安装 |
| uv与Python | 必需 | 安装uv后用 `uv python install 3.11` 获取Python，项目用3.11系列；不要求另装conda |
| PostgreSQL server | 必需 | 使用团队统一的稳定主版本，数据库服务启动并完成连接配置；pgAdmin是可选管理界面 |
| 网络及本机配置 | 必需 | 可访问依赖下载源和MiMo API；各机填写自己的 `.env`，密钥不随代码分发 |
| Git | 版本协作需要，运行不必需 | clone/pull使用；下载源码压缩包运行时可以不装 |
| Playwright浏览器 | 仅端到端测试 | 由项目测试安装步骤下载；不是手动使用网页的前置条件 |

FastAPI、Pydantic、LangGraph、数据库驱动和图片库由锁文件统一安装，不逐个全局安装。优先使用Windows/macOS都有预编译包的依赖，首版不把Visual Studio Build Tools、WSL、Docker或本地GPU列为前置条件。图片排版随项目提供可分发字体，避免依赖某一操作系统字体。安装Node/uv后重开终端验证PATH；PowerShell若拦截npm脚本，可用 `npm.cmd`，不要求全局放宽执行策略。后续M0–M1加入 `scripts/check-env.mjs` 检查运行时、连接配置、数据库可达性与端口，发现缺项时给出明确提示。

目标访问入口：

| 地址 | 用途 |
|---|---|
| `http://localhost:3000` | 服务首页与卖家入口 |
| `http://localhost:3000/o/{order_id}#token=...` | 卖家初次进入私有订单，交换会话后移除 fragment |
| `http://localhost:3000/internal/login` | 负责人登录 |
| `http://localhost:3000/internal/orders/{order_id}` | 内部订单工作台 |
| `http://127.0.0.1:8000/docs` | 本地接口调试，不作为卖家入口 |

线上使用一个 HTTPS 域名，沿用相同页面路径。API 密钥、数据库连接和内部密码不会进入前端配置。当前本机使用以上端口；公网部署尚未选定。

## 3. 分阶段开发清单

总预算可参考 40 个有效开发小时，按实际情况调整；每阶段完成后记录结果再推进。各阶段都补本阶段必要测试，最后阶段集中做端到端验收。

以下文件均为项目内拟建路径。省略前缀的后端模块位于 `apps/server/src/company/`，后端 `alembic/` 位于 `apps/server/`；前端源码位于 `apps/web/src/`。M0–M7覆盖本轮用户授权的模拟闭环；真实接入R等待后续账号授权，不属于本轮完成条件。

### M0：环境与外部能力验证，约 2h

**文件：** `.env.example`、`.gitignore`、`apps/server/pyproject.toml`、`apps/server/src/company/config.py`、`providers/mimo.py`、`cli/smoke_mimo.py`；`fixtures/demo/product.json`、`evidence.json`、`README.md`。

先初始化可安装的 Python 包 `company`，配置文件从项目根目录显式加载，API、worker、CLI 不依赖各自启动目录。确认数据库供应方式，读取比赛 MiMo 接入资料，建立最小 provider 和连通探针。准备一份虚构理线夹资料、合法使用的参考图及带来源标签的演示竞品观察。

探针分别验证文本、图片理解、JSON 结构、无外部副作用的工具调用，记录模型、延迟和实际用量。只验证接口，不发送邮件、发布商品或执行任意 shell。同步排查 Amazon 卖家授权、应用权限、商品身份信息和图片托管条件。

**验收：** 读取已具备的MiMo key完成真实探针，结果可复核。鉴权、额度或权限失败如实记录，不伪造成功；页面和离线流程开发可以继续，但后续真实 Agent 验收必须补过。

### M1：前后端骨架和数据库初始化，约 3h

**文件：** `scripts/init-env.mjs`、`scripts/check-env.mjs`、`apps/server/alembic.ini`、`alembic/env.py`；后端 `main.py`、`api/health.py`、`persistence/database.py`、`cli/init_db.py`；前端 `package.json`、`.env.local.example`、`next.config.ts`、`src/app/layout.tsx`、`src/app/page.tsx`、`src/lib/api.ts`；`README.md`。

创建 Next.js TypeScript 项目，统一简单页面布局、表单和错误展示。Next.js 将 `/api/v1/:path*` 转发到配置中的后端地址。后端连接数据库，初始化 Alembic；数据库初始化命令同时执行应用迁移和 LangGraph checkpointer 的 `setup()`，避免只建业务表却漏掉流程表。迁移作为单独操作，不由多个服务启动时竞相执行。

后端使用 AsyncOpenAI、异步数据库连接和 AsyncPostgresSaver；每个请求或任务使用独立 Session，不把请求 Session 传给 worker。图片处理等阻塞操作移到线程执行。`api.ts` 统一处理 cookie、错误、幂等键和 `409` 提示。

**验收：** 首页能请求后端，数据库 readiness 通过；初始化命令重复运行不破坏数据；前后端分别能构建/导入。骨架验证通过即可准备首个可访问测试部署，不等待全部 Agent 完成。

### M2：订单、私有访问与人工接单，约 5h

**文件：** 后端 `domain/states.py`、`schemas/orders.py`、`schemas/commands.py`、`schemas/artifacts.py`；`persistence/models/orders.py`、`models/access.py`、`models/commands.py`、首个业务迁移；`services/orders.py`、`services/access.py`、`services/commands.py`；`api/orders.py`、`api/access.py`、`api/internal.py`。

前端新增 `src/app/(seller)/orders/new/page.tsx`、`(seller)/o/[orderId]/page.tsx`、`(internal)/internal/login/page.tsx`、`(internal)/internal/orders/page.tsx`、`(internal)/internal/orders/[orderId]/page.tsx`。

实现提交、保存和查询订单；建单允许商品资料不完整，已填写事实分别记录卖家是否确认，不能在初始表单或建单schema强制全部齐全。“资料齐全且事实确认”是M3提请负责人接单的条件，不是生成订单链接的条件。私有入口令牌只保存摘要，交换成限定订单的会话。负责人使用单一账号登录，建立独立内部会话，不先做注册或多角色权限系统。先建设订单、输入快照、访问会话、消息、决定、命令、job 和事件等必要存储，后续产物表逐阶段增加。

提前固定产物版本、发布模式和确认快照的 Pydantic 契约，但本阶段不实现发布。批准/拒绝接口写入幂等命令，不在 API 线程里运行 Agent。未接入完整资料审查前，这部分只用于开发联调，不宣称正式接单链已完成。

**验收：** 刷新页面订单仍在；私有链接能恢复本单；另一订单会话、未登录访客无法读本单/附件/内部接口；负责人能提交批准或拒绝命令。接口文档导出后生成前端类型。

### M3：真实获客、持久工作流与批准恢复，约 6h

**文件：** 后端 `worker.py`、`workflows/state.py`、`graph.py`、`nodes.py`、`persistence/jobs.py`、`checkpoints.py`；`agents/acquisition.py`、`prompts/acquisition.md`、`services/facts.py`、`services/runs.py`；前端 `src/features/intake/`、`features/orders/`。

先实现前半条真实流程：获客提取资料 → 程序校验必填/来源 → 缺资料则请求补充并等待 → 补充后重审 → 等负责人批准/拒绝。获客对话和结构化表单共用一份商品事实；模型提取的信息不自动当作卖家已确认事实。

同一订单使用稳定 thread_id，外部回复通过已落库的命令恢复。jobs 只负责唤醒图；订单级租约和提交时版本检查防止重复执行。记录 AgentRun、工具摘要、事件及用量，页面从真实事件显示进度，不自行推算流程。不同来源的冲突事实并列保存并请求卖家确认，不能由模型选择一个值直接覆盖。

**验收：** 真实 MiMo 能识别缺失资料；批准后进入下一阶段，拒绝后结束。关闭并重启 worker，原等待点仍能恢复；补充/审批重复提交只生效一次；无新输入时不反复请求模型。

### M4：研究、生成、审校和版本链，约 8h

**文件：** 后端 `schemas/research.py`、`schemas/listings.py`、`schemas/reviews.py`；`agents/research.py`、`generation.py`、`review.py` 及对应 prompts；`services/evidence.py`、`versions.py`、`review.py`；`persistence/models/artifacts.py` 及迁移；前端 `src/features/operations/EvidencePanel.tsx`、`RunTimeline.tsx`、`VersionCompare.tsx`。

接入三种真实 MiMo 角色。研究读取本单已确认事实和人工录入竞品证据，输出事实/观察/疑问/定价依据。生成输出英文标题、五点、详情、FAQ、图片计划和声明证据关联；每轮保存不可变版本。审校结合程序规则和 MiMo 语义审查，未通过时带具体意见回生成，按轮数和用量预算限制循环。

研究包增加买家问题、是否适用、证据缺口、应向卖家追问什么、建议修改字段；来源统一记录时间、范围、成功/部分/失败状态。文案声明与图片文字层关联事实编号，优先覆盖包装数量和尺寸变化，不建设通用依赖图。

先跑通文案产线并在内部预览；图片尚未完成时，该版本不能标为完整审校通过或向卖家正式交付。对研究包和审校报告使用带类型及 schema_version 的 JSONB，成品版本单独存储；不必给每种中间字段建独立表。

**验收：** 负责人可查看真实输入来源、研究包、草稿和审校问题；一条无依据声明能被真实审校识别并修改；新版本不覆盖旧版；证据不足时不捏造商品属性或定价理由。假数据仅用于离线测试，不算真实 Agent 验收。

### M5：图片、交付闸门与下载包，约 5h

**文件：** 后端 `providers/images.py`、`providers/storage.py`、`tools/image_layout.py`、`tools/build_package.py`；`services/assets.py`、`services/delivery.py`、`api/assets.py`、`api/deliverables.py`；资产/交付字段迁移。前端 `src/features/deliverables/`、`features/operations/ReleasePanel.tsx`。

使用参考图和经核实的尺寸/卖点制作实际 PNG/JPEG，主图候选与信息附图分模板。审校读取实际图片，检查数量、尺寸、图文声明和商品外观一致性；没有足够参考图则进入补图等待，而不是把图片计划冒充成图片交付。实现上传大小、类型、归属校验和授权下载。

审校通过后进入待交付；负责人可否决并给意见，也可点击“本版不否决并交付”。获客 Agent 形成交付消息，交付服务绑定本版文案、图片与定价资料，卖家才获得可见权限。生成 ZIP：文案、FAQ、定价说明、图片、版本清单，不混入内部诊断和隐私资料。

**验收：** 卖家看不到未交付或被否决版本；否决后回生成并再次审校；可复制文案、预览实际图片和下载同一版本 ZIP。交付请求重放不会重复创建消息或改交其他版本。

### M6：卖家反馈、确认与发布闭环，约 5h

**文件：** 后端 `services/feedback.py`、`services/confirmations.py`、`services/publishing.py`；`agents/publishing.py`、`prompts/publishing.md`；`providers/publishers/base.py`、`simulated.py`，发布/确认迁移；前端 `src/features/deliverables/ConfirmationPanel.tsx`、`features/orders/PublishResult.tsx`、虚构演示商品页。

卖家反馈经获客角色直接交生成，不新增中间主管。区分风格、事实、新增声明和范围变化；“写得更防水”先核依据，增加SKU或改平台交负责人决定范围。表达修改复用事实研究；涉及事实变化时先确认事实、失效依赖，必要时重新研究。修改后重新审校、放行和交付。卖家对完整发布快照确认：文案、图片、售价/币种、SKU、市场和适用库存/履约字段。返修计数绑定当前交付/反馈请求，不随新候选版本重置；数量6改4不误改尺寸6 mm，实物图片仍显示6个则要求换图或人工处理。

上架 Agent 使用真实 MiMo 准备字段和解释校验问题，发布网关校验快照再执行。模拟网关返回明确标记的模拟记录，虚构 SKU 可生成演示商品页，订单显示“模拟订单完成”。模拟只替代外部 Amazon 系统，不替代五个角色的真实模型调用。

**验收：** 完整跑通一单；未确认、旧版本或变更后已失效的确认不能发布。补发布资料若改变快照，回卖家重确认。发布超时进入 UNKNOWN 并对账，不自动重发。真实模式无授权时明确阻塞，不降级假成功。

### M7：人工客服、故障恢复、部署与演示，约 6h

**文件：** 后端 `services/support.py`、`api/support.py`、`api/retries.py`，必要迁移；前端 `src/features/orders/SupportPanel.tsx`、`features/operations/UsagePanel.tsx`；`tests/workflow/`、`tests/integration/`、`apps/web/e2e/`、`playwright.config.ts`；`deploy/README.md`、`docs/DEMO_RUNBOOK.md`、更新 `README.md`。部署平台确定后补进程/HTTPS配置；Dockerfile与Compose仅在选用容器部署时提供，不影响本机方案。

实现人工接管/回复/归还；接管暂停自动对话，是否暂停产线由负责人明确选择。完善失败阶段、错误说明和受控重试；测试产物已提交但 checkpoint 未写入等故障点。内部页汇总模型用量和返修；按用户最新要求不提供负责人活跃工时登记。

在已有骨架部署上更新完整功能，使用单一 HTTPS 入口；API、worker 共享同一数据库与文件存储，配置持久卷或对象存储。部署后验数据库迁移、权限、Cookie、下载和重启恢复。公网采集真实资料前，必须通过订单隔离、内部登录及文件访问测试。

**验收：** 两个浏览器分别扮演卖家与负责人，按运行手册完整演示主流程和拒绝、否决、转人工、失败恢复分支。三分钟演示可展示一处真实审校退回与新版本，现场长模型调用可辅以可核验的历史运行记录，并标明回放。真实询单与虚构演示数据分开统计。

## 4. 必须先统一的接口与执行约束

后端领域规则和服务才是动作合法性的权威来源；前端用后端返回的 `allowed_actions` 展示按钮，不自己维护另一份状态机。FastAPI 通过 `APIRouter` 分组，对前端公开 DTO 与内部诊断 DTO 分开定义。

| 契约 | 实施要求 |
|---|---|
| 订单动作 | 命令包含 action、expected_revision、适用的 version_id/wait_id，使用 `Idempotency-Key` 防重复 |
| 命令响应 | `202` 只表示已入队，返回 command_id；前端通过事件看执行结果，不能立即显示业务成功 |
| 非法动作 | 未登录 `401`，无权限 `403` 或隐藏资源 `404`，过期状态/版本 `409`，结构不合格 `422` |
| 幂等键 | 同键同请求返回原结果，同键不同请求内容拒绝；幂等命令与job同事务入库 |
| 恢复 | 命令绑定本次等待标识，重复恢复不能误应用到下一次等待；节点复用已提交执行凭据 |
| 成品与价格 | 发布使用规范化完整快照哈希，金额使用 Decimal/规范小数字符串，币种明确，不用浮点金额 |
| 文件 | 不可变资产ID与内容哈希，授权校验后读取；卖家ZIP使用交付清单，不按目录全量打包 |
| 配置 | 模型真假模式、发布模式独立配置；密钥缺失或真实调用失败不能返回模拟成功 |

业务节点按“短事务读取输入 → 关闭事务 → 模型/工具执行 → 短事务校验依赖并写产物/状态/事件”工作。模型期间不持有长事务。Graph 中断与副作用拆开，worker 重放时可查执行凭据，不能把 `interrupt()` 放在一个会重复交付或重复发布的大函数中。

API 不直接接收任意 LangGraph state 或节点名；重试只能重试当前被允许的失败运行。jobs 只唤醒图，不另外维护研究→生成→审校的业务调度。首版不建设任意节点编辑器、并行DAG或分布式执行平台。

## 5. 待实现的命令入口和实际操作顺序

以下为已确定的操作约定。当前可执行入口、环境准备和验证结果以README为准。先在Windows PowerShell或macOS终端进入各自的项目根目录，随后使用相同命令，不写死本机绝对路径。

后端统一读取根目录 `.env`；前端读取 `apps/web/.env.local`，只配置服务端使用的 `API_ORIGIN`，不复制后端密钥。必要配置如下，连接密码和访问凭据由环境提供，不提交入库：

| 配置 | 用途 |
|---|---|
| `APP_ENV`、`PUBLIC_APP_URL` | 开发/测试/生产环境与订单链接的站点地址 |
| `DATABASE_URL`、`TEST_DATABASE_URL` | 两套隔离数据库；分别供业务和测试使用 |
| `MIMO_API_KEY`、`MIMO_BASE_URL`、`MIMO_MODEL` | 比赛模型接入 |
| `MIMO_MODE=live`、`PUBLISH_MODE=simulated` | 独立选择模型与平台模式；fake模型仅用于显式测试配置 |
| `ASSET_DIR`、`STORAGE_BACKEND` | 本地资产目录或对象存储；API/worker指向相同位置 |
| `SESSION_SECRET`、`ADMIN_PASSWORD_HASH` | 会话/请求防护所需密钥与单一负责人的密码摘要 |
| 前端 `API_ORIGIN` | 本地为 `http://127.0.0.1:8000`，容器内为API服务地址；不使用公开环境变量暴露密钥 |

### 安装和初始化

```bash
node scripts/init-env.mjs
npm --prefix apps/web ci
uv sync --project apps/server --locked --group dev
```

填写后端 `.env` 中的MiMo key和本机数据库连接，确认原生PostgreSQL服务运行、业务库和测试库已经创建，再继续：

```bash
uv run --project apps/server company-db-init
uv run --project apps/server company-seed-demo
```

初始化开发仓库时使用 `npm install`、`uv lock` 生成锁文件；上述 `ci`、`--locked` 是代码和锁文件已交付后的复现方式。`.env` 填入实际连接与密钥，演示初始化只插入虚构记录，不覆盖已有订单；未配置 key 时不能通过真实能力验证。本机启动过程不包含容器命令。

### 三个终端分别启动

```bash
# 终端一：前端
npm --prefix apps/web run dev
```

```bash
# 终端二：API
uv run --project apps/server company-api
```

```bash
# 终端三：worker
uv run --project apps/server company-worker
```

### 检查与测试

```bash
uv run --project apps/server company-smoke-mimo
uv run --project apps/server company-export-openapi
npm --prefix apps/web run generate:api
uv run --project apps/server pytest apps/server/tests
uv run --project apps/server ruff check apps/server/src apps/server/tests
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e
```

测试前准备 `TEST_DATABASE_URL` 指向的本机独立测试库，可与开发库位于同一PostgreSQL服务中，但不能使用同一个数据库。后端测试统一放 `apps/server/tests`，前端 E2E 放 `apps/web/e2e`；pytest核验连接为测试库，禁止以业务库作为清理目标。Playwright首次使用前安装浏览器，`test:e2e` 负责测试库初始化、在独立端口启动前后端和 FakeMiMo worker、运行测试并清理进程，不复用正在处理真实订单的开发服务，进程控制在两种系统分别验收。

真实 MiMo 烟测单独执行并消耗额度，默认离线测试不产生模型费用。`company-export-openapi` 输出根目录 `contracts/openapi.json`，`generate:api` 用 openapi-typescript 生成 `apps/web/src/lib/generated/api.d.ts`，不依赖正在运行的API；契约变化后二者一同更新。前端脚本分别调用 ESLint、`tsc --noEmit`、`next build` 和 Playwright，不使用旧版 `next lint`。

为减少手工操作，M1 提供 README 启动流程，M7 将已验证命令汇总进运行手册。命令入口由 `apps/server/pyproject.toml` 的 `[project.scripts]` 注册，数据库初始化包装 Alembic 和 checkpointer setup，worker 入口启动图执行循环，不新增另一套工作流调度。

## 6. 验收分层与真实 Amazon 分支

| 验证层 | 使用什么 | 重点 |
|---|---|---|
| 规则测试 | 固定输入，不调模型 | 版本、价格、状态合法性、声明和证据规则 |
| 集成测试 | 真实Postgres + FakeMiMo | 权限、命令事务、工作流等待/恢复、重复提交、崩溃窗口 |
| 页面E2E | 真实前后端 + 测试worker + FakeMiMo + 模拟发布 | 两种身份操作、刷新、补资料、审批、下载与确认 |
| 模型能力验收 | 真实MiMo + 实际图片 + 固定演示输入 | 五角色真实调用、证据支持、审校退回、修正、用量 |
| 发布验收 | 模拟或真实模式分开 | 快照一致性；真实模式另核异步处理、本次版本生效及可售状态 |

必要故障场景包括：重复点击批准；卖家猜订单ID；未交付版本下载；事实变更后旧任务晚到；新版本沿用旧确认；节点写产物后worker崩溃；恢复命令重投；审校超预算；发布超时后重试；SKU旧版可售但本次提交未生效。以上测试验证业务风险，不要求给每个薄包装函数写重复测试。

**后续里程碑 R：真实 Amazon 上架，本轮按用户决定暂缓。** 从 M0 排查，凭据齐备后单独实现 `providers/publishers/amazon.py`、授权配置/续期和契约测试，复用 M6 的确认/发布网关。先取得目标商品类型schema、明确商品身份和适用报价/履约字段，再做本地校验、受控提交和状态查询；图片按授权方案提供给平台抓取，卖家凭据只由后端管理。完整验收需有合法卖家授权、符合schema的输入和明确发布确认，提交后查询处理问题及本次版本生效情况，不能把 HTTP 成功或旧SKU可售认作本版已上架。

缺少权限时仍可完成模拟闭环作为阶段成果，但在提交材料中明确真实发布尚未完成，原需求的该项功能仍未验收。真实接入保留为后续目标，不能只修改一个模式配置就宣称已经接通；它与前述业务开发并行推进，权限具备后必须单独跑通真实发布。

## 7. 每次开发交付与第一批任务

每阶段提交实际代码、迁移、配置模板、启动说明和本阶段验证结果；明确哪些是真实调用、哪些是模拟、哪些还未通过。依赖和表结构按阶段追加，保持上一阶段功能可运行。前端、后端、Agent可以在契约统一后分工，版本规则和graph由一个主要实现者统一维护，避免多人各写一套流程。

**最初执行顺序（历史计划）：M0–M1** 初始化两端工程与锁文件、配置模板、数据库连接/初始化、真实MiMo探针、健康接口、前端转发和首页；交付一套能启动的基础工程。数据库环境或密钥缺失时如实报告具体未通过项，并继续不依赖它的工作。通过基础验证后进入 M2–M3，不在第一轮堆齐所有业务表和五Agent提示词。

参考并核对的官方机制：[FastAPI多文件应用与APIRouter](https://fastapi.tiangolo.com/tutorial/bigger-applications/)、[Next.js安装要求](https://nextjs.org/docs/app/getting-started/installation)、[Next.js rewrites](https://nextjs.org/docs/app/api-reference/config/next-config-js/rewrites)、[LangGraph中断/恢复](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Postgres checkpointer初始化](https://docs.langchain.com/oss/python/langgraph/add-memory)、[uv项目与锁文件](https://docs.astral.sh/uv/guides/projects/)、[MiMo兼容接口与工具调用约束](https://mimo.mi.com/docs/zh-CN/api/chat/openai-api-post)。业务分层、阶段划分和默认值为针对本项目的实施设计，不是官方强制架构。

跨平台及可选数据库环境依据：[PostgreSQL各平台安装包](https://www.postgresql.org/download/)、[Docker容器与环境一致性](https://docs.docker.com/get-started/docker-overview/)、[uv在Windows/macOS的安装](https://docs.astral.sh/uv/getting-started/installation/)。
