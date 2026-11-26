import { describe, expect, it } from "vitest";
import { formatMoney, formatShare } from "../../src/lib/money";
import { formatCountdown, formatDuration, secondsUntil } from "../../src/lib/time";

describe("formatMoney", () => {
  it("formats minor units exactly", () => {
    expect(formatMoney({ amount_minor: 123450, currency: "KES" })).toBe("KES 1,234.50");
    expect(formatMoney({ amount_minor: 5, currency: "KES" })).toBe("KES 0.05");
    expect(formatMoney({ amount_minor: 0, currency: "KES" })).toBe("KES 0.00");
  });

  it("does not lose precision above 2^53", () => {
    const big = Number.MAX_SAFE_INTEGER;
    expect(formatMoney({ amount_minor: big, currency: "KES" })).toBe("KES 90,071,992,547,409.91");
  });

  it("uses the currency's own exponent", () => {
    expect(formatMoney({ amount_minor: 1500, currency: "JPY" })).toBe("JPY 1,500");
  });

  it("keeps the sign of negative amounts", () => {
    expect(formatMoney({ amount_minor: -250, currency: "KES" })).toBe("-KES 2.50");
  });
});

describe("formatShare", () => {
  it("converts a decimal share to a percentage without float math", () => {
    expect(formatShare("0.8125")).toBe("81.25%");
    expect(formatShare("1")).toBe("100%");
  });
});

describe("time helpers", () => {
  it("formats durations and countdowns", () => {
    expect(formatDuration(3725)).toBe("1 h 2 min");
    expect(formatDuration(65)).toBe("1 min 5 s");
    expect(formatCountdown(125)).toBe("2:05");
  });

  it("never returns negative seconds", () => {
    expect(secondsUntil("2020-01-01T00:00:00Z", Date.now())).toBe(0);
  });
});
