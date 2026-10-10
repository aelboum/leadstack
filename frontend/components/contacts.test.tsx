import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContactsView } from "@/components/ContactsView";
import { I18nProvider } from "@/components/I18nProvider";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function page(names: string[]) {
  return new Response(
    JSON.stringify({
      items: names.map((n, i) => ({
        contact_id: `${n}-${i}`,
        first_name: n,
        last_name: null,
        email: null,
        phone_e164: null,
        company_id: null,
        created_at: "2026-10-09T10:00:00Z",
      })),
      next_cursor: null,
    }),
    { status: 200 },
  );
}

describe("ContactsView search", () => {
  it("ignores a slow response that arrives after a newer search", async () => {
    vi.useFakeTimers();
    const resolvers: Array<(r: Response) => void> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => resolvers.push(resolve))),
    );
    render(
      <I18nProvider locale="en">
        <ContactsView tenantId="t1" />
      </I18nProvider>,
    );
    const box = screen.getByRole("searchbox");

    await act(async () => {
      vi.advanceTimersByTime(300); // initial load (request 1)
    });
    fireEvent.change(box, { target: { value: "ka" } });
    await act(async () => {
      vi.advanceTimersByTime(300); // request 2: "ka"
    });
    fireEvent.change(box, { target: { value: "kar" } });
    await act(async () => {
      vi.advanceTimersByTime(300); // request 3: "kar"
    });

    // The newest search answers first, then the stale ones arrive.
    await act(async () => resolvers[2]?.(page(["Karim"])));
    await act(async () => resolvers[1]?.(page(["Kamal", "Karim"])));
    await act(async () => resolvers[0]?.(page(["Everyone"])));

    expect(screen.getByText("Karim")).toBeTruthy();
    expect(screen.queryByText("Kamal")).toBeNull();
    expect(screen.queryByText("Everyone")).toBeNull();
  });
});
