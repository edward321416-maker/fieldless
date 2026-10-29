import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, STATIC_DEMO } from "./api";
import { contentBounds } from "./bbox";
import AddItemPanel from "./AddItemPanel";
import AreaPanel from "./AreaPanel";
import CoverageGuide from "./CoverageGuide";
import Attribution from "./Attribution";
import DemoNotice from "./DemoNotice";
import IntroPanel, { IntroMapKey, IntroPhoto, type Example } from "./IntroPanel";
import { startsWithIntro } from "./intro";
import { isActive, itemFromHash, withRun } from "./itemRun";
import EvidenceCard from "./EvidenceCard";
import FindingList from "./FindingList";
import { isFinished } from "./format";
import { findingsOf, itemName, keyOf, type Finding } from "./items";
import MapView from "./MapView";
import ResultsPanel from "./ResultsPanel";
import PhotoWindow from "./PhotoWindow";
import { reviewProgress } from "./summary";
import type { Bbox, DemoArea, Entrance, Issue, ItemRun, ItemRunSummary, Picked, Scan, ServerConfig, Walk } from "./types";

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
  // A first visit opens on what Fieldless does, with the first example survey loaded behind it (design 1).
  const [intro, setIntro] = useState(() => startsWithIntro(window.location.hash));
  const introRef = useRef(intro);
  introRef.current = intro;
  const [scan, setScan] = useState<Scan | null>(null);
  const [entrances, setEntrances] = useState<Entrance[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [walks, setWalks] = useState<Walk[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [openItem, setOpenItem] = useState<string | null>(null); // an item's findings, listed in the panel
  // An item the agent writes in the app: its run, from the address, and the survey's finished runs (examples).
  const [itemRunId, setItemRunId] = useState<number | null>(() => itemFromHash(window.location.hash));
  const [run, setRun] = useState<ItemRun | null>(null);
  const [runTick, setRunTick] = useState(0); // a new look at the run, after a person's answers are sent
  const [itemRuns, setItemRuns] = useState<ItemRunSummary[]>([]);
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

  // The address names the survey on screen, so a link to it opens there; the introduction keeps it bare.
  useEffect(() => {
    if (scanId !== null && !intro) window.location.hash = `scan=${scanId}${itemRunId !== null ? `&item=${itemRunId}` : ""}`;
  }, [scanId, intro, itemRunId]);

  // Follow the item run while it is queued or running; a person's answers or the end stop the polling.
  useEffect(() => {
    if (itemRunId === null) {
      setRun(null);
      return;
    }
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const r = await api.itemRun(itemRunId);
        if (stopped) return;
        setRun(r);
        if (isActive(r)) timer = window.setTimeout(tick, POLL_MS);
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError && e.status === 404) setItemRunId(null);
        else timer = window.setTimeout(tick, POLL_MS);
      }
    };
    tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [itemRunId, runTick]);

  // Poll the survey until it finishes. A failed poll is retried; only a missing survey (404) stops it.
  useEffect(() => {
    if (scanId === null) return;
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
    if (config?.items) {
      api.scanItems(scan.id).then((r) => !cancelled && setItemRuns(r)).catch(() => !cancelled && setItemRuns([]));
    }
    api.walks(scan.id)
      .then((w) => {
        if (cancelled) return;
        setWalks(w);
        // the photographed streets, not the drawn box's empty corners; the introduction shows the whole example area
        setFitTo(introRef.current ? [...scan.bbox] : contentBounds(w) ?? scan.bbox);
      })
      .catch(() => !cancelled && setWalks([]));
    return () => {
      cancelled = true;
    };
  }, [scan?.id, finished, config?.items]);

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
    setOpenItem(null);
    setError(null);
    if (id === scanId) return; // already open: keep its results on screen
    setItemRunId(null);
    setItemRuns([]);
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

  // A finished run's item and rows join the survey's (itemRun.withRun); its own survey only.
  const joined = useMemo(() => withRun(scan?.items ?? [], issues, run && scan && run.scan_id === scan.id ? run : null),
    [scan?.items, issues, run, scan?.id]);
  const items = joined.items;
  const shownScan = useMemo(() => (scan ? { ...scan, items } : null), [scan, items]);
  const demoName = scan ? demos.find((d) => d.scan_id === scan.id)?.name ?? null : null;

  function goIntro() {
    setIntro(true);
    setPicked(null);
    setReviewing(false);
    setOpenItem(null);
    setItemRunId(null);
    setNewSurvey(false);
    setSelecting(false);
    if (demos.length > 0) openScan(demos[0].scan_id);
    if (scan && demos[0]?.scan_id === scan.id) setFitTo([...scan.bbox]);
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }

  function seeExample() {
    setIntro(false);
    if (demos.length > 0) openScan(demos[0].scan_id);
    if (scan && demos[0]?.scan_id === scan.id) setFitTo(contentBounds(walks) ?? [...scan.bbox]);
  }
  // In the panel's order: item by item, as the standard lists them (the map's colours follow the same order).
  const findings = useMemo(() => {
    const all = findingsOf(joined.issues, entrances);
    const order = new Map(items.map((it, i) => [it.id, i]));
    return all.map((f, i) => ({ f, i }))
      .sort((a, b) => (order.get(a.f.item) ?? 99) - (order.get(b.f.item) ?? 99) || a.i - b.i)
      .map(({ f }) => f);
  }, [joined.issues, entrances, items]);
  // The introduction's photos: per item, its first finding with a photo; over the map, the first finding the models
  // were sure of with a box (the panel's order, so no item is chosen by name).
  const examples: Example[] = useMemo(() => items.flatMap((it) => {
    const f = findings.find((x) => x.item === it.id && x.picked.item.evidence);
    return f ? [{ item: it.id, url: f.picked.item.evidence!.url, box: f.box }] : [];
  }), [items, findings]);
  const introPhoto = useMemo(() => findings.find((f) => f.status === "auto" && f.box && f.box.length === 4
    && f.picked.item.evidence) ?? null, [findings]);
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
  const backLabel = reviewing ? "← Stop reviewing"
    : selected ? `← ${itemName(selected.item, items)} (${sameItem.length})` : "";

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
        <h1 className="brand">
          <button type="button" className="brand-link" onClick={goIntro} title="What Fieldless does">
            {/* the logo: a walk with two photo points (docs/ui-rules.md U10) */}
            <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true">
              <rect width="24" height="24" rx="6" fill="#1d4ed8" />
              <path d="M5 17L10 9l4 5 5-7" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              <circle cx="10" cy="9" r="1.6" fill="#fff" />
              <circle cx="14" cy="14" r="1.6" fill="#fff" />
            </svg>
            Fieldless
          </button>
        </h1>
        {demos.length > 0 && (
          // One picker for the example surveys, as Project Sidewalk picks its city (docs/specs/2026-10-29-cleanup-design.md 2)
          <label className="example-picker">
            <span className="visually-hidden">Example survey</span>
            <select value={intro || newSurvey || !demos.some((d) => d.scan_id === scanId) ? "" : String(scanId)}
              onChange={(e) => {
                if (!e.target.value) return;
                setIntro(false);
                setNewSurvey(false);
                openScan(Number(e.target.value));
              }}>
              <option value="" disabled>Examples</option>
              {demos.map((d) => <option key={d.scan_id} value={d.scan_id}>Example: {d.name}</option>)}
            </select>
          </label>
        )}
        {!STATIC_DEMO && (
          <button type="button" className="primary new-survey" aria-expanded={newSurvey}
            onClick={() => {
              setIntro(false);
              setNewSurvey((v) => !v);
            }}>
            + New survey
          </button>
        )}
      </header>
      <MapView
        selecting={selecting}
        newSurvey={newSurvey}
        coverage={!!config?.coverage_tiles}
        showCoverage={intro || newSurvey}
        bbox={intro ? scan?.bbox ?? null : bbox}
        areaLabel={intro && demoName ? `Example survey area · ${demoName}` : null}
        fitTo={fitTo}
        findings={intro || newSurvey ? [] : findings}
        items={items}
        hidden={hidden}
        focusItem={intro || newSurvey ? null : openItem}
        padLeft={intro ? 370 : 0}
        walks={newSurvey ? previewWalks : walks}
        selected={intro || newSurvey ? null : selected}
        selectedLabel={selectedLabel}
        onSelect={onSelect}
        onPick={(f) => pick(f.picked)}
        onReady={(fn) => (getView.current = fn)}
        onStreets={setStreets}
      >
        {intro && <IntroMapKey coverage={!!config?.coverage_tiles} />}
        {intro && wide && introPhoto && scan && <IntroPhoto finding={introPhoto} items={items} photos={scan.image_count} />}
        {newSurvey && (
          <CoverageGuide coverage={!!config?.coverage_tiles} demos={demos}
            onGo={(b) => setFitTo([b[0], b[1], b[2], b[3]])} />
        )}
        {selected && !newSurvey && !intro && (
          <PhotoWindow finding={selected} items={items} onClose={() => setPicked(null)} />
        )}
      </MapView>
      <aside className="panel" ref={panelRef} tabIndex={-1} aria-label="Survey panel">
        {error && <p className="error" role="alert">{error}</p>}
        {intro && (
          <IntroPanel scan={scan} name={demoName} examples={examples} canSurvey={!STATIC_DEMO} onSee={seeExample}
            onSurvey={() => {
              setIntro(false);
              setNewSurvey(true);
            }} />
        )}
        <div ref={detailRef}>
          {selected && !newSurvey && !intro && (
            <EvidenceCard finding={selected} list={cardList} queue={reviewing} items={items} backLabel={backLabel}
              progress={reviewing && scan ? reviewProgress(scan.counts) : undefined}
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
        {!STATIC_DEMO && !intro && (newSurvey || !scan || !finished) && (
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
        {/* One thing at a time: a finding's card, else an item's findings, else the survey (cleanup design 2). */}
        {scan && finished && !newSurvey && !intro && !selected && openItem && (
          <FindingList scan={shownScan!} item={openItem} findings={findings.filter((f) => f.item === openItem)}
            streets={streets} selectedKey={null} onPick={(f) => pick(f.picked)} onBack={() => setOpenItem(null)} />
        )}
        {scan && finished && !newSurvey && !intro && !selected && !openItem && (
          <ResultsPanel
            scan={shownScan!}
            name={demos.find((d) => d.scan_id === scan.id)?.name ?? null}
            findings={findings}
            hidden={hidden}
            onToggle={toggleItem}
            onOpenItem={setOpenItem}
            onReview={startReview}
            onIntro={goIntro}
            hasReport={!!config?.report}
            agentCity={run?.status === "done" && run.item && run.test?.city
              ? { [run.item.id]: run.test.city } : undefined}
          >
            {config?.items && (
              <AddItemPanel canMake={!!config.make_items && !STATIC_DEMO}
                run={run && run.scan_id === scan.id ? run : null} examples={itemRuns}
                onStart={async (target, code) => {
                  const { id } = await api.startItem(scan.id, target, code);
                  setItemRunId(id);
                  setRunTick((t) => t + 1);
                }}
                onOpen={(id) => {
                  setItemRunId(id);
                  setRunTick((t) => t + 1);
                }}
                onClose={() => setItemRunId(null)} />
            )}
          </ResultsPanel>
        )}
        {STATIC_DEMO && <DemoNotice />}
        <Attribution />
      </aside>
    </div>
  );
}
