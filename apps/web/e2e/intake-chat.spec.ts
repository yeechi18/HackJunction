import { expect, test } from "@playwright/test";

test("获客聊天显示处理反馈，人工接管暂停，负责人结束后恢复回复", async ({ page: seller, browser, baseURL }) => {
  test.skip(!process.env.E2E_OWNER_PASSWORD, "需要隔离测试栈提供负责人账号");
  await seller.goto("/orders/new");
  await expect(seller.getByRole("link", { name: /负责人入口/ })).toHaveAttribute("href", "/internal/login");
  await seller.getByLabel(/你的称呼/).fill("聊天验收虚构卖家");
  const name = `虚构聊天理线夹 ${Date.now()}`;
  await seller.getByLabel(/中文商品名/).fill(name);
  await seller.getByRole("checkbox", { name: "这是一份虚构演示订单" }).check();
  await seller.getByRole("button", { name: /保存资料，创建订单/ }).click();
  const url = new URL(await seller.getByLabel("私有订单链接").inputValue());
  const orderId = url.pathname.split("/").pop();
  await seller.getByRole("link", { name: /查看订单进度/ }).click();
  await seller.waitForURL(current => current.pathname === url.pathname && !current.hash);
  await expect.poll(async () => (await (await seller.request.get(`/api/v1/orders/${orderId}`)).json()).workflow_status).toBe("waiting_for_seller");
  await seller.locator(".workspace-toolbar").getByRole("button", { name: /刷新/ }).click();
  const message = `请继续核对我的资料 ${Date.now()}`;
  const sent = seller.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === `/api/v1/orders/${orderId}/messages`);
  await seller.getByLabel("给这份订单留个消息").fill(message);
  await seller.getByRole("button", { name: "发送消息", exact: true }).click();
  expect((await sent).status()).toBe(201);
  await expect(seller.locator(".message-composer").getByRole("status")).toContainText("消息已保存");
  await seller.getByLabel("需要协助的问题", { exact: true }).fill("虚构测试：请负责人先确认资料。");
  await seller.getByRole("button", { name: "转交人工处理", exact: true }).click();
  await expect(seller.getByRole("status", { name: "自动回复状态", exact: true })).toContainText("自动回复已暂停");
  await expect(seller.getByLabel("给这份订单留个消息")).toHaveCount(0);

  const context = await browser.newContext({ baseURL });
  try {
    const owner = await context.newPage();
    await owner.goto("/internal/login");
    await owner.getByLabel("登录密码").fill(process.env.E2E_OWNER_PASSWORD!);
    await owner.getByRole("button", { name: /登录工作台/ }).click();
    await owner.waitForURL("**/internal/orders");
    await owner.getByLabel("搜索订单", { exact: true }).fill(name);
    await owner.getByRole("link", { name: new RegExp(name) }).click();
    await owner.getByLabel("人工回复", { exact: true }).fill("虚构测试：人工核对已结束，请继续自动审查。");
    await owner.getByRole("checkbox", { name: "回复后结束本次人工处理", exact: true }).check();
    const before = await (await seller.request.get(`/api/v1/orders/${orderId}/messages`)).json();
    const previousReplies = before.items.filter((item: { role: string }) => item.role === "assistant").length;
    await owner.getByRole("button", { name: "回复并结束处理", exact: true }).click();
    await expect.poll(async () => (await (await seller.request.get(`/api/v1/orders/${orderId}`)).json()).ai_reply_paused).toBe(false);
    await seller.locator(".workspace-toolbar").getByRole("button", { name: /刷新/ }).click();
    await expect(seller.getByRole("status", { name: "自动回复状态", exact: true })).toHaveCount(0);
    await expect.poll(async () => {
      const messages = await (await seller.request.get(`/api/v1/orders/${orderId}/messages`)).json();
      return messages.items.filter((item: { role: string }) => item.role === "assistant").length;
    }).toBeGreaterThan(previousReplies);
    await expect(seller.getByLabel("给这份订单留个消息")).toBeEditable();
  } finally { await context.close(); }
});
