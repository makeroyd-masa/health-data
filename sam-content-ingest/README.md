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
| `aging_home_safety` | MedlinePlus + CDC | WS topics ✅ + CDC via HHS syndication (gated) | `medlineplus_terms` / `us_gov` | partial |
| `travel_health` | CDC | HHS Digital Media syndication (gated) | `us_gov` | ❌ (build+fixture) |
| `household_readiness` | Ready.gov / FEMA | HTML pages + reprint PDFs (no API) | `us_gov` / `ready_gov_reprint` | ❌ (build+fixture) |

**Akamai note:** `www.ready.gov` and `api.digitalmedia.hhs.gov` sit behind Akamai bot
management that blocks this environment's HTTP client at the TLS layer (403), even with a
browser User-Agent. Those adapters are fully built and **fixture-tested**; run them live
from an unblocked egress. MedlinePlus, DailyMed work live from anywhere.

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
