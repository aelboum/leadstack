import { describe, expect, it } from "vitest";
import { groupSlots, isoDay } from "@/components/PublicBooking";

describe("public booking slots", () => {
  it("groups by day in the booking page's time zone, not the visitor's", () => {
    // 23:30 UTC on the 11th is 00:30 on the 12th in Lagos (UTC+1 all year).
    // A zone with stable rules: Morocco's offset changed in 2026, and the
    // answer for Africa/Casablanca depends on the runtime's tz database.
    const days = groupSlots(["2026-10-12T08:00:00Z", "2026-10-11T23:30:00Z"], "Africa/Lagos", "en");

    expect(days.map((day) => day.key)).toEqual(["2026-10-12"]);
    expect(days[0]?.slots.map((slot) => slot.label)).toEqual(["00:30", "09:00"]);
  });

  it("keeps Latin digits and 24-hour times in Arabic", () => {
    const [day] = groupSlots(["2026-10-12T08:00:00Z"], "Africa/Lagos", "ar");

    expect(day?.slots[0]?.label).toBe("09:00");
    expect(day?.label).toMatch(/12/);
  });

  it("shows both occurrences of a repeated hour as distinct instants", () => {
    // Paris 2026-10-25: 00:00Z is 02:00 CEST and 01:00Z is 02:00 CET.
    const [day] = groupSlots(["2026-10-25T00:00:00Z", "2026-10-25T01:00:00Z"], "Europe/Paris", "fr");

    expect(day?.slots.map((slot) => slot.instant)).toEqual(["2026-10-25T00:00:00Z", "2026-10-25T01:00:00Z"]);
  });

  it("computes UTC calendar days across month ends", () => {
    const now = new Date("2026-10-31T22:00:00Z");

    expect(isoDay(now, -1)).toBe("2026-10-30");
    expect(isoDay(now, 1)).toBe("2026-11-01");
  });
});
