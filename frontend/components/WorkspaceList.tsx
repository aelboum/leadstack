"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useErrorMessage, useI18n } from "@/components/I18nProvider";
import { api, ApiError, seg, type Page, type Workspace } from "@/lib/api";

type State =
  | { kind: "loading" }
  | { kind: "signedOut" }
  | { kind: "error"; code: string }
  | { kind: "ready"; items: Workspace[] };

export function WorkspaceList() {
  const { t } = useI18n();
  const message = useErrorMessage();
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    api<Page<Workspace>>("/v1/workspaces")
      .then((page) => setState({ kind: "ready", items: page.items }))
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 401) setState({ kind: "signedOut" });
        else setState({ kind: "error", code: error instanceof ApiError ? error.code : "generic" });
      });
  }, []);

  if (state.kind === "loading") return <p className="muted">{t.common.loading}</p>;
  if (state.kind === "signedOut")
    return (
      <section className="card">
        <p>{t.auth.signInRequired}</p>
        {/* SaaS-OS hosted login (OIDC); returns to "/" after sign-in. */}
        <a href="/auth/login">{t.auth.signIn}</a>
      </section>
    );
  if (state.kind === "error") return <p className="error" role="alert">{message(state.code)}</p>;

  return (
    <section className="card">
      <h1>{t.workspaces.title}</h1>
      {state.items.length === 0 ? (
        <p className="muted">{t.workspaces.empty}</p>
      ) : (
        <ul className="list">
          {state.items.map((workspace) => (
            <li key={workspace.tenant_id}>
              <span>
                <strong>{workspace.display_name}</strong>{" "}
                <span className="muted">{t.workspaces.kind[workspace.kind]}</span>
              </span>
              <Link href={`/w/${seg(workspace.tenant_id)}/contacts`}>{t.workspaces.open}</Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
