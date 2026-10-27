import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import AreaPanel from "./AreaPanel";
import Attribution from "./Attribution";
import EvidenceCard from "./EvidenceCard";
import { isFinished } from "./format";
import MapView, { type Picked } from "./MapView";
import ResultsPanel from "./ResultsPanel";
import ReviewPanel from "./ReviewPanel";
import type { Bbox, DemoArea, Entrance, Issue, Scan, ServerConfig } from "./types";

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
  const [showEntrances, setShowEntrances] = useState(true);
  const [showIssues, setShowIssues] = useState(true);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [focus, setFocus] = useState<{ lat: number; lng: number } | null>(null);
  const [fitTo, setFitTo] = useState<Bbox | null>(null);
  const [error, setError] = useState<string | null>(null);
  const getView = useRef<(() => Bbox) | null>(null);

  useEffect(() => {
    api.config().then(setConfig).catch((e: Error) => setError(e.message));
    api.demo().then(setDemos).catch(() => setDemos([]));
  }, []);

  // Poll the survey until it finishes, then load its results.
  useEffect(() => {
    if (scanId === null) return;
    window.location.hash = `scan=${scanId}`;
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const s = await api.scan(scanId);
        if (stopped) return;
        setScan(s);
        if (!isFinished(s)) timer = window.setTimeout(tick, POLL_MS);
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    };
    tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [scanId]);

  const finished = isFinished(scan);
  useEffect(() => {
    if (!scan || !finished) return;
    setFitTo(scan.bbox);
    Promise.all([api.entrances(scan.id), api.issues(scan.id)])
      .then(([e, i]) => {
        setEntrances(e);
        setIssues(i);
      })
      .catch((e: Error) => setError(e.message));
  }, [scan?.id, finished]);

  const onSelect = useCallback((b: Bbox) => {
    setBbox(b);
    setSelecting(false);
  }, []);

  function openScan(id: number) {
    setPicked(null);
    setReviewing(false);
    setEntrances([]);
    setIssues([]);
    setScan(null);
    setScanId(id);
  }

  function pick(p: Picked) {
    setReviewing(false);
    setPicked(p);
    setFocus({ lat: p.item.lat, lng: p.item.lng });
  }

  function updated(u: Picked) {
    if (u.kind === "entrance") setEntrances((list) => list.map((e) => (e.id === u.item.id ? u.item : e)));
    else setIssues((list) => list.flatMap((i) => (i.id !== u.item.id ? [i] : u.item.review_status === "rejected" ? [] : [u.item])));
    if (scan) api.scan(scan.id).then(setScan).catch(() => undefined);
  }

  return (
    <div className="app">
      <header className="top">
        <h1>Fieldless</h1>
        <p>Street surveys without the fieldwork</p>
      </header>
      <MapView
        selecting={selecting}
        bbox={bbox}
        entrances={entrances}
        issues={issues}
        showEntrances={showEntrances}
        showIssues={showIssues}
        focus={focus}
        fitTo={fitTo}
        onSelect={onSelect}
        onPick={pick}
        onReady={(fn) => (getView.current = fn)}
      />
      <aside className="panel">
        {error && <p className="error" role="alert">{error}</p>}
        {demos.length > 0 && (
          <section className="panel-section" aria-labelledby="demo-title">
            <h2 id="demo-title">Surveyed areas</h2>
            <div className="row">
              {demos.map((d) => (
                <button key={d.scan_id} type="button" onClick={() => openScan(d.scan_id)}>{d.name}</button>
              ))}
            </div>
          </section>
        )}
        <AreaPanel
          bbox={bbox}
          selecting={selecting}
          maxDeg2={config?.max_bbox_deg2 ?? 0.0001}
          inviteRequired={config?.invite_required ?? false}
          scan={scan}
          onToggleSelect={() => setSelecting((s) => !s)}
          onUseView={() => getView.current && setBbox(getView.current())}
          onStarted={openScan}
        />
        {reviewing ? (
          <ReviewPanel entrances={entrances} issues={issues} onUpdated={updated} onClose={() => setReviewing(false)} />
        ) : picked ? (
          <EvidenceCard picked={picked} onClose={() => setPicked(null)} onReview={() => setReviewing(true)} />
        ) : null}
        {scan && finished && (
          <ResultsPanel
            scan={scan}
            entrances={entrances}
            issues={issues}
            showEntrances={showEntrances}
            showIssues={showIssues}
            onShowEntrances={setShowEntrances}
            onShowIssues={setShowIssues}
            onPick={pick}
            onReview={() => {
              setPicked(null);
              setReviewing(true);
            }}
          />
        )}
        <Attribution />
      </aside>
    </div>
  );
}
