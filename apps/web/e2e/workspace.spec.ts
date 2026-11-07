import { registerSeller } from "./seller-account";
import { expect, test } from "@playwright/test";

test("登录表单在脚本未加载时禁止提交密码", async ({ browser, baseURL }) => {
  const isolated = await browser.newContext({ javaScriptEnabled: false, baseURL });
  try {
    const page = await isolated.newPage();
    await page.goto("/internal/login");
    await expect(page.getByLabel("登录密码")).toBeDisabled();
    await expect(page.getByRole("button", { name: /登录工作台/ })).toBeDisabled();
    await expect(page.locator("form.login-form")).toHaveAttribute("method", "post");
  } finally { await isolated.close(); }
});

test("首页与状态页可访问，移动端能进入商品资料表单", async ({ page }) => {
  await registerSeller(page.request);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("说好英语");
  await expect(page.getByText("虚构演示样例", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("link", { name: /提交商品资料/ }).click();
  await expect(page).toHaveURL(/\/orders\/new$/);
  await expect(page.getByLabel(/商品明细信息/)).toBeVisible();
  await expect(page.getByRole("button", { name: /保存资料，创建订单/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.goto("/system");
  await expect(page.getByRole("heading", { name: /基础服务状态/ })).toBeVisible();
  await expect(page.getByText("API 进程", { exact: true })).toBeVisible();
});

test("虚构订单支持不完整建单、私有会话恢复、消息与资料补充", async ({ page, browser }) => {
  const suffix = `${Date.now()}-${Math.random().toString(16).slice(2, 7)}`;
  const productName = `Playwright 虚构理线夹 ${suffix}`;
  await registerSeller(page.request);
  await page.goto("/orders/new");
  await page.getByLabel("商品明细信息", { exact: false }).fill("黑色硅胶理线夹，适合整理桌面线缆。");
  await page.getByLabel(/商品名字/).fill(productName);
  await page.getByRole("button", { name: /保存资料，创建订单/ }).click();
  await expect(page.getByRole("heading", { name: /资料已保存/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  const privateUrl = new URL(await page.getByLabel("私有订单链接").inputValue());
  expect(privateUrl.hash.startsWith("#token=")).toBe(true);
  await page.getByRole("link", { name: /查看订单进度/ }).click();
  await page.waitForURL(url => url.pathname === privateUrl.pathname);
  await expect(page).toHaveURL(url => url.pathname === privateUrl.pathname && !url.hash);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(productName);
  await expect(page.getByText("黑色硅胶理线夹，适合整理桌面线缆。", { exact: true })).toBeVisible();
  await expect(page.getByRole("status", { name: "订单进度", exact: true })).toBeVisible();
  await expect(page.getByRole("status", { name: "订单进度", exact: true })).toHaveAttribute("data-workflow-status", /queued|running|waiting_for_seller|waiting_for_owner|failed/);

  const message = `演示消息 ${suffix}`;
  await page.getByLabel("给这份订单留个消息").fill(message);
  await page.getByRole("button", { name: /发送消息/ }).click();
  await expect(page.getByText(message, { exact: true })).toBeVisible();
  // Let the queued intake finish before opening a revision-bound editing form.
  let revision = 0;
  await expect.poll(async () => {
    const response = await page.request.get(`/api/v1/orders/${privateUrl.pathname.split("/").pop()}`);
    const order = await response.json();
    revision = order.revision;
    return order.workflow_status;
  }).toBe("waiting_for_owner");
  await page.locator(".workspace-toolbar").getByRole("button", { name: /刷新/ }).click();
  await expect(page.locator(".order-identity")).toContainText(`资料版本 ${revision}`);
  await page.getByRole("button", { name: /补充商品资料/ }).click();
  await expect(page.locator(".supplement-form .fact-edit-row")).toHaveCount(0);
  await page.getByLabel("补充说明", { exact: true }).fill("参考尺寸4 × 3 × 2 cm，文案简洁，避免夸大性能。");
  await page.getByRole("button", { name: "保存补充资料", exact: true }).click();
  await expect(page.getByText("补充资料已保存，等待重新审查。", { exact: true })).toBeVisible();
  await expect(page.getByText("参考尺寸4 × 3 × 2 cm，文案简洁，避免夸大性能。", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(productName);
  await expect(page.getByText("参考尺寸4 × 3 × 2 cm，文案简洁，避免夸大性能。", { exact: true })).toBeVisible();
  expect(new URL(page.url()).hash).toBe("");

  const isolated = await browser.newContext();
  try {
    const visitor = await isolated.newPage();
    await visitor.goto(`${privateUrl.origin}${privateUrl.pathname}`);
    await expect(visitor.getByRole("heading", { name: "暂时无法打开这份订单" })).toBeVisible();
    await expect(visitor.getByRole("heading", { level: 1 })).toHaveCount(0);
  } finally { await isolated.close(); }
});

test("内部入口要求登录，负责人可查看演示订单并退出", async ({ page, request }) => {
  const password = process.env.E2E_OWNER_PASSWORD;
  test.skip(!password, "设置 E2E_OWNER_PASSWORD 后验证真实负责人登录；测试不读取或展示本地管理员凭据。");
  const productName = `Playwright 内部演示 ${Date.now()}`;
  const account = await registerSeller(request);
  const response = await request.post("/api/v1/orders", { headers: { "X-CSRF-Token": account.csrf_token }, data: { product_name: productName, product_details: "已知硅胶材质，尺寸待核实。" } });
  expect(response.status()).toBe(201);
  await page.goto("/internal/orders");
  await expect(page.getByRole("heading", { name: "请先登录工作台" })).toBeVisible();
  await page.getByRole("link", { name: /前往登录/ }).click();
  await page.getByLabel("登录密码").fill(password!);
  await page.getByRole("button", { name: /登录工作台/ }).click();
  await expect(page).toHaveURL(/\/internal\/orders$/);
  await page.getByLabel("搜索订单", { exact: true }).fill(productName);
  await page.getByRole("link", { name: new RegExp(productName) }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(productName);
  await expect(page.getByRole("heading", { name: "卖家信息", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "提交接单批准", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "提交拒绝接单", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: /退出登录/ }).click();
  await expect(page).toHaveURL(/\/internal\/login$/);
  await page.goto("/internal/orders");
  await expect(page.getByRole("heading", { name: "请先登录工作台" })).toBeVisible();
});
