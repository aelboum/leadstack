"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useErrorMessage, useI18n } from "@/components/I18nProvider";
import { Ltr } from "@/components/Ltr";
import { api, ApiError, post, seg } from "@/lib/api";

interface LeadForm {
  form_id: string;
  name: string;
  public_key: string;
  status: "active" | "disabled";
}

type FieldType = "text" | "email" | "phone" | "textarea";
type Mapping = "first_name" | "last_name" | "email" | "phone" | "message";

export interface FieldChoice {
  key: Mapping;
  type: FieldType;
}

export const FIELD_CHOICES: readonly FieldChoice[] = [
  { key: "first_name", type: "text" },
  { key: "last_name", type: "text" },
  { key: "email", type: "email" },
  { key: "phone", type: "phone" },
  { key: "message", type: "textarea" },
];

/** The API payload for the chosen fields; a form always asks for an email address. */
export function buildFields(chosen: ReadonlySet<Mapping>, labels: Record<Mapping, string>) {
  return FIELD_CHOICES.filter((choice) => choice.key === "email" || chosen.has(choice.key)).map((choice) => ({
    key: choice.key,
    label: labels[choice.key],
    type: choice.type,
    required: choice.key === "email",
    maps_to: choice.key,
  }));
}

export function FormsView({ tenantId }: { tenantId: string }) {
  const { t } = useI18n();
  const message = useErrorMessage();
  const base = `/v1/workspaces/${seg(tenantId)}/forms`;
  const [forms, setForms] = useState<LeadForm[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const origin = typeof window === "undefined" ? "" : window.location.origin;

  const load = useCallback(async () => {
    try {
      setForms(await api<LeadForm[]>(base));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.code : "generic");
    }
  }, [base]);

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

  function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const chosen = new Set(FIELD_CHOICES.map((c) => c.key).filter((key) => data.get(`field_${key}`) === "on"));
    void run(async () => {
      await post(base, {
        name: String(data.get("name") ?? "").trim(),
        consent_text: String(data.get("consent_text") ?? "").trim(),
        fields: buildFields(chosen, t.forms.fieldLabels),
      });
      form.reset();
    });
  }

  if (!forms) return <p className="muted">{error ? message(error) : t.common.loading}</p>;

  return (
    <section className="stack">
      <h1>{t.forms.title}</h1>
      {error ? (
        <p className="error" role="alert">
          {message(error)}
        </p>
      ) : null}
      {forms.length === 0 ? <p className="muted">{t.forms.empty}</p> : null}
      <ul className="list">
        {forms.map((item) => (
          <li key={item.form_id}>
            <strong>
              <bdi>{item.name}</bdi>
            </strong>{" "}
            · {t.forms.status[item.status]}
            <div>
              <Ltr>{`${origin}/f/${item.public_key}`}</Ltr>
            </div>
            <div className="row wrap">
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    post(`${base}/${seg(item.form_id)}/status`, {
                      status: item.status === "active" ? "disabled" : "active",
                    }),
                  )
                }
              >
                {item.status === "active" ? t.forms.disable : t.forms.enable}
              </button>
              {deleting === item.form_id ? (
                <>
                  <span>{t.forms.deleteConfirm}</span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setDeleting(null);
                      void run(() => api(`${base}/${seg(item.form_id)}`, { method: "DELETE" }));
                    }}
                  >
                    {t.forms.deleteYes}
                  </button>
                  <button type="button" onClick={() => setDeleting(null)}>
                    {t.contactDetail.cancel}
                  </button>
                </>
              ) : (
                <button type="button" onClick={() => setDeleting(item.form_id)}>
                  {t.forms.delete}
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>

      <section className="card" aria-labelledby="new-form">
        <h2 id="new-form">{t.forms.create}</h2>
        <form onSubmit={create} className="stack">
          <label className="field">
            <span>{t.forms.name}</span>
            <input name="name" required maxLength={100} />
          </label>
          <fieldset className="stack">
            <legend>{t.forms.fields}</legend>
            {FIELD_CHOICES.map((choice) => (
              <label key={choice.key} className="consent">
                {choice.key === "email" ? (
                  <input type="checkbox" checked disabled readOnly />
                ) : (
                  <input type="checkbox" name={`field_${choice.key}`} defaultChecked={choice.key !== "message"} />
                )}
                <span>{t.forms.fieldLabels[choice.key]}</span>
              </label>
            ))}
          </fieldset>
          <label className="field">
            <span>{t.forms.consentText}</span>
            <textarea name="consent_text" required maxLength={2000} rows={3} />
          </label>
          <button type="submit" className="primary" disabled={busy}>
            {t.contacts.save}
          </button>
        </form>
      </section>
    </section>
  );
}
