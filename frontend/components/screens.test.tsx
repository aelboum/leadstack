import { describe, expect, it } from "vitest";
import { buildRules, minutesOf } from "@/components/CalendarView";
import { formatDateTime } from "@/components/ContactDetailView";
import { buildFields } from "@/components/FormsView";
import { buildAction, triggerLabel } from "@/components/WorkflowsView";
import { dictionaries } from "@/lib/i18n/dictionaries";

describe("booking page rules", () => {
  it("converts local clock times to minutes on the 5-minute grid", () => {
    expect(minutesOf("09:30")).toBe(570);
    expect(minutesOf("09:32")).toBeNull();
    expect(minutesOf("nonsense")).toBeNull();
  });

  it("builds one window per chosen weekday, Monday first", () => {
    expect(buildRules([4, 0], "09:00", "12:00")).toEqual([
      { weekday: 0, start: 540, end: 720 },
      { weekday: 4, start: 540, end: 720 },
    ]);
  });

  it("refuses an empty week or an end before the start", () => {
    expect(buildRules([], "09:00", "12:00")).toBeNull();
    expect(buildRules([0], "12:00", "09:00")).toBeNull();
  });
});

describe("lead form fields", () => {
  it("always asks for a required email address", () => {
    const fields = buildFields(new Set(["first_name"] as const), dictionaries.fr.forms.fieldLabels);

    expect(fields.map((field) => field.key)).toEqual(["first_name", "email"]);
    expect(fields.find((field) => field.key === "email")).toMatchObject({ required: true, maps_to: "email", type: "email" });
    expect(fields[0]?.label).toBe("Prénom");
  });
});

describe("automations", () => {
  it("builds exactly the chosen action and refuses an incomplete one", () => {
    expect(buildAction("add_tag", "t1", "")).toEqual({ type: "add_tag", tag_id: "t1" });
    expect(buildAction("add_tag", "", "")).toBeNull();
    expect(buildAction("create_task", "", "  Call back  ")).toEqual({ type: "create_task", title: "Call back" });
    expect(buildAction("create_task", "", "   ")).toBeNull();
  });

  it("translates every API trigger in every locale", () => {
    for (const locale of ["en", "fr", "ar"] as const) {
      for (const trigger of ["contact.created", "form.submitted", "deal.stage_changed"]) {
        expect(triggerLabel(trigger, dictionaries[locale])).not.toBe(trigger);
      }
    }
  });
});

describe("date and time display", () => {
  it("always names the time zone, in the requested zone", () => {
    const text = formatDateTime("2026-10-12T08:00:00Z", "en", "Africa/Casablanca");

    expect(text).toContain("09:00");
    expect(text).toMatch(/GMT\+1|WEST|\+01/);
  });

  it("keeps Latin digits in Arabic", () => {
    expect(formatDateTime("2026-10-12T08:00:00Z", "ar", "UTC")).toMatch(/08:00/);
  });
});
