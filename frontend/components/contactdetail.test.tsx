import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContactDetailView, eventLabel } from "@/components/ContactDetailView";
import { I18nProvider } from "@/components/I18nProvider";
import { dictionaries } from "@/lib/i18n/dictionaries";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const DETAIL = {
  contact_id: "c1",
  first_name: "Salma",
  last_name: "Bennani",
  email: "salma@example.ma",
  phone_e164: "+212612345678",
  tags: [{ tag_id: "t1", name: "VIP" }],
};

function stubApi(consent: unknown, calls: { url: string; method: string; body: string | null }[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: typeof init?.body === "string" ? init.body : null });
      const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
      if (method !== "GET") return json({}, 200);
      if (url.endsWith("/detail")) return json(DETAIL);
      if (url.endsWith("/consent")) return json(consent);
      if (url.includes("/messages")) return json([]);
      if (url.includes("/timeline"))
        return json({
          items: [
            { item_type: "event", item_id: "e1", at: "2026-10-09T10:00:00Z", note: null, task: null, action: "leadstack.contact.created" },
            { item_type: "event", item_id: "e2", at: "2026-10-09T11:00:00Z", note: null, task: null, action: "leadstack.internal.unknown" },
          ],
          next_cursor: null,
        });
      return json({}, 404);
    }),
  );
  return calls;
}

function show() {
  render(
    <I18nProvider locale="en">
      <ContactDetailView tenantId="w1" contactId="c1" />
    </I18nProvider>,
  );
}

describe("contact detail", () => {
  it("never shows a raw audit code for an unknown event", async () => {
    stubApi(null);
    show();

    expect(await screen.findByText("Contact created")).toBeTruthy();
    expect(screen.getByText("Activity recorded")).toBeTruthy();
    expect(screen.queryByText(/leadstack\./)).toBeNull();
  });

  it("hides the email form for an unsubscribed contact", async () => {
    stubApi({ status: "withdrawn", withdrawn_via: "link" });
    show();

    expect(await screen.findByText(/Unsubscribed/)).toBeTruthy();
    expect(screen.queryByText("Send email")).toBeNull();
    expect(screen.queryByText("Record an unsubscribe request")).toBeNull();
  });

  it("records an unsubscribe only after an explicit confirmation", async () => {
    const calls = stubApi({ status: "pending", withdrawn_via: null });
    show();

    fireEvent.click(await screen.findByText("Record an unsubscribe request"));
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
    fireEvent.click(screen.getByText("Yes, unsubscribe"));

    await waitFor(() => {
      const put = calls.find((call) => call.method === "PUT");
      expect(put?.url).toBe("/v1/workspaces/w1/contacts/c1/consent");
      expect(put?.body).toBe(JSON.stringify({ status: "withdrawn" }));
    });
  });
});

describe("event labels", () => {
  it("has a label for every listed action in every locale", () => {
    const keys = Object.keys(dictionaries.en.contactDetail.events);
    for (const locale of ["fr", "ar"] as const) {
      expect(Object.keys(dictionaries[locale].contactDetail.events).sort()).toEqual([...keys].sort());
    }
    expect(eventLabel(null, dictionaries.fr)).toBe(dictionaries.fr.contactDetail.activity);
  });
});
