"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useErrorMessage, useI18n } from "@/components/I18nProvider";
import { api, ApiError, post, seg } from "@/lib/api";
import type { Dictionary } from "@/lib/i18n/dictionaries";

type Trigger = "contact.created" | "form.submitted" | "deal.stage_changed";
const TRIGGERS: readonly Trigger[] = ["contact.created", "form.submitted", "deal.stage_changed"];

interface Workflow {
  workflow_id: string;
  name: string;
  trigger: Trigger;
  status: "active" | "paused";
  paused_reason: string | null;
}

interface Tag {
  tag_id: string;
  name: string;
}

/** Dictionary keys never contain dots: "contact.created" -> "contact_created". */
export function triggerLabel(trigger: string, t: Dictionary): string {
  const labels = t.workflows.triggers as Record<string, string>;
  return labels[trigger.replaceAll(".", "_")] ?? trigger;
}

/** The single action this simple builder supports per workflow. */
export function buildAction(kind: string, tagId: string, title: string): Record<string, unknown> | null {
  if (kind === "add_tag") return tagId ? { type: "add_tag", tag_id: tagId } : null;
  const trimmed = title.trim();
  return trimmed ? { type: "create_task", title: trimmed } : null;
}

export function WorkflowsView({ tenantId }: { tenantId: string }) {
  const { t } = useI18n();
  const message = useErrorMessage();
  const workspace = `/v1/workspaces/${seg(tenantId)}`;
  const base = `${workspace}/workflows`;
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null);
  const [tags, setTags] = useState<Tag[]>([]);
  const [kind, setKind] = useState("create_task");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, available] = await Promise.all([api<Workflow[]>(base), api<Tag[]>(`${workspace}/tags`)]);
      setWorkflows(list);
      setTags(available);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.code : "generic");
    }
  }, [base, workspace]);

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
    const action = buildAction(kind, String(data.get("tag_id") ?? ""), String(data.get("title") ?? ""));
    if (!action) {
      setError("invalid_workflow");
      return;
    }
    void run(async () => {
      await post(base, {
        name: String(data.get("name") ?? "").trim(),
        trigger: String(data.get("trigger")),
        actions: [action],
      });
      form.reset();
    });
  }

  if (!workflows) return <p className="muted">{error ? message(error) : t.common.loading}</p>;

  return (
    <section className="stack">
      <h1>{t.workflows.title}</h1>
      <p className="muted">{t.workflows.explain}</p>
      {error ? (
        <p className="error" role="alert">
          {error === "invalid_workflow" ? t.workflows.invalid : message(error)}
        </p>
      ) : null}
      {workflows.length === 0 ? <p className="muted">{t.workflows.empty}</p> : null}
      <ul className="list">
        {workflows.map((item) => (
          <li key={item.workflow_id}>
            <strong>
              <bdi>{item.name}</bdi>
            </strong>{" "}
            · {triggerLabel(item.trigger, t)} · {t.workflows.status[item.status]}
            {item.status === "paused" && item.paused_reason ? ` (${t.workflows.pausedAutomatically})` : null}
            <div className="row wrap">
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    post(`${base}/${seg(item.workflow_id)}/status`, {
                      status: item.status === "active" ? "paused" : "active",
                    }),
                  )
                }
              >
                {item.status === "active" ? t.workflows.pause : t.workflows.resume}
              </button>
              {deleting === item.workflow_id ? (
                <>
                  <span>{t.workflows.deleteConfirm}</span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setDeleting(null);
                      void run(() => api(`${base}/${seg(item.workflow_id)}`, { method: "DELETE" }));
                    }}
                  >
                    {t.forms.deleteYes}
                  </button>
                  <button type="button" onClick={() => setDeleting(null)}>
                    {t.contactDetail.cancel}
                  </button>
                </>
              ) : (
                <button type="button" onClick={() => setDeleting(item.workflow_id)}>
                  {t.forms.delete}
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>

      <section className="card" aria-labelledby="new-workflow">
        <h2 id="new-workflow">{t.workflows.create}</h2>
        <form onSubmit={create} className="stack">
          <label className="field">
            <span>{t.forms.name}</span>
            <input name="name" required maxLength={100} />
          </label>
          <label className="field">
            <span>{t.workflows.when}</span>
            <select name="trigger" defaultValue="contact.created">
              {TRIGGERS.map((trigger) => (
                <option key={trigger} value={trigger}>
                  {triggerLabel(trigger, t)}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t.workflows.then}</span>
            <select name="kind" value={kind} onChange={(event) => setKind(event.currentTarget.value)}>
              <option value="create_task">{t.workflows.actions.create_task}</option>
              <option value="add_tag" disabled={tags.length === 0}>
                {t.workflows.actions.add_tag}
              </option>
            </select>
          </label>
          {kind === "add_tag" ? (
            <label className="field">
              <span>{t.contactDetail.tags}</span>
              <select name="tag_id" required>
                {tags.map((tag) => (
                  <option key={tag.tag_id} value={tag.tag_id}>
                    {tag.name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label className="field">
              <span>{t.contactDetail.taskTitle}</span>
              <input name="title" required maxLength={200} />
            </label>
          )}
          <button type="submit" className="primary" disabled={busy}>
            {t.contacts.save}
          </button>
        </form>
      </section>
    </section>
  );
}
