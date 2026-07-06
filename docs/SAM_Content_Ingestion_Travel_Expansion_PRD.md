# SAM Content Ingestion — Travel Health Expansion (PRD v1.2)

**Status:** Ready for implementation
**Extends:** `docs/SAM_Content_Ingestion_PRD.md` (base PRD; referenced sections use base §-numbers)
**New sections here use `§T*`** to avoid clashing with the base PRD.
**Executor:** Claude Code, operating in `sam-content-ingest/`.

---

## §T1 — Summary & goal

Today `travel_health` holds **18 blocks** from six generic CDC prep pages plus Zika (`out/blocks/travel_health.jsonl`). There is no destination-specific content, no trip-type coverage, no country-level data, and no medication-in-transit guidance.

This PRD expands `travel_health` into a production corpus along three axes — **destination**, **trip type**, and **depth** — by:

1. Ingesting **all ~200 CDC Travelers' Health destination pages**.
2. Ingesting **CDC Yellow Book trip-type chapters** (altitude, diving, cruise, medical tourism, chronic-illness travel).
3. Adding a **U.S. State Department** stream (country information + travel advisories) via a new adapter.
4. Adding a **TSA/FAA medication-in-transit overlay** and **linking** it to the existing 768-block DailyMed corpus by `rxcui` — without duplicating drug content.

It also extends `KnowledgeBlock` with **first-class travel facets** (`geo`, `trip_types`) and a **universal freshness model** (`volatility`, `valid_until`) so advisories can be ingested safely.

## §T2 — Non-goals (explicit)

These are deliberately **out of scope** for this pass:

- **MASA-specific content/coverage/ESP** (Global Advisor, Sick While Away, repatriation product terms, AXA). Ingested later.
- **Decision-tree / risk-model / scoring logic.** The ingestion repo produces *data only*; branching, risk models, and score logic live in the consuming app.
- **Commercial or proprietary sources.** License stays `us_gov` / `public_domain` for 100% of new blocks. No DAN, no insurer comparison data, no paywalled content.
- **User-data / coverage-wallet schema.** That is a profile-layer concern, not ingestion.
- **Copying DailyMed drug bodies** into travel blocks. Link by code; never duplicate.

## §T3 — Schema changes (extends base §2)

Edit `sam_ingest/core/schema.py`. All additions are **backward-compatible** (safe defaults) so existing non-travel blocks validate unchanged under `extra="forbid"`.

### §T3.1 New enums & model

```python
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
    periodic  = "periodic"      # destination/country pages, Yellow Book editions
    volatile  = "volatile"      # travel advisories; must carry valid_until

class GeoScope(BaseModel):
    scope: str                  # "global" | "region" | "country" | "subnational"
    country_iso2: str | None = None   # ISO 3166-1 alpha-2 when scope == "country"
    country_name: str | None = None
    region: str | None = None
```

### §T3.2 `KnowledgeBlock` additions

Add to the model with defaults:

```python
geo: GeoScope | None = None
trip_types: list[TripType] = Field(default_factory=list)
volatility: Volatility = Volatility.evergreen
valid_until: str | None = None      # ISO-8601; required when volatility == volatile
```

### §T3.3 New validators

- If `volatility == volatile`, `valid_until` **must** be set.
- If `geo` present and `geo.scope == "country"`, `geo.country_iso2` must be a valid ISO 3166-1 alpha-2 code.
- Keep the existing `summary < 300` and non-empty `body_markdown` rules.

### §T3.4 Migration / idempotency

- `content_hash` remains **body-only** — bodies and hashes stay stable across re-runs (base §4.5).
- Bump `PIPELINE_VERSION` in `sam_ingest/__init__.py` → written to `provenance.pipeline_version`.
- Regenerate everything with `sam-ingest ingest all`. Existing non-travel blocks must re-emit with **identical `content_hash`** (this is an acceptance test — see §T7); only the new defaulted fields appear.

## §T4 — Sources & adapters (extends base §3 / §6.6)

All four streams run under `use_case = travel_health`.

### §T4.1 CDC destination pages — adapter `cdc_pages` (existing, minor change)

- **URL pattern:** `https://wwwnc.cdc.gov/travel/destinations/traveler/none/{slug}` (routes through the existing curl_cffi impersonation; Akamai robots permit `/travel/`).
- **Seed:** enumerate ~200 destinations in `config/travel_health.yaml` (see §T5). Each entry: `{id, country_name, iso2, url, keywords, trip_types?}`.
- **Adapter change:** `CdcPagesAdapter.discover()` currently passes only `audience` + `keywords` in `SourceRef.meta`. Extend the passthrough to include `country_name`, `iso2`, and `trip_types` from the seed entry.
- **Extractor change** (`sam_ingest/extractors/travel_health.py`): read `ref.meta.iso2/country_name` → set `geo = GeoScope(scope="country", country_iso2=…, country_name=…)` on emitted blocks; set `trip_types` from meta.
- **Facets:** `volatility = periodic`; `license = us_gov`. Keep `include_notices: false` — acute notices stay excluded (that's what the State advisory stream is for).
- **Robustness:** CDC slugs don't map 1:1 to ISO country names. Handle 404 / empty-content gracefully, **log every skipped country**, and emit a coverage report at end of run (countries seeded vs. ingested).

### §T4.2 CDC Yellow Book trip-type chapters — adapter `cdc_pages` (existing)

- **URLs:** `https://wwwnc.cdc.gov/travel/yellowbook/2024/…` chapters covering altitude illness, scuba/diving, cruise-ship travel, medical tourism, and travelers with chronic illnesses/disabilities.
- **Seed:** add as `direct_pages` entries tagged with the relevant `trip_types` (e.g. altitude chapter → `[altitude]`; chronic-illness chapter → `[chronic_condition]`).
- **Facets:** `volatility = periodic` (Yellow Book is edition-versioned — **pin the `2024` edition in the URL** and set `source_last_updated`; flag for review when the next edition ships). `geo` normally `null` or `scope="global"`. `license = us_gov`.

### §T4.3 State Dept country info + advisories — adapter `state_gov` (NEW)

- **Build a new adapter** `sam_ingest/adapters/state_gov.py` implementing the `BaseAdapter` contract (`discover` / `fetch` / `parse`), mirroring `cdc_pages.py`. Add `state_dept` to the `Source` enum. Add fixtures under `tests/fixtures/state_gov/` and a `tests/test_state_gov.py` mirroring `tests/test_cdc.py`.
- **Content:** `travel.state.gov` country-information pages **and** travel advisories (Levels 1–4).
  - Country info → `volatility = periodic`, `geo.scope = "country"`.
  - Advisories → `volatility = volatile`, **`valid_until` required**, `geo.scope = "country"`. Capture the **advisory level** as a keyword/metadata token only (e.g. `advisory_level_3`) — do **not** build level→action logic (that's app-layer, §T2).
- **License:** U.S. federal government work → `us_gov`. Preserve State Dept attribution in `citation`.

### §T4.4 TSA/FAA transit rules + DailyMed link — adapter `gov_pages` (NEW) + linking task

- **Generalize the direct-page logic.** Factor the fetch/parse core of `cdc_pages.py` into a reusable direct-page adapter `gov_pages` that takes its logical `source` from config, so TSA/FAA don't require bespoke crawlers. Add `tsa` and `faa` to the `Source` enum (`license = us_gov`). Keep `cdc_pages` working (either as a thin subclass of the generalized adapter or unchanged — do not regress the CDC source name/tests).
- **Content (new `travel_health` blocks):** medications & medical devices through security (TSA), portable-oxygen-concentrator rules (FAA), and general "traveling with medications" logistics (carry-on quantities, refrigeration/insulin, controlled-substance and country-entry restrictions — source restriction facts from State/embassy pages where available; keep general).
  - Tag with `trip_types = [chronic_condition]` where applicable; `volatility = evergreen`.
- **Linking to DailyMed (link, don't copy):** the join key is **`codes.rxcui`** (verified: DailyMed blocks populate `rxcui`, e.g. acetaminophen → `161`; their `keywords` are empty, so rxcui/id is the only reliable join). Med-overlay blocks that reference a specific drug **must populate `codes.rxcui`** for that drug so the app can join travel context ↔ existing DailyMed detail at retrieval time. **Do not** copy DailyMed body text into travel blocks.
  - **Prereq check:** confirm `rxcui` coverage across `out/blocks/medication.jsonl`; report any referenced drug lacking an rxcui.

## §T5 — Config changes

Restructure `config/travel_health.yaml` into source-keyed sections; each adapter reads its own section:

```yaml
cdc_prep:          # existing 6 evergreen pages — keep
  direct_pages: [...]
cdc_destinations:  # NEW — ~200 entries
  direct_pages:
    - {id: mexico, country_name: "Mexico", iso2: MX, url: ".../none/mexico", keywords: [travel, mexico]}
    # ...
cdc_yellowbook:    # NEW — trip-type chapters
  direct_pages: [...]
state_dept:        # NEW
  country_info: [...]
  advisories: [...]
tsa_faa:           # NEW
  direct_pages: [...]
```

- Claude Code should **generate the ~200-entry destination list** from an ISO 3166-1 country list mapped to CDC destination slugs, then reconcile against live pages (skip/log misses per §T4.1). Commit the generated list into the config.
- Keep one YAML per use case (base convention). Do **not** split into multiple use-case files.

## §T6 — Pipeline, freshness & CLI (extends base §4.5 / §4.7)

- No change to `content_hash` semantics (body-only).
- **Freshness is now operational, not one-shot.** Recommended refresh cadence: `volatile` (advisories) daily–weekly via `--refresh`; `periodic` (destinations, Yellow Book, country info) monthly; `evergreen` on demand.
- Extend `sam-ingest stats` to report **counts by `volatility`**, **geo coverage** (# distinct `country_iso2`), and **trip_type** distribution.
- Extend `sam-ingest validate` to flag any `volatile` block **past its `valid_until`** as stale.

## §T7 — Acceptance criteria / QA

1. `sam-ingest ingest travel_health` completes clean; block count grows from 18 into the hundreds (destinations × sections + chapters + State + TSA/FAA).
2. `sam-ingest validate` passes with the new validators active.
3. **Backward-compat proof:** every existing non-travel block re-emits with an **identical `content_hash`**.
4. Every destination block has `geo.scope == "country"` and a valid `country_iso2`.
5. Every trip-type chapter block carries ≥1 `trip_type`.
6. Every advisory block has `volatility == "volatile"` and a non-null `valid_until`.
7. `license ∈ {us_gov, public_domain}` for **100%** of new blocks.
8. Med-overlay blocks that name a drug carry `codes.rxcui` and contain **no** copied DailyMed body text.
9. New adapters (`state_gov`, `gov_pages`) have fixtures + unit tests mirroring the existing `tests/` pattern; `test_schema.py` updated for the new fields; full test suite green.
10. A **destination coverage report** is produced (seeded vs. ingested; misses logged).

## §T8 — Execution sequence (for Claude Code)

1. **Schema** (§T3): enums, `GeoScope`, `KnowledgeBlock` fields, validators, `test_schema.py`. Bump `PIPELINE_VERSION`.
2. **CDC destinations** (§T4.1): extend `cdc_pages` meta passthrough; extend `travel_health` extractor to map geo/trip_types; generate + commit the ~200-entry seed; ingest; verify coverage report.
3. **Yellow Book** (§T4.2): add chapter seeds; ingest.
4. **State Dept** (§T4.3): new `state_gov` adapter + fixtures/tests + config; ingest country info (periodic) + advisories (volatile + `valid_until`).
5. **TSA/FAA + DailyMed link** (§T4.4): generalize direct-page logic into `gov_pages`; add `tsa`/`faa` sources; author med-overlay blocks with `rxcui` join; run the rxcui prereq check.
6. **Finalize:** `sam-ingest ingest all` → `validate` → `stats`. Confirm all §T7 criteria.

## §T9 — Risks & notes

- **Slug ↔ ISO mapping** for CDC destinations is imperfect; expect misses — handle gracefully and report, don't fail the run.
- **Bot management** on `wwwnc.cdc.gov` and `travel.state.gov`: reuse the shared curl_cffi impersonation client and `PoliteClient` rate limits; respect TTLs.
- **Yellow Book edition** is in the URL (`2024`) — pin it and set a review flag for the next edition.
- **Advisory volatility** means the travel corpus is never "done"; `valid_until` + `validate --stale` make staleness visible but require a scheduled refresh.
- **Adapter generalization** (§T4.4) must not regress the existing `cdc` source name or `test_cdc.py`.
