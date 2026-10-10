// Thin client for the LeadStack API on the same origin. The session is the
// SaaS-OS HttpOnly cookie; this code never sees or stores a token.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
  }
}

type Json = Record<string, unknown>;

function errorCode(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && "code" in detail && typeof detail.code === "string") {
    return detail.code;
  }
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 404) return "notFound";
  if (status === 429) return "rateLimited";
  return "generic";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!path.startsWith("/v1/")) throw new Error("API paths must start with /v1/");
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  });
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, errorCode(response.status, body));
  return body as T;
}

export const post = <T>(path: string, data: Json) => api<T>(path, { method: "POST", body: JSON.stringify(data) });

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Workspace {
  tenant_id: string;
  kind: "agency" | "client" | "direct";
  display_name: string;
  locale: string;
  time_zone: string;
  currency: string;
  country: string | null;
  status: string;
  parent_tenant_id: string | null;
}

export interface Contact {
  contact_id: string;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  phone_e164: string | null;
  company_id: string | null;
  created_at: string;
}

export interface Company {
  company_id: string;
  name: string;
  website: string | null;
  phone_e164: string | null;
  created_at: string;
}

/** Path segments are always encoded; ids come from the API, never from free text. */
export const seg = (value: string) => encodeURIComponent(value);

export interface Stage {
  stage_id: string;
  name: string;
  position: number;
  probability: number;
  kind: "open" | "won" | "lost";
}

export interface Pipeline {
  pipeline_id: string;
  name: string;
  stages: Stage[];
}

export interface Deal {
  deal_id: string;
  stage_id: string;
  title: string;
  amount: string | null;
  currency: string | null;
  status: "open" | "won" | "lost";
}

export interface StageSummary {
  stage_id: string;
  deal_count: number;
  /** Exact decimal strings per currency (never JSON numbers). */
  totals: Record<string, string>;
  weighted: Record<string, string>;
}
