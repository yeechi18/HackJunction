import { readFile } from "node:fs/promises";
import { registerSeller } from "./seller-account";
import { expect, test } from "@playwright/test";

async function fillOrder(page: import("@playwright/test").Page) {
  await registerSeller(page.request);
  await page.goto("/orders/new");
  await page.getByLabel("商品明细信息", { exact: false }).fill("黑色硅胶理线夹，适合整理桌面线缆。");
  await page.getByLabel(/商品名字/).fill(`虚构参考图理线夹 ${Date.now()}`);
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

test("脚本未加载时提示启用脚本且不渲染建单表单", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL, javaScriptEnabled: false, userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15" });
  try {
    const page = await context.newPage();
    await page.goto("/orders/new");
    await expect(page.getByText(/请确保已启用 JavaScript/)).toBeVisible();
    await expect(page.locator("form.intake-form")).toHaveCount(0);
    await page.goto("/seller/register");
    await expect(page.getByLabel("邮箱", { exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: /验证邮箱并注册/ })).toBeDisabled();
    await expect(page.locator("form.login-form")).toHaveAttribute("method", "post");
  } finally { await context.close(); }
});

test("五项建单表单自动关联账号、保存商品明细与链接并上传参考图", async ({ page }) => {
  test.skip(!process.env.E2E_REFERENCE_IMAGE, "需要隔离测试栈提供演示参考图");
  await fillOrder(page);
  await expect(page.getByLabel(/你的称呼/)).toHaveCount(0);
  await expect(page.getByLabel(/联系方式/)).toHaveCount(0);
  await expect(page.locator(".form-section")).toHaveCount(2);
  await expect(page.locator(".intake-form input:not([type=checkbox]), .intake-form textarea")).toHaveCount(5);
  await page.getByLabel(/国内平台商品链接/).fill("https://item.jd.com/100000000000.html");
  await page.getByLabel(/补充商品说明与期望卖点/).fill("希望重点说明桌面收纳用途，不添加未经证实的承诺。");
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
  await expect(page.getByText("黑色硅胶理线夹，适合整理桌面线缆。", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "https://item.jd.com/100000000000.html" })).toHaveAttribute("href", "https://item.jd.com/100000000000.html");
  await expect(page.getByText("希望重点说明桌面收纳用途，不添加未经证实的承诺。", { exact: true })).toBeVisible();
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


test("商品图可粘贴，空白明细提示错误且不创建订单", async ({ page }) => {
  test.skip(!process.env.E2E_REFERENCE_IMAGE, "需要演示参考图");
  await fillOrder(page);
  const image = await readFile(process.env.E2E_REFERENCE_IMAGE!);
  await page.getByLabel(/商品明细信息/).evaluate((element, content) => {
    const bytes = Uint8Array.from(atob(content), value => value.charCodeAt(0));
    const clipboard = new DataTransfer();
    clipboard.items.add(new File([bytes], "pasted-reference.png", { type: "image/png" }));
    element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: clipboard, bubbles: true, cancelable: true }));
  }, image.toString("base64"));
  await expect(page.getByText("pasted-reference.png", { exact: true })).toBeVisible();
  await page.getByLabel(/商品明细信息/).fill("   ");
  await page.getByRole("button", { name: /保存资料，创建订单/ }).click();
  await expect(page.locator("form").getByRole("alert")).toContainText("请填写商品名字和已知的商品明细");
  await page.getByLabel(/商品明细信息/).fill("虚构硅胶理线夹，已知黑色，尺寸待补充。");
  await page.getByRole("button", { name: /保存资料，创建订单/ }).click();
  await expect(page.getByText("商品参考图已上传，可在订单中查看。")).toBeVisible();
});

test("球衣资料无需固定参数核实，负责人能查看初审并决定接单", async ({ page, browser, baseURL }) => {
  await registerSeller(page.request);
  await page.goto("/orders/new");
  await expect(page.locator(".intake-form input[type=checkbox]")).toHaveCount(0);
  await page.getByLabel(/商品名字/).fill(`梅西主题球衣 ${Date.now()}`);
  await page.getByLabel(/商品明细信息/).fill("蓝白，梅西，世界杯");
  await page.getByLabel(/补充商品说明与期望卖点/).fill("面向阿根廷球迷，文案简洁。");
  await page.getByRole("button", { name: /保存资料，创建订单/ }).click();
  const link = new URL(await page.getByLabel("私有订单链接").inputValue());
  const orderId = link.pathname.split("/").pop();
  await page.getByRole("link", { name: /查看订单进度/ }).click();
  await expect(page.getByRole("article", { name: "获客初审与文案方向" })).toBeVisible();
  const response = await page.request.get(`/api/v1/orders/${orderId}`);
  const order = await response.json();
  expect(order.phase).toBe("AWAITING_ACCEPTANCE");
  expect(order.facts).toEqual([]);
  expect(order.intake_analysis.content_brief.english_title).toBeTruthy();
  expect(order.intake_analysis.questions).toEqual([]);
  const context = await browser.newContext({ baseURL });
  try {
    const owner = await context.newPage();
    await owner.goto("/internal/login");
    await owner.getByLabel("登录密码").fill(process.env.E2E_OWNER_PASSWORD!);
    await owner.getByRole("button", { name: /登录工作台/ }).click();
    await owner.waitForURL("**/internal/orders");
    await owner.goto(`/internal/orders/${orderId}`);
    await expect(owner.getByRole("button", { name: "提交接单批准", exact: true })).toBeEnabled();
    await expect(owner.getByRole("button", { name: "提交拒绝接单", exact: true })).toBeEnabled();
    await expect(owner.getByRole("article", { name: "获客初审与文案方向" })).toBeVisible();
    const readonly = owner.getByRole("region", { name: "商品图片与参考素材" });
    await expect(readonly.locator('input[type="file"]')).toHaveCount(0);
    const denied = await owner.request.post(`/api/v1/orders/${orderId}/assets`, { headers: { "X-CSRF-Token": (await (await owner.request.get("/api/v1/internal/auth/session")).json()).csrf_token }, multipart: { file: { name: "test.png", mimeType: "image/png", buffer: Buffer.from("not-an-image") } } });
    expect(denied.status()).toBe(403);
    await owner.getByRole("button", { name: "提交接单批准", exact: true }).click();
    await expect.poll(async () => (await (await owner.request.get(`/api/v1/internal/orders/${orderId}/production`)).json()).research.length).toBeGreaterThan(0);
    await expect.poll(async () => (await (await owner.request.get(`/api/v1/internal/orders/${orderId}/production`)).json()).versions.length).toBeGreaterThan(0);
  } finally { await context.close(); }
});
