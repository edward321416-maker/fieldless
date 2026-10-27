import * as maplibregl from "maplibre-gl"; // v6 has no default export
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import "maplibre-gl/dist/maplibre-gl.css";
import type { FeatureCollection } from "geojson";
import { useEffect, useRef } from "react";
import { bboxFromCorners, bboxPolygon, viewToBbox } from "./bbox";
import { labelText } from "./format";
import type { Bbox, Entrance, Issue } from "./types";

// MapLibre 6 looks for its worker next to its own module, which bundling moves; point it at the emitted file.
maplibregl.setWorkerUrl(workerUrl);

// Street photos credit on the map itself (Mapillary terms 11), next to MapLibre's OSM and OpenFreeMap credits.
const MAPILLARY_CREDIT = '<a href="https://www.mapillary.com" target="_blank" rel="noreferrer">Street photos: Mapillary</a>';

export type Picked = { kind: "entrance"; item: Entrance } | { kind: "issue"; item: Issue };

interface Props {
  selecting: boolean;
  bbox: Bbox | null;
  entrances: Entrance[];
  issues: Issue[];
  showEntrances: boolean;
  showIssues: boolean;
  focus: { lat: number; lng: number } | null;
  fitTo: Bbox | null;
  onSelect: (bbox: Bbox) => void;
  onPick: (picked: Picked) => void;
  onReady: (getView: () => Bbox) => void;
}

// OpenFreeMap: OSM vector tiles, no key; the style brings its own OSM and OpenFreeMap attribution.
const STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
const START: [number, number] = [127.0571, 37.5484]; // Seongsu street_b, where D1 checked tactile paving
const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

function areaData(bbox: Bbox | null): FeatureCollection {
  return bbox ? { type: "FeatureCollection", features: [bboxPolygon(bbox)] } : EMPTY;
}

function setArea(map: maplibregl.Map, bbox: Bbox | null): void {
  const source = map.getSource("area") as maplibregl.GeoJSONSource | undefined;
  source?.setData(areaData(bbox));
}

export default function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const latest = useRef(props);
  latest.current = props;

  useEffect(() => {
    const map = new maplibregl.Map({
      container: container.current!, style: STYLE_URL, center: START, zoom: 17,
      attributionControl: { customAttribution: MAPILLARY_CREDIT },
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.on("load", () => {
      map.addSource("area", { type: "geojson", data: areaData(latest.current.bbox) });
      map.addLayer({ id: "area-fill", type: "fill", source: "area", paint: { "fill-color": "#1d4ed8", "fill-opacity": 0.12 } });
      map.addLayer({ id: "area-line", type: "line", source: "area", paint: { "line-color": "#1d4ed8", "line-width": 2 } });
    });
    let start: [number, number] | null = null;
    map.on("mousedown", (e: maplibregl.MapMouseEvent) => {
      if (latest.current.selecting) start = [e.lngLat.lng, e.lngLat.lat];
    });
    map.on("mousemove", (e: maplibregl.MapMouseEvent) => {
      if (!start) return;
      if (e.originalEvent.buttons === 0 || !latest.current.selecting) {
        start = null; // released outside the map, or drawing was cancelled: stop following the cursor
        setArea(map, latest.current.bbox);
        return;
      }
      setArea(map, bboxFromCorners(start, [e.lngLat.lng, e.lngLat.lat]));
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

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (props.selecting) map.dragPan.disable();
    else map.dragPan.enable();
    map.getCanvas().style.cursor = props.selecting ? "crosshair" : "";
  }, [props.selecting]);

  useEffect(() => {
    const map = mapRef.current;
    if (map?.getSource("area")) setArea(map, props.bbox);
  }, [props.bbox]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    markers.current.forEach((m) => m.remove());
    markers.current = [];
    const add = (lat: number, lng: number, className: string, text: string, label: string, onClick: () => void) => {
      const el = document.createElement("button");
      el.type = "button";
      el.className = `marker ${className}`;
      el.textContent = text;
      el.title = label;
      el.setAttribute("aria-label", label);
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        onClick();
      });
      markers.current.push(new maplibregl.Marker({ element: el }).setLngLat([lng, lat]).addTo(map));
    };
    if (props.showEntrances) {
      for (const e of props.entrances) {
        add(e.lat, e.lng, "marker-entrance", e.label.icon, `${e.name || "Entrance"}: ${labelText(e.label)}`,
          () => latest.current.onPick({ kind: "entrance", item: e }));
      }
    }
    if (props.showIssues) {
      for (const i of props.issues) {
        add(i.lat, i.lng, `marker-${i.issue_type}${i.durable ? " marker-durable" : ""}`, i.label.icon, labelText(i.label),
          () => latest.current.onPick({ kind: "issue", item: i }));
      }
    }
  }, [props.entrances, props.issues, props.showEntrances, props.showIssues]);

  useEffect(() => {
    if (props.focus) mapRef.current?.flyTo({ center: [props.focus.lng, props.focus.lat], zoom: 19 });
  }, [props.focus]);

  useEffect(() => {
    const b = props.fitTo;
    if (b) mapRef.current?.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 0 });
  }, [props.fitTo]);

  return <div ref={container} className="map" role="region" aria-label="Survey map" />;
}
