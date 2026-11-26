import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { ApprovalsPage } from "../../src/features/approvals";
import { BASE, renderWithApp } from "./helpers";
import { APPROVAL } from "./fixtures";
import { server } from "./server";

function serve(approval = APPROVAL) {
  const decisions: unknown[] = [];
  server.use(
    http.get(`${BASE}/agent/approvals`, ({ request }) => {
      const state = new URL(request.url).searchParams.get("state");
      return HttpResponse.json({ results: state === "pending" ? [approval] : [], next_cursor: null });
    }),
    http.post(`${BASE}/agent/approvals/${approval.id}/decision`, async ({ request }) => {
      decisions.push(await request.json());
      return HttpResponse.json({ ...approval, state: "approved" });
    }),
  );
  return decisions;
}

describe("ApprovalsPage", () => {
  it("shows what will run, then approves with the echoed hash", async () => {
    const decisions = serve();
    renderWithApp(<ApprovalsPage />);
    const panel = await screen.findByTestId("approval");
    expect(panel).toHaveTextContent("Google Calendar: primary");
    expect(panel).toHaveTextContent("Dentist");
    expect(panel).toHaveTextContent(APPROVAL.payload_sha256.slice(0, 16));
    expect(screen.queryByText(/influenced by content from outside/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => { expect(decisions).toHaveLength(1); });
    expect(decisions[0]).toEqual({ decision: "approve", payload_sha256: APPROVAL.payload_sha256, confirmation: "tap" });
  });

  it("rejects without sending a hash", async () => {
    const decisions = serve();
    renderWithApp(<ApprovalsPage />);
    await screen.findByTestId("approval");
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => { expect(decisions).toHaveLength(1); });
    expect(decisions[0]).toEqual({ decision: "reject", confirmation: "tap" });
  });

  it("warns when the action is tainted by outside content", async () => {
    serve({ ...APPROVAL, tainted: true });
    renderWithApp(<ApprovalsPage />);
    expect(await screen.findByText(/influenced by content from outside/)).toBeInTheDocument();
  });

  it("disables the buttons once the approval has expired", async () => {
    serve({ ...APPROVAL, expires_at: new Date(Date.now() - 1000).toISOString() });
    renderWithApp(<ApprovalsPage />);
    await screen.findByTestId("approval");
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });

  it("shows the server's message when a decision is refused", async () => {
    server.use(
      http.get(`${BASE}/agent/approvals`, ({ request }) =>
        HttpResponse.json({ results: new URL(request.url).searchParams.get("state") === "pending" ? [APPROVAL] : [], next_cursor: null }),
      ),
      http.post(`${BASE}/agent/approvals/${APPROVAL.id}/decision`, () =>
        HttpResponse.json({ error: { code: "approval_expired", message: "This approval has expired.", details: null, request_id: null } }, { status: 409 }),
      ),
    );
    renderWithApp(<ApprovalsPage />);
    await screen.findByTestId("approval");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This approval has expired.");
  });

  it("says so when nothing is waiting", async () => {
    server.use(http.get(`${BASE}/agent/approvals`, () => HttpResponse.json({ results: [], next_cursor: null })));
    renderWithApp(<ApprovalsPage />);
    expect(await screen.findByText("Nothing is waiting for your approval.")).toBeInTheDocument();
  });
});
