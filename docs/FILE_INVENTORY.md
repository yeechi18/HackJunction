# 项目文件与代码量核对

统计时间：2026-11-07 07:37（北京时间）。

**不是一千多行。业务源码合计 14,970 行（含 CSS）；不含 CSS 的 Python、TypeScript/TSX 与提示词为 12,368 行。加测试、迁移、脚本、配置和 JSON 夹具为 22,825 行。自动生成文件与文档另列，不用于充当业务实现代码量。**

这是上述统计时刻的历史文件快照，不是 Git 修改量；统计时尚未初始化 Git。此后项目已建立 Git 历史，后续文件总量和功能可能变化，逐次变更以根目录 `CHANGELOG.md` 与 Git 历史为准，不能将本快照的总行数说成本轮 Git diff。清单包含原有参考文档以及新建或修改的项目文件。

| 类别 | 文件数 | 物理行数 |
|---|---:|---:|
| 后端业务Python | 81 | 10,422 |
| Agent提示词 | 6 | 54 |
| 前端业务与样式 | 43 | 4,494 |
| 测试 | 23 | 5,398 |
| 数据库迁移 | 10 | 831 |
| 运行/测试脚本 | 10 | 1,295 |
| 工程配置/CI | 11 | 309 |
| 测试夹具JSON | 2 | 22 |
| 自动生成契约/类型 | 3 | 7,563 |
| 文档/说明 | 13 | 1,905 |
| 合计 | 202 | 32,293 |

前端进一步拆分：TypeScript/TSX 35 个文件、1,892 行；CSS 8 个文件、2,602 行。TSX 中多处 JSX 在同一物理行，物理行数不能直接代表功能数量或复杂度。仅 `apps/server/src/company/services/workflow.py` 就有 1,187 行。

统计口径：UTF-8 文本按 `splitlines()` 计算，包含空行和注释；不含本清单自身，不含第三方依赖、`.venv`、`.next`、`var`、测试运行结果、缓存、锁文件、图片/PDF等二进制材料；不读取私有 `.env`。生成项包括 OpenAPI、前端 API 类型和 Next.js 类型入口。`scripts/lib/demo-gateway.test.mjs`归测试；启动、环境检查和手动烟测脚本归“运行/测试脚本”。原需求与参考文档在“文档”中，不表示它们全部由本轮新增。

## 主要实现位置

| 位置 | 当前实现 |
|---|---|
| `apps/web/src/app/`、`features/orders/` | 卖家建单、私有访问、初审与说明补充、参考图上传、交付预览/ZIP、反馈、售价与SKU确认、账号内模拟刊登记录 |
| `apps/web/src/features/internal/` | 负责人登录、订单列表、只读证据、研究/审校/版本对比、按当前版本放行或否决 |
| `apps/web/src/features/operations/` | 人工接管/回复/结束、实际处理计时、模型用量、技术故障恢复 |
| `apps/server/src/company/api/`、`services/` | API权限、订单状态、持久任务、版本/交付/发布及支持业务 |
| `apps/server/src/company/agents/`、`prompts/`、`workflows/` | 获客、研究、生成、审校、发布五种职责及反馈整理；LangGraph等待、恢复、受控返修 |
| `apps/server/src/company/providers/`、`tools/` | MiMo真实接入与离线测试、用量计账、图片排版、私有存储、ZIP与模拟发布网关 |
| `apps/server/src/company/persistence/`、`schemas/`、`alembic/` | PostgreSQL模型、输入/输出契约、8个阶段迁移与初始化 |
| `apps/server/tests/`、`apps/web/e2e/` | 规则、真实PostgreSQL集成、完整双身份浏览器验收 |
| `scripts/public-demo.mjs`、`scripts/lib/demo-gateway.mjs` | 临时公网演示进程与带入口认证的网关 |
| `scripts/test-public-demo.mjs`、`scripts/lib/demo-gateway.test.mjs` | 公网演示HTTP烟测、网关认证和转发规则测试 |
| 其他 `scripts/`、`.github/workflows/` | 环境初始化/检查、模型探针、隔离测试栈与跨平台CI配置 |

当前验收证据以 `docs/PROGRESS.md` 为准，个人测试步骤见 `docs/USER_TEST_GUIDE.md`。临时公网试用的范围和生命周期见 `deploy/README.md`，临时可访问不等于已完成长期稳定的生产部署。

现有功能边界：发布到明确标记的模拟网关，尚未接真实 Amazon SP-API；图片为参考图加确定性排版，未接外部生成式修图服务；Windows兼容代码与CI文件已提供，不能当作Windows实机测试已通过。

<details>
<summary>完整文本文件清单（按类别）</summary>

### 后端业务Python

| 文件 | 行数 |
|---|---:|
| `apps/server/src/company/__init__.py` | 4 |
| `apps/server/src/company/agents/__init__.py` | 1 |
| `apps/server/src/company/agents/acquisition.py` | 201 |
| `apps/server/src/company/agents/feedback.py` | 135 |
| `apps/server/src/company/agents/generation.py` | 35 |
| `apps/server/src/company/agents/publishing.py` | 25 |
| `apps/server/src/company/agents/research.py` | 123 |
| `apps/server/src/company/agents/review.py` | 302 |
| `apps/server/src/company/api/__init__.py` | 2 |
| `apps/server/src/company/api/access.py` | 54 |
| `apps/server/src/company/api/assets.py` | 131 |
| `apps/server/src/company/api/auth.py` | 58 |
| `apps/server/src/company/api/deliverables.py` | 35 |
| `apps/server/src/company/api/dependencies.py` | 33 |
| `apps/server/src/company/api/health.py` | 46 |
| `apps/server/src/company/api/internal.py` | 47 |
| `apps/server/src/company/api/orders.py` | 127 |
| `apps/server/src/company/api/production.py` | 275 |
| `apps/server/src/company/api/publication.py` | 112 |
| `apps/server/src/company/api/support.py` | 103 |
| `apps/server/src/company/cli/__init__.py` | 2 |
| `apps/server/src/company/cli/export_openapi.py` | 30 |
| `apps/server/src/company/cli/init_admin.py` | 77 |
| `apps/server/src/company/cli/init_db.py` | 64 |
| `apps/server/src/company/cli/seed_demo.py` | 29 |
| `apps/server/src/company/cli/serve.py` | 25 |
| `apps/server/src/company/cli/smoke_mimo.py` | 179 |
| `apps/server/src/company/config.py` | 82 |
| `apps/server/src/company/domain/__init__.py` | 1 |
| `apps/server/src/company/domain/facts.py` | 8 |
| `apps/server/src/company/domain/states.py` | 38 |
| `apps/server/src/company/main.py` | 57 |
| `apps/server/src/company/persistence/__init__.py` | 2 |
| `apps/server/src/company/persistence/agent_runs.py` | 30 |
| `apps/server/src/company/persistence/assets.py` | 30 |
| `apps/server/src/company/persistence/database.py` | 82 |
| `apps/server/src/company/persistence/deliveries.py` | 34 |
| `apps/server/src/company/persistence/models.py` | 134 |
| `apps/server/src/company/persistence/production.py` | 115 |
| `apps/server/src/company/persistence/publication.py` | 99 |
| `apps/server/src/company/persistence/schema.py` | 14 |
| `apps/server/src/company/persistence/support.py` | 74 |
| `apps/server/src/company/providers/__init__.py` | 2 |
| `apps/server/src/company/providers/factory.py` | 15 |
| `apps/server/src/company/providers/fake.py` | 309 |
| `apps/server/src/company/providers/images.py` | 65 |
| `apps/server/src/company/providers/mimo.py` | 170 |
| `apps/server/src/company/providers/publishers/__init__.py` | 1 |
| `apps/server/src/company/providers/publishers/base.py` | 27 |
| `apps/server/src/company/providers/publishers/simulated.py` | 91 |
| `apps/server/src/company/providers/storage.py` | 124 |
| `apps/server/src/company/providers/usage.py` | 90 |
| `apps/server/src/company/runtime.py` | 12 |
| `apps/server/src/company/schemas/__init__.py` | 1 |
| `apps/server/src/company/schemas/assets.py` | 25 |
| `apps/server/src/company/schemas/commands.py` | 43 |
| `apps/server/src/company/schemas/deliveries.py` | 47 |
| `apps/server/src/company/schemas/listings.py` | 58 |
| `apps/server/src/company/schemas/orders.py` | 144 |
| `apps/server/src/company/schemas/publication.py` | 112 |
| `apps/server/src/company/schemas/research.py` | 56 |
| `apps/server/src/company/schemas/reviews.py` | 34 |
| `apps/server/src/company/schemas/support.py` | 90 |
| `apps/server/src/company/services/__init__.py` | 1 |
| `apps/server/src/company/services/access.py` | 112 |
| `apps/server/src/company/services/assets.py` | 162 |
| `apps/server/src/company/services/delivery.py` | 586 |
| `apps/server/src/company/services/feedback_workflow.py` | 149 |
| `apps/server/src/company/services/image_workflow.py` | 335 |
| `apps/server/src/company/services/model_budget.py` | 80 |
| `apps/server/src/company/services/orders.py` | 462 |
| `apps/server/src/company/services/production.py` | 712 |
| `apps/server/src/company/services/publication.py` | 962 |
| `apps/server/src/company/services/support.py` | 593 |
| `apps/server/src/company/services/workflow.py` | 1,187 |
| `apps/server/src/company/tools/build_package.py` | 99 |
| `apps/server/src/company/tools/image_layout.py` | 195 |
| `apps/server/src/company/worker.py` | 89 |
| `apps/server/src/company/workflows/__init__.py` | 1 |
| `apps/server/src/company/workflows/graph.py` | 116 |
| `apps/server/src/company/workflows/state.py` | 10 |

### Agent提示词

| 文件 | 行数 |
|---|---:|
| `apps/server/src/company/prompts/acquisition.md` | 16 |
| `apps/server/src/company/prompts/feedback.md` | 6 |
| `apps/server/src/company/prompts/generation.md` | 11 |
| `apps/server/src/company/prompts/publishing.md` | 3 |
| `apps/server/src/company/prompts/research.md` | 7 |
| `apps/server/src/company/prompts/review.md` | 11 |

### 前端业务与样式

| 文件 | 行数 |
|---|---:|
| `apps/web/src/app/demo-listings/[publicId]/not-found.tsx` | 7 |
| `apps/web/src/app/demo-listings/[publicId]/page.tsx` | 67 |
| `apps/web/src/app/globals.css` | 1,880 |
| `apps/web/src/app/internal/login/page.tsx` | 9 |
| `apps/web/src/app/internal/orders/[id]/page.tsx` | 10 |
| `apps/web/src/app/internal/orders/page.tsx` | 9 |
| `apps/web/src/app/layout.tsx` | 12 |
| `apps/web/src/app/o/[orderId]/page.tsx` | 10 |
| `apps/web/src/app/orders/new/page.tsx` | 9 |
| `apps/web/src/app/page.tsx` | 38 |
| `apps/web/src/app/portal.css` | 415 |
| `apps/web/src/app/system/page.tsx` | 10 |
| `apps/web/src/components/async-feedback.tsx` | 13 |
| `apps/web/src/components/brand.tsx` | 5 |
| `apps/web/src/components/portal-shell.tsx` | 6 |
| `apps/web/src/components/product-scene.tsx` | 17 |
| `apps/web/src/components/service-status.tsx` | 53 |
| `apps/web/src/features/internal/agent-runs-panel.tsx` | 52 |
| `apps/web/src/features/internal/auth.tsx` | 44 |
| `apps/web/src/features/internal/order-list.tsx` | 44 |
| `apps/web/src/features/internal/order-workspace.tsx` | 75 |
| `apps/web/src/features/internal/production-panel.module.css` | 80 |
| `apps/web/src/features/internal/production-panel.tsx` | 193 |
| `apps/web/src/features/operations/operations-panel.module.css` | 54 |
| `apps/web/src/features/operations/support-panel.tsx` | 120 |
| `apps/web/src/features/operations/usage-panel.tsx` | 233 |
| `apps/web/src/features/orders/deliverables-panel.module.css` | 35 |
| `apps/web/src/features/orders/deliverables-panel.tsx` | 67 |
| `apps/web/src/features/orders/demo-listing-image.tsx` | 12 |
| `apps/web/src/features/orders/demo-listing.module.css` | 30 |
| `apps/web/src/features/orders/fact-editor.tsx` | 20 |
| `apps/web/src/features/orders/new-order.tsx` | 54 |
| `apps/web/src/features/orders/order-panels.tsx` | 23 |
| `apps/web/src/features/orders/reference-assets-panel.module.css` | 73 |
| `apps/web/src/features/orders/reference-assets-panel.tsx` | 153 |
| `apps/web/src/features/orders/seller-publication-panel.module.css` | 35 |
| `apps/web/src/features/orders/seller-publication-panel.tsx` | 185 |
| `apps/web/src/features/orders/seller-workspace.tsx` | 74 |
| `apps/web/src/features/orders/session.ts` | 28 |
| `apps/web/src/features/orders/types.ts` | 52 |
| `apps/web/src/features/orders/use-workspace.ts` | 60 |
| `apps/web/src/features/orders/workflow-progress.ts` | 46 |
| `apps/web/src/lib/api.ts` | 82 |

### 测试

| 文件 | 行数 |
|---|---:|
| `apps/server/tests/conftest.py` | 62 |
| `apps/server/tests/test_acquisition.py` | 121 |
| `apps/server/tests/test_admin_init.py` | 21 |
| `apps/server/tests/test_assets.py` | 332 |
| `apps/server/tests/test_cli.py` | 29 |
| `apps/server/tests/test_config.py` | 50 |
| `apps/server/tests/test_database_integration.py` | 19 |
| `apps/server/tests/test_delivery.py` | 502 |
| `apps/server/tests/test_delivery_workflow.py` | 446 |
| `apps/server/tests/test_fake_provider.py` | 243 |
| `apps/server/tests/test_feedback_workflow.py` | 519 |
| `apps/server/tests/test_health.py` | 77 |
| `apps/server/tests/test_model_usage.py` | 314 |
| `apps/server/tests/test_orders_integration.py` | 281 |
| `apps/server/tests/test_production_agents.py` | 262 |
| `apps/server/tests/test_production_persistence.py` | 325 |
| `apps/server/tests/test_production_workflow.py` | 367 |
| `apps/server/tests/test_publication.py` | 406 |
| `apps/server/tests/test_support.py` | 364 |
| `apps/server/tests/test_workflow.py` | 302 |
| `apps/web/e2e/closed-loop.spec.ts` | 175 |
| `apps/web/e2e/workspace.spec.ts` | 100 |
| `scripts/lib/demo-gateway.test.mjs` | 81 |

### 数据库迁移

| 文件 | 行数 |
|---|---:|
| `apps/server/alembic/env.py` | 55 |
| `apps/server/alembic/script.py.mako` | 23 |
| `apps/server/alembic/versions/0001_m1_schema_marker.py` | 23 |
| `apps/server/alembic/versions/0002_m2_intake.py` | 172 |
| `apps/server/alembic/versions/0003_m3_agent_runs.py` | 44 |
| `apps/server/alembic/versions/0004_m4_production.py` | 177 |
| `apps/server/alembic/versions/0005_m5_assets.py` | 41 |
| `apps/server/alembic/versions/0006_m5_deliveries.py` | 46 |
| `apps/server/alembic/versions/0007_m6_publication.py` | 136 |
| `apps/server/alembic/versions/0008_m7_support.py` | 114 |

### 运行/测试脚本

| 文件 | 行数 |
|---|---:|
| `scripts/check-env.mjs` | 47 |
| `scripts/init-env.mjs` | 23 |
| `scripts/lib/demo-gateway.mjs` | 98 |
| `scripts/public-demo.mjs` | 200 |
| `scripts/run-e2e.mjs` | 24 |
| `scripts/smoke-closed-loop.py` | 340 |
| `scripts/smoke-intake.py` | 100 |
| `scripts/test-public-demo.mjs` | 236 |
| `scripts/test-stack.mjs` | 191 |
| `scripts/visual-smoke.mjs` | 36 |

### 工程配置/CI

| 文件 | 行数 |
|---|---:|
| `.env.example` | 14 |
| `.github/workflows/check.yml` | 103 |
| `.gitignore` | 18 |
| `apps/server/alembic.ini` | 5 |
| `apps/server/pyproject.toml` | 57 |
| `apps/web/.env.local.example` | 1 |
| `apps/web/eslint.config.mjs` | 9 |
| `apps/web/next.config.ts` | 20 |
| `apps/web/package.json` | 33 |
| `apps/web/playwright.config.ts` | 28 |
| `apps/web/tsconfig.json` | 21 |

### 测试夹具JSON

| 文件 | 行数 |
|---|---:|
| `fixtures/demo/evidence.json` | 8 |
| `fixtures/demo/product.json` | 14 |

### 自动生成契约/类型

| 文件 | 行数 |
|---|---:|
| `apps/web/next-env.d.ts` | 7 |
| `apps/web/src/lib/generated/api.d.ts` | 3,009 |
| `contracts/openapi.json` | 4,547 |

### 文档/说明

| 文件 | 行数 |
|---|---:|
| `README.md` | 107 |
| `apps/web/AGENTS.md` | 9 |
| `deploy/README.md` | 49 |
| `docs/DEMO_RUNBOOK.md` | 74 |
| `docs/DEVELOPMENT_PLAN.md` | 268 |
| `docs/IMPLEMENTATION_ARCHITECTURE.md` | 322 |
| `docs/MiMo-Agent-Examples.md` | 202 |
| `docs/PROGRESS.md` | 43 |
| `docs/PROJECT_OVERVIEW.md` | 74 |
| `docs/PROJECT_PLAN_INTEGRATION.md` | 463 |
| `docs/REFERENCE_DECISIONS.md` | 19 |
| `docs/USER_TEST_GUIDE.md` | 60 |
| `docs/调研部分.md` | 215 |

</details>

商品初审新增`schemas/intake.py`结构化分析、`providers/product_page.py`受限公开页面读取、`services/intake_sources.py`真实图片/页面输入准备；前端`features/orders/intake-analysis.tsx`两端共享展示。
