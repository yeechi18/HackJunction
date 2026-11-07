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

test("首页与状态页可访问，移动端能进入上架对话", async ({ page }) => {
  await page.goto("/");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("link", { name: /选择此服务，开始上架/ }).click();
  await expect(page).toHaveURL(/\/orders\/new$/);
  await expect(page.getByLabel(/你的称呼/)).toBeVisible();
  await expect(page.getByRole("button", { name: /确认资料，提交接单申请/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.goto("/system");
  await expect(page.getByRole("heading", { name: /基础服务状态/ })).toBeVisible();
  await expect(page.getByText("API 进程", { exact: true })).toBeVisible();
});

test("虚构订单支持不完整建单、私有会话恢复、消息与资料补充", async ({ page, browser }) => {
  const suffix = `${Date.now()}-${Math.random().toString(16).slice(2, 7)}`;
  const productName = `Playwright 虚构理线夹 ${suffix}`;
  await page.goto("/orders/new");
  await page.getByLabel(/你的称呼/).fill("Playwright 演示卖家");
  await page.route("**/api/v1/orders/intake-chat", route => {
    expect(route.request().headers()["content-type"]).toContain("multipart/form-data");
    expect(route.request().postData()).toContain("filename=\"products.csv\"");
    expect(route.request().postData()).toContain(productName);
    return route.fulfill({ json: {
      reply: "已整理商品名和材质。",
      updates: { product_name: productName, material: "硅胶" },
    } });
  });
  await page.getByLabel("商品数据包", { exact: true }).setInputFiles({
    name: "products.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(`标题,材质\n${productName},硅胶`),
  });
  await expect(page.getByText("products.csv · 待发送")).toBeVisible();
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByLabel(/中文商品名/)).toHaveValue(productName);
  await expect(page.getByLabel(/^材质/)).toHaveValue("硅胶");
  await expect(page.getByLabel(/^尺寸/)).toHaveValue("");
  await page.getByRole("checkbox", { name: "我已核对已填写的商品资料" }).check();
  await page.getByText("其他资料（选填）", { exact: true }).click();
  await page.getByRole("checkbox", { name: "这是一份虚构演示订单" }).check();
  await page.getByRole("button", { name: /确认资料，提交接单申请/ }).click();
  await expect(page.getByRole("heading", { name: /资料已保存/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  const privateUrl = new URL(await page.getByLabel("私有订单链接").inputValue());
  expect(privateUrl.hash.startsWith("#token=")).toBe(true);
  await page.getByRole("link", { name: /查看订单进度/ }).click();
  await page.waitForURL(url => url.pathname === privateUrl.pathname);
  await expect(page).toHaveURL(url => url.pathname === privateUrl.pathname && !url.hash);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(productName);
  await expect(page.getByText("卖家已核实", { exact: true })).toBeVisible();
  await expect(page.getByRole("status", { name: "订单进度", exact: true })).toBeVisible();
  await expect(page.getByRole("status", { name: "订单进度", exact: true })).toHaveAttribute("data-workflow-status", /queued|running|waiting_for_seller|waiting_for_owner|failed/);

  const message = `演示消息 ${suffix}`;
  await page.getByLabel("给这份订单留个消息").fill(message);
  await page.getByRole("button", { name: /发送消息/ }).click();
  await expect(page.getByText(message, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /补充商品资料/ }).click();
  const dimensions = page.getByLabel(/^尺寸/);
  await dimensions.fill("4 × 3 × 2");
  const row = page.locator(".fact-edit-row").filter({ has: dimensions });
  await row.getByLabel("单位", { exact: true }).fill("cm");
  await row.getByRole("checkbox").check();
  await page.getByRole("button", { name: "保存补充资料", exact: true }).click();
  await expect(page.getByText("补充资料已保存，等待重新审查。", { exact: true })).toBeVisible();
  await expect(page.getByText("4 × 3 × 2 cm", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(productName);
  await expect(page.getByText("4 × 3 × 2 cm", { exact: true })).toBeVisible();
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
  const response = await request.post("/api/v1/orders", { data: { seller_name: "Playwright 内部演示卖家", product_name: productName, facts: [], is_demo: true } });
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
  await expect(page.getByRole("button", { name: "提交接单批准", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /退出登录/ }).click();
  await expect(page).toHaveURL(/\/internal\/login$/);
  await page.goto("/internal/orders");
  await expect(page.getByRole("heading", { name: "请先登录工作台" })).toBeVisible();
});
