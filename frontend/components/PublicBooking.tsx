"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useErrorMessage, useI18n } from "@/components/I18nProvider";
import { ApiError, seg } from "@/lib/api";
import { intlLocale, type Locale } from "@/lib/i18n/locale";

interface BookingPage {
  name: string;
  duration_minutes: number;
  time_zone: string;
  slots: string[];
  booking_token: string;
}

export interface SlotDay {
  key: string; // YYYY-MM-DD in the booking page's time zone
  label: string;
  slots: { instant: string; label: string }[];
}

const WINDOW_DAYS = 7;

async function publicCall<T>(path: string, init?: RequestInit): Promise<T> {
  // Public endpoints: no session, no credentials sent.
  const response = await fetch(path, { ...init, credentials: "omit", headers: { "Content-Type": "application/json" } });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const code = (body as { detail?: { code?: string } } | null)?.detail?.code ?? "generic";
    throw new ApiError(response.status, code);
  }
  return body as T;
}

/** UTC calendar day `offset` days from `now` (the server clamps to its own "today"). */
export function isoDay(now: Date, offset: number): string {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + offset))
    .toISOString()
    .slice(0, 10);
}

/**
 * Groups slot instants by calendar day **in the booking page's time zone**
 * (never the visitor's), so everyone sees the times the host offers.
 */
export function groupSlots(slots: string[], timeZone: string, locale: Locale): SlotDay[] {
  const tag = intlLocale(locale);
  const dayKey = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" });
  const dayLabel = new Intl.DateTimeFormat(tag, { timeZone, weekday: "long", day: "numeric", month: "long" });
  const timeLabel = new Intl.DateTimeFormat(tag, { timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const days = new Map<string, SlotDay>();
  for (const instant of [...slots].sort()) {
    const date = new Date(instant);
    const key = dayKey.format(date);
    let day = days.get(key);
    if (!day) {
      day = { key, label: dayLabel.format(date), slots: [] };
      days.set(key, day);
    }
    day.slots.push({ instant, label: timeLabel.format(date) });
  }
  return [...days.values()];
}

export function PublicBooking({ publicKey }: { publicKey: string }) {
  const { t, locale } = useI18n();
  const message = useErrorMessage();
  const [page, setPage] = useState<BookingPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "idle" | "sending" | "done" | "missing">("loading");
  const [error, setError] = useState<string | null>(null);

  // Only the latest request may update the page: quick Earlier/Later clicks
  // can resolve out of order.
  const latest = useRef(0);

  const load = useCallback(
    (from: number) => {
      const request = ++latest.current;
      const now = new Date();
      // One day earlier than UTC "today": the page's zone may still be on the previous day.
      const query = `from=${isoDay(now, from - 1)}&to=${isoDay(now, from + WINDOW_DAYS)}`;
      return publicCall<BookingPage>(`/v1/public/booking/${seg(publicKey)}?${query}`)
        .then((result) => {
          if (request !== latest.current) return;
          setPage(result);
          setState((current) => (current === "done" ? current : "idle"));
        })
        .catch((caught) => {
          if (request !== latest.current) return;
          if (caught instanceof ApiError && caught.status === 404) setState("missing");
          else setError(caught instanceof ApiError ? caught.code : "generic");
        });
    },
    [publicKey],
  );

  useEffect(() => {
    void load(offset);
  }, [load, offset]);

  function move(next: number) {
    // The chosen time belongs to the window being left.
    setSelected(null);
    setError(null);
    setOffset(next);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!page || !selected) return;
    const data = new FormData(event.currentTarget);
    setState("sending");
    setError(null);
    try {
      await publicCall(`/v1/public/booking/${seg(publicKey)}`, {
        method: "POST",
        body: JSON.stringify({
          starts_at: selected,
          name: String(data.get("name") ?? ""),
          email: String(data.get("email") ?? ""),
          phone: String(data.get("phone") ?? "") || null,
          consent: data.get("consent") === "on",
          booking_token: page.booking_token,
          website: String(data.get("website") ?? "") || null,
        }),
      });
      setState("done");
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : "generic";
      setState("idle");
      setError(code);
      if (code === "slot_unavailable") {
        setSelected(null);
        void load(offset); // show what is still free
      }
    }
  }

  if (state === "missing") return <p className="error">{t.errors.notFound}</p>;
  if (!page) return <p className="muted">{error ? message(error) : t.common.loading}</p>;

  const days = groupSlots(page.slots, page.time_zone, locale);
  const chosen = days.flatMap((day) => day.slots.map((slot) => ({ ...slot, day: day.label }))).find((slot) => slot.instant === selected);

  if (state === "done" && chosen)
    return (
      <section className="card" role="status">
        <h1>{t.booking.booked}</h1>
        <p>
          <bdi>{chosen.day}</bdi> · <bdi dir="ltr">{chosen.label}</bdi>
        </p>
        <p className="muted">
          {t.booking.timesIn} <bdi dir="ltr">{page.time_zone}</bdi>
        </p>
      </section>
    );

  const bookingError =
    error === "slot_unavailable"
      ? t.booking.taken
      : error === "booking_rejected"
        ? t.booking.invalid
        : error === "booking_quota_exceeded"
          ? t.errors.rateLimited
          : error
            ? message(error)
            : null;

  return (
    <section className="card">
      <h1>
        <bdi>{page.name}</bdi>
      </h1>
      <p className="muted">
        {page.duration_minutes} {t.booking.minutes} · {t.booking.timesIn} <bdi dir="ltr">{page.time_zone}</bdi>
      </p>
      <div className="row">
        <button type="button" disabled={offset === 0} onClick={() => move(Math.max(0, offset - WINDOW_DAYS))}>
          {t.booking.earlier}
        </button>
        <button type="button" disabled={offset >= 84} onClick={() => move(offset + WINDOW_DAYS)}>
          {t.booking.later}
        </button>
      </div>
      {days.length === 0 ? <p className="muted">{t.booking.noSlots}</p> : null}
      {days.map((day) => (
        <fieldset key={day.key} className="stack">
          <legend>
            <bdi>{day.label}</bdi>
          </legend>
          <div className="row wrap">
            {day.slots.map((slot) => (
              <button
                key={slot.instant}
                type="button"
                className={slot.instant === selected ? "primary" : undefined}
                aria-pressed={slot.instant === selected}
                onClick={() => setSelected(slot.instant)}
              >
                <bdi dir="ltr">{slot.label}</bdi>
              </button>
            ))}
          </div>
        </fieldset>
      ))}
      {chosen ? (
        <form onSubmit={submit} className="stack" noValidate>
          <p>
            <bdi>{chosen.day}</bdi> · <bdi dir="ltr">{chosen.label}</bdi>
          </p>
          <label className="field">
            <span>{t.booking.name} *</span>
            <input name="name" required maxLength={200} autoComplete="name" />
          </label>
          <label className="field">
            <span>{t.booking.email} *</span>
            <input name="email" type="email" dir="ltr" required maxLength={254} autoComplete="email" />
          </label>
          <label className="field">
            <span>{t.booking.phone}</span>
            <input name="phone" type="tel" dir="ltr" maxLength={32} autoComplete="tel" />
          </label>
          {/* Honeypot: hidden from people and assistive technology; bots fill it. */}
          <div aria-hidden="true" className="visually-hidden">
            <label>
              Website
              <input name="website" tabIndex={-1} autoComplete="off" />
            </label>
          </div>
          <label className="consent">
            <input type="checkbox" name="consent" required />
            <span>{t.booking.consent}</span>
          </label>
          <button type="submit" className="primary" disabled={state === "sending"}>
            {state === "sending" ? t.contacts.saving : t.booking.confirm}
          </button>
        </form>
      ) : null}
      {bookingError ? (
        <p className="error" role="alert">
          {bookingError}
        </p>
      ) : null}
    </section>
  );
}
