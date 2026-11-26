import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mockApi } from "./mock-api";

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email").fill("me@example.com");
  await page.getByLabel("Password").fill("correct horse");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

test("sign in shows a dashboard whose numbers match the API", async ({ page }) => {
  const calls = await mockApi(page);
  await signIn(page);

  expect(calls.logins[0]).toMatchObject({ email: "me@example.com", device: { platform: "web" } });
  await expect(page.getByTestId("card-spending")).toContainText("KES 1,234.56");
  await expect(page.getByTestId("card-calls").getByTestId("stat-received")).toHaveText("12");
  await expect(page.getByTestId("card-calls").getByTestId("stat-missed")).toHaveText("3");
  await expect(page.getByTestId("card-messages").getByTestId("stat-sent")).toHaveText("40");
  await expect(page.getByTestId("card-email")).toContainText("Email is not connected");
  await expect(page.getByTestId("card-activity").getByTestId("stat-pending-approvals")).toHaveText("1");
});

test("a reload keeps the session through the refresh token", async ({ page }) => {
  await mockApi(page);
  await signIn(page);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("jarvis.refresh"))).toBeNull();
});

test("chat: send, approve the proposed action, receive the answer", async ({ page }) => {
  const calls = await mockApi(page);
  await signIn(page);
  await page.getByRole("link", { name: "Chat" }).click();

  await page.getByRole("textbox", { name: "Message" }).fill("Book the dentist");
  await page.getByRole("button", { name: "Send" }).click();

  const panel = page.getByTestId("approval");
  await expect(panel).toContainText("Google Calendar: primary");
  await expect(panel).toContainText("Dentist");
  await panel.getByRole("button", { name: "Approve" }).click();

  await expect(page.getByText("Booked for Friday.")).toBeVisible();
  await expect(page.getByTestId("run-status")).toHaveCount(0);
  expect(calls.messages).toEqual([{ content: "Book the dentist", source: "text" }]);
  expect(calls.decisions).toEqual([{ decision: "approve", payload_sha256: "cd".repeat(32), confirmation: "tap" }]);
});

test("integrations: an unexpected authorization address is never opened", async ({ page }) => {
  const calls = await mockApi(page);
  await signIn(page);
  await page.getByRole("link", { name: "Integrations" }).click();
  await page.getByRole("button", { name: "Connect" }).first().click();
  await expect(page.getByRole("alert")).toContainText("unexpected sign-in address");
  expect(calls.connects).toBe(1);
  expect(page.url()).toContain("/integrations");
});

test("signing out returns to the login page and clears the session", async ({ page }) => {
  await mockApi(page);
  await signIn(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("form", { name: "Sign in" })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("jarvis.refresh"))).toBeNull();
});
