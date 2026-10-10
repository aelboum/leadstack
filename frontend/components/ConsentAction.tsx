"use client";

import { useState } from "react";
import { useI18n } from "@/components/I18nProvider";

/**
 * Confirm / unsubscribe pages. The action needs a click (POST): mail
 * scanners that prefetch links must not confirm or unsubscribe anyone.
 */
export function ConsentAction({ token, action }: { token: string; action: "confirm" | "unsubscribe" }) {
  const { t } = useI18n();
  const [state, setState] = useState<"idle" | "sending" | "done" | "invalid" | "error">("idle");
  const copy = action === "confirm" ? t.consent.confirm : t.consent.unsubscribe;

  async function run() {
    setState("sending");
    const response = await fetch(`/v1/public/consent/${action}`, {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    }).catch(() => null);
    if (response?.status === 204) setState("done");
    else if (response?.status === 400 || response?.status === 422) setState("invalid");
    else setState("error");
  }

  if (state === "done") return <p role="status">{copy.done}</p>;
  if (state === "invalid") return <p className="error" role="alert">{t.consent.invalid}</p>;
  return (
    <section className="card">
      <h1>{copy.title}</h1>
      <p>{copy.explain}</p>
      <button type="button" className="primary" disabled={state === "sending"} onClick={() => void run()}>
        {copy.button}
      </button>
      {state === "error" ? <p className="error" role="alert">{t.errors.generic}</p> : null}
    </section>
  );
}
