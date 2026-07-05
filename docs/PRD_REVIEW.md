# Critical Review — SAM Evergreen Content Ingestion PRD

**Reviewer:** Claude Code
**Date:** 2026-07-05
**Reviewed doc:** `docs/SAM_Content_Ingestion_PRD.md` (v1.0)
**Scope of this pass:** design/feasibility review only. No pipeline code written; PRD not modified.

All five sources were verified against **live** developer docs / pages on 2026-07-05. The
headline: the PRD is well-structured and mostly accurate, but **two of the five sources
(CDC and AHRQ) do not work the way the PRD describes** and need rework before coding, and
there are several internal inconsistencies and one licensing landmine (MedlinePlus consumer
drug info) that should be resolved first.

Verdict badges used below: ✅ confirmed · ⚠️ works with corrections · ❌ blocked / broken as written.

---

## Decisions log (2026-07-05)

Resolved with the owner. Note the reframing: **the ingestion run doubles as a "content-richness probe"** — a deliberate goal is to measure how much clean content each use case yields from free public sources, to inform prioritization (including where licensed sources like Mayo would materially help).

1. **CDC/HHS (Q1):** Repoint the adapter to `api.digitalmedia.hhs.gov` and run a discovery spike to see what STEADI + evergreen travel content is actually available. Proceed on the basis of what we find.
2. **AHRQ (Q2):** **Cut the AHRQ networked adapter** (blocked by CloudFront 403 + robots ban on `anthropic-ai`/`Claude-Web`; content is app-only anyway). Keep the `visit_prep` use case: drive it off the **MedlinePlus** "Talking With Your Doctor" topic, plus a **one-time static seed** of AHRQ's public-domain "10 Questions" list dropped into `config/` with citation (PD reuse, not crawling). Adapter count drops 5→4; §6.3 acceptance rewritten. Finding for prioritization: the richer structured AHRQ question bank is not ingestible → a candidate for a licensed/arranged source later.
3. **MedlinePlus drug info (Q3):** Acknowledged unusable (it's the ASHP monographs §5.3 forbids). Medication content stays clinical (DailyMed-only) until Mayo.
4. **Medication selection (Q4):** **One SPL per RxCUI, latest `spl_version`, prefer generic.** Derive the ~100-drug seed from **RxNorm's Current Prescribable Content** set (not a hand-curated list).
5. **Sub-sections (Q5):** Accept one `Overview` block per MedlinePlus topic as the norm; isolate named sub-sections only opportunistically.
6. **Determinism (Q6):** "Byte-identical re-runs" = identical modulo `ingested_at`/`retrieved_at`/run-timestamp; bump those only when `content_hash` changes.
7. **Language (Q7):** Add `language` field now (default `en`); ingest English-only for v1.
8. **FEMA legal (Q8):** Still needs MASA legal sign-off on PDF→markdown extraction under FEMA reprint terms. Implementation kept defensible in the meantime.
9. **Pluggable sink (Q9):** Build `pipeline.py` with a swappable output sink now, for a future no-persist licensed source.
10. **Stack (Q10):** Python 3.11+ per PRD default.

**Added scope:** `stats` / `manifest.json` should emit a **per-use-case richness summary** (block count, avg body length, % patient-facing vs. clinical, source coverage) so each run directly feeds prioritization.

---

## 0. TL;DR

| Source | Use case(s) | Status | Headline |
|---|---|---|---|
| MedlinePlus | condition_explainer, aging, visit_prep | ✅ / ⚠️ | Endpoints all live. But bulk XML filenames are date-stamped (no static URL), and the feed does **not** flag A.D.A.M./ASHP content — exclusion is by URL section, which the health-topic feed already satisfies. Sub-section structure SAM wants may not exist in `<full-summary>`. |
| Ready.gov / FEMA | household_readiness | ✅ / ⚠️ | No API (correct). Reprint terms confirmed verbatim. Fix two page slugs; PDFs are on ready.gov and **frozen since 2025-09-30**; scraper **must** send a browser User-Agent or gets 403. |
| CDC Syndication | aging (STEADI), travel_health | ❌ | `tools.cdc.gov/api/v2` still returns 200 but the content has **drained** — STEADI/falls/travel all return 0 results. Service migrated to **HHS Digital Media** (`api.digitalmedia.hhs.gov`). Params, defaults, and field names in the PRD are wrong. |
| DailyMed | medication | ✅ / ⚠️ | v2 REST API live and accurate. Param is `drug_name` not `drugname`; `/spls` is metadata-only; must ingest **XML** (not JSON) for section bodies; use `/spls/{SETID}/history` for versioning. |
| AHRQ | visit_prep | ❌ | ahrq.gov is behind CloudFront (**403 to bots**) and robots.txt **explicitly blocks `anthropic-ai`/`Claude-Web`**. The four "encounter-type question paths" are **app features, not published HTML pages** — only "10 Questions" + PDFs are ingestible. §6.3 acceptance criteria are unachievable as written. |

**Do these before writing code:** (1) repoint CDC → HHS and re-scope which STEADI/travel items actually exist there; (2) redefine the AHRQ acquisition method to PDFs / manual capture and rewrite §6.3 acceptance; (3) resolve the MedlinePlus-consumer-drug-info vs ASHP-exclusion contradiction in §6.4; (4) fix the byte-identical-output vs. embedded-timestamps contradiction; (5) add a `language` field to the schema.

---

## 1. Completeness & clarity

Things that are ambiguous, underspecified, or internally inconsistent, roughly in priority order.

### 1.1 Internal inconsistency — "byte-identical output" vs. embedded timestamps
- DoD (line 236) requires re-runs to produce **byte-identical output**. But every `KnowledgeBlock` embeds `ingested_at`, `citation.retrieved_at` (line 56, 60), and the manifest embeds a run timestamp (line 71). Those change every run, so output can never be byte-identical unless timestamps are frozen from state or excluded from the guarantee.
- **Decision needed:** define determinism as "identical modulo `ingested_at`/`retrieved_at`/run-timestamp," and either (a) persist `ingested_at`/`retrieved_at` in the state file and only bump them when `content_hash` changes, or (b) exclude them from the comparison. Option (a) is better — it makes `retrieved_at` meaningful (it reflects the last *change*, not the last run).

### 1.2 §6.4 medication seed — name→SPL is one-to-many, selection policy undefined
- "top ~100 common medications by RxCUI/name" resolves to **many** SPLs per drug (brand + generic + every manufacturer/repackager). `/spls?drug_name=ibuprofen` returns dozens. The acceptance "each seeded drug resolves to ≥1 SPL" is trivially met but says nothing about *which* label to keep.
- **Decision needed:** selection policy — prefer RxCUI over free-text name; pick one representative SPL per RxCUI (e.g., latest `spl_version`, `name_type=g` generic, or a specific `marketing_category`), or emit blocks for all and dedupe by section `content_hash`. Also: where does the "top 100" list come from? (RxNorm current-prescribable set? A hand-curated YAML? Name it.)

### 1.3 §6.2 sub-section structure is assumed, may not exist
- §6.2 wants blocks for `Overview/Summary`, `Symptoms`, `When to see a doctor`, `Causes/Risk factors`, `Prevention`. Verified: the MedlinePlus bulk XML health-topic record exposes `<full-summary>` (a single HTML narrative), `<mesh-heading>`, `<group>`, `<related-topic>`, and `<site>` links — it is **not** reliably divided into those named clinical sub-sections. Those subsections mostly live on the *linked* `<site>` resources (external orgs), which is a different (and messier, non-PD) ingestion problem.
- **Decision needed:** either (a) accept one `Overview` block per topic from `<full-summary>` for v1 and drop the promise of isolated "When to see a doctor" blocks, or (b) add heading-based splitting of the summary HTML on a best-effort basis and accept that many topics won't yield the full set. The acceptance criterion "'When to see a doctor'-type guidance isolated as its own block where the source provides it" already hedges with "where the source provides it" — make explicit that this will frequently be absent.

### 1.4 No `language` field in the schema
- MedlinePlus (`healthTopicsSpanish`, `<other-language>`), CDC/HHS (`language.isoCode`), and Ready.gov (Spanish pages) all carry Spanish content. The schema has no `language` field, so Spanish content is either silently dropped or silently mixed with English.
- **Decision needed:** add `"language": "en"` to the schema (default `en`) and decide whether v1 ingests Spanish at all. Recommend adding the field now (cheap, and Mayo is multi-language too) and defaulting seeds to English.

### 1.5 `summary` generation is undefined
- Line 68 says `summary` is "simple extraction/truncation of the section's lead — not an LLM call." But the algorithm is unspecified (first sentence? first N chars with sentence-boundary trim? strip leading boilerplate first?). Truncating dense SPL or checklist text at 300 chars mid-item produces poor gists.
- **Decision needed:** specify the rule (e.g., "first complete sentence(s) up to 300 chars from the section body after boilerplate strip; for list-only sections, use the section title + item count"). Keep it deterministic as stated.

### 1.6 `id` slug stability & collisions
- `id` = `source:topic:section-slug`. Section titles vary across runs/sources and slugs can collide (two sections slugging to the same value) or drift (a retitled section changes the id, orphaning the old block and breaking incremental state).
- **Decision needed:** define the slug function (lowercasing, ASCII-fold, hyphenate, max length) and a collision suffix policy (`-2`), and key incremental state on `(source, source_id, section_index)` rather than on the human-readable slug so a retitle doesn't create a phantom new block.

### 1.7 Minor / clarity
- **`license` enum vs. §6 usage:** §6.1 says `ready_gov_reprint` / `us_gov`; §6 elsewhere uses `us_gov` for AHRQ/CDC. The enum (line 58) covers these — fine — but the CDC content is now HHS-served; keep `source: "cdc"` as the logical publisher and `license: "us_gov"`. Document that mapping.
- **`codes` for SPL version:** DailyMed idempotency needs `spl_version` (integer). There's nowhere clean to store it. Add `source_version` to the schema (or `provenance.source_version`) so change-detection isn't inferring from `source_last_updated` alone.
- **Manifest schema** (line 71) should be pinned down (explicit JSON shape) since `validate`/`stats` depend on it.
- **`retrieved_at` vs `ingested_at`** are redundant as written (both "now"); see 1.1 for how to make them distinct and useful.

---

## 2. Technical feasibility — per-source verification (live, 2026-07-05)

### 2.1 MedlinePlus — ✅ live, ⚠️ two corrections
Docs: `https://medlineplus.gov/about/developers/webservices/`, `https://medlineplus.gov/xml.html`, `https://medlineplus.gov/xmldescription.html`, terms `https://medlineplus.gov/about/using/usingcontent/`.

- **Bulk health-topic XML — live.** Published daily (Tue–Sat). **Filenames are date-stamped** — e.g. `https://medlineplus.gov/xml/mplus_topics_2026-07-04.xml` (~29 MB) and a compressed `.zip` (~4.6 MB). There is **no static "latest" URL** → the adapter must resolve the current date file or scrape the index on `xml.html`. **Do not hardcode a URL.**
- **Record structure to map from:** root `<health-topic>` with attrs `title`, `url`, `id`, `language`, `date-created`, `meta-desc`; children `<full-summary>` (HTML body), `<also-called>`, `<mesh-heading><descriptor>` (→ `codes` / keywords), `<group>`, `<related-topic>`, `<see-reference>`, `<primary-institute>`, `<site>` (linked external resources). Map: `title`→title, `<full-summary>`→body_markdown, `date-created`/file date→`source_last_updated`, MeSH→keywords.
- **Web Service — live, exactly as PRD.** `https://wsearch.nlm.nih.gov/ws/query?db=healthTopics&term=…` (XML). Also `db=healthTopicsSpanish`. **No key, no registration.** Response: `<nlmSearchResult>/<list>/<document>/<content name="…">` (title, FullSummary, mesh, snippet, groupName); paging via `retstart`/`retmax` (default 10).
- **Rate limit / caching — confirmed exactly:** 85 req/min per IP (exceed → IP suspended); cache 12–24 h. Data only changes once/day.
- **Licensing — confirmed, with a correction the PRD must absorb:** federal health-topic summaries are public domain. A.D.A.M. encyclopedia (`/ency/`) and ASHP consumer med monographs (`/druginfo/`) are copyrighted. **There is NO attribute/element in the XML or Web Service that flags A.D.A.M./ASHP content.** The PRD's plan to "skip if a section's attribution names a third-party rights holder" (§5.3) cannot rely on a feed flag. The good news: A.D.A.M./ASHP are *separate site sections*, not embedded in `<full-summary>`, so **if you only ingest the health-topic XML/Web Service you are already A.D.A.M./ASHP-free.** Exclusion = "don't ingest `/ency/` or `/druginfo/`," not "parse a flag."
  - Terms explicitly prohibit exactly one thing worth quoting in the README: *"You may not ingest and/or brand the copyrighted content found on MedlinePlus in an EHR, patient portal, or other health IT system."* → keep to the PD summaries.
  - Attribution string: *"Courtesy of MedlinePlus from the National Library of Medicine"*; no logo; no reframing under another domain. PRD's characterization is correct.
- **MedlinePlus Connect — live** (`https://connect.medlineplus.gov/service`), HL7 Infobutton, code lookup by ICD-10-CM/SNOMED/RxCUI/NDC/LOINC OIDs; XML/JSON. Useful later for `codes`-driven matching and directly relevant to the Mayo/Infobutton future.

### 2.2 Ready.gov / FEMA — ✅ live, ⚠️ slug + freshness + UA
Verified live; site is server-rendered **Drupal 11** (no JS rendering needed).

- **No API — confirmed.** HTML pages + linked PDFs.
- **Page URLs — fix two:**
  - `Build A Kit` → `https://www.ready.gov/kit` (`/build-a-kit` redirects)
  - `Make A Plan` → `https://www.ready.gov/plan`
  - **`Emergency Supply List` → no standalone page** (`/emergency-supply-list` = 404). Content is on `/kit`; the checklist is a PDF: `.../2024-05/ready_supply-kit-checklist.pdf`.
  - `Family Emergency Communication Plan` → `https://www.ready.gov/plan-form` (+ fillable card PDF `.../2025-06/family-communication-plan_fillable-card.pdf`)
  - `Evacuation` → `/evacuation`; `Financial Preparedness` → `/financial-preparedness`
  - Hazards: `/hurricanes`, `/floods`, `/wildfires`, `/power-outages`, and **`/heat` (not `/extreme-heat`, which 404s).**
- **Content structure maps well:** hazard pages use **Before / During / After** H2/H3 sections; `/kit` and `/financial-preparedness` are heavily `<li>`-based checklists. Clean fit for a checklist/step block.
- **Reprint terms — confirmed verbatim** (`https://www.ready.gov/publications`): free to download/reproduce; *"content, photos, graphics and figures will not be altered in any way"*; must not imply FEMA/US-Gov endorsement. See §4 for the alteration-vs-markdown risk.
- **⚠️ Freshness:** publications page states they are *"up to date as of Sept. 30, 2025"* and *"will not be updated on a regular basis after that date."* The PDF library is effectively **frozen** — set expectations accordingly and don't build elaborate PDF change-detection.
- **PDF hosting:** on ready.gov itself under `/sites/default/files/YYYY-MM/…` (not fema.gov). Downloadable directly.
- **robots.txt:** no restrictions on target paths (only CMS internals + `PetalBot`).
- **⚠️ Anti-bot:** ready.gov returns **403 without a browser-like User-Agent**; with a normal desktop-Chrome UA every request is 200. No Cloudflare/JS challenge. **fema.gov is behind Akamai and 403s programmatic requests even with a UA** — irrelevant since PDFs are on ready.gov, but the footer copyright page (fema.gov) can't be scraped; capture it manually if needed.

### 2.3 CDC Content Syndication — ❌ broken as written; migrated to HHS
This is the highest-risk finding. Docs: `https://tools.cdc.gov/api/docs/info.aspx` (no deprecation notice — a gap), successor `https://digitalmedia.hhs.gov` + API `https://api.digitalmedia.hhs.gov`.

- **`tools.cdc.gov/api/v2/resources/media` still returns 200** (6,579 resources, 66 pages) — so it *looks* alive — **but the catalog has drained to mostly MMWR/dashboards.** `q=fall`, `q=travel`, `q=STEADI` all return **0**; `/topics` has **no Travelers' Health and no Falls/STEADI/Older-Adult topic.** The PRD's exact plan (filter `topic=travel`, STEADI media items) **will return nothing.**
- **Successor is the HHS Digital Media platform.** CDC's own current syndication widget hard-points at `chost=digitalmedia.hhs.gov`. Same API software, same paths — repoint the base to `https://api.digitalmedia.hhs.gov/api/v2/resources/`.
- **Path/param/field corrections vs. the PRD:**
  - Paths OK: `/resources/media`, `/resources/topics`, `/resources/media/{id}/syndicate`.
  - **Search params:** valid are `q`, `topic`, `topicIds`, `mediatypes`, `sourcename`, `sourceacronym`, `sourceurl`, `audience`, `languagename`, `languageisocode`, `max`, `offset`, `pagenum`, `sort`, `order`, `fields`. `searchtext` and `topics` → **HTTP 400**.
  - **`stripScripts` is NOT stripped by default** (PRD says it is). Set `stripScripts=true` explicitly (also `stripStyles`/`stripImages`/`stripBreaks` as needed).
  - **Field names:** title = **`name`** (not `title`); abstract/body HTML = `description` (short) or `/syndicate` `content` (full); dates = `dateModified` / `dateContentUpdated` / `dateContentReviewed`; attribution/license = `attribution` + `source.{name,acronym,websiteUrl}`; also `sourceUrl`, `persistentUrl`, `tags[]`, `geoTags[]`, `language.{name,isoCode}`.
  - Formats: JSON (default), JSONP, XML (`.xml`/`?format=xml`); envelope `meta` + `results[]`.
- **STEADI & Travelers' Health:** exist on the web (`cdc.gov/steadi`, `wwwnc.cdc.gov/travel`) and are syndicated **through HHS**, but their presence in the storefront must be **verified item-by-item** — do not assume the full STEADI toolkit or all travel-prep pages are syndicated. Travel also offers an **RSS fallback** (`https://wwwnc.cdc.gov/travel/page/rss`). Keep the PRD's "confirm availability before enabling" gate — and apply it to STEADI/travel too, not just the NIA stub.
- **Auth:** read/syndicate API needs **no key** (registration only for generating embed snippets). Honor **100 calls / 60 s** per connection. HHS host **403s generic bots** → send a proper User-Agent.
- **NIA stub:** point it at the HHS platform (NIH/NIA publish through it), not a separate system.

### 2.4 DailyMed — ✅ live, ⚠️ small but important corrections
Docs: `https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm` + per-endpoint help under `/dailymed/webservices-help/v2/`.

- **v2 is current.** Base `https://dailymed.nlm.nih.gov/dailymed/services/v2/`; JSON/XML by extension; GET only.
- **`/spls` filters — param is `drug_name` (not `drugname`).** Also supports `rxcui`, `ndc`, `setid`, `application_number`, `dea_schedule_code`, `published_date` + `published_date_comparison` (lt/lte/gt/gte/eq), `name_type`, etc. **Pagination: `pagesize` default/max 100, `page` from 1.** ✅
- **⚠️ `/spls` returns metadata only** (`setid`, `spl_version`, `title`, `published_date`) — **not** label content.
- **Full label = `/spls/{SETID}.xml`.** The help page documents **only XML** for the full section-structured HL7 document. **Ingest the XML** for LOINC-coded section extraction; do **not** assume `/spls/{SETID}.json` returns full section bodies.
- **SPL sections are LOINC-coded** (map these into blocks):

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

  Handle **both** Warnings codes (a label uses one or the other). Treat PPI (42230-3) and Information for Patients (34076-0) as **distinct** patient-facing sections. Authoritative list: `https://www.fda.gov/industry/structured-product-labeling-resources/section-headings-loinc`.
- **Versioning / change detection:** SETID (UUID) stable across versions; `spl_version` increments. Use **`/spls/{SETID}/history`** to detect new versions. Store `spl_version` (see §1.7).
- **Auth/limits:** no key; **no documented rate limit** — build polite throttling/backoff anyway.

### 2.5 AHRQ — ❌ acquisition method broken; content model mismatch
- **QuestionBuilder has no API — confirmed** (iOS/Android app + "Question Builder Online"). Content must come from published pages/PDFs.
- **❌ The four "encounter-type question paths" are app features, not published HTML pages.** The FAQ states the app *"suggests questions to help users talk about a health problem, get or change a medication, get medical tests, and talk about surgery"* — but those per-category question sets are **generated inside the interactive app**, not published as four scrapable documents. The only cleanly ingestible static AHRQ content is **"The 10 Questions You Should Know"** (`https://www.ahrq.gov/questions/10questions.html`, verbatim 10-item list) plus **downloadable PDFs** (poster/brochure). → **§6.3's acceptance ("one block per encounter type") is not achievable from published AHRQ web content.**
- **❌ ahrq.gov actively blocks automated access:** behind CloudFront returning **403 to datacenter/bot requests regardless of User-Agent**, and robots.txt **explicitly Disallows `anthropic-ai`, `Claude-Web`, `GPTBot`, `ChatGPT-User`, `CCBot`.** An automated HTML crawler against ahrq.gov both fails technically and violates robots. **Acquisition must be manual/one-time PDF capture** (downloaded via an allowed path) or an arranged AHRQ feed — not a crawler.
- **License:** AHRQ content is US-Gov public domain; *"may be used and reproduced without special permission within the United States, but citation as to source is requested"* (`/policy/electronic/about/policyix.html`). Some pages embed marked third-party material → per-item check. Use the AHRQ "Internet Citation" block as `attribution_text`.
- **Caregiver framing — confirmed** ("patients and caregivers" / "patients and families") → supports `audience: caregiver`.
- **MedlinePlus pairing — confirmed:** "Talking With Your Doctor" at `https://medlineplus.gov/talkingwithyourdoctor.html` (no bot blocking; PD; includes caregiver framing). This is the reliable, ingestible half of visit_prep.

---

## 3. Architecture assessment

### 3.1 `KnowledgeBlock` schema — solid core, five concrete fixes
Good: source-prefixed stable ids, `codes` object, hash-based dedupe/change-detection, attribution-as-data, per-block chunking. Recommended changes:
1. **Add `language`** (§1.4).
2. **Add `source_version`** (or `provenance.source_version`) for SPL `spl_version` / other native version integers so change-detection doesn't lean solely on `source_last_updated` (§1.7).
3. **`use_case` should probably be a list, not a single enum.** One MedlinePlus topic (e.g., Falls) legitimately serves both `aging_home_safety` and could serve `condition_explainer`; a single-value enum forces duplicate blocks with different ids for the same content. Either make it `use_cases: []`, or keep single-value and accept intentional duplication (document which). For v1, single-value + duplication is acceptable but call it out.
4. **`content_hash` over `body_markdown` only** misses title/section-heading/`codes` changes. Fine for dedupe; for *change detection* consider hashing `body_markdown + section + codes` so a corrected heading or added code triggers a refresh. Minor.
5. **`license` belongs per-block, not per-adapter** — see 3.2.

### 3.2 `SourceAdapter` interface — mostly good, two mismatches
- **`license: str` as an adapter-level attribute is wrong-grained.** License varies *within* a source: Ready.gov pages (`us_gov`) vs. reprint PDFs (`ready_gov_reprint`); MedlinePlus PD summaries vs. excluded copyrighted sections. `parse()` should return license per `ParsedSection` (or the pipeline should assign it per item via a rule), not read a single string off the adapter.
- **`fetch(ref) -> RawItem` is page-oriented and fits four sources but not bulk XML.** MedlinePlus bulk is *one 29 MB file containing all topics*. Modeling that as "discover → N refs, fetch each" means `discover()` downloads and splits the bulk file, then `fetch()` is a no-op/local lookup. That's fine, but the interface should explicitly allow `discover()` to populate a local store that `fetch()` reads, and the docstring should say so. Otherwise the first implementer will try to HTTP-fetch per topic and hit the 85/min cap needlessly.
- **Pagination/cursoring** (DailyMed, HHS) lives inside `discover()` — acceptable, but document that adapters own their own paging and must respect per-host limits via the shared client.
- Otherwise the `discover/fetch/parse` split is clean and the "extractors compose adapters, adapters don't talk to consumers" rule is good.

### 3.3 Chunk-by-section — works where structure exists, weakest link is MedlinePlus & CDC
- **Strong:** SPL (LOINC-coded sections — deterministic), Ready.gov (Before/During/After H2/H3), AHRQ 10 Questions (single list).
- **Weak:** MedlinePlus `<full-summary>` is a single HTML narrative, not reliably sub-sectioned (§1.3); CDC/HHS `/syndicate` returns a variable HTML blob whose heading structure differs per item. Section-splitting here is heuristic (split on `<h2>/<h3>`), and many items will produce a single block. Set that expectation in acceptance criteria rather than promising named sections.
- Recommend a **shared `chunk.py` heading-splitter** (split on h2/h3, fall back to whole-body) plus **per-adapter section maps** (LOINC→name for SPL; heading-regex for Ready.gov phases). Keep boilerplate strip (nav, "Skip to content," related rails) in one place.

### 3.4 Caching / rate-limiting / change-detection / idempotency
- **Rate limiting:** the central polite client is the right call. Encode **per-host** limits: MedlinePlus 85/min + 12–24 h cache; HHS 100/60 s; DailyMed/Ready.gov/wwwnc.cdc — conservative default (e.g., ≤1 req/s) since undocumented. **Mandatory browser-like User-Agent** for ready.gov and the HHS host (both 403 generic agents) — but keep it honest/identifying (project + contact) per §4.3; a UA that identifies you *and* looks like a normal client. Don't spoof to evade a block that's actually a robots prohibition (that's the AHRQ case — don't crawl it at all).
- **Caching key:** URL+params is fine **except** the date-stamped MedlinePlus bulk file, whose key churns daily even when content is identical. Cache by **content hash** for bulk files, or normalize the key to drop the date. Otherwise `--refresh`-less re-runs on a new day re-download 29 MB unnecessarily.
- **Change detection:** `content_hash` + `source_last_updated` is good; wire per-source signals — DailyMed `/spls/{SETID}/history` `spl_version`; HHS `dateContentUpdated`; MedlinePlus file date / `date-created`; Ready.gov (frozen — hash is enough); AHRQ (static — hash is enough).
- **Idempotency:** see the timestamp contradiction (§1.1) — this is the one real correctness bug in the DoD.

### 3.5 The "drop in a Mayo adapter behind the same interface" claim — harder than stated
The PRD repeatedly asserts Mayo drops in with "no consumer changes." Three things make that harder than the schema alone implies:
1. **Storage/redistribution rights differ fundamentally.** Every design choice here assumes *freely storable, plaintext-persistable* content: cache raw responses to disk, write `body_markdown` to `.jsonl` **and** human-readable `.md` files, keep them in the repo/`out/`. Licensed Mayo content typically **prohibits local persistence/redistribution** and may require per-render entitlement/attribution and usage reporting. A Mayo adapter behind this interface would violate its license the moment `pipeline.py` writes `out/markdown/…`. The interface is fine; the **pipeline's persistence model is the incompatibility.** Plan for a "no-persist / entitlement" mode before claiming drop-in parity.
2. **Delivery cadence differs.** Mayo is described as Bulk/**Realtime API + HL7 Infobutton** (code-triggered, real-time lookup by ICD-10/RxCUI). That's a query-time retrieval pattern, not a crawl-and-batch-normalize pattern. "Re-run the same extractors" doesn't map cleanly to an Infobutton lookup service. The `codes` field is the right hook; the batch pipeline is not.
3. **`use_case`/section mapping is bespoke per source.** The extractors encode source-specific section maps (LOINC, Ready.gov phases). Mayo's structure will need its own map. The *schema* is stable; the *extractor logic* is not "drop-in." That's fine — just don't oversell it.

**Recommendation:** keep the `KnowledgeBlock` schema and `codes` design (genuinely good and Mayo-ready), but add a note that the **persistence + delivery model** will need a variant for licensed sources, and make `pipeline.py` persistence pluggable (a "sink" abstraction) now so it's not a rewrite later.

---

## 4. Legal & compliance (Section 5 sanity check)

Overall §5 is thoughtful and mostly right. Gaps/risks:

1. **MedlinePlus A.D.A.M./ASHP exclusion — mechanism corrected (not weakened).** There is no feed flag; exclusion works because you ingest only the health-topic XML/Web Service, which is federal PD and contains no A.D.A.M./ASHP. ✅ *provided* the adapter never follows `<site>`/`/ency/`/`/druginfo/` links into copyrighted content. Add an explicit guard: never ingest URLs under `/ency/` or `/druginfo/`, and don't ingest the linked `<site>` external resources.
2. **⚠️ Hidden contradiction in §6.4:** the PRD suggests "optional `medlineplus` (consumer-friendly framing)" for medications. **MedlinePlus's consumer drug information IS the ASHP monographs** (`/druginfo/`) — the exact content §5.3 forbids. So the consumer-friendly MedlinePlus drug framing is **not available**. Remove that option from §6.4 or replace it with MedlinePlus *health-topic* context (a related condition topic), not drug monographs. **This needs an explicit decision.**
3. **⚠️ FEMA reprint "no alteration" vs. HTML/PDF→markdown.** Reprint terms say content "will not be altered in any way." Converting a PDF to stripped markdown, dropping images, and re-chunking is arguably alteration of the *publication*. The defensible position: you are extracting text for internal evidence-grounding/citation, not "reproducing the publication" for redistribution, and you always link back to the unaltered source. But get this **blessed by legal**, store the `source_url` to the original PDF on every block, never present extracted text as "the FEMA publication," and never imply FEMA endorsement of SAM. Also record the **2025-09-30 freeze date** in citations.
4. **❌ AHRQ robots.txt bans Claude/Anthropic crawlers.** Even though AHRQ content is PD, robots.txt explicitly disallows `anthropic-ai`/`Claude-Web` and CloudFront 403s bots. §5.1 ("do not scrape around robots restrictions") **prohibits crawling ahrq.gov.** Acquisition must be manual PDF download / one-time capture through an allowed path. This is both a legal (§5.1) and technical blocker — resolve the acquisition method before building §6.3.
5. **CDC/HHS attribution.** HHS-syndicated content is US-Gov PD; carry `attribution` + `source` from the API into `attribution_text`; `license: us_gov`. Registration is only for embed-snippet generation, not the read API — fine. Note some CDC items embed third-party material; honor the API's `attribution` if it names a rights holder.
6. **License enum** has no value for "HHS-syndicated"; use `us_gov` and document. No change needed to the enum.
7. **General:** the "carry attribution as data so the UI renders it" principle is exactly right and should be enforced by the schema-validation fail-closed rule (§4.6). Good.

---

## 5. Risks & edge cases

- **CDC migration (highest):** entire CDC design must repoint to HHS; even then STEADI/travel item availability is unconfirmed. Mitigation: a discovery spike against the HHS storefront *before* committing §6.5/§6.6; RSS fallback for travel; keep the "confirm before enabling" gate.
- **AHRQ blocked (high):** no programmatic access + content-model mismatch (§2.5). Mitigation: manual PDF capture + rely on MedlinePlus "Talking With Your Doctor" for the ingestible half; rewrite §6.3 acceptance.
- **MedlinePlus consumer drug info = ASHP (high, legal):** §6.4 optional path is unusable (§4.2).
- **Dense SPL text (medium):** many SPLs have **no** PPI/Information-for-Patients section → medication blocks will often be clinical-only and low-readability. Acceptance already hedges ("where available"); set expectation that a large fraction of drugs yield only clinical sections. This is precisely the gap Mayo consumer drug content later fills.
- **PDF extraction (medium):** Ready.gov PDFs have multi-column layouts, tables, fillable form fields (the communication-plan card), and images. Text extraction will be messy for the fillable card (it's a form, not prose). Use `pdfplumber`/`pypdf`, expect to hand-curate a few, and skip pure-graphic PDFs. Content is frozen (2025-09-30) so this is a one-time cost.
- **MedlinePlus sub-sectioning (medium):** named clinical sections often absent (§1.3).
- **Bot blocks / 403s (medium):** ready.gov + HHS require a browser-like UA; AHRQ must not be crawled at all. Bake UA handling and a per-host "crawl allowed?" gate into the client.
- **Pagination/partial fetch (low–medium):** DailyMed 100/page and HHS `offset`/`pagenum` need robust loop + resume; a failed page mid-run should not corrupt state — persist progress and make `--refresh` re-fetch only failed refs. Log partial failures explicitly (§4.8).
- **Dynamic content (low):** CDC travel *notices* correctly excluded by default; keep the flag off.
- **Determinism (low, but it's a stated DoD):** timestamp issue (§1.1).
- **Dedupe collisions (low):** identical short bodies across sources could hash-collide and drop a legitimate block; dedupe within `(source, source_id)` scope, not globally, or include `source` in the hash scope.

---

## 6. Proposed implementation plan (phased)

**Phase 0 — Core (build once, no source risk).**
`schema.py` (add `language`, `source_version`; pydantic v2), `http.py` (per-host rate limits, backoff/tenacity, disk cache keyed by URL+params *and* content-hash for bulk files, mandatory identifying browser-like UA, per-host "allowed?" gate), `cache.py`, `chunk.py` (h2/h3 splitter + boilerplate strip + html→markdown via markdownify), `cite.py`, `pipeline.py` (section→block, hashing, dedupe, **pluggable sink** so licensed sources can later run no-persist), `cli.py`. Ship with fixtures + schema tests. Resolve §1.1 determinism here.

**Phase 1 — Ready.gov / `household_readiness` (build first).**
Rationale: the PRD's own flagship flow, smallest fixed set, clean server-rendered HTML, public domain, no API risk. Delivers an end-to-end vertical slice that proves the whole pipeline. Fix slugs (`/kit`, `/plan`, `/heat`), handle the supply-list-as-PDF case, browser UA, record freeze date. Ship the PDF extractor here.

**Phase 2 — MedlinePlus / `condition_explainer`.**
Highest-value health content, clean bulk XML, no key. Implement dynamic date-file resolution, `mode: bulk|list`, `<full-summary>`→body, MeSH→keywords, `/ency` + `/druginfo` hard-exclusion guard. Set realistic sub-section expectations (§1.3). Reuse the same MedlinePlus adapter for the visit_prep and aging topics later.

**Phase 3 — DailyMed / `medication`.**
Structured LOINC sections; well-documented API. Implement `drug_name`/`rxcui` discovery, one-SPL-per-RxCUI selection policy (§1.2), XML section extraction with the LOINC map, `/spls/{SETID}/history` versioning, flag patient-facing sections. Drop the MedlinePlus-consumer-drug option (ASHP, §4.2).

**Phase 4 — `visit_prep` (AHRQ + MedlinePlus), scoped down.**
Build the reliable MedlinePlus "Talking With Your Doctor" half automatically. For AHRQ: **manual one-time capture** of the "10 Questions" page + brochure/poster PDFs into `config/` fixtures (no live crawl). Rewrite §6.3 acceptance to "1 general '10 questions' block + MedlinePlus visit-prep block(s); encounter-type paths deferred (app-only)."

**Phase 5 — CDC via HHS / `aging_home_safety` + `travel_health` (highest risk, last).**
Precede with a **discovery spike**: enumerate the HHS storefront for STEADI + evergreen travel items and confirm they exist and are syndicated. Then build the `cdc_syndication` adapter against `api.digitalmedia.hhs.gov` with corrected params/fields, `stripScripts=true`, RSS fallback for travel, notices excluded. Leave the NIA stub pointing at HHS. Pair aging with the MedlinePlus topics from Phase 2.

**Stack adjustments to the PRD's default (Python 3.11+):**
- Keep `httpx`, `lxml`/`bs4`, `markdownify`, `pydantic` v2, `tenacity`, `pyyaml`, `orjson`.
- **Add** `pdfplumber` (and/or `pypdf`) for Ready.gov PDFs.
- Consider `platformdirs` for cache location; a tiny SQLite or JSON state file for incremental state.
- Rename adapter file `cdc_syndication.py` conceptually to an **HHS-backed** syndication client (keep `source: "cdc"` as the logical publisher; base URL = HHS).

---

## 7. Open questions — please confirm before I start coding

1. **CDC/HHS scope:** OK to repoint the CDC adapter to `api.digitalmedia.hhs.gov` and gate §6.5/§6.6 behind a discovery spike that confirms STEADI + evergreen travel items actually exist there? If they largely don't, do we drop/defer those two use cases for v1?
2. **AHRQ acquisition:** given ahrq.gov blocks Anthropic/bot crawlers (robots + CloudFront 403), is **manual one-time capture** of the "10 Questions" page + PDFs into config acceptable? And do you accept rewriting §6.3 to drop the four per-encounter-type blocks (they're app-only, not published)?
3. **MedlinePlus drug info:** confirm we **remove** the "MedlinePlus consumer-friendly drug framing" option from §6.4 (it's ASHP monographs, which §5.3 forbids). Medication readability stays clinical until Mayo lands — acceptable for v1?
4. **Medication selection policy:** which "top ~100" list is authoritative, and how do we pick one SPL per drug (latest version? generic? all + dedupe)?
5. **MedlinePlus sub-sections:** accept that `condition_explainer` will usually yield a single `Overview` block per topic (named sub-sections often absent), rather than the full Symptoms/When-to-see-a-doctor/etc. set?
6. **Determinism definition:** OK to define "byte-identical re-runs" as *identical modulo `ingested_at`/`retrieved_at`/run-timestamp*, and only bump those when `content_hash` changes?
7. **Language:** English-only for v1 (add the `language` field now, default `en`), or ingest Spanish too?
8. **FEMA reprint / legal sign-off:** who signs off that PDF→markdown text extraction for internal grounding is within FEMA's "no alteration" reprint terms?
9. **Persistence model for the future Mayo drop-in:** do you want `pipeline.py` built with a pluggable sink now (so a licensed no-persist source can reuse it), or is that explicitly deferred?
10. **Language/stack:** stay on Python 3.11+ per the PRD default (my recommendation), or is TS/Node preferred?

---

*Sources verified live 2026-07-05: MedlinePlus (`medlineplus.gov/about/developers/webservices/`, `/xml.html`, `/about/using/usingcontent/`, `connect.medlineplus.gov`), Ready.gov (`ready.gov` pages + `/publications`, `robots.txt`), CDC/HHS (`tools.cdc.gov/api/docs/info.aspx`, `digitalmedia.hhs.gov`, `api.digitalmedia.hhs.gov`, `wwwnc.cdc.gov/travel`), DailyMed (`dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm`, FDA LOINC section-headings), AHRQ (`ahrq.gov/questions/*`, `/policy/electronic/about/policyix.html`, via Wayback due to bot block).*
