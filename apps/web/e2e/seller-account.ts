import { expect, type APIRequestContext } from "@playwright/test";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";

export async function readEmailCode(email: string) {
  if (!process.env.E2E_MAIL_DIR) throw Error("Use the isolated test stack with its private email sink.");
  const key = createHash("sha256").update(email.trim().toLowerCase()).digest("hex");
  return JSON.parse(await readFile(path.join(process.env.E2E_MAIL_DIR, `${key}.json`), "utf8")).code as string;
}

export async function registerSeller(request: APIRequestContext) {
  const credentials = { email: `browser-${randomUUID()}@example.invalid`, password: `A1!${randomBytes(4).toString("hex")}` };
  const sent = await request.post("/api/v1/seller/auth/verification-code", { data: { email: credentials.email } });
  expect(sent.status()).toBe(200);
  const verification_code = await readEmailCode(credentials.email);
  const response = await request.post("/api/v1/seller/auth/register", { data: { ...credentials, verification_code, display_name: "浏览器虚构卖家" } });
  expect(response.status()).toBe(201);
  return { ...await response.json(), credentials } as { id: string; csrf_token: string; credentials: typeof credentials };
}
