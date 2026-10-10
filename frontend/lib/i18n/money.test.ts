import { describe, expect, it } from "vitest";
import { formatMoney } from "./locale";

describe("money formatting", () => {
  it("formats exact decimal strings in every locale with Latin digits", () => {
    // Beyond 2^53: exact because the amount is passed as a string.
    expect(formatMoney("1999999999999.98", "MAD", "en")).toMatch(/1,999,999,999,999\.98/);
    for (const locale of ["fr", "ar", "en"] as const) {
      const text = formatMoney("12500.50", "MAD", locale);
      expect(text).toMatch(/12.?500[.,]50/);
      expect(text).not.toMatch(/[٠-٩]/);
    }
    expect(formatMoney("15000", "XOF", "fr")).not.toMatch(/[.,]\d{2}\s*F/);
  });
});
