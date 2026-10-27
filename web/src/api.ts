import type {
  Bbox, DemoArea, Entrance, EntranceReview, Issue, IssueReview, Preview, Scan, ServerConfig,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
  reviewEntrance: (id: number, body: EntranceReview) => request<Entrance>(`/api/entrances/${id}/review`, post(body)),
  reviewIssue: (id: number, body: IssueReview) => request<Issue>(`/api/issues/${id}/review`, post(body)),
};
