import type {
  Bbox, DemoArea, Entrance, EntranceReview, Issue, IssueReview, ItemRun, ItemRunSummary, Preview, Scan, ServerConfig,
  Walk,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

// The public demo is a static site (deploy/static_site.py): each GET answer is a file at <path>.json, and nothing
// can be changed.
export const STATIC_DEMO = import.meta.env.VITE_STATIC_DEMO === "1";
const READ_ONLY = "This public demo is read-only. Run Fieldless with your own keys to survey new streets.";

export const staticPath = (path: string) => `${path}.json`;

// A finished survey's files (report, GeoJSON, CSV per item, evidence photos); the static demo has the same zip.
export const filesUrl = (scanId: number) => `/api/scans/${scanId}/files.zip`;

// A finished survey's results report, a document page for the city; the static demo keeps it as a file.
export const reportUrl = (scanId: number, isStatic = STATIC_DEMO) =>
  `/api/scans/${scanId}/report${isStatic ? ".html" : ""}`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (STATIC_DEMO) {
    if (init?.method && init.method !== "GET") throw new ApiError(405, READ_ONLY);
    path = staticPath(path);
  }
  let res: Response;
  try {
    res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  } catch {
    throw new ApiError(0, "Could not reach the server. Check the connection and try again.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // the body was not JSON; keep the status text
    }
    throw new ApiError(res.status, detail);
  }
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError(res.status, "The server sent an unexpected answer.");
  }
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  config: () => request<ServerConfig>("/api/config"),
  demo: () => request<DemoArea[]>("/api/demo"),
  preview: (bbox: Bbox) => request<Preview>("/api/preview", post({ bbox })),
  startScan: (bbox: Bbox, inviteCode: string) =>
    request<{ id: number }>("/api/scans", post({ bbox, invite_code: inviteCode || null })),
  scan: (id: number) => request<Scan>(`/api/scans/${id}`),
  entrances: (id: number) => request<Entrance[]>(`/api/scans/${id}/entrances`),
  issues: (id: number) => request<Issue[]>(`/api/scans/${id}/issues`),
  reviewEntrance: (id: number | string, body: EntranceReview) => request<Entrance>(`/api/entrances/${id}/review`, post(body)),
  reviewIssue: (id: number | string, body: IssueReview) => request<Issue>(`/api/issues/${id}/review`, post(body)),
  walks: (id: number) => request<Walk[]>(`/api/scans/${id}/walks`),
  // items the agent writes in the app (docs/specs/2026-10-29-agent-items-design.md)
  itemRun: (id: number) => request<ItemRun>(`/api/items/${id}`),
  scanItems: (scanId: number) => request<ItemRunSummary[]>(`/api/scans/${scanId}/items`),
  startItem: (scanId: number, target: string, inviteCode: string) =>
    request<{ id: number }>(`/api/scans/${scanId}/items`, post({ target, invite_code: inviteCode || null })),
  sendLabels: (id: number, labels: Record<number, "yes" | "no" | "unclear">) =>
    request<unknown>(`/api/items/${id}/labels`, post({ labels })),
  undoEntrance: (id: number | string) => request<Entrance>(`/api/entrances/${id}/review/undo`, post({})),
  undoIssue: (id: number | string) => request<Issue>(`/api/issues/${id}/review/undo`, post({})),
};
