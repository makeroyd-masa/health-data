# PRD — SAM Evergreen Content Ingestion Pipelines

**Owner:** MASA / SAM
**Audience:** Claude Code (implementation agent)
**Status:** Ready to build
**Version:** 1.1

> **v1.1 changelog (2026-07-05).** Revised after a live verification pass of all sources and an owner decision review (see `docs/PRD_REVIEW.md`). Key changes: reframed the goal to include a **content-richness probe** for prioritization; **removed the AHRQ networked adapter** (blocked + app-only content) — `visit_prep` now runs off MedlinePlus + a static seed; **repointed CDC syndication to the HHS Digital Media platform**; corrected DailyMed (`drug_name`, XML-only section bodies, LOINC map, version history) and MedlinePlus (date-stamped bulk XML, no A.D.A.M./ASHP feed flag) details; added `language` and `provenance.source_version` to the schema; fixed the determinism definition; dropped the unusable "MedlinePlus consumer drug info" option (it's the excluded ASHP monographs); added a per-use-case richness summary to the manifest. Adapter count is now **4**, not 5.

---

## 0. How to use this document

This PRD specifies a set of **content extractors / ingestion pipelines** that pull authoritative, legally-clean public content and normalize it into a single **SAM knowledge-block** format that SAM's evidence layer (and later a RAG index) will consume.

- Sections **1–5** are the **shared core**: goals, the normalized schema, the adapter interface, the pipeline, and cross-cutting (legal, caching, testing) requirements. **Read these first — they apply to every extractor.**
- Section **6** contains **one self-contained spec per use case**. You can implement them one at a time. Each names its source adapter(s), seed inputs, field mapping, tags, and acceptance criteria.
- Section **7** is repo layout and stack. Section **8** is explicitly **out of scope**.

The single most important rule: **every extractor emits the same `KnowledgeBlock` schema (§2).** Do not let schemas diverge per source. Sources are adapters behind one contract, so a future Mayo Clinic adapter (post-license) drops in without touching consumers. (See §8 for the two ways that drop-in is harder than it looks — persistence rights and delivery cadence — and how we hedge for them now.)

---

## 1. Goals & context

SAM is an AI advocacy assistant. Its guided flows surface short, cited, plain-language content **at the point of need** (e.g., inside a "Household Readiness" flow, or when a user asks a medication question). Mayo Clinic content is being licensed for this but is weeks/months out. In the meantime we will demonstrate the mechanic — and learn which content types serve which use cases — using public-domain / open federal sources.

**Dual intent — this is also a content-richness probe.** Beyond demonstrating the mechanic, a primary purpose of this exercise is to **measure how much clean, usable content each use case actually yields from free public sources.** Those results tell us which use cases we can already solve well, which are thin and need a licensed/structured source (e.g., Mayo, or an arranged feed), and therefore how to prioritize building a functioning SAM prototype. Every run should make that assessment easy to read (see the richness summary in §2 / §4).

**In scope:** extractors for 6 use cases across 4 public sources, producing normalized knowledge blocks + a manifest, with citations, licensing metadata, caching, and change detection.

**Not in scope:** symptom triage (reserved for Mayo, post-license), the LLM router, the SAM app itself, and any Mayo content. See §8.

**Success looks like:** for each use case, running `ingest <use_case>` produces a set of valid, deduplicated, cited `KnowledgeBlock` records that the SAM prototype can query by keyword/topic and render at a point-of-need slot, with a clearly swappable source — **and** a richness summary that shows, per use case, how much content we captured and of what kind (patient-facing vs. clinical, coverage, size).

---

## 2. The normalized output: `KnowledgeBlock`

Every extractor emits records conforming to this schema. Represent it with a validated model (e.g., pydantic in Python or zod in TS). One article/page becomes **one or more** blocks — chunk by meaningful section so each block is independently surfaceable and citable.

```jsonc
{
  "id": "medlineplus:diabetes:when-to-see-a-doctor",   // stable, source-prefixed slug
  "use_case": "condition_explainer",                    // enum, see §6
  "source": "medlineplus",                              // enum: medlineplus | ready_gov | cdc | dailymed
  "source_id": "4589",                                  // native id / setid / topic id / page slug
  "source_url": "https://medlineplus.gov/diabetes.html",
  "language": "en",                                     // ISO code; v1 ingests en only (field present for future multi-language / Mayo)
  "title": "Diabetes",
  "section": "When to see a doctor",                    // the sub-section this block represents
  "audience": "patient",                                // patient | caregiver | general
  "summary": "Short (<300 char) plain-language gist for point-of-need surfacing.",
  "body_markdown": "Clean markdown. No nav/boilerplate. Plain language.",
  "keywords": ["diabetes", "blood sugar", "hyperglycemia"],
  "codes": { "icd10": [], "snomed": [], "rxcui": [], "ndc": [], "loinc": [] }, // populate when available
  "citation": {
    "publisher": "MedlinePlus (U.S. National Library of Medicine)",
    "source_url": "https://medlineplus.gov/diabetes.html",
    "attribution_text": "Courtesy of MedlinePlus from the National Library of Medicine",
    "retrieved_at": "2026-07-03T00:00:00Z"             // last time content CHANGED (see idempotency, §4.5), not last run
  },
  "license": "medlineplus_terms", // public_domain | us_gov | medlineplus_terms | ready_gov_reprint
  "source_last_updated": "2026-05-01",
  "ingested_at": "2026-07-03T00:00:00Z",               // last time content CHANGED, not last run
  "content_hash": "sha256:…",     // hash of normalized body; used for dedupe + change detection
  "provenance": {
    "extractor": "condition_explainer",
    "adapter": "medlineplus",
    "pipeline_version": "1.1",
    "source_version": "3"        // native version integer where the source exposes one (e.g., DailyMed spl_version); else null
  }
}
```

Notes:
- **`language`** is present from v1 (default `"en"`). v1 ingests English only; the field exists so Spanish content (MedlinePlus `healthTopicsSpanish`, CDC/HHS `language.isoCode`) and multi-language Mayo content drop in without a schema change.
- **`codes`** carry ICD-10 / SNOMED / RxCUI / NDC / LOINC when the source exposes them (DailyMed exposes RxCUI/NDC/setid + LOINC section codes; MedlinePlus exposes MeSH; MedlinePlus Connect can map codes). These make later matching — and swap-in of Mayo's ICD-10-indexed / HL7 Infobutton content — trivial. Populate opportunistically now.
- **`provenance.source_version`** stores a native version integer where one exists (DailyMed `spl_version`), so change detection does not rely on `source_last_updated` alone. `null` where the source has no version concept.
- **`summary`** is generated by simple, deterministic extraction — the first complete sentence(s) up to 300 chars from the section body after boilerplate strip; for list-only sections, use the section title plus item count. **Not** an LLM call in this pipeline (keep this deterministic and offline).
- **`content_hash`** is over the normalized `body_markdown`. Use it for dedupe **within `(source, source_id)` scope** (not globally, so two sources with identical short text don't collide) and for change detection.
- **Timestamps and determinism.** `ingested_at` and `citation.retrieved_at` record when the block's content **last changed**, not when the pipeline last ran — they are only bumped when `content_hash` changes (persisted in the state file). This is what makes the "byte-identical re-run" guarantee in §9 achievable.

**Output format:** write blocks as newline-delimited JSON (`out/blocks/<use_case>.jsonl`) **and** as individual markdown-with-frontmatter files (`out/markdown/<use_case>/<id>.md`) for human review. Write a top-level `out/manifest.json` (counts per use case/source, run timestamp, pipeline version, per-block id + hash + source_url).

**Richness summary (for prioritization).** `manifest.json` must also include, **per use case**: block count, source coverage (which adapters contributed and how many blocks each), average and total `body_markdown` length, and share of blocks by `audience` (patient / caregiver / general) and by nature (patient-facing vs. clinical, where the source distinguishes — notably DailyMed). `stats` prints this summary. This is the deliverable that tells us which use cases are content-rich today and which need a licensed source.

---

## 3. Adapter interface

Each source is an **adapter** implementing one interface. Use-case extractors (§6) compose adapters; they do not talk to the network directly.

```python
class SourceAdapter(Protocol):
    name: str            # "medlineplus" | "ready_gov" | "cdc" | "dailymed"

    def discover(self, seed: SeedConfig) -> Iterable[SourceRef]:
        """Resolve seeds (topic lists, page URLs, drug lists, API queries) into item refs.
        Adapters own their own pagination and respect per-host limits via the shared client.
        For bulk-file sources (e.g. MedlinePlus bulk XML), discover() downloads and splits the
        bulk file into per-item refs; fetch() then reads from that local store."""

    def fetch(self, ref: SourceRef) -> RawItem:
        """Fetch raw content for one ref. Uses the shared cache + rate limiter."""

    def parse(self, raw: RawItem) -> list[ParsedSection]:
        """Return cleaned sections: {section_title, body_markdown, codes?, license, meta}.
        NOTE: license is per-section, not per-adapter — it varies within a source
        (e.g. Ready.gov HTML pages = us_gov, Ready.gov reprint PDFs = ready_gov_reprint)."""
```

- **License is assigned per `ParsedSection`, not per adapter** — it legitimately varies inside a single source. `parse()` sets it (or the pipeline assigns it via a documented rule per item).
- The core pipeline turns `ParsedSection`s into `KnowledgeBlock`s (adds ids, use_case tag, citation, license, hashes, provenance) and writes output through a **pluggable output sink** (see §4.9) so a future no-persist licensed source can reuse the same pipeline.
- Adapters must never emit boilerplate (site nav, cookie banners, "Skip to content," related-link rails). Convert HTML → markdown and strip chrome.

---

## 4. Shared pipeline requirements

1. **Config-driven seeds.** Every extractor's inputs (topic lists, page URLs, drug lists) live in `config/<use_case>.yaml`, not in code. Ship sensible starter seeds (§6) but keep them editable.
2. **Caching.** Cache every raw HTTP response to disk (keyed by URL + params) with a TTL. Re-runs read cache unless `--refresh`. **Exception:** for date-stamped bulk files (MedlinePlus bulk XML), key the cache by **content hash** (or a date-normalized key) so a new calendar day does not force a needless 29 MB re-download when content is unchanged. This is both a courtesy to public services and what makes runs fast/idempotent.
3. **Rate limiting & retries.** Central polite HTTP client: per-host rate limits, exponential backoff with jitter (e.g., tenacity), and a **descriptive, identifying `User-Agent`** naming the project and a contact. Per-host limits to encode:
   - **MedlinePlus:** ≤ **85 requests/minute** per IP (exceeding it suspends the IP); cache results **12–24 h**. Honor both.
   - **HHS Digital Media (CDC syndication):** ≤ **100 calls / 60 s** per connection.
   - **DailyMed / Ready.gov / wwwnc.cdc.gov:** no documented limit — use a conservative default (≤ ~1 req/s) with backoff.
   - **Browser-like UA required, but honest.** ready.gov and the HHS API return 403 to generic fetchers; send a normal desktop UA **that still identifies the project + contact**. Do **not** use a spoofed UA to evade an actual `robots` prohibition (that is why AHRQ is out of scope for crawling — see §5).
4. **Change detection / incremental.** Skip items whose `content_hash` and version signal are unchanged since the last run (persist a small state file). Per-source version signals: DailyMed → `/spls/{SETID}/history` `spl_version`; CDC/HHS → `dateContentUpdated`/`dateModified`; MedlinePlus → bulk-file date / `date-created`; Ready.gov & static seeds → content hash only (content is frozen/static). `--refresh` forces re-fetch.
5. **Deterministic output.** Same inputs → same blocks (stable ids, sorted output). "Byte-identical re-run" is defined as **identical modulo `ingested_at` / `citation.retrieved_at` / the manifest run-timestamp** — those are bumped only when `content_hash` changes (§2). No randomness, no network calls in the transform stage, no LLM calls anywhere in this pipeline. Define the slug function explicitly (lowercase, ASCII-fold, hyphenate, max length, `-2` collision suffix) and key incremental state on `(source, source_id, section_index)` so a retitled section does not create a phantom new block.
6. **Mandatory provenance.** Every block must have a populated `citation`, `license`, `source_url`, and `provenance`. Fail the block (log + skip, don't crash) if any is missing.
7. **CLI.** `ingest <use_case> [--refresh] [--limit N] [--dry-run]`; `ingest all`; `validate` (schema-check existing output); `stats` (print manifest summary **including the per-use-case richness summary**).
8. **Logging.** Structured logs: per item fetched/cached/skipped/failed, and a run summary. A failed page mid-run must not corrupt state — persist progress so `--refresh` re-fetches only failed refs; log partial failures explicitly.
9. **Pluggable output sink.** `pipeline.py` writes through a sink abstraction (default sink = the `jsonl` + `markdown` + `manifest` writer in §2). This is small now and avoids a rewrite later when a licensed source (Mayo) may forbid local persistence (§8).

---

## 5. Cross-cutting legal & compliance requirements (non-negotiable)

These protect MASA and the eventual Mayo relationship. Bake them in.

1. **Only ingest the sanctioned feed/format for each source** (APIs, bulk files, syndication, or explicitly reproducible pages — see §6). Do not scrape around paywalls, terms, or `robots` restrictions. (This is why AHRQ is not crawled — its `robots.txt` disallows `anthropic-ai`/`Claude-Web` and it 403s bots; its public-domain "10 Questions" content enters only as a one-time manually-captured static seed, §6.3.)
2. **Attribution on every block.** Populate `citation.attribution_text`. For MedlinePlus specifically: use "Courtesy of MedlinePlus from the National Library of Medicine," **do not** use the MedlinePlus logo, **do not** reframe pages under another domain, and **do not** imply endorsement. For FEMA/Ready.gov reprints: **do not alter content** and **do not imply government endorsement**. Carry these as data so the UI can render them.
3. **Exclude known copyrighted subsets.** MedlinePlus mixes public-domain federal summaries with copyrighted material. **There is no flag in the feed for this** — exclusion is **by source section/URL**: ingest only the health-topic XML / Web Service (federally-produced, public-domain summaries) and **never follow links into `/ency/` (A.D.A.M. Medical Encyclopedia) or `/druginfo/` (ASHP drug monographs)**, nor ingest the linked external `<site>` resources. The MedlinePlus terms explicitly forbid ingesting/branding the copyrighted content in a health IT system, so stay on the summaries. If any content's attribution names a third-party rights holder, skip it and log it.
4. **License field is required** and must reflect the actual source (`public_domain`, `us_gov`, `medlineplus_terms`, `ready_gov_reprint`). CDC content syndicated via HHS is `us_gov`.
5. **FEMA reprint "no alteration" caveat.** Reprint terms state content "will not be altered in any way." We extract text for internal evidence-grounding/citation (not redistribution of the publication), always store `source_url` to the original unaltered PDF, never present extracted text *as* the FEMA publication, and never imply FEMA endorses SAM. **MASA legal sign-off on this interpretation is pending and will be obtained in time** — it does not block capturing the data for the richness assessment, but must be resolved before any external-facing use.
6. **No Mayo content** enters this pipeline. Mayo is a future adapter behind the same interface (§8).

---

## 6. Per-use-case extractor specs

Use-case enum: `household_readiness | condition_explainer | visit_prep | medication | aging_home_safety | travel_health`.

### 6.1 Household Emergency Readiness — `household_readiness`
- **Adapter:** `ready_gov` (FEMA / Ready.gov). License: `ready_gov_reprint` (PDF reprints) / `us_gov` (HTML pages).
- **Why this source:** For the v1 Household Readiness flow, Ready.gov is arguably a better fit than Mayo — it is public domain and publishes the exact artifacts the flow generates. Recommended **first** build (small fixed set, clean server-rendered HTML, no API risk).
- **Access:** Ingest the sanctioned Ready.gov content pages and their linked publication PDFs (FEMA makes Ready publications free to download and reproduce under simple reprint terms). No API; a small fixed page/PDF set. **Site is server-rendered Drupal (no JS rendering needed) but returns 403 without a browser-like User-Agent — send one (§4.3).** PDFs are hosted on ready.gov under `/sites/default/files/YYYY-MM/…`. **Note the publication library is frozen as of 2025-09-30** (FEMA will not update it regularly after that) — do not build elaborate PDF change detection.
- **Seed (`config/household_readiness.yaml`)** — verified current slugs:
  - core: Build A Kit (`/kit`), Make A Plan (`/plan`), Family Emergency Communication Plan (`/plan-form`, incl. the fillable-card PDF), Evacuation (`/evacuation`), Financial Preparedness (`/financial-preparedness`). **Emergency Supply List has no standalone page** — its content is on `/kit`; capture the supply-kit checklist PDF (`/sites/default/files/2024-05/ready_supply-kit-checklist.pdf`).
  - hazards: Hurricanes (`/hurricanes`), Floods (`/floods`), Wildfires (`/wildfires`), Power Outages (`/power-outages`), Extreme Heat (**`/heat`** — not `/extreme-heat`).
  - (Editable list.)
- **Parse/map:** each page → sections. Hazard pages use **Before / During / After** H2/H3 sections; kit/financial pages are `<li>` checklists. Preserve checklist structure in `body_markdown`. `audience`: `general`. Tag hazard pages with the hazard keyword so the flow's geo-risk branch can select them.
- **PDF handling:** extract text from linked publication PDFs (`pdfplumber`/`pypdf`); keep list structure; skip images/graphics and pure-graphic PDFs; the fillable communication-plan card is a form, not prose — expect to hand-curate it. (Reprint terms forbid altering — we store text as reference and link to the unaltered PDF; we do not redistribute altered artwork.)
- **Acceptance:** ≥ the full seed set ingested; supply-list (from `/kit` + PDF) and communication-plan content present as discrete blocks; each hazard page tagged; all reprint-PDF blocks `license = ready_gov_reprint` (HTML-page blocks `us_gov`), attribution populated, no altered content.

### 6.2 Condition explainer / new diagnosis — `condition_explainer`
- **Adapter:** `medlineplus`. License: `medlineplus_terms` (federal summaries are public domain).
- **Access:** Prefer the **MedlinePlus bulk health-topic XML files** (complete set, published daily Tue–Sat) for coverage; use the **MedlinePlus Web Service** (`https://wsearch.nlm.nih.gov/ws/query?db=healthTopics&term=…`, XML) for targeted lookups/testing. Free, no registration/licensing. Respect 85 req/min + 12–24 h caching. **Bulk filenames are date-stamped (`mplus_topics_YYYY-MM-DD.xml`) — resolve the current file dynamically from `https://medlineplus.gov/xml.html`; do not hardcode a URL.**
- **Seed:** either ingest all health topics from the bulk XML, or a starter list of ~50 common conditions (`config/condition_explainer.yaml`). Support both via a `mode: bulk|list` config.
- **Parse/map:** from each `<health-topic>` record, extract `<full-summary>` (HTML narrative) as the body, `title`, `date-created`, and `<mesh-heading><descriptor>` → keywords/`codes`. **Reality check:** `<full-summary>` is generally a single narrative, **not** reliably divided into Symptoms / When-to-see-a-doctor / Causes / Prevention. Emit one `Overview` block per topic as the norm; where the summary HTML contains clear `<h2>/<h3>` sub-headings, split into those additional blocks opportunistically. **Never follow `/ency/` (A.D.A.M.), `/druginfo/` (ASHP), or external `<site>` links.** `audience`: `patient`.
- **Acceptance:** each seeded topic yields ≥1 block (an `Overview` at minimum); named sub-section blocks emitted where the source's headings support it; **zero `/ency/` or `/druginfo/` content**; keyword field populated for retrieval.

### 6.3 Appointment / visit prep — `visit_prep`
- **Adapter:** `medlineplus` only. **The AHRQ networked adapter is cut** — ahrq.gov blocks bots (CloudFront 403 + `robots.txt` disallows `anthropic-ai`/`Claude-Web`), and its per-encounter question sets are generated inside an app, not published as ingestible pages. Crawling it would violate §5.1.
- **Access:** MedlinePlus **"Talking With Your Doctor"** topic (`https://medlineplus.gov/talkingwithyourdoctor.html`) via the `medlineplus` adapter. **Plus a one-time static seed:** AHRQ's public-domain **"The 10 Questions You Should Know"** list, transcribed once into `config/visit_prep_static/` with its AHRQ "Internet Citation" as attribution (PD reuse of content a human read — not crawling). Optionally include AHRQ brochure/poster PDFs the same way if a human downloads them.
- **Parse/map:** MedlinePlus "Talking With Your Doctor" → block(s), `audience`: `patient` and `caregiver` (the topic includes family/caregiver framing — tag accordingly). The static "10 Questions" → one `general` question-bank block (clean markdown list), `us_gov` license, AHRQ citation.
- **Acceptance:** ≥1 MedlinePlus visit-prep block + the general "10 Questions" block; caregiver-tagged where applicable; no dependency on any AHRQ API or crawl. **Prioritization finding to record in the richness summary:** the richer structured, encounter-specific AHRQ question bank is *not* ingestible from public sources → flag `visit_prep` as a candidate for a licensed/arranged source later.

### 6.4 Medication questions — `medication`
- **Adapter:** `dailymed`. **No MedlinePlus option here** — MedlinePlus's consumer drug information *is* the ASHP monographs (`/druginfo/`) excluded by §5.3. Medication content stays clinical (DailyMed-only) until licensed consumer drug content (Mayo) lands.
- **Access:** **DailyMed REST API v2**, base `https://dailymed.nlm.nih.gov/dailymed/services/v2/` (append `.xml`/`.json`). List/filter SPLs via `/spls` (filter by **`drug_name`** [not `drugname`], `rxcui`, `ndc`; `pagesize` default/max **100**, `page` from 1). **`/spls` returns metadata only** (`setid`, `spl_version`, `title`, `published_date`). Fetch a full label via **`/spls/{SETID}.xml`** — **ingest the XML**; the full section-structured HL7 document is documented for XML only, so do not assume `.json` returns full section bodies. No API key; no documented rate limit (throttle politely anyway).
- **Seed:** `config/medication.yaml`, derived from **RxNorm's Current Prescribable Content** set — top ~100 common medications by RxCUI (editable). Resolve each drug via RxCUI.
- **Selection policy:** **one SPL per RxCUI** — the latest `spl_version`, preferring the generic (`name_type=g`) where available. (Cleanest, fewest blocks; captures the current authoritative label.)
- **Parse/map:** SPL sections are LOINC-coded. Emit one block per consumer-relevant section, extracting by LOINC code:

  | Section | LOINC |
  |---|---|
  | Indications & Usage | 34067-9 |
  | Dosage & Administration | 34068-7 |
  | Warnings and Precautions | 43685-7 |
  | Warnings (plain) | 34071-1 |
  | Adverse Reactions | 34084-4 |
  | Drug Interactions | 34073-7 |
  | Information for Patients | 34076-0 |
  | SPL Patient Package Insert (PPI) | 42230-3 |
  | SPL MedGuide | 42231-1 |

  Handle **both** Warnings codes (a label uses one or the other). Treat **PPI (42230-3)** and **Information for Patients (34076-0)** as distinct patient-facing sections and **flag them as the preferred surfacing blocks**. Populate `codes.rxcui` / `codes.ndc` / `codes.loinc` and `source_id` = SETID. Set `audience`: `patient`. Keep clinical language faithful — do **not** rewrite medical content.
- **Change detection / idempotency:** detect new label versions via **`/spls/{SETID}/history`** (higher `spl_version`); store `spl_version` in `provenance.source_version`. Idempotent on re-run by SETID + version.
- **Note:** DailyMed labels are authoritative but clinical; many SPLs have **no** PPI / Information-for-Patients section, so a large share of drugs will yield clinical-only blocks. This is exactly where licensed Mayo consumer drug content would later improve readability — the swap is a source change, same schema. The richness summary should surface the patient-facing vs. clinical share for this use case.
- **Acceptance:** each seeded drug resolves (via RxCUI) to one representative SPL and yields section blocks; `codes.rxcui` populated; patient-facing section flagged where available; idempotent on re-run by SETID + `spl_version`.

### 6.5 Aging / caregiving / home safety — `aging_home_safety`
- **Adapters:** `medlineplus` (primary) + `cdc` (STEADI falls-prevention, via HHS syndication). **Gated behind a discovery spike (see Access).**
- **Access:** MedlinePlus topics as in §6.2. **CDC content now syndicates through the HHS Digital Media platform** — base `https://api.digitalmedia.hhs.gov/api/v2/resources/` (the legacy `tools.cdc.gov/api/v2` still responds but its catalog has drained — STEADI/falls return zero there). Enumerate via `/topics` and `/media` (params: `q`, `topic`, `sourceurl`, `mediatypes`, `audience`, `max`, `offset`, `pagenum` — **not** `searchtext`/`topics`); fetch cleaned HTML via `/media/{id}/syndicate` **with `stripScripts=true` explicitly** (scripts are *not* stripped by default). Map fields: **title = `name`** (not `title`), body = `/syndicate` `content`, dates = `dateContentUpdated`/`dateModified`, attribution = `attribution` + `source`. No key for read; honor 100 calls/60 s; send a browser-like UA (HHS 403s generic bots). **Discovery spike first:** enumerate the HHS storefront to confirm which STEADI items are actually syndicated before building; if little is available, defer the CDC half and note it in the richness summary.
- **Seed:** MedlinePlus topics — Falls, Home Safety, Caregivers, Assistive Devices, Older Adult Health, Alzheimer's Caregivers (`config/aging_home_safety.yaml`); CDC — STEADI / Older Adult Fall Prevention media items **confirmed present in the HHS storefront**. (Optional future: NIA content via the same HHS platform — leave a stub; confirm availability before enabling.)
- **Parse/map:** home-safety and fall-risk content → checklist/room-by-room blocks where the source provides them; caregiver content tagged `audience: caregiver`. Preserve list structure.
- **Acceptance:** MedlinePlus seed set ingested; CDC/HHS items ingested **where the spike confirms availability** (else explicitly logged as unavailable); caregiver vs. patient audience tagged; CDC blocks carry CDC attribution + `us_gov` license; MedlinePlus exclusions (`/ency/`, `/druginfo/`) respected.

### 6.6 Travel health readiness — `travel_health`
- **Adapter:** `cdc` (CDC Travelers' Health, via HHS syndication). **Gated behind a discovery spike.**
- **Access:** HHS Digital Media platform (as in §6.5). Filter for Travelers' Health media (content lives under `wwwnc.cdc.gov/travel`). Fetch cleaned HTML via `/syndicate` (`stripScripts=true`). **Fallback:** if evergreen travel-prep items are thin in the HHS storefront, use the Travelers' Health **RSS feed** (`https://wwwnc.cdc.gov/travel/page/rss`) as an alternate ingestion path. Confirm the specific evergreen items exist before building.
- **Seed:** general travel-prep content (before-you-go checklists, travel health kit, "getting sick/injured abroad," destination-prep guidance) (`config/travel_health.yaml`). **Exclude time-sensitive travel *notices*** (dynamic, stale quickly) unless a `include_notices: true` flag is set.
- **Parse/map:** prep checklists and "what to do if you need care abroad" → blocks; tag with `travel`. This use case connects to SAM's travel branch / benefits, so keep "care away from home" content as its own block. `audience`: `general`.
- **Acceptance:** evergreen travel-prep set ingested **where the spike confirms availability** (else logged); notices excluded by default; CDC attribution + `us_gov` license on every block.

---

## 7. Repo layout & stack

Default stack: **Python 3.11+** (`httpx` or `requests`, `lxml`/`beautifulsoup4`, `markdownify`, `pydantic` v2, `tenacity`, `pyyaml`, `orjson`, **`pdfplumber`/`pypdf` for Ready.gov PDFs**). TypeScript/Node is acceptable if preferred — keep the schema and interface identical.

```
sam-content-ingest/
  config/
    household_readiness.yaml
    condition_explainer.yaml
    visit_prep.yaml
    visit_prep_static/        # one-time captured AHRQ "10 Questions" (PD) + optional PDFs
    medication.yaml
    aging_home_safety.yaml
    travel_health.yaml
  src/
    core/
      schema.py          # KnowledgeBlock model + validation
      pipeline.py        # section -> block, hashing, dedupe, write via pluggable sink
      sink.py            # output sink abstraction (default: jsonl + markdown + manifest)
      http.py            # polite client: cache, per-host rate limit, retries, identifying UA
      cache.py
      cite.py            # attribution/license helpers
      chunk.py           # section splitting (h2/h3), html->markdown, boilerplate strip
      cli.py             # ingest / validate / stats (incl. richness summary)
    adapters/
      medlineplus.py
      ready_gov.py
      cdc_syndication.py   # HHS Digital Media platform (api.digitalmedia.hhs.gov); source stays "cdc"
      dailymed.py
      # NOTE: no ahrq.py — AHRQ is a static seed under config/, not a networked adapter
    extractors/
      household_readiness.py
      condition_explainer.py
      visit_prep.py
      medication.py
      aging_home_safety.py
      travel_health.py
  out/
    blocks/*.jsonl
    markdown/<use_case>/*.md
    manifest.json         # includes per-use-case richness summary
  tests/
    fixtures/            # recorded API/page responses (do NOT hit live services in CI)
    test_<adapter>.py
    test_schema.py
  README.md
```

**Testing:** unit-test each adapter's `parse` against **recorded fixtures** (never hammer live `.gov`/HHS services in CI). Validate all emitted blocks against the schema. Include one end-to-end test per use case that runs the extractor against fixtures and asserts the acceptance criteria in §6.

---

## 8. Out of scope (explicit)

- **Symptom triage / "should I be concerned?" logic.** Reserved for **Mayo Clinic** licensed content (their evidence-based symptom-triage algorithms) once licensed. Do **not** build or approximate triage here, and do not ingest any triage content.
- **Any Mayo Clinic content.** When the license lands, add a `mayo.py` adapter implementing the same `SourceAdapter` interface (their content is available via Bulk/Realtime API, ICD-10-indexed, HL7 Infobutton-ready) and re-run the same extractors. The `codes` field and schema already anticipate this. **Two caveats the interface alone does not solve** (hedged for now, not built): (a) **persistence rights** — licensed content typically forbids the local raw-cache + `.jsonl` + `.md` persistence this pipeline does, which is why `pipeline.py` writes through a pluggable sink (§4.9) so a no-persist mode can be added; (b) **delivery cadence** — Mayo's Infobutton is query-time lookup by code, not crawl-and-batch, so "re-run the same extractors" maps to the schema/`codes`, not necessarily the batch pipeline.
- **The LLM router, RAG index build, and the SAM app UI.** This pipeline produces the knowledge blocks those systems consume; it does not implement them. Output must be a clean, queryable block set + manifest + richness summary.

---

## 9. Definition of done

- `ingest all` produces valid, deduplicated, cited blocks for all six use cases, plus `manifest.json` **with the per-use-case richness summary**.
- Every block passes schema validation and carries `citation`, `license`, `source_url`, `provenance` (incl. `language`).
- Re-running without `--refresh` performs no network calls (cache hits) and produces output **identical modulo `ingested_at`/`retrieved_at`/run-timestamp** (which change only when `content_hash` changes).
- No `/ency/` (A.D.A.M.) or `/druginfo/` (ASHP) content; no crawled AHRQ content (static seed only); no altered FEMA reprints; MedlinePlus attribution rules respected; CDC content sourced via HHS; no Mayo content.
- Tests pass against fixtures; a short `README.md` documents each source, its access method, its license, and how to run each extractor.
