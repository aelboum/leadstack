"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { formatDateTime } from "@/components/ContactDetailView";
import { useErrorMessage, useI18n } from "@/components/I18nProvider";
import { Ltr } from "@/components/Ltr";
import { api, ApiError, post, seg, type Workspace } from "@/lib/api";

interface Appointment {
  appointment_id: string;
  contact_id: string;
  starts_at: string;
  time_zone: string;
  status: "booked" | "cancelled";
  source: "public" | "manual";
}

interface BookingType {
  booking_type_id: string;
  name: string;
  public_key: string;
  duration_minutes: number;
  time_zone: string;
  status: "active" | "disabled";
}

const UPCOMING_DAYS = 30;

/** "09:30" -> 570 minutes after local midnight; null when not on the 5-minute grid. */
export function minutesOf(clock: string): number | null {
  const match = /^(\d{2}):(\d{2})$/.exec(clock);
  if (!match) return null;
  const total = Number(match[1]) * 60 + Number(match[2]);
  return total <= 1440 && total % 5 === 0 ? total : null;
}

/** Weekly rules (Monday = 0) for the chosen weekdays and one daily window. */
export function buildRules(weekdays: number[], start: string, end: string) {
  const from = minutesOf(start);
  const to = minutesOf(end);
  if (from === null || to === null || from >= to || weekdays.length === 0) return null;
  return [...weekdays].sort().map((weekday) => ({ weekday, start: from, end: to }));
}

export function CalendarView({ tenantId }: { tenantId: string }) {
  const { t, locale } = useI18n();
  const message = useErrorMessage();
  const workspace = `/v1/workspaces/${seg(tenantId)}`;
  const [appointments, setAppointments] = useState<Appointment[] | null>(null);
  const [types, setTypes] = useState<BookingType[]>([]);
  const [zone, setZone] = useState("UTC");
  const [me, setMe] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const origin = typeof window === "undefined" ? "" : window.location.origin;

  const load = useCallback(async () => {
    try {
      const now = new Date();
      const until = new Date(now.getTime() + UPCOMING_DAYS * 86_400_000);
      const range = new URLSearchParams({ from: now.toISOString(), to: until.toISOString() });
      const [list, bookingTypes, profile, session] = await Promise.all([
        api<Appointment[]>(`${workspace}/appointments?${range}`),
        api<BookingType[]>(`${workspace}/booking-types`),
        api<Workspace>(workspace),
        // SaaS-OS session endpoint: who is signed in (the default host). A
        // failure here only disables creating booking pages.
        fetch("/auth/me", { credentials: "same-origin" })
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null),
      ]);
      setAppointments(list);
      setTypes(bookingTypes);
      setZone(profile.time_zone);
      setMe((session as { user_id?: string } | null)?.user_id ?? null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.code : "generic");
    }
  }, [workspace]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.code : "generic");
    } finally {
      setBusy(false);
    }
  }

  function createType(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const weekdays = [0, 1, 2, 3, 4, 5, 6].filter((day) => data.get(`day_${day}`) === "on");
    const rules = buildRules(weekdays, String(data.get("start")), String(data.get("end")));
    if (!rules || !me) {
      setError("invalid_booking_type");
      return;
    }
    void run(async () => {
      await post(`${workspace}/booking-types`, {
        name: String(data.get("name") ?? "").trim(),
        host_user_id: me,
        duration_minutes: Number(data.get("duration")),
        time_zone: zone,
        rules,
      });
      form.reset();
    });
  }

  if (!appointments) return <p className="muted">{error ? message(error) : t.common.loading}</p>;

  return (
    <section className="stack">
      <h1>{t.calendar.title}</h1>
      {error ? (
        <p className="error" role="alert">
          {error === "invalid_booking_type" ? t.calendar.invalidType : message(error)}
        </p>
      ) : null}

      <section className="card" aria-labelledby="upcoming">
        <h2 id="upcoming">{t.calendar.upcoming}</h2>
        {appointments.length === 0 ? <p className="muted">{t.calendar.empty}</p> : null}
        <ul className="list">
          {appointments.map((item) => (
            <li key={item.appointment_id}>
              {/* Shown in the appointment's own time zone, which is named. */}
              <Ltr>{formatDateTime(item.starts_at, locale, item.time_zone)}</Ltr>{" "}
              <span className="muted">
                <Ltr>{item.time_zone}</Ltr>
              </span>{" "}
              · {t.calendar.status[item.status]} · {t.calendar.source[item.source]}
              <div className="row wrap">
                <a href={`/w/${seg(tenantId)}/contacts/${seg(item.contact_id)}`}>{t.calendar.viewContact}</a>
                {item.status === "booked" ? (
                  cancelling === item.appointment_id ? (
                    <>
                      <span>{t.calendar.cancelConfirm}</span>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          setCancelling(null);
                          void run(() => post(`${workspace}/appointments/${seg(item.appointment_id)}/cancel`, {}));
                        }}
                      >
                        {t.calendar.cancelYes}
                      </button>
                      <button type="button" onClick={() => setCancelling(null)}>
                        {t.calendar.keep}
                      </button>
                    </>
                  ) : (
                    <button type="button" onClick={() => setCancelling(item.appointment_id)}>
                      {t.calendar.cancel}
                    </button>
                  )
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="card" aria-labelledby="booking-pages">
        <h2 id="booking-pages">{t.calendar.bookingPages}</h2>
        {types.length === 0 ? <p className="muted">{t.calendar.noPages}</p> : null}
        <ul className="list">
          {types.map((item) => (
            <li key={item.booking_type_id}>
              <strong>
                <bdi>{item.name}</bdi>
              </strong>{" "}
              · {item.duration_minutes} {t.booking.minutes} · {t.forms.status[item.status]}
              <div>
                <Ltr>{`${origin}/b/${item.public_key}`}</Ltr>
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    post(`${workspace}/booking-types/${seg(item.booking_type_id)}/status`, {
                      status: item.status === "active" ? "disabled" : "active",
                    }),
                  )
                }
              >
                {item.status === "active" ? t.forms.disable : t.forms.enable}
              </button>
            </li>
          ))}
        </ul>
        <form onSubmit={createType} className="stack">
          <h3>{t.calendar.newPage}</h3>
          <label className="field">
            <span>{t.forms.name}</span>
            <input name="name" required maxLength={100} />
          </label>
          <label className="field">
            <span>{t.calendar.duration}</span>
            <input name="duration" type="number" dir="ltr" min={5} max={480} step={5} defaultValue={30} required />
          </label>
          <fieldset className="stack">
            <legend>{t.calendar.days}</legend>
            <div className="row wrap">
              {t.calendar.weekdays.map((label, day) => (
                <label key={label} className="consent">
                  <input type="checkbox" name={`day_${day}`} defaultChecked={day < 5} />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="row wrap">
            <label className="field">
              <span>{t.calendar.from}</span>
              <input name="start" type="time" dir="ltr" step={300} defaultValue="09:00" required />
            </label>
            <label className="field">
              <span>{t.calendar.to}</span>
              <input name="end" type="time" dir="ltr" step={300} defaultValue="17:00" required />
            </label>
          </div>
          <p className="muted">
            {t.booking.timesIn} <Ltr>{zone}</Ltr>
          </p>
          <button type="submit" className="primary" disabled={busy || !me}>
            {t.contacts.save}
          </button>
          {me ? null : <p className="muted">{t.calendar.hostUnknown}</p>}
        </form>
      </section>
    </section>
  );
}
