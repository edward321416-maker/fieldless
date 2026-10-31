import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ActionTab } from "./ActionHub";
import { api, ApiError, STATIC_DEMO } from "./api";
import { clampBboxToMax, contentBounds } from "./bbox";
import AddItemPanel from "./AddItemPanel";
import AreaPanel from "./AreaPanel";
import type { CallStats } from "./callStats";
import { cityCounts, cityStates, missedRecords, type MissedRecord } from "./cityMatch";
import CityRecordCard from "./CityRecordCard";
import CoverageGuide from "./CoverageGuide";
import Attribution from "./Attribution";
import DemoNotice from "./DemoNotice";
import Overview, { type Example } from "./Overview";
import { startsWithIntro } from "./intro";
import { isActive, itemFromHash, withRun } from "./itemRun";
import EvidenceCard from "./EvidenceCard";
import FindingList from "./FindingList";
import { isFinished } from "./format";
import HowMade from "./HowMade";
import { findingsOf, itemName, keyOf, type Finding } from "./items";
import LensSwitch, { type Lens } from "./LensSwitch";
import MapView from "./MapView";
import PlaceSearch from "./PlaceSearch";
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
  const [placeLabel, setPlaceLabel] = useState<string | null>(null);
  const [scanId, setScanId] = useState<number | null>(scanFromHash());
  // A first visit opens on what Fieldless does, with the first example survey loaded behind it (design 1).
  const [intro, setIntro] = useState(() => startsWithIntro(window.location.hash));
  const [scan, setScan] = useState<Scan | null>(null);
  const [resultsOf, setResultsOf] = useState<number | null>(null); // the survey whose findings are loaded
  const [entrances, setEntrances] = useState<Entrance[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [walks, setWalks] = useState<Walk[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [openItem, setOpenItem] = useState<string | null>(null); // an item's findings, listed in the panel
  // A survey with a city check: the map shows its findings, or the same findings against the city's records, where
  // a record the survey did not find opens its card.
  const [lens, setLens] = useState<Lens>("findings");
  const [record, setRecord] = useState<MissedRecord | null>(null);
  const [actionTab, setActionTab] = useState<ActionTab>("dispatch");
  // An item the agent writes in the app: its run, from the address, and the survey's finished runs (examples).
  const [itemRunId, setItemRunId] = useState<number | null>(() => itemFromHash(window.location.hash));
  const [run, setRun] = useState<ItemRun | null>(null);
  const [runTick, setRunTick] = useState(0); // a new look at the run, after a person's answers are sent
  const [itemRuns, setItemRuns] = useState<ItemRunSummary[]>([]);
  const [callStats, setCallStats] = useState<CallStats | null>(null); // which model did what, when the server keeps it
  // The overview's own data: the first example's finished item runs and the other example surveys.
  const [overviewRuns, setOverviewRuns] = useState<ItemRun[]>([]);
  const [others, setOthers] = useState<{ scan: Scan; name: string }[]>([]);
  const [streets, setStreets] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<Picked | null>(null);
  const [hideFeatured, setHideFeatured] = useState(false);
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
  // The first screen's data did not arrive: without a word it would be an empty page. Try again asks once more.
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadTick, setLoadTick] = useState(0);
  const getView = useRef<(() => Bbox) | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const overviewRef = useRef<HTMLElement>(null);

  useEffect(() => {
    setLoadFailed(false);
    api.config().then(setConfig).catch(() => setLoadFailed(true));
    api.demo()
      .then((areas) => {
        setDemos(areas);
        // A first visit opens the first surveyed area, so results are on screen at once.
        if (areas.length > 0) setScanId((id) => id ?? areas[0].scan_id);
      })
      .catch(() => setLoadFailed(true));
  }, [loadTick]);

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
        setResultsOf(scan.id);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    if (config?.items) {
      api.scanItems(scan.id).then((r) => !cancelled && setItemRuns(r)).catch(() => !cancelled && setItemRuns([]));
    }
    api.calls(scan.id).then((c) => !cancelled && setCallStats(c)).catch(() => !cancelled && setCallStats(null));
    api.walks(scan.id)
      .then((w) => {
        if (cancelled) return;
        setWalks(w);
        // the photographed streets, not the drawn box's empty corners
        setFitTo(contentBounds(w) ?? scan.bbox);
      })
      .catch(() => !cancelled && setWalks([]));
    return () => {
      cancelled = true;
    };
  }, [scan?.id, finished, config?.items]);

  // The overview sets the other example surveys beside the first; one that fails to load is left out.
  useEffect(() => {
    if (!intro || demos.length < 2 || others.length > 0) return;
    let cancelled = false;
    Promise.all(demos.slice(1).map((d) => api.scan(d.scan_id).then((s) => [{ scan: s, name: d.name }]).catch(() => [])))
      .then((list) => !cancelled && setOthers(list.flat()));
    return () => {
      cancelled = true;
    };
  }, [intro, demos, others.length]);

  // The overview shows the first example's two newest finished item runs, each with its phrase tries and results.
  useEffect(() => {
    if (!intro) return;
    let cancelled = false;
    const done = itemRuns.filter((r) => r.status === "done")
      .sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 2);
    Promise.all(done.map((r) => api.itemRun(r.id).catch(() => null)))
      .then((list) => !cancelled && setOverviewRuns(list.filter((r): r is ItemRun => r?.status === "done")));
    return () => {
      cancelled = true;
    };
  }, [intro, itemRuns]);

  // Bring the evidence card, the review panel or a city record's card into view when it opens (on a phone it sits
  // below the map).
  useEffect(() => {
    if (picked || reviewing || record) detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [picked?.kind, picked?.item.id, reviewing, record?.key]);

  const onSelect = useCallback((b: Bbox) => {
    setBbox(b);
    setSelecting(false);
    setNewSurvey(true);
  }, []);

  function openScan(id: number) {
    setPicked(null);
    setHideFeatured(false);
    setReviewing(false);
    setOpenItem(null);
    setLens("findings");
    setRecord(null);
    setError(null);
    setBbox(null);
    if (id === scanId) return; // already open: keep its results on screen
    setItemRunId(null);
    setItemRuns([]);
    setCallStats(null);
    setEntrances([]);
    setIssues([]);
    setWalks([]);
    setScan(null);
    setScanId(id);
  }

  function pick(p: Picked) {
    setReviewing(false);
    setRecord(null);
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
    setHideFeatured(false);
    setReviewing(false);
    setOpenItem(null);
    setLens("findings");
    setRecord(null);
    setItemRunId(null);
    setNewSurvey(false);
    setSelecting(false);
    setActionTab("dispatch");
    if (demos.length > 0) openScan(demos[0].scan_id);
    // the overview has no map: the next map opened from it shows the example's photographed streets
    if (scan && demos[0]?.scan_id === scan.id) setFitTo(contentBounds(walks) ?? [...scan.bbox]);
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
  // The overview's photos and counts: per item, its first finding with a photo (the panel's order, so no item is
  // chosen by name), and how many findings it has, entrances included.
  const examples: Example[] = useMemo(() => items.flatMap((it) => {
    const f = findings.find((x) => x.item === it.id && x.picked.item.evidence);
    return f ? [{ item: it.id, url: f.picked.item.evidence!.url, box: f.box }] : [];
  }), [items, findings]);
  const itemCounts = useMemo(() => Object.fromEntries(items.map((it) => [it.id, findings.filter((f) => f.item === it.id).length])),
    [items, findings]);
  const selected: Finding | null = picked ? findings.find((f) => f.key === keyOf(picked)) ?? null : null;
  // "Utility or streetlight pole · 3 of 20 · position approximate": the marker's name tag (ui-redesign 3.3).
  const selectedLabel = useMemo(() => {
    if (!selected) return "";
    const same = findings.filter((f) => f.item === selected.item);
    return `${itemName(selected.item, items)} · ${same.indexOf(selected) + 1} of ${same.length} · position approximate`;
  }, [selected, findings, items]);
  // The survey against the city's own records (cityMatch): each finding's pairing, the records in a photo's view no
  // finding was paired with, and the counts per item. The switch shows on a finished survey's results only, and
  // gives way to a finding's card.
  const cityStateMap = useMemo(() => cityStates(scan?.city_check, issues), [scan?.city_check, issues]);
  const records = useMemo(() => missedRecords(scan?.city_check), [scan?.city_check]);
  const cityItemCounts = useMemo(() => cityCounts(scan?.city_check, issues), [scan?.city_check, issues]);
  const isExample = demos.some((d) => d.scan_id === scan?.id); // the server keeps the example surveys read-only
  const showLens = !!scan?.city_check && finished && !intro && !newSurvey && !selected;
  const onRecords = showLens && lens === "records";
  const shownRecord = onRecords ? record : null; // its dot is on the map
  // On entry to Results view, highlight the first visible finding with a street photo & viewing cone so the map
  // immediately pairs a sightline cone with a compact bottom-left photo preview (Alternative 1 spatial UI).
  const featured: Finding | null = useMemo(() => {
    if (selected || shownRecord || newSurvey || intro || !finished || onRecords || hideFeatured) return null;
    const pool = findings.filter((f) => !hidden.has(f.item) && (!openItem || f.item === openItem));
    return pool.find((f) => f.picked.item.evidence && f.heading !== null && f.box !== null)
      ?? pool.find((f) => f.picked.item.evidence)
      ?? null;
  }, [selected, shownRecord, newSurvey, intro, finished, onRecords, hideFeatured, findings, hidden, openItem]);
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

  function useCurrentView() {
    if (!getView.current) return;
    const maxDeg2 = config?.max_bbox_deg2 ?? 0.0001;
    setNewSurvey(true);
    setSelecting(false);
    setPlaceLabel("Current map view");
    setBbox(clampBboxToMax(getView.current(), maxDeg2));
  }

  function startDrawingRange() {
    setNewSurvey(true);
    setSelecting((s) => !s);
  }

  const activeBbox = bbox ?? scan?.bbox ?? null;
  const activeAreaLabel = newSurvey
    ? (placeLabel ? `📍 ${placeLabel} (drag corners to resize)` : "📐 Selected range (drag corners to resize)")
    : (demoName ? `📐 Survey range · ${demoName} (drag corner to change)` : null);

  // The top bar: on the landing page (intro) it shows section links and a Launch App CTA; inside the app it shows
  // the PlaceSearch omnibox, Example picker, and New survey toggle.
  const loadNotice = loadFailed && (
    <p className="error" role="alert">
      The server did not answer, so the example surveys are not here yet.{" "}
      <button type="button" className="link-button" onClick={() => setLoadTick((t) => t + 1)}>Try again</button>
    </p>
  );
  const header = (
    <header className="top">
      <button type="button" className="skip-link" onClick={() => (intro ? overviewRef : panelRef).current?.focus()}>
        {intro ? "Skip to the overview" : "Skip to the survey panel"}
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
      {intro ? (
        <>
          <nav className="landing-nav" aria-label="Landing page sections">
            <a href="#overview-items">What it finds</a>
            <a href="#overview-flow">How it works</a>
            <a href="#overview-checks">Validation</a>
            <a href="#overview-cities">Cities</a>
          </nav>
          <button type="button" className="primary new-survey" onClick={seeExample}>
            Launch App →
          </button>
        </>
      ) : (
        <>
          <PlaceSearch
            demos={demos}
            activeScanId={intro || newSurvey ? null : scanId}
            onOpenDemo={(id) => {
              setIntro(false);
              setNewSurvey(false);
              setPlaceLabel(null);
              openScan(id);
            }}
            onPickPlace={(b, label) => {
              setIntro(false);
              setNewSurvey(true);
              setSelecting(false);
              setPlaceLabel(label);
              setFitTo([b[0], b[1], b[2], b[3]]);
              setBbox(b);
            }}
          />
          {demos.length > 0 && (
            // One picker for the example surveys, as Project Sidewalk picks its city (docs/specs/2026-10-29-cleanup-design.md 2)
            <label className="example-picker">
              <span className="visually-hidden">Example survey</span>
              <select value={intro || newSurvey || !demos.some((d) => d.scan_id === scanId) ? "" : String(scanId)}
                onChange={(e) => {
                  if (!e.target.value) return;
                  setIntro(false);
                  setNewSurvey(false);
                  setPlaceLabel(null);
                  openScan(Number(e.target.value));
                }}>
                <option value="" disabled>Examples</option>
                {demos.map((d) => <option key={d.scan_id} value={d.scan_id}>Example: {d.name}</option>)}
              </select>
            </label>
          )}
          <button type="button" className="primary new-survey" aria-expanded={newSurvey}
            onClick={() => {
              setIntro(false);
              setNewSurvey((v) => !v);
            }}>
            {newSurvey ? "← Back to results" : "+ New survey"}
          </button>
        </>
      )}
    </header>
  );

  // A first visit reads the overview, a full-width page without the map; a survey opened from it brings the map.
  if (intro) {
    return (
      <div className="app overview-mode">
        {header}
        <main className="overview-page" id="overview" ref={overviewRef} tabIndex={-1}>
          {loadNotice}
        {error && <p className="error" role="alert">{error}</p>}
          <Overview scan={resultsOf === scan?.id ? scan : null} name={demoName} issues={issues} counts={itemCounts}
            examples={examples} runs={overviewRuns.filter((r) => r.scan_id === scan?.id)} others={others}
            canSurvey={!STATIC_DEMO} onSee={seeExample}
            onSurvey={() => {
              setIntro(false);
              setNewSurvey(true);
            }}
            onOpenScan={(id) => {
              setIntro(false);
              openScan(id);
            }}
            onOpenRun={(scanId, runId) => {
              setIntro(false);
              openScan(scanId);
              setItemRunId(runId);
              setRunTick((t) => t + 1);
            }}
            onLaunchAction={(tab) => {
              setIntro(false);
              setNewSurvey(false);
              setPicked(null);
              setRecord(null);
              setOpenItem(null);
              if (demos.length > 0) openScan(demos[0].scan_id);
              if (scan && demos[0]?.scan_id === scan.id) setFitTo(contentBounds(walks) ?? [...scan.bbox]);
              if (tab === "gis") {
                setActionTab("gis");
                setLens("records");
              } else if (tab === "dispatch") {
                setActionTab("dispatch");
                setLens("findings");
              }
            }}
          />
          {STATIC_DEMO && <DemoNotice />}
          <Attribution />
        </main>
      </div>
    );
  }

  return (
    <div className="app">
      {header}
      <MapView
        selecting={selecting}
        newSurvey={newSurvey}
        coverage={!!config?.coverage_tiles}
        showCoverage={newSurvey}
        bbox={activeBbox}
        areaLabel={activeAreaLabel}
        fitTo={fitTo}
        findings={newSurvey ? [] : findings}
        items={items}
        hidden={hidden}
        focusItem={newSurvey || onRecords ? null : openItem}
        lens={showLens ? lens : "findings"}
        cityStates={cityStateMap}
        records={onRecords ? records : []}
        padLeft={0}
        walks={newSurvey ? previewWalks : walks}
        selected={newSurvey ? null : selected}
        featured={newSurvey ? null : featured}
        selectedLabel={selectedLabel}
        onSelect={(b) => {
          setPlaceLabel("Custom range on map");
          onSelect(b);
        }}
        onPick={(f) => pick(f.picked)}
        onPickRecord={setRecord}
        onReady={(fn) => (getView.current = fn)}
        onStreets={setStreets}
      >
        {showLens && (
          <LensSwitch lens={lens} onChange={(l) => {
            setLens(l);
            setRecord(null);
          }} />
        )}
        {newSurvey && (
          <CoverageGuide
            coverage={!!config?.coverage_tiles}
            demos={demos}
            onGo={(b) => {
              setPlaceLabel(null);
              setFitTo([b[0], b[1], b[2], b[3]]);
              setBbox(b);
            }}
          />
        )}
        {/* Always-visible floating range selector bar on the map so the user can draw/resize the survey box anytime */}
        <div className="map-survey-bar" role="region" aria-label="Select survey range on map" hidden={!!selected && !newSurvey}>
          {/* The bar leaves the photo window's credit in view (Mapillary terms): none over a large photo, no words over a small one. */}
          {(selecting || newSurvey || !featured) && (
            <span className="map-survey-place">
              {selecting
                ? "Drag on map to draw range…"
                : placeLabel
                  ? `📍 ${placeLabel}`
                  : "📐 Area range (drag blue corner or draw):"}
            </span>
          )}
          <button
            type="button"
            className={selecting ? "primary" : ""}
            aria-pressed={selecting}
            onClick={startDrawingRange}
          >
            {selecting ? "Cancel drawing" : "📐 Draw range on map"}
          </button>
          <button type="button" onClick={useCurrentView}>
            🎯 Use current map view
          </button>
          {newSurvey && scan && finished && (
            <button
              type="button"
              onClick={() => {
                setNewSurvey(false);
                setSelecting(false);
                setBbox(null);
                setPlaceLabel(null);
              }}
            >
              ← Results
            </button>
          )}
        </div>
        {(selected || featured) && !newSurvey && (
          <PhotoWindow
            finding={(selected ?? featured)!}
            items={items}
            preview={!selected}
            onInspect={() => featured && pick(featured.picked)}
            onClose={() => (selected ? setPicked(null) : setHideFeatured(true))}
          />
        )}
      </MapView>
      <aside className="panel" ref={panelRef} tabIndex={-1} aria-label="Survey panel">
        {loadNotice}
        {error && <p className="error" role="alert">{error}</p>}
        <div ref={detailRef}>
          {selected && !newSurvey && (
            <EvidenceCard finding={selected} list={cardList} queue={reviewing} items={items} backLabel={backLabel}
              readOnly={isExample}
              progress={reviewing && scan ? reviewProgress(scan.counts) : undefined}
              checks={scan?.checks ?? []} photoOnMap={wide && !!selected.picked.item.evidence}
              street={streets[selected.key]} city={scan?.city_check}
              onNavigate={(f) => setPicked(f.picked)} onUpdated={updated}
              onClose={() => {
                setPicked(null);
                setReviewing(false);
              }} />
          )}
          {shownRecord && <CityRecordCard record={shownRecord} items={items} onClose={() => setRecord(null)} />}
        </div>
        {/* What is being looked at comes first; a new survey is one button away (docs/ui-rules.md U6). */}
        {(newSurvey || (!STATIC_DEMO && (!scan || !finished))) && (
          <AreaPanel
            bbox={activeBbox}
            selecting={selecting}
            maxDeg2={config?.max_bbox_deg2 ?? 0.0001}
            inviteRequired={config?.invite_required ?? false}
            scan={newSurvey ? null : scan}
            onToggleSelect={() => setSelecting((s) => !s)}
            onUseView={useCurrentView}
            onStarted={(id) => {
              setNewSurvey(false);
              openScan(id);
            }}
            onPreview={(p) => setPreviewWalks((p?.walks ?? []).map((points, clip) => ({ clip, points })))}
          />
        )}
        {/* One thing at a time: a finding's card, else a city record's, else an item's findings, else the survey
            (cleanup design 2). */}
        {scan && finished && !newSurvey && !selected && !shownRecord && openItem && (
          <FindingList scan={shownScan!} item={openItem} findings={findings.filter((f) => f.item === openItem)}
            streets={streets} selectedKey={null} onPick={(f) => pick(f.picked)} onBack={() => setOpenItem(null)} />
        )}
        {scan && finished && !newSurvey && !selected && !shownRecord && !openItem && (
          <ResultsPanel
            scan={shownScan!}
            name={demos.find((d) => d.scan_id === scan.id)?.name ?? null}
            findings={findings}
            hidden={hidden}
            onToggle={toggleItem}
            onOpenItem={(id) => {
              setHideFeatured(false);
              setOpenItem(id);
            }}
            onReview={startReview}
            canReview={!isExample}
            onIntro={goIntro}
            hasReport={!!config?.report}
            agentCity={run?.status === "done" && run.item && run.test?.city
              ? { [run.item.id]: run.test.city } : undefined}
            lens={showLens ? lens : "findings"}
            cityCounts={cityItemCounts}
            streets={streets}
            cityStates={cityStateMap}
            actionTab={actionTab}
            onPickFinding={(f) => pick(f.picked)}
            onPickRecord={(r) => {
              setLens("records");
              setRecord(r);
            }}
            onSwitchLens={(l) => {
              setLens(l);
              setRecord(null);
            }}
          >
            {callStats && callStats.scan_id === scan.id && <HowMade stats={callStats} />}
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
