import { expect, test } from "@playwright/test";

async function fillOrder(page: import("@playwright/test").Page) {
  await page.goto("/orders/new");
  await page.getByLabel(/你的称呼/).fill("建单验收虚构卖家");
  await page.getByLabel(/中文商品名/).fill(`虚构参考图理线夹 ${Date.now()}`);
  await page.getByRole("checkbox", { name: "这是一份虚构演示订单" }).check();
}

test("旧版Safari在无JavaScript时仍显示兼容性提示", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL, javaScriptEnabled: false, userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15" });
  try {
    const page = await context.newPage();
    await page.goto("/orders/new");
    await expect(page.getByRole("heading", { name: "请更新浏览器后再提交资料" })).toBeVisible();
    await expect(page.getByText(/Safari 16.4 或更新版本/)).toBeVisible();
    await expect(page.locator("form")).toHaveCount(0);
  } finally { await context.close(); }
});

test("脚本未加载时不允许填写或提交建单表单", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL, javaScriptEnabled: false, userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15" });
  try {
    const page = await context.newPage();
    await page.goto("/orders/new");
    await expect(page.getByText(/正在加载表单交互/)).toBeVisible();
    await expect(page.getByLabel(/你的称呼/)).toBeDisabled();
    await expect(page.getByRole("button", { name: /保存资料，创建订单/ })).toBeDisabled();
    await expect(page.locator("form.intake-form")).toHaveAttribute("method", "post");
  } finally { await context.close(); }
});

test("核实勾选随事实变化重置，建单时上传图片并保留私有访问", async ({ page }) => {
  test.skip(!process.env.E2E_REFERENCE_IMAGE, "需要隔离测试栈提供演示参考图");
  await fillOrder(page);
  const material = page.getByLabel(/^材质/);
  const confirmed = page.getByRole("checkbox", { name: "我已核实" }).first();
  await expect(confirmed).toBeDisabled();
  await material.fill("硅胶");
  await confirmed.check();
  await expect(confirmed).toBeChecked();
  await material.fill("silicone");
  await expect(confirmed).not.toBeChecked();
  await confirmed.check();
  await page.getByLabel("商品参考图", { exact: false }).setInputFiles(process.env.E2E_REFERENCE_IMAGE!);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  if (process.env.E2E_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.E2E_ARTIFACT_DIR}/intake-mobile.png`, fullPage: true });
  await page.getByRole("button", { name: /保存资料，创建订单/ }).click();
  await expect(page.getByRole("heading", { name: /资料已保存/ })).toBeVisible();
  await expect(page.getByText("商品参考图已上传，可在订单中查看。")).toBeVisible();
  const url = new URL(await page.getByLabel("私有订单链接").inputValue());
  expect(url.hash).toMatch(/^#token=/);
  await page.getByRole("link", { name: /查看订单进度/ }).click();
  await expect(page).toHaveURL(current => current.pathname === url.pathname && !current.hash);
  await expect(page.getByText("卖家已核实", { exact: true })).toBeVisible();
  const reference = page.locator("article").filter({ has: page.getByRole("heading", { name: "商品参考图", exact: true }) });
  await expect(reference).toHaveCount(1);
  await expect(reference.locator("img")).toBeVisible();
  await reference.getByRole("button", { name: "删除参考图", exact: true }).click();
  await reference.getByRole("button", { name: "取消", exact: true }).click();
  await expect(reference).toHaveCount(1);
  await reference.getByRole("button", { name: "删除参考图", exact: true }).click();
  await reference.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.getByText("参考图已删除。", { exact: true })).toBeVisible();
  await expect(reference).toHaveCount(0);
});

test("建单失败显示反馈且恢复提交按钮，非法图片不提交订单", async ({ page }) => {
  let requests = 0;
  await page.route("**/api/v1/orders", async route => { requests += 1; await route.fulfill({ status: 503, contentType: "application/json", body: "{}" }); });
  await fillOrder(page);
  await page.getByLabel("商品参考图", { exact: false }).setInputFiles({ name: "wrong.txt", mimeType: "text/plain", buffer: Buffer.from("not an image") });
  await page.getByRole("button", { name: /保存资料，创建订单/ }).click();
  await expect(page.getByRole("alert").filter({ hasText: "请选择 PNG、JPEG 或 WebP" }).first()).toBeVisible();
  expect(requests).toBe(0);
  await page.getByRole("button", { name: "清除选择" }).click();
  await page.getByRole("button", { name: /保存资料，创建订单/ }).click();
  await expect(page.getByRole("alert").filter({ hasText: "服务暂时不可用" })).toBeVisible();
  await expect(page.getByRole("button", { name: /保存资料，创建订单/ })).toBeEnabled();
  await expect(page.getByRole("heading", { name: /资料已保存/ })).toHaveCount(0);
  expect(requests).toBe(1);
});

test("图片失败后只重试图片，不重复建单", async ({ page }) => {
  test.skip(!process.env.E2E_REFERENCE_IMAGE, "需要隔离测试栈提供演示参考图");
  let orders = 0;
  let uploads = 0;
  page.on("request", request => { if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/orders") orders += 1; });
  await page.route("**/api/v1/orders/*/assets", async route => {
    if (route.request().method() === "POST" && ++uploads === 1) await route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
    else await route.continue();
  });
  await fillOrder(page);
  await page.getByLabel("商品参考图", { exact: false }).setInputFiles(process.env.E2E_REFERENCE_IMAGE!);
  await page.getByRole("button", { name: /保存资料，创建订单/ }).click();
  await expect(page.getByRole("alert").filter({ hasText: "订单已创建，但参考图未上传成功" })).toBeVisible();
  const link = await page.getByLabel("私有订单链接").inputValue();
  await page.getByRole("button", { name: "重试上传参考图" }).click();
  await expect(page.getByText("商品参考图已上传，可在订单中查看。")).toBeVisible();
  expect(await page.getByLabel("私有订单链接").inputValue()).toBe(link);
  expect(orders).toBe(1);
  expect(uploads).toBe(2);
});
