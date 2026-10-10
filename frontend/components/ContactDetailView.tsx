"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useErrorMessage, useI18n } from "@/components/I18nProvider";
import { Ltr } from "@/components/Ltr";
import { api, ApiError, post, seg } from "@/lib/api";
import { intlLocale, type Locale } from "@/lib/i18n/locale";
import type { Dictionary } from "@/lib/i18n/dictionaries";

interface Detail {
  contact_id: string;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  phone_e164: string | null;
  tags: { tag_id: string; name: string }[];
}

interface Note {
  note_id: string;
  body: string;
}

interface Task {
  task_id: string;
  title: string;
  due_at: string | null;
  status: "open" | "done";
}

interface TimelineItem {
  item_type: "note" | "task" | "event";
  item_id: string;
  at: string;
  note: Note | null;
  task: Task | null;
  action: string | null;
}

interface Consent {
  status: "pending" | "granted" | "withdrawn";
  withdrawn_via: "link" | "member" | null;
}

interface Message {
  message_id: string;
  subject: string;
  status: "queued" | "sending" | "sent" | "failed" | "suppressed";
  created_at: string;
}

/**
 * Date and time with the zone abbreviation, so a time is never ambiguous:
 * in `timeZone` when given, otherwise in the viewer's own zone.
 */
export function formatDateTime(value: string, locale: Locale, timeZone?: string): string {
  return new Intl.DateTimeFormat(intlLocale(locale), {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZoneName: "short",
    timeZone,
  }).format(new Date(value));
}

/** A translated label for an audit action; unknown actions get a neutral label, never a raw code. */
export function eventLabel(action: string | null, t: Dictionary): string {
  const labels = t.contactDetail.events as Record<string, string>;
  // Dictionary keys use underscores ("leadstack_contact_created").
  return (action && labels[action.replaceAll(".", "_")]) || t.contactDetail.activity;
}

export function ContactDetailView({ tenantId, contactId }: { tenantId: string; contactId: string }) {
  const { t, locale } = useI18n();
  const message = useErrorMessage();
  const workspace = `/v1/workspaces/${seg(tenantId)}`;
  const base = `${workspace}/contacts/${seg(contactId)}`;
  const [detail, setDetail] = useState<Detail | null>(null);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [consent, setConsent] = useState<Consent | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmingUnsubscribe, setConfirmingUnsubscribe] = useState(false);

  const fail = useCallback((caught: unknown) => {
    setError(caught instanceof ApiError ? caught.code : "generic");
  }, []);

  const loadTimeline = useCallback(
    async (after: string | null) => {
      const params = new URLSearchParams({ limit: "30" });
      if (after) params.set("cursor", after);
      const page = await api<{ items: TimelineItem[]; next_cursor: string | null }>(`${base}/timeline?${params}`);
      setTimeline((previous) => (after ? [...previous, ...page.items] : page.items));
      setCursor(page.next_cursor);
    },
    [base],
  );

  const refresh = useCallback(async () => {
    try {
      const [loaded, currentConsent, sent] = await Promise.all([
        api<Detail>(`${base}/detail`),
        api<Consent | null>(`${base}/consent`),
        api<Message[]>(`${base}/messages?limit=20`),
        loadTimeline(null),
      ]);
      setDetail(loaded);
      setConsent(currentConsent ?? null);
      setMessages(sent);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) setMissing(true);
      else fail(caught);
    }
  }, [base, fail, loadTimeline]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function run(action: () => Promise<unknown>, done?: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      if (done) setNotice(done);
      await refresh();
    } catch (caught) {
      fail(caught);
    } finally {
      setBusy(false);
    }
  }

  function addNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const body = String(new FormData(form).get("body") ?? "").trim();
    if (!body) return;
    void run(async () => {
      await post(`${base}/notes`, { body });
      form.reset();
    });
  }

  function addTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const title = String(data.get("title") ?? "").trim();
    const due = String(data.get("due") ?? "");
    if (!title) return;
    void run(async () => {
      // <input type="datetime-local"> is local wall-clock time: send an explicit instant.
      await post(`${workspace}/tasks`, {
        title,
        contact_id: contactId,
        ...(due ? { due_at: new Date(due).toISOString() } : {}),
      });
      form.reset();
    });
  }

  function sendEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const subject = String(data.get("subject") ?? "").trim();
    const body = String(data.get("message") ?? "").trim();
    if (!subject || !body) return;
    void run(async () => {
      await post(`${base}/messages`, { subject, body });
      form.reset();
    }, t.contactDetail.emailQueued);
  }

  if (missing) return <p className="error">{t.errors.notFound}</p>;
  if (!detail) return <p className="muted">{error ? message(error) : t.common.loading}</p>;

  const name = [detail.first_name, detail.last_name].filter(Boolean).join(" ");
  const unsubscribed = consent?.status === "withdrawn";

  return (
    <section className="stack">
      <header className="card">
        <h1>
          <bdi>{name || detail.email || detail.phone_e164}</bdi>
        </h1>
        {detail.email ? (
          <p>
            <Ltr>{detail.email}</Ltr>
          </p>
        ) : null}
        {detail.phone_e164 ? (
          <p>
            <Ltr>{detail.phone_e164}</Ltr>
          </p>
        ) : null}
        {detail.tags.length ? (
          <ul className="row wrap" aria-label={t.contactDetail.tags}>
            {detail.tags.map((tag) => (
              <li key={tag.tag_id} className="tag">
                <bdi>{tag.name}</bdi>
              </li>
            ))}
          </ul>
        ) : null}
      </header>

      {error ? (
        <p className="error" role="alert">
          {message(error)}
        </p>
      ) : null}
      {notice ? <p role="status">{notice}</p> : null}

      {detail.email ? (
        <section className="card" aria-labelledby="email-heading">
          <h2 id="email-heading">{t.contactDetail.email}</h2>
          <p className="muted">
            {t.contactDetail.consentLabel}: {t.contactDetail.consent[consent?.status ?? "none"]}
          </p>
          {unsubscribed ? null : (
            <>
              <form onSubmit={sendEmail} className="stack">
                <label className="field">
                  <span>{t.contactDetail.subject}</span>
                  <input name="subject" required maxLength={200} />
                </label>
                <label className="field">
                  <span>{t.contactDetail.message}</span>
                  <textarea name="message" required maxLength={10000} rows={5} />
                </label>
                <button type="submit" className="primary" disabled={busy}>
                  {t.contactDetail.send}
                </button>
              </form>
              {confirmingUnsubscribe ? (
                <div className="row" role="group" aria-label={t.contactDetail.unsubscribeConfirm}>
                  <span>{t.contactDetail.unsubscribeConfirm}</span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setConfirmingUnsubscribe(false);
                      void run(() => api(`${base}/consent`, { method: "PUT", body: JSON.stringify({ status: "withdrawn" }) }));
                    }}
                  >
                    {t.contactDetail.unsubscribeYes}
                  </button>
                  <button type="button" onClick={() => setConfirmingUnsubscribe(false)}>
                    {t.contactDetail.cancel}
                  </button>
                </div>
              ) : (
                <button type="button" onClick={() => setConfirmingUnsubscribe(true)}>
                  {t.contactDetail.unsubscribe}
                </button>
              )}
            </>
          )}
          {messages.length ? (
            <ul className="list">
              {messages.map((item) => (
                <li key={item.message_id}>
                  <bdi>{item.subject}</bdi> · {t.contactDetail.messageStatus[item.status]} ·{" "}
                  <Ltr>{formatDateTime(item.created_at, locale)}</Ltr>
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      <section className="card" aria-labelledby="notes-heading">
        <h2 id="notes-heading">{t.contactDetail.addNote}</h2>
        <form onSubmit={addNote} className="stack">
          <label className="field">
            <span>{t.contactDetail.note}</span>
            <textarea name="body" required maxLength={10000} rows={3} />
          </label>
          <button type="submit" disabled={busy}>
            {t.contacts.save}
          </button>
        </form>
      </section>

      <section className="card" aria-labelledby="tasks-heading">
        <h2 id="tasks-heading">{t.contactDetail.addTask}</h2>
        <form onSubmit={addTask} className="stack">
          <label className="field">
            <span>{t.contactDetail.taskTitle}</span>
            <input name="title" required maxLength={200} />
          </label>
          <label className="field">
            <span>{t.contactDetail.due}</span>
            <input name="due" type="datetime-local" dir="ltr" />
          </label>
          <button type="submit" disabled={busy}>
            {t.contacts.save}
          </button>
        </form>
      </section>

      <section className="card" aria-labelledby="timeline-heading">
        <h2 id="timeline-heading">{t.contactDetail.timeline}</h2>
        {timeline.length === 0 ? <p className="muted">{t.contactDetail.noActivity}</p> : null}
        <ul className="list">
          {timeline.map((item) => (
            <li key={`${item.item_type}-${item.item_id}`}>
              <span className="muted">
                <Ltr>{formatDateTime(item.at, locale)}</Ltr>
              </span>{" "}
              {item.note ? (
                <bdi className="prewrap">{item.note.body}</bdi>
              ) : item.task ? (
                <label className="consent">
                  <input
                    type="checkbox"
                    checked={item.task.status === "done"}
                    disabled={busy}
                    onChange={(event) => {
                      const status = event.currentTarget.checked ? "done" : "open";
                      const taskId = item.task?.task_id ?? "";
                      void run(() =>
                        api(`${workspace}/tasks/${seg(taskId)}`, { method: "PATCH", body: JSON.stringify({ status }) }),
                      );
                    }}
                  />
                  <span>
                    <bdi>{item.task.title}</bdi>
                    {item.task.due_at ? (
                      <>
                        {" "}
                        · {t.contactDetail.due} <Ltr>{formatDateTime(item.task.due_at, locale)}</Ltr>
                      </>
                    ) : null}
                  </span>
                </label>
              ) : (
                <span>{eventLabel(item.action, t)}</span>
              )}
            </li>
          ))}
        </ul>
        {cursor ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              // `busy` blocks a second click: the same page is never appended twice.
              setBusy(true);
              void loadTimeline(cursor)
                .catch(fail)
                .finally(() => setBusy(false));
            }}
          >
            {t.contacts.loadMore}
          </button>
        ) : null}
      </section>
    </section>
  );
}
