"""The normalized output schema (KnowledgeBlock) and the adapter data contracts.

Every extractor emits `KnowledgeBlock` records conforming to this module. Do not let
schemas diverge per source (PRD §2). The intermediate types (`SourceRef`, `RawItem`,
`ParsedSection`, `ItemContext`) are the contract between adapters and the core pipeline
(PRD §3).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


# --------------------------------------------------------------------------- enums


class UseCase(str, Enum):
    household_readiness = "household_readiness"
    condition_explainer = "condition_explainer"
    visit_prep = "visit_prep"
    medication = "medication"
    aging_home_safety = "aging_home_safety"
    travel_health = "travel_health"


class Source(str, Enum):
    medlineplus = "medlineplus"
    ready_gov = "ready_gov"
    cdc = "cdc"  # logical publisher; content is fetched via the HHS Digital Media platform
    dailymed = "dailymed"
    ahrq = "ahrq"  # logical source for the one-time static "10 Questions" seed (no crawl)
    state_dept = "state_dept"  # travel.state.gov country info + advisories
    tsa = "tsa"                # tsa.gov medications/devices through security
    faa = "faa"                # faa.gov in-transit medical device rules


class Audience(str, Enum):
    patient = "patient"
    caregiver = "caregiver"
    general = "general"


class License(str, Enum):
    public_domain = "public_domain"
    us_gov = "us_gov"
    medlineplus_terms = "medlineplus_terms"
    ready_gov_reprint = "ready_gov_reprint"


class TripType(str, Enum):
    international = "international"
    domestic_far = "domestic_far"
    cruise = "cruise"
    road_rv = "road_rv"
    altitude = "altitude"
    diving = "diving"
    adventure_remote = "adventure_remote"
    medical_tourism = "medical_tourism"
    chronic_condition = "chronic_condition"


class Volatility(str, Enum):
    evergreen = "evergreen"     # prep content; refresh on demand
    periodic = "periodic"       # destination/country pages, Yellow Book editions
    volatile = "volatile"       # travel advisories; must carry valid_until


# --------------------------------------------------------------------------- model


# ISO 3166-1 alpha-2 codes (bundled — no external dependency) for geo validation.
ISO_ALPHA2: frozenset[str] = frozenset(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM "
    "BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX "
    "CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG "
    "GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR "
    "IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV "
    "LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE "
    "NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO "
    "RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF "
    "TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF "
    "WS YE YT ZA ZM ZW XK".split()
)


class Codes(BaseModel):
    icd10: list[str] = Field(default_factory=list)
    snomed: list[str] = Field(default_factory=list)
    rxcui: list[str] = Field(default_factory=list)
    ndc: list[str] = Field(default_factory=list)
    loinc: list[str] = Field(default_factory=list)


class GeoScope(BaseModel):
    scope: str                        # "global" | "region" | "country" | "subnational"
    country_iso2: str | None = None   # ISO 3166-1 alpha-2 when scope == "country"
    country_name: str | None = None
    region: str | None = None


class Citation(BaseModel):
    publisher: str
    source_url: str
    attribution_text: str
    # last time the content CHANGED (not last run) — see pipeline idempotency (PRD §2/§4.5)
    retrieved_at: str


class Provenance(BaseModel):
    extractor: str
    adapter: str
    pipeline_version: str
    # native version integer where the source exposes one (e.g. DailyMed spl_version); else None
    source_version: str | None = None


class KnowledgeBlock(BaseModel):
    """One independently-surfaceable, citable chunk of source content (PRD §2)."""

    model_config = {"extra": "forbid"}

    id: str
    use_case: UseCase
    source: Source
    source_id: str
    source_url: str
    language: str = "en"
    title: str
    section: str
    audience: Audience
    summary: str
    body_markdown: str
    keywords: list[str] = Field(default_factory=list)
    codes: Codes = Field(default_factory=Codes)
    citation: Citation
    license: License
    source_last_updated: str | None = None
    ingested_at: str  # last time content CHANGED, not last run
    content_hash: str
    provenance: Provenance
    # --- travel facets (PRD §T3); safe defaults keep non-travel blocks unchanged ---
    geo: GeoScope | None = None
    trip_types: list[TripType] = Field(default_factory=list)
    volatility: Volatility = Volatility.evergreen
    valid_until: str | None = None  # ISO-8601; required when volatility == volatile

    @field_validator("summary")
    @classmethod
    def _summary_len(cls, v: str) -> str:
        # PRD §2: short (<300 char) plain-language gist.
        if len(v) > 300:
            raise ValueError(f"summary must be <300 chars, got {len(v)}")
        return v

    @field_validator("body_markdown")
    @classmethod
    def _body_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("body_markdown must be non-empty")
        return v

    @model_validator(mode="after")
    def _travel_rules(self) -> "KnowledgeBlock":
        # PRD §T3.3: volatile blocks must carry an expiry.
        if self.volatility == Volatility.volatile and not self.valid_until:
            raise ValueError("valid_until is required when volatility == 'volatile'")
        # Country-scoped geo must carry a valid ISO 3166-1 alpha-2 code.
        if self.geo and self.geo.scope == "country":
            code = (self.geo.country_iso2 or "").upper()
            if code not in ISO_ALPHA2:
                raise ValueError(f"geo.country_iso2 {code!r} is not a valid ISO alpha-2 code")
        return self


# ------------------------------------------------- adapter <-> pipeline contract


@dataclass
class SeedConfig:
    """Parsed `config/<use_case>.yaml` handed to an adapter's discover()."""

    use_case: str
    raw: dict = field(default_factory=dict)

    def get(self, key: str, default=None):
        return self.raw.get(key, default)


@dataclass
class SourceRef:
    """A resolved reference to one source item, produced by discover()."""

    url: str
    source_id: str
    title: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class RawItem:
    """Raw fetched content for one ref, produced by fetch()."""

    ref: SourceRef
    content: bytes
    content_type: str = ""
    from_cache: bool = False

    def text(self, encoding: str = "utf-8") -> str:
        return self.content.decode(encoding, errors="replace")


@dataclass
class ParsedSection:
    """A cleaned section produced by parse(). License is PER-SECTION (PRD §3)."""

    section_title: str
    body_markdown: str
    license: License
    codes: dict = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    audience: Audience | None = None  # overrides ItemContext default when set
    meta: dict = field(default_factory=dict)  # e.g. is_patient_facing, source_version


@dataclass
class ItemContext:
    """Item-level metadata the pipeline needs to build blocks for one source item.

    Assembled by the extractor from the adapter's ref/raw output plus per-source
    citation constants.
    """

    source: Source
    source_id: str
    source_url: str
    title: str
    publisher: str
    attribution_text: str
    default_license: License
    default_audience: Audience
    adapter_name: str
    extractor_name: str
    use_case: UseCase
    # human-readable stem for block ids (e.g. "diabetes"); defaults to slug(title)
    id_stem: str = ""
    source_last_updated: str | None = None
    source_version: str | None = None
    language: str = "en"
    # travel facets (PRD §T3) — set by travel extractors, ignored elsewhere
    geo: GeoScope | None = None
    trip_types: list[TripType] = field(default_factory=list)
    volatility: Volatility = Volatility.evergreen
    valid_until: str | None = None


# --------------------------------------------------------------------------- helpers

_slug_strip = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 80) -> str:
    """Deterministic slug: ASCII-fold, lowercase, hyphenate, truncate (PRD §4.5)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = _slug_strip.sub("-", text.lower()).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "section"


def content_hash(body_markdown: str) -> str:
    """sha256 over the normalized body (PRD §2). Whitespace-normalized for stability."""
    normalized = "\n".join(line.rstrip() for line in body_markdown.strip().splitlines())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
