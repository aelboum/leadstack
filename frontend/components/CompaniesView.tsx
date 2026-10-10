"use client";

import { useCallback, useEffect, useState } from "react";
import { useErrorMessage, useI18n } from "@/components/I18nProvider";
import { Ltr } from "@/components/Ltr";
import { api, ApiError, seg, type Company, type Page } from "@/lib/api";

export function CompaniesView({ tenantId }: { tenantId: string }) {
  const { t } = useI18n();
  const message = useErrorMessage();
  const [items, setItems] = useState<Company[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    (after: string | null) => {
      const params = new URLSearchParams({ limit: "50" });
      if (after) params.set("cursor", after);
      api<Page<Company>>(`/v1/workspaces/${seg(tenantId)}/companies?${params}`)
        .then((page) => {
          setItems((previous) => (after && previous ? [...previous, ...page.items] : page.items));
          setCursor(page.next_cursor);
        })
        .catch((caught: unknown) => setError(caught instanceof ApiError ? caught.code : "generic"));
    },
    [tenantId],
  );

  useEffect(() => load(null), [load]);

  return (
    <section className="card">
      <h1>{t.companies.title}</h1>
      {error ? (
        <p className="error" role="alert">
          {message(error)}
        </p>
      ) : null}
      {items === null && !error ? <p className="muted">{t.common.loading}</p> : null}
      {items?.length === 0 ? <p className="muted">{t.companies.empty}</p> : null}
      {items && items.length > 0 ? (
        <ul className="list">
          {items.map((company) => (
            <li key={company.company_id}>
              <bdi>{company.name}</bdi>
              {company.phone_e164 ? <Ltr>{company.phone_e164}</Ltr> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {cursor ? (
        <button type="button" onClick={() => load(cursor)}>
          {t.contacts.loadMore}
        </button>
      ) : null}
    </section>
  );
}
