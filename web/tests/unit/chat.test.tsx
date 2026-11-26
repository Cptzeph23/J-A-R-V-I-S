import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { ChatPage } from "../../src/features/chat";
import { BASE, renderWithApp } from "./helpers";
import { APPROVAL, WORKFLOW } from "./fixtures";
import { server } from "./server";

const CONVERSATION = {
  id: WORKFLOW.conversation,
  title: "Planning",
  archived_at: null,
  created_at: "2026-11-25T06:00:00Z",
  updated_at: "2026-11-25T06:00:00Z",
};

const message = (id: string, role: string, content: string, at: string) => ({
  id,
  conversation: CONVERSATION.id,
  role,
  content,
  source: "text",
  status: "complete",
  created_at: at,
});

function sse(...frames: [string, unknown][]): Response {
  const text = frames.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");
  return new Response(text, { headers: { "Content-Type": "text/event-stream" } });
}

describe("ChatPage", () => {
  it("renders history oldest first, as plain text", async () => {
    server.use(
      http.get(`${BASE}/agent/conversations`, () => HttpResponse.json({ results: [CONVERSATION], next_cursor: null })),
      http.get(`${BASE}/agent/conversations/${CONVERSATION.id}/messages`, () =>
        HttpResponse.json({
          results: [
            message("m2", "assistant", "<img src=x onerror=alert(1)> Done.", "2026-11-25T06:02:00Z"),
            message("m1", "user", "Hello", "2026-11-25T06:01:00Z"),
          ],
          next_cursor: null,
        }),
      ),
    );
    renderWithApp(<ChatPage />);
    await screen.findByText("Hello");
    const list = screen.getByRole("list", { name: "Messages" });
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Hello");
    expect(items[1]).toHaveTextContent("<img src=x onerror=alert(1)> Done.");
    expect(list.querySelector("img")).toBeNull();
  });

  it("sends a message, follows the run, shows an approval, and posts the decision", async () => {
    const sent: { body: unknown; key: string | null }[] = [];
    const decisions: unknown[] = [];
    const waiting = { workflow: { ...WORKFLOW, state: "awaiting_approval" }, approvals: [APPROVAL], answer: null };
    const finished = { workflow: { ...WORKFLOW, state: "succeeded" }, approvals: [], answer: null };
    let history = [message("m1", "user", "Hello", "2026-11-25T06:01:00Z")];
    server.use(
      http.get(`${BASE}/agent/conversations`, () => HttpResponse.json({ results: [CONVERSATION], next_cursor: null })),
      http.get(`${BASE}/agent/conversations/${CONVERSATION.id}/messages`, () => HttpResponse.json({ results: history, next_cursor: null })),
      http.post(`${BASE}/agent/conversations/${CONVERSATION.id}/messages`, async ({ request }) => {
        sent.push({ body: await request.json(), key: request.headers.get("Idempotency-Key") });
        history = [message("m3", "user", "Book the dentist", "2026-11-25T06:03:00Z"), ...history];
        return HttpResponse.json({ message: history[0], workflow: WORKFLOW }, { status: 202 });
      }),
      http.get(`${BASE}/agent/workflows/${WORKFLOW.id}/stream`, () => sse(["snapshot", waiting], ["timeout", {}])),
      http.post(`${BASE}/agent/approvals/${APPROVAL.id}/decision`, async ({ request }) => {
        decisions.push(await request.json());
        history = [message("m4", "assistant", "Booked.", "2026-11-25T06:04:00Z"), ...history];
        server.use(http.get(`${BASE}/agent/workflows/${WORKFLOW.id}/stream`, () => sse(["snapshot", finished], ["done", {}])));
        return HttpResponse.json({ ...APPROVAL, state: "approved" });
      }),
    );

    renderWithApp(<ChatPage />);
    await screen.findByText("Hello");
    await userEvent.type(screen.getByLabelText("Message"), "Book the dentist");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => { expect(sent).toHaveLength(1); });
    expect(sent[0]?.body).toEqual({ content: "Book the dentist", source: "text" });
    expect(sent[0]?.key).toMatch(/^[0-9a-f-]{36}$/);

    const panel = await screen.findByTestId("approval");
    expect(panel).toHaveTextContent("Google Calendar: primary");
    await userEvent.click(within(panel).getByRole("button", { name: "Approve" }));
    await waitFor(() => { expect(decisions).toHaveLength(1); });
    expect(decisions[0]).toEqual({ decision: "approve", payload_sha256: APPROVAL.payload_sha256, confirmation: "tap" });

    expect(await screen.findByText("Booked.")).toBeInTheDocument();
    await waitFor(() => { expect(screen.queryByTestId("run-status")).not.toBeInTheDocument(); });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled(); // empty input again
  });

  it("explains a 409 while a run is still open and reuses no stale key", async () => {
    const keys: (string | null)[] = [];
    server.use(
      http.get(`${BASE}/agent/conversations`, () => HttpResponse.json({ results: [CONVERSATION], next_cursor: null })),
      http.get(`${BASE}/agent/conversations/${CONVERSATION.id}/messages`, () => HttpResponse.json({ results: [], next_cursor: null })),
      http.post(`${BASE}/agent/conversations/${CONVERSATION.id}/messages`, ({ request }) => {
        keys.push(request.headers.get("Idempotency-Key"));
        return HttpResponse.json({ error: { code: "workflow_active", message: "raw", details: null, request_id: null } }, { status: 409 });
      }),
    );
    renderWithApp(<ChatPage />);
    await screen.findByLabelText("Message");
    await userEvent.type(screen.getByLabelText("Message"), "again");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("still working on your previous message");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => { expect(keys).toHaveLength(2); });
    expect(keys[0]).not.toEqual(keys[1]);
  });

  it("reuses the same key when a send fails on the network and is retried", async () => {
    const keys: (string | null)[] = [];
    server.use(
      http.get(`${BASE}/agent/conversations`, () => HttpResponse.json({ results: [CONVERSATION], next_cursor: null })),
      http.get(`${BASE}/agent/conversations/${CONVERSATION.id}/messages`, () => HttpResponse.json({ results: [], next_cursor: null })),
      http.post(`${BASE}/agent/conversations/${CONVERSATION.id}/messages`, ({ request }) => {
        keys.push(request.headers.get("Idempotency-Key"));
        return HttpResponse.error();
      }),
    );
    renderWithApp(<ChatPage />);
    await screen.findByLabelText("Message");
    await userEvent.type(screen.getByLabelText("Message"), "hi");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => { expect(keys).toHaveLength(2); });
    expect(keys[0]).toBe(keys[1]);
  });
});
