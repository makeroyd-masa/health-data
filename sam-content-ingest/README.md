# SAM Evergreen Content Ingestion

Ingestion pipelines that pull authoritative, legally-clean public content and normalize it
into a single `KnowledgeBlock` schema for SAM's evidence layer. See `../docs/SAM_Content_Ingestion_PRD.md`
(v1.1) for the full spec and `../docs/PRD_REVIEW.md` for the source verification / decisions.

The run doubles as a **content-richness probe**: `manifest.json` / `stats` report, per use
case, how much clean content each source yields — to inform which use cases SAM can solve now
vs. which need a licensed source (e.g. Mayo).

## Quick start

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
python -m sam_ingest.core.cli ingest condition_explainer   # run one use case
python -m sam_ingest.core.cli ingest all                   # run everything
python -m sam_ingest.core.cli stats                        # richness summary
python -m sam_ingest.core.cli validate                     # schema-check output
python -m pytest -q                                        # tests (fixtures only)
```

CLI flags: `--refresh` (bypass cache), `--limit N` (cap items per source), `--dry-run`,
`-v` (debug logs), `--root DIR` (project root; default cwd). Output lands in `out/`
(`blocks/*.jsonl`, `markdown/<use_case>/*.md`, `manifest.json`, `state.json`, `.cache/`).

## Sources, access & licensing

| Use case | Source | Access | License | Live from this env? |
|---|---|---|---|---|
| `condition_explainer` | MedlinePlus | Web Service (list mode) / dated bulk XML | `medlineplus_terms` (PD summaries) | ✅ |
| `medication` | DailyMed | REST API v2 — `/spls` + `/spls/{SETID}.xml` (LOINC sections) | `public_domain` | ✅ |
| `visit_prep` | MedlinePlus + AHRQ (static) | WS topic + one-time static "10 Questions" seed | `medlineplus_terms` / `us_gov` | ✅ |
| `aging_home_safety` | MedlinePlus + CDC | WS topics + CDC via HHS syndication | `medlineplus_terms` / `us_gov` | ✅ |
| `travel_health` | CDC + State Dept + TSA/FAA | ~200 CDC destination pages, Yellow Book chapters, State advisories (RSS) + country info, TSA/FAA transit rules + rxcui-linked drug overlay | `us_gov` | ✅ |
| `household_readiness` | Ready.gov / FEMA | HTML pages + reprint PDFs (no API) | `us_gov` / `ready_gov_reprint` | ✅ |

**Travel expansion (v1.2, PRD `docs/…Travel_Expansion_PRD.md`).** `travel_health` is a
production corpus (~4,800 blocks) across three axes — destination, trip type, depth. Blocks
carry travel facets: `geo` (country + ISO-3166 alpha-2), `trip_types`, and a freshness model
(`volatility` ∈ evergreen/periodic/volatile, with `valid_until` required on volatile
advisories, derived deterministically from the RSS pubDate + 180d). State advisories come
from the Travel Advisories **RSS feed** (level captured as an `advisory_level_N` keyword);
country-info pages are scraped best-effort (AEM structure varies — misses logged per §T9).
TSA/FAA medication-in-transit blocks **link to DailyMed by `codes.rxcui`** (never copy label
text). `stats` reports counts by volatility, distinct-country geo coverage, and trip_type;
`validate --stale` flags volatile blocks past `valid_until`. The ~200-country destination
seed and `core/countries.py` name→ISO2 map were generated with `pycountry` at build time
(not a runtime dependency).

**Bot-management note (Akamai/curl_cffi).** `www.ready.gov` and `api.digitalmedia.hhs.gov`
(and `wwwnc.cdc.gov`/`www.cdc.gov`) sit behind Akamai bot management that 403s a plain HTTP
client at the TLS layer. `robots.txt` **permits** the paths we ingest on these hosts (it's
bot-management, not a robots prohibition — unlike AHRQ, which disallows us and stays out of
scope), so `core/http.py` routes those hosts through **`curl_cffi`** (Chrome TLS
impersonation, `_IMPERSONATE_HOSTS`) and everything else through `httpx`. MedlinePlus and
DailyMed work over plain `httpx`. Ready.gov can also run from a one-time local capture (see
`config/household_readiness.yaml` `local_dir:` + `local_capture/ready_gov/`).

**CDC content finding (discovery spike, PRD §6.5/§6.6).** HHS syndication is reachable but
**thin for our use cases**: `q=STEADI`/`travelers health` full-text → 0 results;
`sourceUrlContains=cdc.gov/steadi` → 1 provider-materials page; travel → mostly images + a
couple of (stale) Zika pages. The evergreen STEADI falls-prevention and Travelers' Health
prep content SAM wants is **not in the syndication catalog** — it would need direct-page
fetch from `cdc.gov/steadi` / `wwwnc.cdc.gov/travel` (both reachable via curl_cffi) or isn't
syndicated. This is a prioritization signal: `aging`'s CDC half and `travel_health` are thin
from free sources.

**Legal (PRD §5), enforced in code:**
- MedlinePlus: only health-topic summaries; `/ency/` (A.D.A.M.) and `/druginfo/` (ASHP)
  are excluded **by URL** (the feed carries no flag). Attribution: "Courtesy of MedlinePlus…", no logo.
- Ready.gov reprint PDFs: text extracted for grounding, linked to the unaltered source,
  never altered/redistributed, no implied endorsement (`ready_gov_reprint`).
- AHRQ is **not crawled** (robots disallows AI agents + CloudFront 403); the PD "10 Questions"
  list is a one-time static seed in `config/visit_prep_static/`.
- No Mayo content. Every block carries `citation`, `license`, `source_url`, `provenance`.

## Architecture

```
core/     schema (KnowledgeBlock) · http (cache+rate-limit+UA) · chunk (html→md, section split)
          pipeline (section→block, dedupe, deterministic timestamps) · sink (jsonl+md+manifest)
          state (incremental) · cli
adapters/ medlineplus · ready_gov · dailymed · cdc_syndication   (SourceAdapter contract)
extractors/ one per use case; compose adapters, build ItemContext, call the pipeline
```

- **Determinism:** re-running without `--refresh` makes no network calls and produces output
  identical modulo `ingested_at`/`retrieved_at`/run-timestamp (bumped only when content changes).
- **Change detection:** per `(source, source_id, section_index)` content hash + source version
  (DailyMed `spl_version`); state in `out/state.json`.
- **Swappability:** the `KnowledgeBlock` schema + `codes` field anticipate a future Mayo
  adapter; `pipeline.py` writes through a pluggable sink so a no-persist licensed source fits.

## Editing seeds

Each use case reads `config/<use_case>.yaml`. MedlinePlus list-mode terms resolve via the
Web Service relevance search — weak/ambiguous matches are logged (`weak match for term …`)
so you can pin a better term. Medication drugs can be pinned by `rxcui`.
