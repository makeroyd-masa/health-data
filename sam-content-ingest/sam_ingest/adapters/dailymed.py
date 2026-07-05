"""DailyMed adapter (PRD §6.4).

DailyMed REST API v2. `/spls` lists SPL metadata (setid, spl_version, ...); the full
section-structured HL7 label is fetched as XML from `/spls/{SETID}.xml`. Sections are
LOINC-coded; we extract the consumer-relevant ones. Selection policy: one SPL per drug —
latest spl_version, preferring the generic label. Labels are public domain.
"""

from __future__ import annotations

import logging
from typing import Iterable

from lxml import etree

from ..core.schema import License, ParsedSection, RawItem, SeedConfig, SourceRef
from .base import BaseAdapter

log = logging.getLogger("sam_ingest.adapters.dailymed")

_BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
_NS = {"v3": "urn:hl7-org:v3"}
_SPL_TTL = 14 * 24 * 3600  # labels change infrequently; version-checked separately

# Consumer-relevant SPL sections by LOINC code -> canonical label (PRD §6.4).
# Canonical labels give stable block ids across drugs regardless of the SPL's own title.
_LOINC: dict[str, str] = {
    "34066-1": "Boxed Warning",
    "34067-9": "Indications & Usage",
    "34068-7": "Dosage & Administration",
    "34070-3": "Contraindications",
    "43685-7": "Warnings and Precautions",
    "34071-1": "Warnings",
    "34084-4": "Adverse Reactions",
    "34073-7": "Drug Interactions",
    "34076-0": "Information for Patients",       # incl. "Patient Counseling Information"
    "42230-3": "Patient Package Insert",         # patient-facing
    "42231-1": "Medication Guide",               # patient-facing
    # OTC "Drug Facts" safety subsections (siblings; consumer-relevant)
    "50570-1": "Do Not Use",
    "50569-3": "Ask a Doctor Before Use If",
    "50568-5": "Ask a Doctor or Pharmacist Before Use",
    "50567-7": "When Using This Product",
    "50566-9": "Stop Use and Ask a Doctor If",
    "53414-9": "Pregnancy or Breast-Feeding",
}


class DailyMedAdapter(BaseAdapter):
    name = "dailymed"

    def discover(self, seed: SeedConfig) -> Iterable[SourceRef]:
        prefer_generic = bool(seed.get("prefer_generic", True))
        for drug in seed.get("drugs", []):
            rxcui = str(drug["rxcui"]) if drug.get("rxcui") else None
            name = drug.get("name")
            ref = self._resolve_one(name=name, rxcui=rxcui, prefer_generic=prefer_generic)
            if ref is None:
                log.info("no SPL for drug %r", name or rxcui)
                continue
            yield ref

    def _resolve_one(self, *, name, rxcui, prefer_generic) -> SourceRef | None:
        """One SPL per drug: latest spl_version, preferring the generic label."""
        base_params = {"rxcui": rxcui} if rxcui else {"drug_name": name}
        # Try generic first, then fall back to any label.
        for extra in ([{"name_type": "g"}, {}] if prefer_generic else [{}]):
            params = {**base_params, **extra, "pagesize": "100"}
            resp = self.client.get(f"{_BASE}/spls.json", params=params, ttl=_SPL_TTL)
            data = _json_data(resp.text())
            if data:
                best = max(data, key=lambda d: int(d.get("spl_version", 0)))
                return SourceRef(
                    url=f"{_BASE}/spls/{best['setid']}.xml",
                    source_id=best["setid"],
                    title=_clean_title(best.get("title", name or rxcui)),
                    meta={
                        "rxcui": rxcui,
                        "spl_version": str(best.get("spl_version", "")),
                        "published_date": best.get("published_date"),
                        "drug_name": name,
                    },
                )
        return None

    def fetch(self, ref: SourceRef, *, refresh: bool = False) -> RawItem:
        # No Accept header: the .xml extension selects the format; an explicit
        # Accept: application/xml makes DailyMed return 406.
        resp = self.client.get(ref.url, ttl=_SPL_TTL, refresh=refresh)
        return RawItem(ref=ref, content=resp.content, content_type=resp.content_type,
                       from_cache=resp.from_cache)

    def parse(self, raw: RawItem) -> list[ParsedSection]:
        try:
            root = etree.fromstring(raw.content)
        except etree.XMLSyntaxError as e:
            log.warning("SPL XML parse failed for %s: %s", raw.ref.url, e)
            return []

        rxcui = raw.ref.meta.get("rxcui")
        version = raw.ref.meta.get("spl_version")
        sections: list[ParsedSection] = []
        seen_codes: set[str] = set()
        for sec in root.findall(".//v3:section", _NS):
            code_el = sec.find("v3:code", _NS)
            if code_el is None:
                continue
            code = code_el.get("code")
            if code not in _LOINC or code in seen_codes:
                continue
            if _has_mapped_ancestor(sec):  # avoid duplicating nested target sections
                continue
            body = _narrative(sec).strip()
            if not body:
                continue
            seen_codes.add(code)
            label = _LOINC[code]
            sections.append(
                ParsedSection(
                    section_title=label,
                    body_markdown=body,
                    license=License.public_domain,
                    codes={"rxcui": [rxcui] if rxcui else [], "loinc": [code]},
                    meta={"source_version": version, "loinc": code},
                )
            )
        return sections


# --------------------------------------------------------------------------- helpers

def _json_data(text: str) -> list:
    import json
    try:
        return json.loads(text).get("data", [])
    except json.JSONDecodeError:
        return []


def _clean_title(title: str) -> str:
    # "IBUPROFEN (IBUPROFEN) TABLET [CVS PHARMACY]" -> keep the readable head.
    return title.split("[")[0].strip().title() if title else title


def _has_mapped_ancestor(sec) -> bool:
    parent = sec.getparent()
    while parent is not None:
        if etree.QName(parent).localname == "section":
            code_el = parent.find("v3:code", _NS)
            if code_el is not None and code_el.get("code") in _LOINC:
                return True
        parent = parent.getparent()
    return False


def _inline(el) -> str:
    return " ".join(" ".join(el.itertext()).split())


def _narrative(el) -> str:
    """Convert an SPL section's HL7 narrative subtree to markdown (paragraphs/lists)."""
    ln = etree.QName(el).localname
    if ln == "paragraph":
        return _inline(el)
    if ln == "list":
        items = [f"- {_inline(it)}" for it in el.findall("v3:item", _NS) if _inline(it)]
        return "\n".join(items)
    if ln == "table":
        rows = []
        for tr in el.findall(".//v3:tr", _NS):
            cells = [" ".join(c.itertext()).strip() for c in tr]
            if any(cells):
                rows.append(" | ".join(cells))
        return "\n".join(rows)
    if ln in ("title", "caption"):
        t = _inline(el)
        return f"**{t}**" if t else ""
    if ln == "code":
        return ""  # skip the section's own <code>/<codeSystem>
    # container (section, text, component, excerpt, ...): recurse in document order
    parts = [p for c in el if (p := _narrative(c))]
    if parts:
        return "\n\n".join(parts)
    return _inline(el)
