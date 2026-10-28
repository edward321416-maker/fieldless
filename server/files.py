"""A finished survey as one zip a city can take (demo scene S8): assist.report's survey report (HTML and Markdown),
assist.export's GeoJSON of every finding with a CSV per survey item, and the blurred evidence photo of each finding
under evidence/, where the GeoJSON and CSV point. No model is called."""
import io
import tempfile
import zipfile
from pathlib import Path

from assist import export, report
from pipeline.survey_items import Standard


def survey_zip(scan_data: dict, standard: Standard, evidence_dir: Path, area: str | None = None,
               notes: dict | None = None) -> bytes:
    """scan_data as eval.store.load_scan returns it: {"scan", "frames", "issues", "entrances"}. notes: the report's
    saved sentences (db.report_text). A photo missing from evidence_dir is left out; its finding still links the
    original on Mapillary."""
    with tempfile.TemporaryDirectory() as tmp:
        report.write(scan_data, standard, Path(tmp), area=area, notes=notes)
        photos = sorted({f["properties"]["evidence"] for f in export.features(scan_data, standard)} - {None})
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(Path(tmp).iterdir()):
                z.write(path, path.name)
            for rel in photos:  # "evidence/<name>"; JPEGs are already compressed
                source = Path(evidence_dir) / Path(rel).name
                if source.is_file():
                    z.write(source, rel, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()
