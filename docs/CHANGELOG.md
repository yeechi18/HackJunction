# 变更日志

按实际 Git 提交记录，最新在前。日期使用北京时间。

## 维护约定

- 每次提交记录：解决的问题、主要改动、验证结果，以及是否需要重启或迁移数据库。
- 尚未提交的工作集中放在“待提交”；完成提交后，下次维护时根据 `git log` 归档并补充真实 commit ID，不编造哈希。
- 代码与对应日志应一起提交。对话中的一次修改不一定是一次 Git 提交；若多轮修改合并提交，日志也合并记录。
- 不记录密钥、密码、私有订单链接或真实卖家资料。运行日志保留在被忽略的本地 `var/reports/`。

## 待提交 — 完善订单交互、参考图管理及负责人界面

建议提交标题：`fix: improve order intake, image management and owner workflow`

### 解决的问题与改动

| 问题 | 处理方式 | 主要文件 |
|---|---|---|
| Safari 16.3 能显示建单页，但无法正常交互；加载未完成时点击提交缺少解释 | 服务端识别低于支持范围的 Safari，展示升级提示；支持的浏览器在交互加载完成前禁用表单并提示；提交错误显示在表单顶部并聚焦 | `apps/web/src/app/orders/new/page.tsx`、`features/orders/new-order.tsx` |
| 建单页没有图片入口，需先建单再进入详情上传 | 第02部分增加参考图选择；先保存订单，再取得本单会话并携带 CSRF 上传；图片失败保留订单与私有链接，允许单独重试 | `features/orders/new-order.tsx`、`reference-image.ts`、`reference-assets-panel.tsx` |
| 订单详情无法删除上传的参考图 | 新增有订单权限和 CSRF 校验的 DELETE 接口；列表提供可删除状态及原因；确认后删除未用于生产的记录及文件，保护生产中的参考图和历史成品依据 | `apps/server/src/company/api/assets.py`、`services/assets.py`、`schemas/assets.py`；前端参考图面板 |
| 聊天发送后缺少反馈，转人工导致自动回复暂停时原因不明显 | 增加消息已保存与处理状态提示；订单摘要返回人工暂停状态，聊天区和获客阶段进度明确显示暂停及恢复条件 | `services/orders.py`、`schemas/orders.py`；`seller-workspace.tsx`、`workflow-progress.ts` |
| 负责人审核入口不明显 | 公共页面底部增加“负责人入口”，链接到已有登录页 | `apps/web/src/components/portal-shell.tsx` |
| 人工证据录入、手动启动生产容易混淆负责人和 Agent 职责 | 移除上述操作入口，研究依据与版本内容改为只读查看；保留放行、否决和版本对比，接单批准后仍自动推进 Agent 产线 | `features/internal/production-panel.tsx` 及样式 |
| 负责人不需要手动登记处理时间 | 删除前端计时逻辑、登记表单及工时展示；保留模型用量和受控技术恢复，不删除后端历史数据 | `features/operations/usage-panel.tsx` 及样式 |
| 接口契约、使用说明和验收需与页面改动一致 | 同步 OpenAPI 和前端类型；增加建单、删除及人工暂停/恢复的页面测试，修正版本约束编辑测试的等待时序，更新使用指南与负责人职责 | `contracts/openapi.json`、`apps/web/src/lib/generated/api.d.ts`、前后端测试、`docs/`、`README.md` |

表中省略的前端 `features/` 路径均位于 `apps/web/src/features/`，后端 `services/`、`schemas/` 位于 `apps/server/src/company/`。

### 验证

- 后端包含真实 PostgreSQL 的完整测试：133 项通过；新增删除权限、文件移除、生产占用及历史依据保护测试。
- 页面验收：建单/图片/聊天改动后 11 组通过；移除人工录入和计时后，再运行完整双角色闭环 1 组通过，包含对应入口已移除的断言。
- 前端 lint、typecheck、隔离生产构建、Python Ruff 和 `git diff --check` 通过。
- 自动测试使用模拟模型响应，没有调用真实 MiMo；浏览器使用独立测试库与临时 Chrome。

### 运行影响与边界

- 本次新增后端删除接口和摘要字段，需要重启 `company-api`；前端开发服务刷新页面即可加载改动。没有数据库结构变更，不需要迁移；worker 可保持运行。
- Safari 16.3 的处理是明确提示升级，不代表已让该旧版本支持完整交互；使用 Safari 16.4+ 或新版 Chrome/Edge。
- 研究 Agent 当前分析已有资料与证据，自动采集竞品信息尚未接入。移除人工录入入口不代表已实现自动市场调研。
- Amazon 仍为明确标记的模拟发布，未接入真实 SP-API；图片仍采用参考图与确定性排版。

## 2026-11-07 — `1c17d0a`

提交：`Fix mocked MiMo tests under CI fake mode`

**问题：** Windows、macOS 的离线检查及 Ubuntu PostgreSQL 检查都在同两项测试失败。CI 设置 `MIMO_MODE=fake`，测试却直接构造要求 `live` 的 MiMo SDK 适配器。

**修改：** 仅调整 `apps/server/tests/test_model_usage.py`，显式设置测试环境、`mimo_mode="live"`、测试密钥和 `offline.invalid` 地址；继续使用 `httpx.MockTransport` 模拟网络响应，验证被拒绝响应仍计入用量并安全记录日志。

**验证：** 使用 CI 相同环境变量，本机离线测试 55 项通过、75 项集成测试按预期跳过；包含 PostgreSQL 的完整测试 130 项通过，Ruff 通过。这些是本机验证结果，不代替 GitHub 三平台新一轮检查结果。

**运行影响：** 仅测试代码变更，无需重启应用、修改模型配置或迁移数据库。

## 2026-11-07 — `248850f`

提交：`Initial project implementation`

**目标：** 从零建立可在 Mac 本机演示的 AI 电商运营公司，完成 M0–M7 的真实 MiMo 与模拟上架闭环。

**主要实现：**

- Next.js 前端、FastAPI 后端、独立 LangGraph worker 和原生 PostgreSQL；提供环境初始化、迁移、依赖锁及跨平台 CI 配置。
- 卖家建单、私有访问、资料补充及事实确认；负责人登录、接单决策和权限隔离。
- 获客、研究、生成、审校、发布等 Agent 职责，持久化任务、等待恢复、用量记录及受控返修。
- 实际图片上传/存储、参考图排版、图文审校、不可变版本、负责人放行/否决、卖家 ZIP 下载。
- 卖家反馈、规格修改、价格和 SKU 确认、明确标为模拟的刊登结果；人工客服接管、回复、结束和技术恢复。
- 临时 HTTPS 演示、全站入口密码、独立演示库与生产前端；提供运行、用户验收及文件清单文档。

**当时验证：** 本机后端 130 项、浏览器 5 组、公网 HTTP 9 组及公网 Chrome 登录/移动布局检查通过；前端检查与构建通过。CI 三平台随后暴露的测试环境问题见下一条提交。

**边界：** Windows 尚未实机验收；临时公网出现过超时，不是长期稳定托管；真实 Amazon、自动竞品采集和外部生成式修图未接入。首次提交包含的人工证据录入和工时界面已在当前待提交变更中移除。
