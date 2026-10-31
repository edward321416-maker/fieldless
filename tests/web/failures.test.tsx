import { act, useEffect, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../../web/src/App";

// Case 6: what the screen shows when an API call fails. The whole App runs against a fake fetch; only the map is left
// out (it needs WebGL). `gap` marks what the app should do and does not yet: the test passes while the gap is open and
// fails the day it is closed, when `gap` becomes `it`. Run with SHOW_GAPS=1 to see each gap fail on its own assertion.
const gap = process.env.SHOW_GAPS ? it : it.fails;

const BBOX = [-122.32, 47.61, -122.3185, 47.611];

vi.mock("../../web/src/MapView", () => ({
  default: (props: { children?: ReactNode; onReady: (fn: () => number[]) => void }) => {
    useEffect(() => props.onReady(() => BBOX), []);
    return <div data-testid="map">{props.children}</div>;
  },
}));

type Reply = { status?: number; json?: unknown; html?: string };
type Routes = Record<string, Reply | Reply[]>;

const down = (detail: string, status = 503): Reply => ({ status, json: { detail } });
// What a host in front of the server sends, or FastAPI on an uncaught error: not JSON, and over HTTP/2 no status text.
const edge = (status = 503): Reply => ({ status, html: "<html><body>Service Unavailable</body></html>" });

const counts = { defects: 1, obstructions: 0, entrances: 0, entrances_with_steps: 0, needs_review: 0, field_check: 0,
  verified: 0, rejected: 0, dropped: 0 };
const survey = (over: object = {}) => ({
  id: 17, status: "done", stage_status: {}, bbox: BBOX, image_count: 60, captured_from: "2024-05-01T00:00:00Z",
  captured_to: "2025-09-24T00:00:00Z", cost_usd: 4.17, processing_seconds: 420, review_seconds: 0, error: null,
  standard: "US", checks: [], items: [{ id: "curb_ramp", target: "curb ramp", map_icon: "", kind: "check", inventory: false }],
  km: 0.94, usd_per_km: 4.44, counts, ...over });
const issue = { id: 5, lat: 47.6101, lng: -122.3195, heading: 90, box: null, issue_type: "curb_ramp", durable: true,
  defect: true, label: { icon: "!", text: "Curb ramp defect" }, check: null, second_opinion: null, object_label: "no",
  frame_start: 1, frame_end: 1, review_status: "auto", review_reasons: [], evidence: null,
  captured_at: "2025-09-24T00:00:00Z", old_photo: false };
const running = (over: object = {}) => ({ id: 4, scan_id: 17, target: "bench", status: "running", progress: ["Drafting"],
  photos: [], item: null, issues: [], cost_usd: 0.1, error: null, test: null, created_at: "2026-11-05T00:00:00Z", ...over });

const healthy: Routes = {
  "GET /api/config": { json: { invite_required: false, max_bbox_deg2: 0.0001, items: true, make_items: true,
    coverage_tiles: true, report: true } },
  "GET /api/demo": { json: [{ scan_id: 17, name: "Seattle", bbox: BBOX }, { scan_id: 1, name: "Seongsu", bbox: BBOX }] },
  "GET /api/scans/17": { json: survey() },
  "GET /api/scans/17/issues": { json: [issue] },
  "GET /api/scans/17/entrances": { json: [] },
  "GET /api/scans/17/items": { json: [] },
  "GET /api/scans/17/walks": { json: [] },
  "GET /api/scans/1": { json: survey({ id: 1, standard: "KR" }) },
};

let roots: Root[] = [];
let mounts: HTMLElement[] = [];

function serve(changes: Routes): void {
  const routes = { ...healthy, ...changes };
  const left = new Map(Object.entries(routes).map(([k, v]) => [k, Array.isArray(v) ? [...v] : [v]]));
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    const replies = left.get(`${init?.method ?? "GET"} ${path}`);
    const reply = !replies ? down("not found", 404) : replies.length > 1 ? replies.shift()! : replies[0];
    const status = reply.status ?? 200;
    return { ok: status < 400, status, statusText: "",
      json: async () => {
        if (reply.html !== undefined) throw new SyntaxError("not JSON");
        return reply.json;
      } };
  });
}

// Lets every promise and React update settle; with seconds, moves the polling timers on.
async function settle(seconds = 0): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 25; i++) await Promise.resolve();
    if (seconds) await vi.advanceTimersByTimeAsync(seconds * 1000);
    for (let i = 0; i < 25; i++) await Promise.resolve();
  });
}

async function open(hash = ""): Promise<HTMLElement> {
  window.location.hash = hash;
  const mount = document.createElement("div");
  document.body.append(mount);
  const root = createRoot(mount);
  roots.push(root);
  mounts.push(mount);
  await act(async () => root.render(<App />));
  await settle();
  return mount;
}

const alertText = (screen: HTMLElement) => screen.querySelector('[role="alert"]')?.textContent ?? null;

function click(screen: HTMLElement, name: string): Promise<void> {
  const button = [...screen.querySelectorAll("button")].find((b) => b.textContent?.includes(name));
  expect(button, `a button named ${name}`).toBeTruthy();
  return act(async () => button!.click());
}

async function newSurvey(screen: HTMLElement): Promise<void> {
  await click(screen, "+ New survey");
  await click(screen, "Use this map view");
  await settle();
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia = (() => ({ matches: true, addEventListener() {}, removeEventListener() {} })) as never;
  Element.prototype.scrollIntoView = () => undefined;
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  act(() => roots.forEach((r) => r.unmount()));
  mounts.forEach((m) => m.remove());
  roots = [];
  mounts = [];
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the first screen", () => {
  it("opens on the first example when every call works", async () => {
    serve({});
    const screen = await open();
    expect(screen.textContent).toContain("See the finished survey of Seattle");
    expect(alertText(screen)).toBeNull();
  });

  gap("says so when /api/demo fails", async () => {
    serve({ "GET /api/demo": down("Service Unavailable") });
    expect(alertText(await open())).not.toBeNull();
  });

  gap("tries /api/demo again after a failure", async () => {
    serve({ "GET /api/demo": [down("Service Unavailable"), healthy["GET /api/demo"] as Reply] });
    const screen = await open();
    await settle(30);
    expect(screen.textContent).toContain("See the finished survey of Seattle");
  });

  it("shows a failed /api/scans/17 and recovers on the next poll", async () => {
    serve({ "GET /api/scans/17": [down("Service Unavailable"), healthy["GET /api/scans/17"] as Reply] });
    const screen = await open();
    expect(alertText(screen)).toBe("Service Unavailable");
    await settle(3);
    expect(alertText(screen)).toBeNull();
    expect(screen.textContent).toContain("See the finished survey of Seattle");
  });

  gap("shows a message when a 503 is not JSON", async () => {
    serve({ "GET /api/scans/17": edge() });
    expect(alertText(await open())).toBeTruthy();
  });

  it("shows a failed /api/scans/17/issues", async () => {
    serve({ "GET /api/scans/17/issues": down("Service Unavailable") });
    expect(alertText(await open())).toBe("Service Unavailable");
  });

  gap("loads the issues again after one failure", async () => {
    serve({ "GET /api/scans/17/issues": [down("Service Unavailable"), healthy["GET /api/scans/17/issues"] as Reply] });
    const screen = await open();
    await settle(30);
    expect(screen.textContent).toContain("See the finished survey of Seattle");
  });

  it("stays whole when /api/scans/17/items fails", async () => {
    serve({ "GET /api/scans/17/items": down("Service Unavailable") });
    const screen = await open();
    expect(screen.textContent).toContain("See the finished survey of Seattle");
    expect(alertText(screen)).toBeNull();
  });

  it("leaves out the other example when /api/scans/1 fails", async () => {
    serve({ "GET /api/scans/1": down("Service Unavailable") });
    const screen = await open();
    expect(screen.textContent).toContain("See the finished survey of Seattle");
    expect(screen.textContent).not.toContain("Seongsu standard");
  });

  it("says a survey in the address does not exist", async () => {
    serve({ "GET /api/scans/99": down("scan 99 not found", 404) });
    expect(alertText(await open("#scan=99"))).toBe("scan 99 not found");
  });

  gap("gets the report link and add-item back after /api/config fails once", async () => {
    serve({ "GET /api/config": [down("Service Unavailable"), healthy["GET /api/config"] as Reply] });
    const screen = await open("#scan=17");
    await settle(30);
    expect(screen.textContent).toContain("Survey report");
  });

  gap("shows a message, not a blank page, when a reply makes the page throw", async () => {
    serve({ "GET /api/scans/17": { json: survey({ counts: undefined }) } });
    let screen: HTMLElement | null = null;
    try {
      screen = await open("#scan=17");
    } catch {
      screen = mounts[mounts.length - 1]; // React unmounted everything and rethrew
    }
    expect(screen!.textContent).not.toBe("");
  });
});

describe("a survey that did not finish", () => {
  const scan19 = (over: object): Routes => ({
    "GET /api/scans/19": { json: survey({ id: 19, km: null, usd_per_km: null, ...over }) },
    "GET /api/scans/19/issues": { json: [] }, "GET /api/scans/19/entrances": { json: [] },
    "GET /api/scans/19/items": { json: [] }, "GET /api/scans/19/walks": { json: [] },
  });

  gap("says when an area has no photos", async () => {
    serve(scan19({ status: "failed", image_count: 0, error: "NoPhotos: no recent pedestrian photos in this area" }));
    expect((await open("#scan=19")).textContent).toContain("No recent pedestrian photos were found here");
  });

  gap("says when it failed", async () => {
    serve(scan19({ status: "failed", error: "5 requests in a row failed" }));
    expect((await open("#scan=19")).textContent).toContain("did not finish every step");
  });

  gap("says when it stopped at the cap", async () => {
    serve(scan19({ status: "stopped_cost_cap", error: "cost cap $3.00" }));
    expect((await open("#scan=19")).textContent).toContain("Stopped at the spending cap");
  });

  it("shows its stages while it runs", async () => {
    serve(scan19({ status: "running", stage_status: { collect: "running" } }));
    expect((await open("#scan=19")).textContent).toContain("Survey 19: running");
  });

  gap("stops showing a survey as running once the server has lost it", async () => {
    serve({ "GET /api/scans/19": [{ json: survey({ id: 19, status: "running" }) }, down("scan 19 not found", 404)] });
    const screen = await open("#scan=19");
    await settle(3);
    expect(alertText(screen)).toBe("scan 19 not found");
    expect(screen.textContent).not.toContain("Survey 19: running");
  });
});

describe("the new-survey flow", () => {
  it("shows why a preview failed", async () => {
    serve({ "POST /api/preview": down("Mapillary did not answer. Try again in a minute.", 502) });
    const screen = await open("#scan=17");
    await newSurvey(screen);
    expect(alertText(screen)).toBe("Mapillary did not answer. Try again in a minute.");
  });

  gap("shows a message when a failed preview is not JSON", async () => {
    serve({ "POST /api/preview": edge(502) });
    const screen = await open("#scan=17");
    await newSurvey(screen);
    expect(alertText(screen)).toBeTruthy();
  });

  it("shows why a start was refused", async () => {
    const preview = { json: { photos: 42, sequences: 2, captured_from: null, captured_to: null, est_cost_usd: 1, items: [] } };
    serve({ "POST /api/preview": preview, "POST /api/scans": down("Today's survey budget is used up. Try again tomorrow (UTC).") });
    const screen = await open("#scan=17");
    await newSurvey(screen);
    await click(screen, "Start survey");
    await settle();
    expect(alertText(screen)).toContain("Today's survey budget is used up");
  });

  gap("shows a message when a failed start is not JSON", async () => {
    const preview = { json: { photos: 42, sequences: 2, captured_from: null, captured_to: null, est_cost_usd: 1, items: [] } };
    serve({ "POST /api/preview": preview, "POST /api/scans": edge(500) });
    const screen = await open("#scan=17");
    await newSurvey(screen);
    await click(screen, "Start survey");
    await settle();
    expect(alertText(screen)).toBeTruthy();
  });
});

describe("the add-item flow", () => {
  async function submitItem(screen: HTMLElement): Promise<void> {
    await click(screen, "+ Add a survey item");
    const [target, code] = [...screen.querySelectorAll<HTMLInputElement>(".add-item-form input")];
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      set.call(target, "bench");
      target.dispatchEvent(new Event("input", { bubbles: true }));
      set.call(code, "code");
      code.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => screen.querySelector<HTMLFormElement>(".add-item-form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await settle();
  }

  it("shows why a start was refused", async () => {
    serve({ "POST /api/scans/17/items": down("A survey or an item is running; try again in a few minutes.", 409) });
    const screen = await open("#scan=17");
    await submitItem(screen);
    expect(alertText(screen)).toContain("A survey or an item is running");
  });

  gap("shows a message when a failed start is not JSON", async () => {
    serve({ "POST /api/scans/17/items": edge() });
    const screen = await open("#scan=17");
    await submitItem(screen);
    expect(alertText(screen)).toBeTruthy();
  });

  it("keeps polling through a 503 and shows why a run failed", async () => {
    serve({ "GET /api/items/4": [down("Service Unavailable"), { json: running({ status: "failed", error: "No reliable wording found" }) }] });
    const screen = await open("#scan=17&item=4");
    await settle(3);
    expect(screen.textContent).toContain("No reliable wording found");
  });

  gap("says so when the server has lost a run", async () => {
    serve({ "GET /api/items/4": [{ json: running() }, down("item run 4 not found", 404)] });
    const screen = await open("#scan=17&item=4");
    await settle(3);
    expect(screen.textContent).toContain("not found");
  });
});
