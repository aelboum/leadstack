"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useErrorMessage, useI18n } from "@/components/I18nProvider";
import { Ltr } from "@/components/Ltr";
import { api, ApiError, post, seg, type Contact, type Page } from "@/lib/api";

const SEARCH_DELAY_MS = 250;

function displayName(contact: Contact): string {
  return [contact.first_name, contact.last_name].filter(Boolean).join(" ");
}

export function ContactsTable({ items, tenantId }: { items: Contact[]; tenantId?: string }) {
  const { t } = useI18n();
  return (
    <table>
      <thead>
        <tr>
          <th scope="col">{t.contacts.firstName}</th>
          <th scope="col">{t.contacts.email}</th>
          <th scope="col">{t.contacts.phone}</th>
        </tr>
      </thead>
      <tbody>
        {items.map((contact) => (
          <tr key={contact.contact_id}>
            {/* Names may be Arabic or Latin: let the browser isolate them. */}
            <td>
              {tenantId ? (
                <Link href={`/w/${seg(tenantId)}/contacts/${seg(contact.contact_id)}`}>
                  <bdi>{displayName(contact) || contact.email || contact.phone_e164}</bdi>
                </Link>
              ) : (
                <bdi>{displayName(contact)}</bdi>
              )}
            </td>
            <td>{contact.email ? <Ltr>{contact.email}</Ltr> : null}</td>
            <td>{contact.phone_e164 ? <Ltr>{contact.phone_e164}</Ltr> : null}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ContactsView({ tenantId }: { tenantId: string }) {
  const { t } = useI18n();
  const message = useErrorMessage();
  const base = `/v1/workspaces/${seg(tenantId)}/contacts`;
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Contact[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Only the latest request may update the list: debounced searches and
  // "load more" can resolve out of order.
  const latest = useRef(0);

  const load = useCallback(
    async (q: string, after: string | null) => {
      const request = ++latest.current;
      setLoading(true);
      setError(null);
      const params = new URLSearchParams({ limit: "50" });
      if (q.trim()) params.set("q", q.trim());
      if (after) params.set("cursor", after);
      try {
        const page = await api<Page<Contact>>(`${base}?${params}`);
        if (request !== latest.current) return;
        setItems((previous) => (after ? [...previous, ...page.items] : page.items));
        setCursor(page.next_cursor);
      } catch (caught) {
        if (request !== latest.current) return;
        setError(caught instanceof ApiError ? caught.code : "generic");
      } finally {
        if (request === latest.current) setLoading(false);
      }
    },
    [base],
  );

  useEffect(() => {
    const timer = setTimeout(() => void load(query, null), SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [query, load]);

  return (
    <>
      <NewContactForm tenantId={tenantId} onCreated={() => void load(query, null)} />
      <section className="card">
        <h1>{t.contacts.title}</h1>
        <input
          type="search"
          aria-label={t.contacts.search}
          placeholder={t.contacts.search}
          value={query}
          maxLength={100}
          onChange={(event) => setQuery(event.target.value)}
        />
        {error ? (
          <p className="error" role="alert">
            {message(error)}
          </p>
        ) : null}
        {!loading && items.length === 0 && !error ? (
          <p className="muted">{query ? t.contacts.noResults : t.contacts.empty}</p>
        ) : null}
        {items.length > 0 ? <ContactsTable items={items} tenantId={tenantId} /> : null}
        {loading ? <p className="muted">{t.common.loading}</p> : null}
        {cursor && !loading ? (
          <button type="button" onClick={() => void load(query, cursor)}>
            {t.contacts.loadMore}
          </button>
        ) : null}
      </section>
    </>
  );
}

export function NewContactForm({ tenantId, onCreated }: { tenantId: string; onCreated: () => void }) {
  const { t } = useI18n();
  const message = useErrorMessage();
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const body: Record<string, string> = {};
    for (const key of ["first_name", "last_name", "email", "phone"]) {
      const value = String(data.get(key) ?? "").trim();
      if (value) body[key] = value;
    }
    setSaving(true);
    setStatus(null);
    try {
      await post(`/v1/workspaces/${seg(tenantId)}/contacts`, body);
      form.reset();
      setStatus({ ok: true, text: t.contacts.created });
      onCreated();
    } catch (caught) {
      setStatus({ ok: false, text: message(caught instanceof ApiError ? caught.code : "generic") });
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card">
      <h2>{t.contacts.add}</h2>
      <form className="form" onSubmit={submit} noValidate>
        <label className="field">
          {t.contacts.firstName}
          <input name="first_name" maxLength={100} autoComplete="off" />
        </label>
        <label className="field">
          {t.contacts.lastName}
          <input name="last_name" maxLength={100} autoComplete="off" />
        </label>
        <label className="field">
          {t.contacts.email}
          <input name="email" type="email" dir="ltr" maxLength={254} autoComplete="off" />
        </label>
        <label className="field">
          {t.contacts.phone}
          <input name="phone" type="tel" dir="ltr" maxLength={32} autoComplete="off" />
        </label>
        <button type="submit" className="primary" disabled={saving}>
          {saving ? t.contacts.saving : t.contacts.save}
        </button>
      </form>
      {status ? (
        <p className={status.ok ? "muted" : "error"} role={status.ok ? "status" : "alert"}>
          {status.text}
        </p>
      ) : null}
    </section>
  );
}
