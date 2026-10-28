import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, STATIC_DEMO } from "./api";
import { contentBounds } from "./bbox";
import AreaPanel from "./AreaPanel";
import Attribution from "./Attribution";
import DemoNotice from "./DemoNotice";
import EvidenceCard from "./EvidenceCard";
import { isFinished } from "./format";
import { findingsOf, itemName, keyOf, type Finding } from "./items";
import MapView from "./MapView";
import ResultsPanel from "./ResultsPanel";
import PhotoWindow from "./PhotoWindow";
import type { Bbox, DemoArea, Entrance, Issue, Picked, Scan, ServerConfig, Walk } from "./types";

const POLL_MS = 3000;

function scanFromHash(): number | null {
  const match = window.location.hash.match(/scan=(\d+)/);
  return match ? Number(match[1]) : null;
}

export default function App() {
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [demos, setDemos] = useState<DemoArea[]>([]);
  const [selecting, setSelecting] = useState(false);
  const [bbox, setBbox] = useState<Bbox | null>(null);
  const [scanId, setScanId] = useState<number | null>(scanFromHash());
  const [scan, setScan] = useState<Scan | null>(null);
  const [entrances, setEntrances] = useState<Entrance[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [walks, setWalks] = useState<Walk[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [streets, setStreets] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<Picked | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [newSurvey, setNewSurvey] = useState(false);
  const [previewWalks, setPreviewWalks] = useState<Walk[]>([]);
  // Over 800 px the photo opens over the map (PhotoWindow); on a phone it stays in the card.
  const [wide, setWide] = useState(() => window.matchMedia("(min-width: 801px)").matches);
  useEffect(() => {
    const query = window.matchMedia("(min-width: 801px)");
    const onChange = () => setWide(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  const [fitTo, setFitTo] = useState<Bbox | null>(null);
  const [error, setError] = useState<string | null>(null);
  const getView = useRef<(() => Bbox) | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    api.config().then(setConfig).catch((e: Error) => setError(e.message));
    api.demo()
      .then((areas) => {
        setDemos(areas);
        // A first visit opens the first surveyed area, so results are on screen at once.
        if (areas.length > 0) setScanId((id) => id ?? areas[0].scan_id);
      })
      .catch(() => setDemos([]));
  }, []);

  // Poll the survey until it finishes. A failed poll is retried; only a missing survey (404) stops it.
  useEffect(() => {
    if (scanId === null) return;
    window.location.hash = `scan=${scanId}`;
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const s = await api.scan(scanId);
        if (stopped) return;
        setError(null);
        setScan(s);
        if (!isFinished(s)) timer = window.setTimeout(tick, POLL_MS);
      } catch (e) {
        if (stopped) return;
        setError((e as Error).message);
        if (!(e instanceof ApiError && e.status === 404)) timer = window.setTimeout(tick, POLL_MS);
      }
    };
    tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [scanId]);

  // When the survey has finished, load its results (a later survey's answer is never overwritten by an earlier one).
  const finished = isFinished(scan);
  useEffect(() => {
    if (!scan || !finished) return;
    let cancelled = false;
    setFitTo(scan.bbox);
    Promise.all([api.entrances(scan.id), api.issues(scan.id)])
      .then(([e, i]) => {
        if (cancelled) return;
        setEntrances(e);
        setIssues(i);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    api.walks(scan.id)
      .then((w) => {
        if (cancelled) return;
        setWalks(w);
        setFitTo(contentBounds(w) ?? scan.bbox); // the photographed streets, not the drawn box's empty corners
      })
      .catch(() => !cancelled && setWalks([]));
    return () => {
      cancelled = true;
    };
  }, [scan?.id, finished]);

  // Bring the evidence card or the review panel into view when it opens (on a phone it sits below the map).
  useEffect(() => {
    if (picked || reviewing) detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [picked?.kind, picked?.item.id, reviewing]);

  const onSelect = useCallback((b: Bbox) => {
    setBbox(b);
    setSelecting(false);
  }, []);

  function openScan(id: number) {
    setPicked(null);
    setReviewing(false);
    setError(null);
    if (id === scanId) return; // already open: keep its results on screen
    setEntrances([]);
    setIssues([]);
    setWalks([]);
    setScan(null);
    setScanId(id);
  }

  function pick(p: Picked) {
    setReviewing(false);
    setPicked(p);
  }

  const items = scan?.items ?? [];
  // In the panel's order: item by item, as the standard lists them (the map's colours follow the same order).
  const findings = useMemo(() => {
    const all = findingsOf(issues, entrances);
    const order = new Map((scan?.items ?? []).map((it, i) => [it.id, i]));
    return all.map((f, i) => ({ f, i }))
      .sort((a, b) => (order.get(a.f.item) ?? 99) - (order.get(b.f.item) ?? 99) || a.i - b.i)
      .map(({ f }) => f);
  }, [issues, entrances, scan?.items]);
  const selected: Finding | null = picked ? findings.find((f) => f.key === keyOf(picked)) ?? null : null;
  // "Utility or streetlight pole · 3 of 20 · position approximate": the marker's name tag (ui-redesign 3.3).
  const selectedLabel = useMemo(() => {
    if (!selected) return "";
    const same = findings.filter((f) => f.item === selected.item);
    return `${itemName(selected.item, items)} · ${same.indexOf(selected) + 1} of ${same.length} · position approximate`;
  }, [selected, findings, items]);
  const toggleItem = (item: string) =>
    setHidden((h) => {
      const next = new Set(h);
      if (next.has(item)) next.delete(item);
      else next.add(item);
      return next;
    });

  function updated(u: Picked) {
    if (u.kind === "entrance") setEntrances((list) => list.map((e) => (e.id === u.item.id ? u.item : e)));
    else if (issues.some((i) => i.id === u.item.id)) {
      setIssues((list) => list.flatMap((i) => (i.id !== u.item.id ? [i] : u.item.review_status === "rejected" ? [] : [u.item])));
    } else if (scan && u.item.review_status !== "rejected") {
      api.issues(scan.id).then(setIssues).catch(() => undefined); // an undone rejection: back on the map, in place
    }
    if (scan) api.scan(scan.id).then(setScan).catch(() => undefined);
  }

  const sameItem = selected ? findings.filter((f) => f.item === selected.item) : [];
  const cardList = reviewing ? findings : sameItem;

  function startReview() {
    const first = findings.find((f) => f.status === "needs_review");
    if (!first) return;
    setPicked(first.picked);
    setReviewing(true);
  }

  return (
    <div className="app">
      <header className="top">
        <button type="button" className="skip-link" onClick={() => panelRef.current?.focus()}>
          Skip to the survey panel
        </button>
        <h1>Fieldless</h1>
        <p className="tagline">Street surveys without the fieldwork</p>
        {demos.length > 0 && (
          <nav className="area-tabs" aria-label="Surveyed areas">
            {demos.map((d) => (
              <button key={d.scan_id} type="button" aria-current={d.scan_id === scanId && !newSurvey ? "page" : undefined}
                onClick={() => {
                  setNewSurvey(false);
                  openScan(d.scan_id);
                }}>
                {d.name}
              </button>
            ))}
          </nav>
        )}
        {!STATIC_DEMO && (
          <button type="button" className="primary new-survey" aria-expanded={newSurvey}
            onClick={() => setNewSurvey((v) => !v)}>
            + New survey
          </button>
        )}
      </header>
      <MapView
        selecting={selecting}
        bbox={bbox}
        fitTo={fitTo}
        findings={newSurvey ? [] : findings}
        items={items}
        hidden={hidden}
        onToggleItem={toggleItem}
        onShowAll={() => setHidden(new Set())}
        walks={newSurvey ? previewWalks : walks}
        selected={newSurvey ? null : selected}
        selectedLabel={selectedLabel}
        onSelect={onSelect}
        onPick={(f) => pick(f.picked)}
        onReady={(fn) => (getView.current = fn)}
        onStreets={setStreets}
      >
        {selected && !newSurvey && (
          <PhotoWindow finding={selected} list={sameItem} items={items} onStep={(f) => setPicked(f.picked)}
            onClose={() => setPicked(null)} />
        )}
      </MapView>
      <aside className="panel" ref={panelRef} tabIndex={-1} aria-label="Survey panel">
        {error && <p className="error" role="alert">{error}</p>}
        <div ref={detailRef}>
          {selected && !newSurvey && (
            <EvidenceCard finding={selected} list={cardList} queue={reviewing} items={items}
              checks={scan?.checks ?? []} photoOnMap={wide && !!selected.picked.item.evidence}
              street={streets[selected.key]} city={scan?.city_check}
              onNavigate={(f) => setPicked(f.picked)} onUpdated={updated}
              onClose={() => {
                setPicked(null);
                setReviewing(false);
              }} />
          )}
        </div>
        {/* What is being looked at comes first; a new survey is one button away (docs/ui-rules.md U6). */}
        {!STATIC_DEMO && (newSurvey || !scan || !finished) && (
          <AreaPanel
            bbox={bbox}
            selecting={selecting}
            maxDeg2={config?.max_bbox_deg2 ?? 0.0001}
            inviteRequired={config?.invite_required ?? false}
            scan={newSurvey ? null : scan}
            onToggleSelect={() => setSelecting((s) => !s)}
            onUseView={() => getView.current && setBbox(getView.current())}
            onStarted={(id) => {
              setNewSurvey(false);
              openScan(id);
            }}
            onPreview={(p) => setPreviewWalks((p?.walks ?? []).map((points, clip) => ({ clip, points })))}
          />
        )}
        {scan && finished && !newSurvey && (
          <ResultsPanel
            scan={scan}
            name={demos.find((d) => d.scan_id === scan.id)?.name ?? null}
            findings={findings}
            streets={streets}
            selectedKey={selected?.key ?? null}
            onPick={(f) => pick(f.picked)}
            onReview={startReview}
          />
        )}
        {STATIC_DEMO && <DemoNotice />}
        <Attribution />
      </aside>
    </div>
  );
}
