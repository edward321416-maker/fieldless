import * as maplibregl from "maplibre-gl"; // v6 has no default export
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Feature, FeatureCollection } from "geojson";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { bboxFromCorners, bboxPolygon, viewToBbox } from "./bbox";
import { destination, distanceToLineM, targetBearing, viewWedge, type LngLat } from "./geo";
import { colourOf, itemName, STATUS_SENTENCE, type Badge, type Finding } from "./items";
import Legend from "./Legend";
import { markerCanvas, markerName, RATIO } from "./markerArt";
import { namedRoads, streetText } from "./streets";
import type { Bbox, ItemMeta, Walk } from "./types";

// MapLibre 6 looks for its worker next to its own module, which bundling moves; point it at the emitted file.
maplibregl.setWorkerUrl(workerUrl);

// Street photos credit on the map itself (Mapillary terms 11), next to MapLibre's OSM and OpenFreeMap credits.
const MAPILLARY_CREDIT =
  'Street photos: <a href="https://www.mapillary.com" target="_blank" rel="noreferrer"><img src="/mapillary-logo.png" alt="Mapillary" class="mapillary-logo"></a>';

// OpenFreeMap positron: grey, with no shop icons, so only the survey's markers carry colour (docs/ui-rules.md U4).
// OSM vector tiles, no key; the style brings its own OSM and OpenFreeMap attribution.
const STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
const START: [number, number] = [127.0571, 37.5484]; // Seongsu street_b, where D1 checked tactile paving
const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };
const TARGET_M = 10; // the object is drawn this far along its bearing: the distance is not measured (approximate)
const ACCENT = "#1d4ed8";

interface Props {
  selecting: boolean;
  bbox: Bbox | null;
  fitTo: Bbox | null;
  findings: Finding[];
  items: ItemMeta[];
  hidden: Set<string>;
  onToggleItem: (item: string) => void;
  onShowAll: () => void;
  walks: Walk[];
  selected: Finding | null;
  selectedLabel: string;
  onSelect: (bbox: Bbox) => void;
  onPick: (f: Finding) => void;
  onReady: (getView: () => Bbox) => void;
  onStreets: (names: Record<string, string>) => void;
  children?: ReactNode; // drawn over the map (the selected finding's photo)
}

function areaData(bbox: Bbox | null): FeatureCollection {
  return bbox ? { type: "FeatureCollection", features: [bboxPolygon(bbox)] } : EMPTY;
}

const point = (c: LngLat, props: Record<string, unknown> = {}): Feature =>
  ({ type: "Feature", properties: props, geometry: { type: "Point", coordinates: c } });

// The selected finding drawn on the map: the camera where the photo was taken, the wedge it shows, a dashed ray to
// the object and the object's marker on it, about TARGET_M along (the distance is not measured). Without a box the
// marker stays at the camera.
function selectionData(f: Finding | null): { data: FeatureCollection; at: LngLat; bearing: number | null } {
  if (!f) return { data: EMPTY, at: [0, 0], bearing: null };
  const camera: LngLat = [f.lng, f.lat];
  const features: Feature[] = [];
  if (f.heading !== null) {
    features.push({ type: "Feature", properties: { part: "wedge" },
      geometry: { type: "Polygon", coordinates: [viewWedge(f.lat, f.lng, f.heading)] } });
  }
  const bearing = targetBearing(f.heading, f.box);
  let at = camera;
  if (bearing !== null) {
    at = destination(f.lat, f.lng, bearing, TARGET_M);
    features.push({ type: "Feature", properties: { part: "ray" }, geometry: { type: "LineString", coordinates: [camera, at] } });
    features.push(point(camera, { part: "camera" }));
  }
  features.push(point(at, { part: "marker", icon: markerName(f.item, f.badge) }));
  return { data: { type: "FeatureCollection", features }, at, bearing };
}

// The name tag sits beyond the marker, away from the camera, so it never covers the wedge or the ray.
function labelAnchor(bearing: number | null): maplibregl.PositionAnchor {
  if (bearing === null) return "bottom";
  const b = ((bearing % 360) + 360) % 360;
  return b < 90 || b >= 270 ? "bottom" : "top"; // above or below, centred: never under the legend at the side
}

function nearestWalk(f: Finding | null, walks: Walk[]): number | null {
  if (!f) return null;
  let best: { clip: number; m: number } | null = null;
  for (const w of walks) {
    const m = distanceToLineM([f.lng, f.lat], w.points);
    if (!best || m < best.m) best = { clip: w.clip, m };
  }
  return best && best.m < 30 ? best.clip : null;
}

export default function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const label = useRef<maplibregl.Popup | null>(null);
  const [loaded, setLoaded] = useState(false);
  const latest = useRef(props);
  latest.current = props;

  useEffect(() => {
    const map = new maplibregl.Map({
      container: container.current!, style: STYLE_URL, center: START, zoom: 17,
      attributionControl: { customAttribution: MAPILLARY_CREDIT },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 18, className: "map-tip" });
    map.on("load", () => {
      map.addSource("walks", { type: "geojson", data: EMPTY });
      map.addSource("area", { type: "geojson", data: areaData(latest.current.bbox) });
      map.addSource("selection", { type: "geojson", data: EMPTY });
      map.addSource("findings", { type: "geojson", data: EMPTY, cluster: true, clusterRadius: 12, clusterMaxZoom: 16 });
      map.addLayer({ id: "walks", type: "line", source: "walks", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#6b7280", "line-width": 3, "line-opacity": 0.55 } });
      map.addLayer({ id: "walk-picked", type: "line", source: "walks", filter: ["==", ["get", "clip"], -1],
        layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": ACCENT, "line-width": 4 } });
      map.addLayer({ id: "area-fill", type: "fill", source: "area", paint: { "fill-color": ACCENT, "fill-opacity": 0.08 } });
      map.addLayer({ id: "area-line", type: "line", source: "area", paint: { "line-color": ACCENT, "line-width": 2 } });
      map.addLayer({ id: "sel-wedge", type: "fill", source: "selection", filter: ["==", ["get", "part"], "wedge"],
        paint: { "fill-color": ACCENT, "fill-opacity": 0.18 } });
      map.addLayer({ id: "sel-ray", type: "line", source: "selection", filter: ["==", ["get", "part"], "ray"],
        paint: { "line-color": "#111827", "line-width": 2, "line-dasharray": [2, 2] } });
      map.addLayer({ id: "clusters", type: "circle", source: "findings", filter: ["has", "point_count"],
        paint: { "circle-color": "#ffffff", "circle-stroke-color": "#374151", "circle-stroke-width": 2,
          "circle-radius": ["step", ["get", "point_count"], 16, 10, 20, 30, 24] } });
      map.addLayer({ id: "cluster-count", type: "symbol", source: "findings", filter: ["has", "point_count"],
        layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": ["Noto Sans Bold"], "text-size": 13 },
        paint: { "text-color": "#111827" } });
      map.addLayer({ id: "findings", type: "symbol", source: "findings", filter: ["!", ["has", "point_count"]],
        layout: { "icon-image": ["get", "icon"], "icon-allow-overlap": true, "icon-ignore-placement": true,
          "symbol-sort-key": ["get", "order"] } });
      map.addLayer({ id: "sel-camera", type: "circle", source: "selection", filter: ["==", ["get", "part"], "camera"],
        paint: { "circle-radius": 6, "circle-color": "#111827", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
      map.addLayer({ id: "sel-marker", type: "symbol", source: "selection", filter: ["==", ["get", "part"], "marker"],
        layout: { "icon-image": ["get", "icon"], "icon-size": 40 / 32, "icon-allow-overlap": true,
          "icon-ignore-placement": true } });
      setLoaded(true);
    });
    // An image the map asks for but nobody added yet (a style reload): draw it from its name.
    map.on("styleimagemissing", (e: { id: string }) => {
      const [, itemId, badge] = e.id.split(":");
      if (!itemId || map.hasImage(e.id)) return;
      const { items } = latest.current;
      const meta = items.find((i) => i.id === itemId);
      const c = markerCanvas(colourOf(itemId, items), meta?.map_icon ?? "", meta?.target ?? itemId,
        badge === "none" ? null : (badge as Finding["badge"]));
      map.addImage(e.id, c.getContext("2d")!.getImageData(0, 0, c.width, c.height), { pixelRatio: RATIO });
    });
    map.on("click", "findings", (e) => {
      const key = e.features?.[0]?.properties?.key as string | undefined;
      const f = latest.current.findings.find((x) => x.key === key);
      if (f) latest.current.onPick(f);
    });
    map.on("click", "clusters", async (e) => {
      const feature = e.features?.[0];
      if (!feature) return;
      const source = map.getSource("findings") as maplibregl.GeoJSONSource;
      const zoom = await source.getClusterExpansionZoom(feature.properties?.cluster_id);
      map.easeTo({ center: (feature.geometry as GeoJSON.Point).coordinates as [number, number], zoom });
    });
    map.on("mousemove", "findings", (e) => {
      map.getCanvas().style.cursor = "pointer";
      const key = e.features?.[0]?.properties?.key as string | undefined;
      const f = latest.current.findings.find((x) => x.key === key);
      if (!f) return;
      const { items } = latest.current;
      const street = streetsRef.current[f.key];
      const text = [itemName(f.item, items), STATUS_SENTENCE[f.status] ?? f.status, street].filter(Boolean).join(" · ");
      hover.setLngLat([f.lng, f.lat]).setHTML(`<strong></strong><br><span>Click to open the photo</span>`).addTo(map);
      const el = hover.getElement();
      el.querySelector("strong")!.textContent = text; // text, never HTML: names come from the data
    });
    map.on("mouseleave", "findings", () => {
      map.getCanvas().style.cursor = "";
      hover.remove();
    });
    map.on("mouseenter", "clusters", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "clusters", () => (map.getCanvas().style.cursor = ""));
    // Street names come from the road-name tiles once the survey area is on screen.
    map.on("idle", () => nameStreets(map));

    let start: [number, number] | null = null;
    map.on("mousedown", (e: maplibregl.MapMouseEvent) => {
      if (latest.current.selecting) start = [e.lngLat.lng, e.lngLat.lat];
    });
    map.on("mousemove", (e: maplibregl.MapMouseEvent) => {
      if (!start) return;
      if (e.originalEvent.buttons === 0 || !latest.current.selecting) {
        start = null; // released outside the map, or drawing was cancelled: stop following the cursor
        (map.getSource("area") as maplibregl.GeoJSONSource | undefined)?.setData(areaData(latest.current.bbox));
        return;
      }
      (map.getSource("area") as maplibregl.GeoJSONSource | undefined)
        ?.setData(areaData(bboxFromCorners(start, [e.lngLat.lng, e.lngLat.lat])));
    });
    map.on("mouseup", (e: maplibregl.MapMouseEvent) => {
      if (!start) return;
      const bbox = bboxFromCorners(start, [e.lngLat.lng, e.lngLat.lat]);
      start = null;
      latest.current.onSelect(bbox);
    });
    mapRef.current = map;
    latest.current.onReady(() => {
      const b = map.getBounds();
      return viewToBbox(b.getWest(), b.getSouth(), b.getEast(), b.getNorth());
    });
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  const streetsRef = useRef<Record<string, string>>({});
  function nameStreets(map: maplibregl.Map) {
    const missing = latest.current.findings.filter((f) => !(f.key in streetsRef.current));
    if (missing.length === 0 || map.getZoom() < 14) return;
    const roads = namedRoads(map.querySourceFeatures("openmaptiles", { sourceLayer: "transportation_name" }) as never);
    if (roads.length === 0) return;
    const found: Record<string, string> = {};
    for (const f of missing) {
      const text = streetText([f.lng, f.lat], roads);
      if (text) found[f.key] = text;
    }
    if (Object.keys(found).length === 0) return;
    streetsRef.current = { ...streetsRef.current, ...found };
    latest.current.onStreets(streetsRef.current);
  }

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (props.selecting) map.dragPan.disable();
    else map.dragPan.enable();
    map.getCanvas().style.cursor = props.selecting ? "crosshair" : "";
  }, [props.selecting]);

  useEffect(() => {
    if (loaded) (mapRef.current?.getSource("area") as maplibregl.GeoJSONSource | undefined)?.setData(areaData(props.bbox));
  }, [props.bbox, loaded]);

  // The markers: one image per item and badge, a source of the findings not hidden by the legend.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !loaded) return;
    const shown = props.findings.filter((f) => !props.hidden.has(f.item));
    for (const f of shown) {
      const name = markerName(f.item, f.badge);
      if (map.hasImage(name)) continue;
      const meta = props.items.find((i) => i.id === f.item);
      const c = markerCanvas(colourOf(f.item, props.items), meta?.map_icon ?? "", meta?.target ?? f.item, f.badge);
      map.addImage(name, c.getContext("2d")!.getImageData(0, 0, c.width, c.height), { pixelRatio: RATIO });
    }
    (map.getSource("findings") as maplibregl.GeoJSONSource).setData({
      type: "FeatureCollection",
      features: shown.map((f) => point([f.lng, f.lat], {
        key: f.key, icon: markerName(f.item, f.badge), order: f.badge ? 1 : 0, // markers with a badge draw on top
      })),
    });
    streetsRef.current = Object.fromEntries(Object.entries(streetsRef.current)
      .filter(([k]) => props.findings.some((f) => f.key === k)));
    nameStreets(map);
  }, [props.findings, props.hidden, props.items, loaded]);

  useEffect(() => {
    if (!loaded) return;
    (mapRef.current?.getSource("walks") as maplibregl.GeoJSONSource | undefined)?.setData({
      type: "FeatureCollection",
      features: props.walks.filter((w) => w.points.length > 1).map((w) => ({
        type: "Feature", properties: { clip: w.clip }, geometry: { type: "LineString", coordinates: w.points },
      })),
    });
  }, [props.walks, loaded]);

  // The selected finding: bigger, the others dimmed, its photo's place, view and direction, and a name tag.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !loaded) return;
    const f = props.selected;
    const key = f?.key ?? "";
    // the selected marker moves to the selection layer; the others dim
    map.setFilter("findings", ["all", ["!", ["has", "point_count"]], ["!=", ["get", "key"], key]]);
    map.setPaintProperty("findings", "icon-opacity", key ? 0.35 : 1);
    const { data, at, bearing } = selectionData(f);
    (map.getSource("selection") as maplibregl.GeoJSONSource).setData(data);
    map.setFilter("walk-picked", ["==", ["get", "clip"], nearestWalk(f, props.walks) ?? -1]);
    label.current?.remove();
    label.current = null;
    if (f) {
      // the photo window covers the lower part of a wide map: centre the marker in the part left free
      const covered = f.picked.item.evidence && map.getContainer().clientWidth > 800
        ? Math.min(460, map.getContainer().clientHeight * 0.55) : 0;
      map.easeTo({ center: at, zoom: Math.max(map.getZoom(), 19), duration: 600, padding: { top: 0, bottom: covered, left: 0, right: 0 } });
      label.current = new maplibregl.Popup({ closeButton: false, closeOnClick: false, anchor: labelAnchor(bearing),
        offset: 26, className: "map-label" }).setLngLat(at).setText(props.selectedLabel).addTo(map);
    }
  }, [props.selected?.key, props.selectedLabel, props.walks, loaded]);

  useEffect(() => {
    const b = props.fitTo;
    if (b) mapRef.current?.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 0 });
  }, [props.fitTo]);

  const counts = new Map<string, number>();
  const badges = new Set<Badge>();
  for (const f of props.findings) {
    counts.set(f.item, (counts.get(f.item) ?? 0) + 1);
    if (f.badge && !props.hidden.has(f.item)) badges.add(f.badge);
  }
  return (
    <div className="map-wrap">
      <div ref={container} className="map" role="region" aria-label="Survey map" />
      {props.findings.length > 0 && (
        <Legend items={props.items} counts={counts} badges={badges} hidden={props.hidden} onToggle={props.onToggleItem}
          onShowAll={props.onShowAll} />
      )}
      {props.children}
    </div>
  );
}
