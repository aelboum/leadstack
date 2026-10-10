"use client";

import { useCallback, useEffect, useState } from "react";
import { useErrorMessage, useI18n } from "@/components/I18nProvider";
import { Ltr } from "@/components/Ltr";
import { api, ApiError, post, seg, type Deal, type Page, type Pipeline, type StageSummary } from "@/lib/api";
import { formatMoney } from "@/lib/i18n/locale";

const MAX_BOARD_DEALS = 500;

/** Totals per currency, each formatted in its own currency; never summed across currencies. */
export function CurrencyTotals({ totals }: { totals: Record<string, string> }) {
  const { locale } = useI18n();
  const entries = Object.entries(totals).sort(([a], [b]) => a.localeCompare(b));
  if (entries.length === 0) return null;
  return (
    <ul className="list">
      {entries.map(([currency, amount]) => (
        <li key={currency}>
          <Ltr>{formatMoney(amount, currency, locale)}</Ltr>
        </li>
      ))}
    </ul>
  );
}

export function DealsBoard({ tenantId }: { tenantId: string }) {
  const { t, locale } = useI18n();
  const message = useErrorMessage();
  const base = `/v1/workspaces/${seg(tenantId)}`;
  const [pipeline, setPipeline] = useState<Pipeline | null | undefined>(undefined);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [summary, setSummary] = useState<StageSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const pipelines = await api<Pipeline[]>(`${base}/pipelines`);
      const first = pipelines[0] ?? null;
      setPipeline(first);
      if (!first) return;
      const stages = await api<StageSummary[]>(`${base}/pipelines/${seg(first.pipeline_id)}/summary`);
      // Follow the cursor (bounded); counts and totals come from the summary,
      // so they stay correct even if the board cannot show every deal.
      const all: Deal[] = [];
      let cursor: string | null = null;
      do {
        const params = new URLSearchParams({ pipeline_id: first.pipeline_id, limit: "100" });
        if (cursor) params.set("cursor", cursor);
        const page: Page<Deal> = await api<Page<Deal>>(`${base}/deals?${params}`);
        all.push(...page.items);
        cursor = page.next_cursor;
      } while (cursor && all.length < MAX_BOARD_DEALS);
      setDeals(all);
      setSummary(stages);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.code : "generic");
    }
  }, [base]);

  useEffect(() => {
    void load();
  }, [load]);

  async function move(deal: Deal, stageId: string) {
    try {
      await post(`${base}/deals/${seg(deal.deal_id)}/stage`, { stage_id: stageId });
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.code : "generic");
    }
  }

  async function createDefault() {
    try {
      await post(`${base}/pipelines`, { name: t.deals.title });
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.code : "generic");
    }
  }

  if (error) return <p className="error" role="alert">{message(error)}</p>;
  if (pipeline === undefined) return <p className="muted">{t.common.loading}</p>;
  if (pipeline === null)
    return (
      <section className="card">
        <p className="muted">{t.deals.empty}</p>
        <button type="button" className="primary" onClick={() => void createDefault()}>
          {t.deals.create}
        </button>
      </section>
    );

  return (
    <section className="card">
      <h1>
        <bdi>{pipeline.name}</bdi>
      </h1>
      <div className="board">
        {pipeline.stages.map((stage) => {
          const inStage = deals.filter((d) => d.stage_id === stage.stage_id);
          const stats = summary.find((s) => s.stage_id === stage.stage_id);
          const totals = stats?.totals ?? {};
          return (
            <section key={stage.stage_id} className="column" aria-label={stage.name}>
              <h2>
                <bdi>{stage.name}</bdi> <span className="muted">({stats?.deal_count ?? inStage.length})</span>
              </h2>
              <CurrencyTotals totals={totals} />
              {inStage.length === 0 ? <p className="muted">{t.deals.noDeals}</p> : null}
              <ul className="list">
                {inStage.map((deal) => (
                  <li key={deal.deal_id}>
                    <span>
                      <bdi>{deal.title}</bdi>
                      {deal.amount && deal.currency ? (
                        <>
                          {" "}
                          <Ltr>{formatMoney(deal.amount, deal.currency, locale)}</Ltr>
                        </>
                      ) : null}
                    </span>
                    <label>
                      <span className="visually-hidden">{t.deals.moveTo}</span>
                      <select value={deal.stage_id} onChange={(e) => void move(deal, e.target.value)}>
                        {pipeline.stages.map((s) => (
                          <option key={s.stage_id} value={s.stage_id}>
                            {s.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>
    </section>
  );
}
