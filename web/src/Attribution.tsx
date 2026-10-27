import { MapillaryMark } from "./EvidenceCard";

export default function Attribution() {
  return (
    <footer className="attribution">
      Street photos:{" "}
      <a href="https://www.mapillary.com" target="_blank" rel="noreferrer"><MapillaryMark /></a>{" "}
      contributors,{" "}
      <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noreferrer">CC BY-SA 4.0</a>
      {" · "}Map data ©{" "}
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors
      {" · "}Tiles: <a href="https://openfreemap.org" target="_blank" rel="noreferrer">OpenFreeMap</a>
    </footer>
  );
}
