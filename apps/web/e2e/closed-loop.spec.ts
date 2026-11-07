import { expect, test, type Page } from "@playwright/test";
import { mkdir, stat } from "node:fs/promises";
import path from "node:path";

type BrowserDelivery = { id: string; version_number: number; listing: { title: string } };

const facts = [
  { key: "material", value: "silicone", confirmed: true },
  { key: "pack_quantity", value: "6", unit: "pieces", confirmed: true },
  { key: "dimensions", value: "4 x 3 x 2", unit: "cm", confirmed: true },
  { key: "dimension_scope", value: "each individual cable clip", confirmed: true },
  { key: "cable_diameter", value: "6", unit: "mm", confirmed: true },
  { key: "color", value: "black", confirmed: true },
  { key: "use_cases", value: "Organizing desk cables up to 6 mm in diameter", confirmed: true },
];

async function phase(page: Page, orderId: string) {
  const result = await page.request.get(`/api/v1/orders/${orderId}`);
  expect(result.ok()).toBe(true);
  return (await result.json()).phase;
}

async function screenshot(page: Page, name: string) {
  if (!process.env.E2E_ARTIFACT_DIR) return;
  await mkdir(process.env.E2E_ARTIFACT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(process.env.E2E_ARTIFACT_DIR, name), fullPage: true });
}

test("双角色页面跑通审核交付、反馈返修、模拟发布及人工接管", async ({ page: seller, browser, request }) => {
  test.setTimeout(180000);
  const readiness = await request.get("/api/v1/health/ready");
  expect(readiness.ok()).toBe(true);
  test.skip((await readiness.json()).model_mode !== "fake", "完整页面自动测试只在显式离线模型环境执行。");
  const password = process.env.E2E_OWNER_PASSWORD;
  expect(password).toBeTruthy();
  const runtimeErrors: string[] = [];
  seller.on("pageerror", error => runtimeErrors.push(error.message));
  const created = await request.post("/api/v1/orders", { data: {
    seller_name: "离线页面闭环模拟卖家", product_name: `Fictional Browser Cable Clips ${Date.now()}`,
    facts, is_demo: true,
  } });
  expect(created.status()).toBe(201);
  const order = await created.json();
  await seller.goto(order.private_url);
  await expect(seller).toHaveURL(url => url.pathname === `/o/${order.id}` && !url.hash);
  await seller.getByLabel("上传商品参考图", { exact: true }).setInputFiles(
    process.env.E2E_REFERENCE_IMAGE ?? path.resolve("../../fixtures/demo/reference.png"),
  );
  await seller.getByRole("button", { name: "上传参考图", exact: true }).click();
  await expect(seller.getByText("参考图已保存，可在下方查看。", { exact: true })).toBeVisible();
  await expect.poll(() => phase(seller, order.id), { timeout: 20000 }).toBe("AWAITING_ACCEPTANCE");

  const ownerContext = await browser.newContext({ baseURL: new URL(order.private_url).origin, viewport: { width: 1440, height: 1000 } });
  try {
    const owner = await ownerContext.newPage();
    owner.on("pageerror", error => runtimeErrors.push(error.message));
    await owner.goto("/internal/login");
    await owner.getByLabel("登录密码").fill(password!);
    await owner.getByRole("button", { name: /登录工作台/ }).click();
    await expect(owner).toHaveURL(/\/internal\/orders$/);
    await owner.goto(`/internal/orders/${order.id}`);
    await expect(owner.getByRole("heading", { name: "Agent 研究与内容产物", exact: true })).toBeVisible();
    await owner.getByRole("button", { name: "提交接单批准", exact: true }).click();
    await expect.poll(() => phase(owner, order.id), { timeout: 30000 }).toBe("AWAITING_RELEASE");
    await expect(owner.getByRole("button", { name: "本版放行并交付", exact: true })).toBeEnabled();
    const beforeRelease = await seller.request.get(`/api/v1/orders/${order.id}/deliverables`);
    expect((await beforeRelease.json()).items).toHaveLength(0);
    expect((await seller.request.get(`/api/v1/orders/${order.id}/download`)).status()).toBe(404);

    await owner.getByLabel("否决原因", { exact: true }).fill("演示返修：保持全部事实，让文案更简洁。");
    await owner.getByRole("button", { name: "否决本版", exact: true }).click();
    await expect.poll(async () => {
      const result = await owner.request.get(`/api/v1/internal/orders/${order.id}/production`);
      const value = await result.json();
      return value.versions.length >= 2 && value.versions[0].full_passed;
    }, { timeout: 30000 }).toBe(true);
    await expect.poll(() => phase(owner, order.id)).toBe("AWAITING_RELEASE");
    await expect(owner.getByRole("heading", { name: "当前最新候选 · v2", exact: true })).toBeVisible();
    const productionResponse = await owner.request.get(`/api/v1/internal/orders/${order.id}/production`);
    const producedVersions = (await productionResponse.json()).versions;
    const versionSelector = owner.getByLabel("查看版本", { exact: false });
    await versionSelector.selectOption(producedVersions[producedVersions.length - 1].id);
    await expect.poll(async () => {
      const action = owner.getByRole("button", { name: "本版放行并交付", exact: true });
      return await action.count() === 0 || !await action.isEnabled();
    }).toBe(true);
    await versionSelector.selectOption(producedVersions[0].id);
    await expect(owner.getByRole("button", { name: "本版放行并交付", exact: true })).toBeEnabled();
    await owner.getByRole("button", { name: "本版放行并交付", exact: true }).click();
    await expect.poll(() => phase(seller, order.id)).toBe("AWAITING_SELLER");
    const downloadLink = seller.getByRole("link", { name: "下载本版 ZIP", exact: true }).first();
    await expect(downloadLink).toBeVisible();
    const downloadPromise = seller.waitForEvent("download");
    await downloadLink.click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/^listing-v\d+\.zip$/);
    const downloadPath = await download.path();
    expect(downloadPath).toBeTruthy();
    expect((await stat(downloadPath!)).size).toBeGreaterThan(1000);

    await seller.getByLabel("反馈内容", { exact: true }).fill("请把标题和文案写得更简洁，全部商品事实保持不变。");
    await seller.getByRole("button", { name: "提交修改反馈", exact: true }).click();
    await expect.poll(async () => {
      const result = await owner.request.get(`/api/v1/internal/orders/${order.id}/production`);
      const value = await result.json();
      return value.versions.length >= 3 && value.versions[0].full_passed;
    }, { timeout: 30000 }).toBe(true);
    await expect.poll(() => phase(owner, order.id)).toBe("AWAITING_RELEASE");
    const updatedProduction = await owner.request.get(`/api/v1/internal/orders/${order.id}/production`);
    await versionSelector.selectOption((await updatedProduction.json()).versions[0].id);
    await expect(owner.getByRole("heading", { name: "当前最新候选 · v3", exact: true })).toBeVisible();
    await expect(owner.getByRole("button", { name: "本版放行并交付", exact: true })).toBeEnabled();
    await owner.getByRole("button", { name: "本版放行并交付", exact: true }).click();
    await expect.poll(() => phase(seller, order.id)).toBe("AWAITING_SELLER");
    const deliveredResponse = await seller.request.get(`/api/v1/orders/${order.id}/deliverables`);
    const deliveredVersions: BrowserDelivery[] = (await deliveredResponse.json()).items;
    const deliverySelector = seller.getByLabel("查看交付版本", { exact: false });
    await expect(deliverySelector).toBeVisible();
    const latestDelivery = deliveredVersions.reduce((latest, item) => item.version_number > latest.version_number ? item : latest);
    const oldestDelivery = deliveredVersions.reduce((oldest, item) => item.version_number < oldest.version_number ? item : oldest);
    await deliverySelector.selectOption(oldestDelivery.id);
    await expect(seller.getByRole("button", { name: "确认本版并模拟上架", exact: true })).toBeDisabled();
    await deliverySelector.selectOption(latestDelivery.id);
    await expect(seller.getByLabel("售价（USD）", { exact: true })).toBeEnabled();
    await seller.getByLabel("售价（USD）", { exact: true }).fill("12.99");
    await seller.getByLabel("卖家 SKU", { exact: true }).fill("BROWSER-FICTIONAL-DEMO");
    await seller.getByRole("checkbox", { name: /我已检查第/ }).check();
    await seller.getByRole("button", { name: "确认本版并模拟上架", exact: true }).click();
    await expect.poll(() => phase(seller, order.id), { timeout: 30000 }).toBe("COMPLETED");
    const publication = await seller.request.get(`/api/v1/orders/${order.id}/publication`);
    const published = await publication.json();
    expect(published.mode).toBe("simulated");
    expect(published.status).toBe("SIMULATED");
    expect(published.attempt.submit_count).toBe(1);
    expect(published.confirmation.listing_price).toBe("12.99");
    const publicPage = await browser.newPage();
    try {
      await publicPage.goto(published.listing_url);
      await expect(publicPage.getByRole("heading", { level: 2 })).toHaveText(latestDelivery.listing.title);
      await expect(publicPage.getByText(/未发布到 Amazon/).first()).toBeVisible();
      expect(await publicPage.locator("img").count()).toBeGreaterThanOrEqual(2);
      await screenshot(publicPage, "simulated-listing-desktop.png");
    } finally { await publicPage.close(); }

    await seller.getByLabel("需要协助的问题", { exact: true }).fill("模拟工单：请人工确认已经完成的刊登为模拟结果。");
    await seller.getByRole("button", { name: "转交人工处理", exact: true }).click();
    await expect(seller.getByText("负责人正在人工处理，自动资料回复暂时暂停。", { exact: true })).toBeVisible();
    await expect(owner.getByLabel("人工回复", { exact: true })).toBeVisible({ timeout: 15000 });
    await owner.getByLabel("人工回复", { exact: true }).fill("已核实：本次仅为模拟刊登，没有提交到 Amazon。");
    await owner.getByRole("checkbox", { name: "回复后结束本次人工处理", exact: true }).check();
    await owner.getByRole("button", { name: "回复并结束处理", exact: true }).click();
    await expect.poll(async () => {
      const result = await seller.request.get(`/api/v1/orders/${order.id}/support-requests`);
      return (await result.json()).ai_reply_paused;
    }).toBe(false);
    await expect(seller.getByText("已核实：本次仅为模拟刊登，没有提交到 Amazon。", { exact: true }).first()).toBeVisible({ timeout: 15000 });

    await expect(owner.getByRole("heading", { name: "记录本段人工处理时间", exact: true })).toHaveCount(0);
    await expect(owner.getByRole("button", { name: "开始计时", exact: true })).toHaveCount(0);
    await expect(owner.getByText("已保存活跃工时", { exact: true })).toHaveCount(0);
    await expect(owner.getByText("补录竞品或商品观察", { exact: true })).toHaveCount(0);
    await expect(owner.getByRole("button", { name: "启动内容生产", exact: true })).toHaveCount(0);
    await expect(owner.getByRole("heading", { name: "模型用量与任务恢复", exact: true })).toBeVisible();
    await screenshot(owner, "owner-closed-loop-desktop.png");
    await owner.setViewportSize({ width: 390, height: 844 });
    expect(await owner.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await screenshot(owner, "owner-closed-loop-mobile.png");
    await seller.setViewportSize({ width: 390, height: 844 });
    expect(await seller.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await screenshot(seller, "seller-closed-loop-mobile.png");
    expect(runtimeErrors).toEqual([]);
  } finally { await ownerContext.close(); }
});
