"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useErrorMessage, useI18n } from "@/components/I18nProvider";
import { ApiError, seg } from "@/lib/api";

interface FieldDef {
  key: string;
  label: string;
  type: "text" | "email" | "phone" | "textarea";
  required: boolean;
}

interface Definition {
  name: string;
  fields: FieldDef[];
  consent_text: string;
  success_message: string | null;
  redirect_url: string | null;
  form_token: string;
}

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

/** Only https redirects are ever followed (the backend enforces this too). */
export function safeRedirect(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

export function PublicForm({ publicKey }: { publicKey: string }) {
  const { t } = useI18n();
  const message = useErrorMessage();
  const [definition, setDefinition] = useState<Definition | null>(null);
  const [state, setState] = useState<"idle" | "sending" | "done" | "missing">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    publicCall<Definition>(`/v1/public/forms/${seg(publicKey)}`)
      .then(setDefinition)
      .catch(() => setState("missing"));
  }, [publicKey]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!definition) return;
    const data = new FormData(event.currentTarget);
    const values: Record<string, string> = {};
    for (const field of definition.fields) values[field.key] = String(data.get(field.key) ?? "");
    setState("sending");
    setError(null);
    try {
      const result = await publicCall<{ success_message: string | null; redirect_url: string | null }>(
        `/v1/public/forms/${seg(publicKey)}/submissions`,
        {
          method: "POST",
          body: JSON.stringify({
            values,
            consent: data.get("consent") === "on",
            form_token: definition.form_token,
            website: String(data.get("website") ?? ""),
          }),
        },
      );
      const target = safeRedirect(result.redirect_url);
      if (target) window.location.assign(target);
      else setState("done");
    } catch (caught) {
      setState("idle");
      setError(caught instanceof ApiError ? caught.code : "generic");
    }
  }

  if (state === "missing") return <p className="error">{t.errors.notFound}</p>;
  if (!definition) return <p className="muted">{t.common.loading}</p>;
  if (state === "done")
    return (
      <p role="status">
        <bdi>{definition.success_message ?? t.publicForm.thanks}</bdi>
      </p>
    );

  return (
    <section className="card">
      <h1>
        <bdi>{definition.name}</bdi>
      </h1>
      <form onSubmit={submit} className="stack" noValidate>
        {definition.fields.map((field) => (
          <label key={field.key} className="field">
            <span>
              <bdi>{field.label}</bdi>
              {field.required ? " *" : null}
            </span>
            {field.type === "textarea" ? (
              <textarea name={field.key} required={field.required} maxLength={5000} rows={4} />
            ) : (
              <input
                name={field.key}
                type={field.type === "phone" ? "tel" : field.type}
                dir={field.type === "text" ? undefined : "ltr"}
                required={field.required}
                maxLength={field.type === "email" ? 254 : 200}
              />
            )}
          </label>
        ))}
        {/* Honeypot: hidden from people and assistive technology; bots fill it. */}
        <div aria-hidden="true" className="visually-hidden">
          <label>
            Website
            <input name="website" tabIndex={-1} autoComplete="off" />
          </label>
        </div>
        <label className="consent">
          <input type="checkbox" name="consent" required />
          <span>
            <bdi>{definition.consent_text}</bdi>
          </span>
        </label>
        <button type="submit" className="primary" disabled={state === "sending"}>
          {state === "sending" ? t.contacts.saving : t.publicForm.submit}
        </button>
        {error ? (
          <p className="error" role="alert">
            {error === "invalid_submission" ? t.publicForm.invalid : message(error)}
          </p>
        ) : null}
      </form>
    </section>
  );
}
