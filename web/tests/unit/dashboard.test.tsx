import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { DashboardPage } from "../../src/features/dashboard";
import { formatMoney } from "../../src/lib/money";
import { BASE, renderWithApp } from "./helpers";
import { SUMMARY } from "./fixtures";
import { server } from "./server";

describe("DashboardPage", () => {
  it("shows exactly the numbers the API returned", async () => {
    let range: string | null = null;
    server.use(
      http.get(`${BASE}/dashboard/summary`, ({ request }) => {
        range = new URL(request.url).searchParams.get("range");
        return HttpResponse.json(SUMMARY);
      }),
    );
    renderWithApp(<DashboardPage />);

    const spending = await screen.findByTestId("card-spending");
    expect(within(spending).getByText(formatMoney(SUMMARY.spending.total!))).toBeInTheDocument();
    expect(within(spending).getByText("KES 1,234.56")).toBeInTheDocument();
    expect(within(spending).getByText("7 transactions")).toBeInTheDocument();
    expect(within(within(spending).getByRole("table", { name: "Spending by category" })).getByText("KES 800.00")).toBeInTheDocument();

    const calls = screen.getByTestId("card-calls");
    expect(within(calls).getByTestId("stat-received")).toHaveTextContent("12");
    expect(within(calls).getByTestId("stat-missed")).toHaveTextContent("3");
    expect(within(calls).getByTestId("stat-outgoing")).toHaveTextContent("9");
    expect(within(calls).getByTestId("stat-rejected")).toHaveTextContent("1");
    expect(within(calls).getByText("Talk time 1 h 2 min")).toBeInTheDocument();

    const messages = screen.getByTestId("card-messages");
    expect(within(messages).getByTestId("stat-sent")).toHaveTextContent("40");
    expect(within(messages).getByTestId("stat-received")).toHaveTextContent("55");

    const activity = screen.getByTestId("card-activity");
    expect(within(activity).getByTestId("stat-pending-approvals")).toHaveTextContent("2");
    expect(within(activity).getByTestId("stat-unread-notifications")).toHaveTextContent("5");
    expect(range).toBe("week");
  });

  it("explains an unavailable card instead of showing zeros", async () => {
    server.use(http.get(`${BASE}/dashboard/summary`, () => HttpResponse.json(SUMMARY)));
    renderWithApp(<DashboardPage />);
    const email = await screen.findByTestId("card-email");
    expect(within(email).getByRole("status")).toHaveTextContent("Email is not connected");
    expect(within(email).queryByTestId("stat-unread")).not.toBeInTheDocument();
  });

  it("does not turn missing phone data into zero calls", async () => {
    const summary = {
      ...SUMMARY,
      calls: { available: false, reason: "collection_not_enabled", source: "phone", data_as_of: null },
    };
    server.use(http.get(`${BASE}/dashboard/summary`, () => HttpResponse.json(summary)));
    renderWithApp(<DashboardPage />);
    const calls = await screen.findByTestId("card-calls");
    expect(within(calls).getByRole("status")).toHaveTextContent("not turned on");
    expect(within(calls).queryByTestId("stat-received")).not.toBeInTheDocument();
  });

  it("re-requests when the range changes", async () => {
    const seen: string[] = [];
    server.use(
      http.get(`${BASE}/dashboard/summary`, ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("range") ?? "");
        return HttpResponse.json(SUMMARY);
      }),
    );
    const { default: userEvent } = await import("@testing-library/user-event");
    renderWithApp(<DashboardPage />);
    await screen.findByTestId("card-spending");
    await userEvent.setup().click(screen.getByRole("button", { name: "This month" }));
    await screen.findByRole("button", { name: "This month", pressed: true });
    expect(seen).toEqual(["week", "month"]);
  });

  it("shows the API error with its request id and offers a retry", async () => {
    server.use(
      http.get(`${BASE}/dashboard/summary`, () =>
        HttpResponse.json({ error: { code: "server_error", message: "Boom.", details: null, request_id: "req-9" } }, { status: 500 }),
      ),
    );
    renderWithApp(<DashboardPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Boom.");
    expect(alert).toHaveTextContent("req-9");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
