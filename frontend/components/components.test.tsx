import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContactsTable } from "@/components/ContactsView";
import { I18nProvider, useErrorMessage } from "@/components/I18nProvider";
import { api, ApiError } from "@/lib/api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const contact = {
  contact_id: "c1",
  first_name: "محمد",
  last_name: "Benali",
  email: "m.benali@example.ma",
  phone_e164: "+212612345678",
  company_id: null,
  created_at: "2026-10-09T10:00:00Z",
};

describe("ContactsTable", () => {
  it("isolates phone numbers and emails as left-to-right inside an arabic page", () => {
    render(
      <I18nProvider locale="ar">
        <ContactsTable items={[contact]} />
      </I18nProvider>,
    );
    for (const value of ["+212612345678", "m.benali@example.ma"]) {
      const node = screen.getByText(value);
      expect(node.tagName).toBe("BDI");
      expect(node.getAttribute("dir")).toBe("ltr");
    }
    expect(screen.getByText("محمد Benali").tagName).toBe("BDI");
    expect(screen.getByRole("columnheader", { name: "الهاتف" })).toBeTruthy();
  });
});

describe("error messages", () => {
  function Probe({ code }: { code: string }) {
    return <p>{useErrorMessage()(code)}</p>;
  }

  it("translates known API codes and falls back for unknown ones", () => {
    render(
      <I18nProvider locale="fr">
        <Probe code="invalid_phone" />
        <Probe code="something_new" />
      </I18nProvider>,
    );
    expect(screen.getByText(/numéro valide/)).toBeTruthy();
    expect(screen.getByText(/Une erreur s’est produite/)).toBeTruthy();
  });
});

describe("api client", () => {
  it("parses LeadStack error envelopes and keeps the session same-origin", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: { code: "invalid_email", message: "Invalid email address." } }), {
        status: 422,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api("/v1/workspaces/x/contacts")).rejects.toMatchObject({ status: 422, code: "invalid_email" });
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ credentials: "same-origin" });
  });

  it("maps platform errors without a code by status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 })));
    await expect(api("/v1/x")).rejects.toEqual(new ApiError(404, "notFound"));
  });

  it("refuses paths outside the API namespace", async () => {
    await expect(api("https://evil.example/steal")).rejects.toThrow();
  });
});
