import { expect, test } from "@playwright/test";
import { registerSeller } from "./seller-account";

test("商品数据包整理为当前建单字段，账号归属与自由明细保持不变", async ({ page }) => {
  const account = await registerSeller(page.request);
  const productName = `导入验收商品 ${Date.now()}`;
  await page.goto("/orders/new");
  await page.getByLabel(/补充商品说明与期望卖点/).fill("文案简洁");
  await page.route("**/api/v1/orders/intake-chat", async route => {
    expect(route.request().headers()["x-csrf-token"]).toBe(account.csrf_token);
    expect(route.request().headers()["content-type"]).toContain("multipart/form-data");
    expect(route.request().postData()).toContain("products.csv");
    expect(route.request().postData()).toContain("product_details");
    expect(route.request().postData()).not.toContain("seller_name");
    await route.fulfill({ json: { reply: "已整理商品名与明细，请核对资料卡。", updates: {
      product_name: productName, product_details: "黑色硅胶理线夹，6个装，尺寸未知。",
    } } });
  });
  await page.getByLabel("商品数据包（选填）", { exact: true }).setInputFiles({
    name: "products.csv", mimeType: "text/csv", buffer: Buffer.from(`标题,明细\n${productName},黑色硅胶理线夹，6个装`),
  });
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByLabel(/商品名字/)).toHaveValue(productName);
  await expect(page.getByLabel(/商品明细信息/)).toHaveValue("黑色硅胶理线夹，6个装，尺寸未知。");
  await expect(page.getByLabel(/补充商品说明与期望卖点/)).toHaveValue("文案简洁");
  await expect(page.getByRole("log", { name: "上架对话" })).toContainText("已整理商品名与明细");
  if (process.env.E2E_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.E2E_ARTIFACT_DIR}/intake-desktop.png`, fullPage: true });
  await page.getByRole("button", { name: "保存资料，创建订单", exact: true }).click();
  const url = new URL(await page.getByLabel("私有订单链接").inputValue());
  const response = await page.request.get(`/api/v1/orders/${url.pathname.split("/").pop()}`);
  expect(response.status()).toBe(200);
  const order = await response.json();
  expect(order.product_details).toBe("黑色硅胶理线夹，6个装，尺寸未知。");
  expect(order.facts).toEqual([]);
  expect(order.notes).toBe("文案简洁");
});

test("建单前聊天遵循真实账号会话和CSRF，整理失败仍可手动建单", async ({ page, playwright, baseURL }) => {
  const endpoint = "/api/v1/orders/intake-chat";
  const anonymous = await playwright.request.newContext({ baseURL });
  try {
    expect((await anonymous.post(endpoint, { multipart: { payload: JSON.stringify({ message: "硅胶理线夹", fields: {} }) } })).status()).toBe(401);
  } finally { await anonymous.dispose(); }
  const account = await registerSeller(page.request);
  const payload = { payload: JSON.stringify({ message: "硅胶理线夹", fields: {} }) };
  expect((await page.request.post(endpoint, { multipart: payload })).status()).toBe(403);
  expect((await page.request.post(endpoint, { headers: { "X-CSRF-Token": account.csrf_token }, multipart: payload })).status()).toBe(200);
  await page.goto("/orders/new");
  await page.route("**/api/v1/orders/intake-chat", route => route.fulfill({ status: 503, json: {} }));
  await page.getByLabel("描述你的商品", { exact: true }).fill("黑色硅胶理线夹");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "也可以直接填写资料卡" })).toBeVisible();
  await page.getByLabel(/商品名字/).fill(`手动建单 ${Date.now()}`);
  await page.getByLabel(/商品明细信息/).fill("黑色硅胶理线夹，无需固定参数核实。");
  await page.getByRole("button", { name: "保存资料，创建订单", exact: true }).click();
  await expect(page.getByRole("heading", { name: "资料已保存." })).toBeVisible();
});
