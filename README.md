# 🤖 LLMDataExtraction — Domain Data Extractor

An automated pipeline that finds **ecommerce-ready B2C/B2B leads**: it reads a list of company domains from Google Sheets, **filters them by web-traffic quality first**, scrapes and LLM-analyzes the qualifying websites (products, shipping, B2C/B2B signals), enriches with Apollo / Seamless / Ahrefs, and stores the results back to Google Sheets and HubSpot.

This README documents **every important design decision**, why it was made, and where it lives in the code.

---

## Table of Contents

- [1. High-Level Flow](#1-high-level-flow)
- [2. Architecture: Step Functions Pipeline](#2-architecture-step-functions-pipeline)
- [3. Key Design Decisions](#3-key-design-decisions)
  - [A. Traffic gating — we only scrape qualifying domains](#a-traffic-gating--we-only-scrape-qualifying-domains)
  - [B. Lead status calculation](#b-lead-status-calculation)
  - [C. How results are stored](#c-how-results-are-stored)
  - [D. Scraping strategy](#d-scraping-strategy)
  - [E. LLM extraction](#e-llm-extraction)
  - [F. Enrichment providers (Apollo / Seamless / Ahrefs)](#f-enrichment-providers-apollo--seamless--ahrefs)
  - [G. Deployment & infrastructure](#g-deployment--infrastructure)
  - [H. Field mapping — how HubSpot / sheet keys are set](#h-field-mapping--how-hubspot--sheet-keys-are-set)
  - [I. Known gaps between intended and current behavior](#i-known-gaps-between-intended-and-current-behavior)
- [4. Environment Variables](#4-environment-variables)
- [5. Local Execution & Testing](#5-local-execution--testing)
- [6. Utility: Ahrefs Backfill Script](#6-utility-ahrefs-backfill-script)
- [7. Sheet Formats](#7-sheet-formats)
- [8. n8n Pipelines (Downstream Automation)](#8-n8n-pipelines-downstream-automation)
- [9. Operational Notes](#9-operational-notes)
- [10. TODO](#10-todo)

---

## 1. High-Level Flow

### 1.1 Complete end-to-end flow

When a domain is added to the Google Sheet, the scraper pipeline takes over:

1. **Pick up the domain** — the splitter reads rows from the sheet and schedules chunks of work.
2. **Get traffic data** — SimilarWeb (via RapidAPI) returns total monthly visits, US-share, and engagement metrics for the domain (`rapidapi/similarwebapi_search.py`).
3. **Validate against the traffic criteria** (`executor.domain_has_valid_traffic`, `executor.py:334`).
   - **Qualified** → scrape the website and analyze it with the LLM (see Section D/E), enrich (Apollo/Seamless/Ahrefs if enabled), and **store the result in HubSpot and Google Sheets**.
   - **Not qualified** → **do not scrape**. Record the outcome with `scrape_results = NO_SCRAPE`, and `hs_lead_status` is computed inside that function (the traffic-band reason). This non-lead is still persisted to HubSpot and Google Sheets so it can be handled downstream.
4. Results are handed to the **downstream n8n pipelines** (Lead Assignment to Owners, Lead Scoring — see [Section 8](#8-n8n-pipelines-downstream-automation)).

```
Google Sheet (domains)
   │
   ▼
SplitDomains ──► generates job_id, chunks domains, detects already-known leads
   │
   ▼
Map (max 4 concurrent Workers)
   │
   ├─ Traffic gate (SimilarWeb) ──► no  ──► record scrape_results=NO_SCRAPE +
   │                                    hs_lead_status  (save to HubSpot/Sheets, no scrape)
   │
   ├─ Scrape website (HTTP scraper → Playwright fallback) + block detection
   ├─ LLM analysis (Gemini) — business profile, B2C/B2B, products, shipping
   ├─ (optional) Enrich: Apollo / Seamless / Ahrefs
   │
   ▼
Stage per-chunk results to S3 (staging/{workflow}/{job_id}/{chunk_id}.json)
   │
   ▼
Cleanup ──► save to HubSpot + Good/Error sheets (or in-place single sheet)
   │         delete processed rows from production input sheet
   │
   ▼
n8n ──► Lead Assignment to Owners (Clear Owner for Unqualified first) + Lead Scoring
```

`lambdas/splitter.py` → `lambdas/worker.py` → `lambdas/cleanup.py`, orchestrated by two Step Functions state machines defined in `serverless.yml`.

---

## 2. Architecture: Step Functions Pipeline

**Decision: two state machines sharing one codebase, selected by `workflow_mode`.**

- `workflow_mode` is **passed as execution input**, never read from an environment variable at runtime. It is **hardcoded inside each state machine** via the `Parameters` field on the `SplitDomains` task (`serverless.yml`):
  - `DomainDataExtractorStateMachine` → `workflow_mode: "production"`
  - `DomainDataExtractorStateMachine_SingleSheet` → `workflow_mode: "regular"`
- Each state machine runs on its **own EventBridge scheduler** (`rate(15 minutes)`), both sharing one scheduler IAM role scoped to the two state-machine ARNs.
- Why: a single deploy manages both workflows, but each is independently schedulable, and a bug in one cannot silently change the other’s mode.

**Decision: Split → Map (Workers) → Cleanup.**

- **Splitter** (`lambdas/splitter.py`): reads the sheet, dedupes rows, filters already-seen domains, splits the remaining list into chunks of `CHUNK_SIZE` (default **5**), and generates a `job_id`.
- **Worker** (`lambdas/worker.py`): processes one chunk (max **4 concurrent**), stages `{processed, failed}` to S3.
- **Cleanup** (`lambdas/cleanup.py`): reads all staged objects under `staging/{workflow_mode}/{job_id}/`, merges them, saves results, and cleans up the input sheet.
- Why split/stage/save this way: the 15-minute Lambda timeout and parallelism constraints make chunking necessary; staging to S3 decouples the compute-heavy workers from the write step so a single cleanup pass can write everything in batch.

**Decision: job IDs are UUIDs, verified collision-free against S3.**

- `_generate_job_id` (`lambdas/splitter.py`) lists S3 under `staging/{workflow_mode}/{job_id}/` and regenerates until unique (max 10 attempts). Staging is namespaced per workflow so job IDs never collide across the two pipelines.

**Decision: staging files are not deleted yet.**

- `_cleanup_staging` exists in `lambdas/cleanup.py` but is **commented out** during the testing phase so failed jobs can be re-read and re-saved from the same S3 objects.

---

## 3. Key Design Decisions

### A. Traffic gating — we only scrape qualifying domains

**Decision: check SimilarWeb traffic BEFORE scraping; skip (but still record) domains that don't qualify.**

`executor.domain_has_valid_traffic()` (`executor.py:334`) applies, in order:

| Condition | Result |
|---|---|
| `total_monthly_visits < 100` | Unqualified — `lifecycle_stage = "1410598780"`, status `"Unqualified Revenue Less 1 mil"` |
| `total_monthly_visits > 500000` | Unqualified — `lifecycle_stage = "1410598780"`, status `"Unqualified Revenue Plus 100 mil"` |
| `us_traffic < 0.5` (US share < 50%) | Unqualified — `lifecycle_stage = "1410598780"`, status `"Unqualified Revenue Less 1 mil"` |
| otherwise | **Qualified** — `lifecycle_stage = "lead"`, proceed to scrape |

- This runs at the top of `process_domain()` (`executor.py:356`), **before** any scraping or LLM work, and **before** enrichment.
- Traffic source: **SimilarWeb via RapidAPI** (`rapidapi/similarwebapi_search.py`) — total monthly visits (latest month), bounce rate, pages/visit, time-on-site, traffic-source breakdown, and US traffic share.
- Why: the customer only wants leads in a specific revenue band. Traffic is the cheapest proxy for company size, so it filters the expensive work (scraping + LLM) to a fraction of the input.
- **Important nuance:** an unqualified domain is still returned with `mode == "success"` (see `executor.py:364-369`) so it is written to the sheet/HubSpot with its unqualified status and the traffic metrics attached — it is a *recorded* non-lead, not an error. The scrape outcome is recorded as `scrape_results = NO_SCRAPE` (intended), and `hs_lead_status` (the traffic-band reason) is computed by `domain_has_valid_traffic`. `hs_lead_status` and `lifecycle_stage` are persisted to HubSpot.
- The traffic result is attached to the record (`traffic_result`) and persisted to HubSpot (`total_monthly_visits`, `usa_traffic`, engagement and source fields) — see the code-gap note in [Section 3.I](#i-known-gaps-between-intended-and-current-behavior) for the current limitation on qualified leads.
- Note: `SIMILARWEBAPI_ENABLED` exists in `config.py` but is **not yet wired into `process_domain`** — the traffic gate always runs today.

**`lifecyclestage` values produced by this gate** (`executor.py:334-354`):

| Outcome | `lifecyclestage` | HubSpot label |
|---|---|---|
| Any traffic-reject condition (visits < 100, visits > 500k, US share < 50%) | `1410598780` | `Unqualified - Research Bad Fit` |
| Qualified | `lead` | `Lead Lift Commerce` |

(Source: HubSpot `lifecyclestage` options in `domain_properties.json`.)

### B. Lead status calculation

**Decision: status is a decision tree, computed in `compute_lead_status` (`executor.py:391`), with traffic-based statuses applied earlier.**

1. Website not live (`website_availability` in `["No", "N/A"]`) → `Unqualified - Website Down`
2. Not US-based (`is_us_based != "Yes"`) → `Unqualified - Non-US Based`
3. Bad product type (`bad_product_type == "Yes"`) → `Unqualified - Bad Product Type`
4. Neither B2C nor B2B → `Unqualified – Junk Lead / No Shipping`
5. Otherwise → `Lift Prime` (the target lead)

Statuses set elsewhere:

| Path | Status |
|---|---|
| Traffic gate fails (A) | `Unqualified Revenue Less 1 mil` / `Unqualified Revenue Plus 100 mil` |
| Non-US TLD (`.com/.us/.net` only) | `Unqualified - Non-US Based` |
| Scraper blocked (Cloudflare/403/429…) | `Unqualified - Website Blocked` |
| No body scraped | `Unqualified - Website Down` (default) |
| Gemini returns nothing | `Error: LLM Failed` |
| Default/fallback summary | `Unqualified - Website Down` |

**Reference — `lifecyclestage` value per scenario** (labels from `domain_properties.json`):

| Scenario | `lifecyclestage` (HubSpot) | HubSpot label | Where |
|---|---|---|---|
| Traffic gate rejects (any reason) | `1410598780` | `Unqualified - Research Bad Fit` | `executor.py:341/344/347` |
| Traffic gate passes (qualified) | `lead` (computed, **not written** — see gap #3) | `Lead Lift Commerce` | `executor.py:350` |
| Website down / no body / blocked / non-US / LLM-failed | `""` (not set) | — | — |
| n8n "Clear Owner for Unqualified" | `136007941` | `Unqualified- Cold Calling Effort Completed` | clear_owner_unqualified_companies.json:112 |
| n8n "Lead Assignment" eligibility filter | must equal `lead` | `Lead Lift Commerce` | lead_assignment_to_owners.json:171 |

> The scraper's `1410598780` ("Research Bad Fit") and n8n's `136007941` ("Cold Calling Effort Completed") are **different** lifecycle stages in the portal — not two spellings of the same one. Whether that's intentional is an open question (see [Section 3.I](#i-known-gaps-between-intended-and-current-behavior) #4).

- B2C/B2B flags come from the **LLM** (visible HTML signals only — see Section E), so the final status depends on the prompt’s accuracy.
- The merged status is recomputed from **both** homepage and about/contact summaries (`merge_summary_outputs`), with the homepage winning ties.

**Decision: revenue is only fetched for qualified leads.**

- Only when `lead_status == "Lift Prime"` does the pipeline run a Google search (`ZoomInfo annual revenue of {domain}`, via Zyte browser mode) and extract a numeric revenue with the `revenue_prompt` (`executor.py:180`, `fetch_revenue`).

### C. How results are stored

**Decision: two storage models behind one strategy interface (`BaseSheetStrategy`).**

The strategy is chosen from `workflow_mode` in `executor.py:46`. `MainExecutor` and the Lambdas only depend on the interface — success/error/skip behavior is fully polymorphic.

#### Production workflow (`ProductionSheetStrategy`) — multi-sheet + HubSpot

- Input sheet: only **column A (URL)** is required.
- **Dedupe against history:** every input domain is compared against domains already present in the good/skip/error sheets **and** against HubSpot (`get_existing_domains`). Known domains are **not re-scraped**; they are appended to the *skip* sheet (`save_skipped_results`).
- Only unseen domains are chunked and processed.
- On success: written to the **good sheet** and created in **HubSpot**.
- On error: domain + status written to the **error sheet**.
- After the job: `on_complete` **deletes processed (and skipped) rows from the input sheet** — the input sheet acts as a queue (`google_sheets.py:445`).

#### Regular (single-sheet) workflow (`RegularSheetStrategy`) — in-place updates

- Requires **both Company Name (col A) and URL (col B)**; rows missing either are skipped.
- Rows are (re)processed only when the B2C/B2B column is empty and the last status isn’t `LLM Failed` (`get_records`, `google_sheets.py:147`).
- Results are written **back into the same row**, starting at column C — row order must not change mid-run (see Operational Notes).
- Column layout after col B (matching `save_results` / `_update`):

```
C… : 23 business fields (phone, availability, address, B2C, B2B, industry,
      ecomm platform, lead status, revenue, shipping, carriers, product
      size/dimensions for smallest & largest product, redirect, old status)
      + Apollo (12 cols)
      + Seamless (11 cols)
      + Ahrefs (8 cols: 49–56)
      + Scrape date (col 57)
```

#### HubSpot

- Every success is created via **single company create** (`POST /crm/v3/objects/companies`, `hubspot/records_api.py:add_company`) rather than batch — chosen for per-company error isolation (the batch/upsert path exists but is commented out).
- The payload (`HubSpotDataMapper.map_to_hubspot_payload`, `google_sheets.py:557`) maps business fields, traffic metrics, `lifecyclestage`, `hs_lead_status`, scrape date, and (if enabled) enrichment fields.
- `get_existing_domains` uses a single search with an `IN` filter on `name` (HubSpot filter-group limit is respected; values are sent in one group, see the code comment).

#### Stats

- Every Gemini call logs prompt tokens / completion tokens / computed cost / model name to a **stats sheet** (`update_stats`), skipped only when `ENVIRONMENT=dev`.

### D. Scraping strategy

**Decision: layered fetching with a real-browser fallback.**

1. Only `.com`, `.us`, `.net` domains are processed (`settings.valid_domains`) — everything else is immediately `Unqualified - Non-US Based`.
2. Four URL variants are tried (`http/https` × `with/without www`); the **first successful response wins** (`get_first_successful_response=True`).
3. HTTP scraper (`scraper/generic_scraper.py`) with a rotating Chrome User-Agent and **5-way concurrency**; if no response, retry with headless **Playwright Chromium** (`scraper/bot_scraper.py`), depth-limited to 2.
4. **Zyte** (`ZYTE_ENABLED`) routes requests through Zyte’s proxy (JSON POST, `browserHtml` or `httpResponseBody` + redirect following). Used for the revenue search.

**Decision: explicit block detection.**

`ScrapeBlockDetector` (`scraper/block_detector.py`) flags a page as blocked from:
- HTTP status 403/429/503/521–524 (confidence 1.0)
- Cloudflare challenge markers (`cf-ray`, "checking your browser", cf-wrapper/challenge elements)
- Bot-detection / rate-limit keywords in `<title>`/`<h1>` or minimal-content pages
- Blocking page structure (recaptcha/hcaptcha, PerimeterX, DataDome) — requires score ≥ 2 to avoid false positives
- Empty/minimal responses

Blocked domains become `Unqualified - Website Blocked` and are not LLM-analyzed.

**Decision: aggressive HTML cleaning before the LLM.**

`scraper/parser.py` strips scripts, styles, SVGs, hidden elements (`display:none`, `visibility:hidden`, `aria-hidden`, `hidden`), inputs/videos/br/noscript, keeps images/headings/tables/links, then minifies (strips `\n`/`\t`). Pages under ~1000 chars of content are dropped unless flagged blocked. Why: cheaper prompts, less LLM noise, and it forces the prompt to reason from visible content only.

**Decision: raw + parsed HTML are archived to S3.**

Every fetched page is stored as `{host}/http-raw-{path}.html`, `http-parsed-{path}.html`, and `bot-*-{path}.html` for the bot scraper — for audit and debugging.

### E. LLM extraction

**Decision: Gemini, JSON-only, evidence-based extraction.**

- `LLMHelper` (`llm/llm_helpers.py`) calls Gemini `generateContent` with a system instruction: *"never infer/assume/fill in missing information; return the default negative value when evidence is absent; output raw JSON only."*
- The `main_prompt` defines strict, signal-based rules — e.g. `b2c_sales` requires a **cart signal + a priced product or product category**, `b2b_sales` requires explicit B2B keywords scanned individually, phone numbers must be ≥10 digits, etc. (see `llm/prompts/main_prompt`).
- Homepage is analyzed with `thinkingLevel=MEDIUM`; **product images** are extracted from the HTML (image extension + product-path hint, excluding logos/icons), deduped (highest resolution, collapse angle variants), downloaded (max 10, ≤5MB each, 8 workers) and sent to Gemini as inline images — used to determine product dimensions/sizes.
- Up to **2 retries** with 5–10 s jitter on API failures.
- The response is cleaned (`clean_llm_response_json_data` strips markdown fences) and parsed into a `DomainResponse`.

**Decision: a second scrape pass for missing critical data.**

If phone, address, B2C, or B2B is missing after the homepage analysis, the about/contact links (extracted by the LLM from the homepage) are scraped and analyzed, then **merged**: secondary values fill gaps (`"No"/""/"N/A"`), email-as-phone is replaced, `industry = "Other"` is upgraded, and `lead_status` is recomputed (`merge_summary_outputs`).

**Decision: per-workflow model override.**

`OVERRIDE_MODEL_NAME` only affects the **regular (single-sheet)** workflow, letting you test prompt/model changes against a cheap model (e.g. `gemini-3.1-flash-lite`) without touching production (`LLMHelper.__init__`).

### F. Enrichment providers (Apollo / Seamless / Ahrefs)

**Decision: each provider is independently toggleable, default OFF.**

- `APOLLO_ENABLED`, `SEAMLESS_ENABLED`, `AHREFS_ENABLED` (`config.py`, default `false`).
- When disabled, the worker **skips the API entirely** and returns an empty result object per domain (`executor.py:376-384`), so the sheet still has the columns (blank) and can be backfilled later (see Section 6).
- When enabled, enrichment runs **only after the traffic gate and scrape succeed**, against the resolved `website_url` (the redirected domain if the site redirected) — one domain per request at runtime (`executor.py:372-388`).
- **Current caveat:** the whole enrichment block is gated on `strategy.pre_enrich`, which is `False` on every strategy today (see [Section 3.I](#i-known-gaps-between-intended-and-current-behavior)). The flags alone do not currently enable enrichment.

**Apollo** (`apollo/companies_search.py`)
- Uses bulk enrich; matches returned orgs back to requested domains **by value** (`primary_domain`/`domain`), not position — Apollo may omit unmatched domains or reorder.
- Normalizes website (strips scheme/`www.`) and phone (strips `+1`).

**Seamless** (`seamless/companies_search.py`)
- Async submit→poll model (`companies/research` then poll), up to 10 polls / 3 s.
- **Validates returned domains match the request** (strip `www.`/slashes); mismatches are discarded.
- Guarantees **every requested domain appears** in the result dict (empty `SeamlessResult()` on failure) so callers never KeyError.

**Ahrefs** (`ahrefs/companies_search.py`)
- Batch-analysis endpoint, **100 targets/request**, **1.1 s pacing** (respecting the 60 req/min cap).
- Limited SELECT fields: `org_traffic_top_by_country`, `org_traffic`, `paid_traffic`, `org_keywords`, `paid_keywords`, `backlinks`, `refdomains`. The top country is flattened to `top_org_traffic_country_name/value`.
- Uses **indexed mapping** (not `zip`) so every requested domain is represented, defaulting to `AhrefsResult()` when the API returns nothing for it.

### G. Deployment & infrastructure

**Decision: one container image for all Lambdas; one CloudFormation stack.**

- All three functions run the same multi-stage Docker image (`Dockerfile`) with Playwright Chromium installed and `awslambdaric` as the entrypoint. Memory differs per function (worker 1024 MB, others 256 MB; worker timeout 900 s).
- `serverless.yml` provisions: 3 Lambda functions, an S3 bucket (raw/parsed HTML + staging, `DeletionPolicy: Retain`), 2 Step Functions state machines, 2 EventBridge schedulers, and the scheduler IAM role.
- Env vars are injected from `${env:VAR, default}` so nothing sensitive is stored in the repo.

**Decision: `./deploy.sh` is the single deploy entry point.**

- Loads `.${STAGE}_env` (default `.prod_env`), installs serverless plugins, builds/pushes the image to ECR tagged with the **git short SHA** (skipping the build if the tag already exists), then `serverless deploy`.
- `./deploy.sh --image-tag <sha>` reuses an existing image; `./deploy.sh --destroy` tears the stack down.

---

### H. Field mapping — how HubSpot / sheet keys are set

This is how the outcome keys are meant to be recorded. Every value is written via the HubSpot payload map (`HubSpotDataMapper.map_to_hubspot_payload`, `google_sheets.py:557`); unset fields go to `""`.

#### Lead outcome fields (most important)

| HubSpot property | Source | How it is set / values |
|---|---|---|
| `scraper_results` | scrape outcome | `NO_SCRAPE` when the domain **fails the traffic gate** (no website is scraped). Otherwise the computed `lead_status` — `Lift Prime` for a qualified lead, or an `Unqualified …` reason. |
| `hs_lead_status` | `hs_lead_status` | Computed by `domain_has_valid_traffic` (`executor.py:334`) **only** for traffic-rejected domains. Values: `Unqualified Revenue Less 1 mil` or `Unqualified Revenue Plus 100 mil` (blank for scraped leads). |
| `lifecyclestage` | `lifecycle_stage` | Set by the traffic gate: `1410598780` (traffic-rejected) or `lead` (qualified). **Gap:** the `lead` value is computed but not written for qualified leads today (see [Section 3.I](#i-known-gaps-between-intended-and-current-behavior) #3). |

So for any lead exactly one outcome applies:

- **Qualified lead** → scraped & analyzed → `scraper_results = lead_status` (`Lift Prime` or an `Unqualified …` reason), `hs_lead_status = ""`, `lifecyclestage = lead`.
- **Traffic-rejected lead** → not scraped → `scraper_results = NO_SCRAPE`, `hs_lead_status` = the traffic-band reason, `lifecyclestage = 1410598780`.

#### Business profile fields (from LLM extraction)

| HubSpot property | Source (`DomainResponse`) | Values / notes |
|---|---|---|
| `name`, `website`, `domain` | `company_url` | the input domain |
| `confirmed_website___headquarters_phone__` | `hq_phone_no` | LLM-extracted HQ phone (≥10 digits) or `""` |
| `is_website_live_` | `website_availability` | `Yes` / `No` (LLM) |
| `address_listed_on_website_` | `hq_address_listed` | `Yes` / `No` (LLM) |
| `do_the_sell_b2c` | `b2c_sales` | `yes` / `no` (lowercased LLM) |
| `do_they_sell_b2b` | `b2b_sales` | `yes` / `no` (lowercased LLM) |
| `industry_type_verified` | `industry_classification` | lowercased industry string |
| `ecommerce_platform` | `ecommerce_platform` | platform name (e.g. `Shopify`) or `unknown` |
| `annual_revenue_scraper` | `revenue` | numeric revenue (**`Lift Prime` leads only**) else `""` |
| `scraper_shipping_messages` | `shipping_messaging` | LLM |
| `scraper_shipping_methods` | `shipping_methods` | LLM |
| `scraper_carriers` | `carriers` | LLM |
| `scraper_product_size` | `product_size_weight` | LLM |
| `hs_redirect_domain` | `redirected_to` | original domain when the site redirected to another domain, else `""` |

#### Product dimension fields (LLM + product-image analysis)

| HubSpot property | Source |
|---|---|
| `scraper_smallest_product_cubic` | `smallest_product_cubic_size` |
| `scraper_product_dimensions` | `smallest_product_dim` |
| `scraper_smallest_product_name` | `smallest_product_name` |
| `scraper_largest_product_cubic` | `largest_product_cubic_size` |
| `scraper_largest_product_dimensions` | `largest_product_dim` |
| `scraper_largest_product_name` | `largest_product_name` |

#### Traffic fields (SimilarWeb via RapidAPI, from `traffic_result`)

| HubSpot property | Source (`traffic_result`) |
|---|---|
| `total_monthly_visits` | `total_monthly_visits` (latest month) |
| `usa_traffic` | `us_traffic` (% of traffic from the US) |
| `bounce_rate` | `bounce_rate` |
| `pages_per_visit` | `page_per_visit` |
| `time_on_site` | `time_on_site` |
| `traffic_source_search_organic` | `search_organic` |
| `traffic_source_search_paid` | `search_paid` |
| `traffic_source_direct` | `traffic_source_direct` |
| `traffic_source_referrals` | `traffic_source_referrals` |

#### Bookkeeping

- `traffic_enrichment_date` → today (`YYYY-MM-DD`)
- `traffic_enrichment_status_request` → `completed`
- `scrape_date` → today as epoch milliseconds

**Normalizations / fixes applied when populating these keys:**

- List-valued LLM fields (e.g. multiple shipping methods) are joined with `,` before writing (`llm/llm_helpers.py`).
- Apollo website is stripped of `http(s)://`/`www.` and phone is stripped of a leading `+1`.
- Seamless returned domains are validated against the request (strip `www.`/slashes); mismatched results are discarded.
- Ahrefs is mapped by **index**, not position, so every requested domain is represented (empty `AhrefsResult()` when the API returns nothing); the top country is flattened into name/value columns.

#### How a traffic-rejected lead is saved (per strategy)

A domain that fails `domain_has_valid_traffic` is **not scraped**. `process_domain` still returns `mode == "success"` with a **default summary** (`executor.py:364-369`): business fields empty/`No`, `lead_status = "Unqualified - Website Down"`, `hs_lead_status` = the traffic reason, `lifecycle_stage = 1410598780`, `traffic_result` populated, and empty Apollo/Seamless/Ahrefs. Because `mode` is `"success"`, it lands in the worker's `processed` set (`lambdas/worker.py:37`) and is persisted via the strategy's `save_results`.

**SingleSheet (`RegularSheetStrategy.save_results`, `google_sheets.py:182`)**

- Writes the default summary **in place** into the original row (cells starting column C), exactly like a normal result: `lead_status = "Unqualified - Website Down"`, empty business fields, `old_lead_status`, and the scrape date.
- The row layout currently written is the **23 business columns + scrape date (cols C–Z)**; the Apollo/Seamless/Ahrefs and traffic blocks are **commented out** (`google_sheets.py:186-198`).
- **Not persisted anywhere:** `hs_lead_status`, `lifecycle_stage`, traffic metrics, and the `NO_SCRAPE` marker. The only trace on the sheet is the default `lead_status`.
- No HubSpot write (single-sheet workflow is sheet-only).

**ProductionSheet (`ProductionSheetStrategy.save_results`, `google_sheets.py:364`)**

- **HubSpot:** created via `add_company` — this is the only place traffic metrics, `hs_lead_status`, `lifecycle_stage`, and `traffic_enrichment_date` are actually stored (mapper, `google_sheets.py:557`).
- **Good sheet:** appended via `_build_success_row` (`google_sheets.py:401`) — domain + the same default business fields / `lead_status`, with the enrichment and traffic blocks commented out; no `hs_lead_status` column.
- **Input sheet:** the row is deleted afterwards by cleanup (`on_complete`), since the production input sheet is a queue.

**Net effect today:** for a traffic-rejected lead the rejection context (`hs_lead_status`, traffic metrics, `NO_SCRAPE`) is only ever written to **HubSpot** (production). Neither sheet writes traffic columns, and the single sheet does not persist the rejection reason beyond the default `lead_status = "Unqualified - Website Down"`.

---

### I. Known gaps between intended and current behavior

This is a running list of places where the **documented intent above** and the **current code** differ. It exists so an agent working on this repo knows what is real vs. aspirational. Each item names the file/line where the fix would go.

1. **Enrichment is effectively dead code today.** The enrichment block in `process_domain` (`executor.py:372-388`) is gated on `if self.strategy.pre_enrich:`, but `pre_enrich` returns `False` on **both** strategies — `RegularSheetStrategy` (`google_sheets.py:140`), `ProductionSheetStrategy` (`google_sheets.py:310`), and the base class (`google_sheets.py:103`). Nothing ever sets it `True`, so `APOLLO_ENABLED` / `SEAMLESS_ENABLED` / `AHREFS_ENABLED` have **no effect** in the worker path today. Intended: set `pre_enrich = True` on `ProductionSheetStrategy` so enrichment runs after the traffic gate + scrape succeed.

2. **Qualified leads never get `traffic_result` attached — the HubSpot mapper can crash on them.** `result.traffic_result = traffic_data` is set only in the traffic-reject path (`executor.py:368`) and inside the never-run `pre_enrich` block (`executor.py:388`). For a qualified lead, `traffic_result` stays `None`, so `map_to_hubspot_payload` (`google_sheets.py:598`) does `output.traffic_result.total_monthly_visits` → `AttributeError`, which the worker catches and routes to `failed`. Same root cause as gap #1 — fixing `pre_enrich` also fixes this. Intended: attach `traffic_result` for every processed record.

3. **`lifecyclestage = "lead"` is only ever *computed*, never written for qualified leads.** `domain_has_valid_traffic` returns `"lead"` (`executor.py:350`) but that value is only applied to the default summary in the traffic-reject branch (`executor.py:366`). A qualified lead's `output.lifecycle_stage` stays `None` → HubSpot receives `""`. Intended: qualified leads get `lifecyclestage = lead`. (Consequence: the n8n **Lead Assignment** pipeline filters on `lifecyclestage = lead` and would currently match no scraper-written companies.)

4. **The scraper and n8n write **different** "unqualified" `lifecyclestage` values.** The scraper writes `1410598780` (executor.py:341/344/347) = portal stage *"Unqualified - Research Bad Fit"*. The n8n **Clear Owner for Unqualified Companies** workflow sets `136007941` = portal stage *"Unqualified- Cold Calling Effort Completed"* (clear_owner_unqualified_companies.json:112). These are two distinct lifecycle stages (confirmed in `domain_properties.json`), so a company cleared by n8n carries a different unqualified stage than one rejected by the traffic gate. Intended: decide whether both should point at the same canonical "Unqualified" stage, or whether the difference is deliberate (e.g. gate = research bad fit, n8n = cold-calling effort done).

5. **Other failure statuses have no `lifecyclestage` at all.** `_process_record` failure paths (website down, blocked → `Unqualified - Website Blocked`, non-US TLD, `Error: LLM Failed`) return the default summary without setting `lifecycle_stage`, so HubSpot gets `""`. Intended (if required): set an unqualified lifecycle stage for every non-`Lift Prime` outcome — to be confirmed with the team.

6. **`scraper_results = NO_SCRAPE` is the intended value but is not literally written today.** The mapper writes `scraper_results = output.lead_status` (`google_sheets.py:576`); for a traffic-rejected lead `lead_status` is the default `"Unqualified - Website Down"` (not the string `"NO_SCRAPE"`). The `NO_SCRAPE` concept is documented intent; the literal string is not produced. Intended: write `NO_SCRAPE` when the traffic gate rejected the domain (a `no_scrape` flag on `DomainResponse`, set in `process_domain`).

> The n8n pipelines already operate as documented (they run against HubSpot data independent of the scraper code), but pipeline #3's eligibility filter (`lifecyclestage = lead`) depends on gap #3 being fixed for scraper-written leads to be assignable.

### Repo reference files

- `domain_properties.json` — a **HubSpot portal schema dump** (companies object): property names, labels, options (e.g. the `lifecyclestage` options and `hs_lead_status` values referenced throughout this README). Read it to resolve a portal property ID → human label. Not consumed by the code at runtime.

---

## 4. Environment Variables

```bash
ENVIRONMENT=production            # "dev" disables stats-sheet logging
AWS_BUCKET_NAME="domain-html-storage-bucket"
CREDENTIALS_PATH=credentials.json # Google service-account key
GEMINI_API_KEY=<...>
MODEL_NAME=gemini-3-flash-preview
OVERRIDE_MODEL_NAME=gemini-3.1-flash-lite   # regular workflow only, optional
MAX_INPUT_RECORDS=20              # optional; domains per run
ZYTE_ENABLED=false
ZYTE_API_KEY=<...>
HUBSPOT_API_KEY=<...>
APOLLO_API_KEY=<...>              # required (config), used only if enabled
SEAMLESS_API_KEY=<...>
AHREFS_API_KEY=<...>
SIMILARWEB_API_KEY=<...>          # traffic gate
CHUNK_SIZE=5

# Enrichment toggles (default OFF)
APOLLO_ENABLED=false
SEAMLESS_ENABLED=false
AHREFS_ENABLED=false

# Production sheet
SPREADSHEET_ID=<...>
SHEET_NAME="Scraper Tool INSERT HERE"
GOOD_RESULTS_SHEET="Scraper GOOD RESULTS History"
SKIP_RESULTS_SHEET="Scraper SKIP exist known lead History"
ERROR_RESULTS_SHEET="Scraper ERROR For Manual Scrapping History"

# Single sheet (regular workflow)
SINGLE_SPREADSHEET_ID=<...>
SINGLE_SHEET_NAME="Ray -Matt Leads"

# Stats / history
STATS_SPREADSHEET_ID=<...>
STATS_SHEET=Sheet1
```

Notes:
- `MAX_INPUT_RECORDS` caps how many domains one run processes.
- `OVERRIDE_MODEL_NAME` **only** affects the SingleSheet (regular) workflow — used for prompt testing on a different model.
- `APOLLO_ENABLED` / `SEAMLESS_ENABLED` / `AHREFS_ENABLED` **default to disabled**; enabling each one turns on its API enrichment.

---

## 5. Local Execution & Testing

```bash
uv sync
playwright install chromium --with-deps
```

- **Run the Lambda handlers locally:** `python main.py` runs whichever handler `TEST_HANDLER` selects (`splitter`, `worker`, or `cleanup` — default `cleanup`) against pre-defined sample payloads mirroring the real Step Functions events. Requires env vars to be exported first. Note: the worker path via `process_domain` includes the traffic gate; `MainExecutor.run()` (legacy direct path, `executor.py:59`) is defined but not wired into any entrypoint and does **not** run the traffic gate.
- **API server:** `uvicorn api_server:app` exposes `POST /process` which calls `executor.process_domain(domain)` (includes the traffic gate).

---

## 6. Utility: Ahrefs Backfill Script

`backfill_ahrefs.py` fills in missing Ahrefs columns for rows that were processed with Ahrefs disabled:

- Scans a sheet, finds rows where the **backlinks column (col 55)** is empty, and lists `(row_no, domain)`.
- `--sheet single|production` chooses the target sheet (default `production`).
- `--check-col N` changes the "is it populated?" column; `--limit N` caps rows.
- Add `--write` to batch-fetch from Ahrefs and write the 8 Ahrefs fields back into cols 49–56.

```bash
python backfill_ahrefs.py --sheet single --write --limit 20
```

---

## 7. Sheet Formats

**Production input sheet** (only col A used):

| Company URL |
|---|
| baileysblossoms.com |
| protectoproducts.com |

**Single sheet** (cols A and B required; results written in place from col C):

| Company Name | Company Name URL | Confirmed Website / HQ Phone # | Lead: is Website live? | … | Lead Status | Annual Revenue | … |
|---|---|---|---|---|---|---|---|
| Bailey Blossom | baileysblossoms.com | | | | | | |

---

## 8. n8n Pipelines (Downstream Automation)

After the scraper lands results in HubSpot/Google Sheets, **n8n** workflows act on those leads. This section documents each pipeline and its logic. These run **outside** the scraper codebase but are part of the overall process described in [Section 1](#1-high-level-flow).

> **Full node-by-node documentation** (every step, exact JS, endpoints, and assignment rules) lives in [`n8n_pipelines/README.md`](n8n_pipelines/README.md), alongside the workflow exports (`n8n_pipelines/*.json`).

### 8.1 Overview

```
Scraper results (HubSpot + Sheets)
        │
        ▼
Enrich Traffic Data             Lead Assignment to Owners        Lead Scoring (WIP)
  (SimilarWeb → HubSpot)            ├─ (1) Clear Owner for Unqualified Domains
                                    └─ (2) Owners pipeline
```

**Enrich Traffic Data** (`enrich_traffic_data.json`) — polls HubSpot for companies flagged `traffic_enrichment_status_request = "requested"`, pulls live SimilarWeb traffic metrics for each (visits, engagement, traffic sources, US share), and writes them back to the company, flipping the flag to `"completed"`. See [`n8n_pipelines/README.md`](n8n_pipelines/README.md) for node-by-node detail.

### 8.2 Pipeline: Lead Assignment to Owners

Assigns scraped leads to owners. It is a **two-stage** pipeline and always runs the stages in this order (full node-by-node detail in [`n8n_pipelines/README.md`](n8n_pipelines/README.md)).

**Stage 1 — Clear Owner for Unqualified Domains** (`clear_owner_unqualified_companies.json`)

- Purges owner assignment from every lead whose `hs_lead_status` contains the substring `"unqualified"` (case-insensitive) or exactly matches `"dm located but cannot reach"` / `"# of leads changed to unqualified not interested from unknown"`.
- Unqualified leads (e.g. those written as `NO_SCRAPE` by the traffic gate, or flagged `Unqualified …` by the lead-status rules) must not be owned by anyone; they are not real prospects. Clearing ownership first keeps the assignment stage clean so it only operates on qualified, owned-eligible leads.
- Cleared records get `hubspot_owner_id` emptied and `lifecyclestage` set to the unqualified value (`136007941`).

**Stage 2 — Owners pipeline** (`lead_assignment_to_owners.json`, runs every 30 min)

- First calls Stage 1, then computes each target owner's shortfall against a fixed quota (**300 leads/owner**, configurable in the `Total Leads to be Assigned to Each Owner` node) and top-ups by assigning unowned eligible leads in round-robin order (owner by owner) via HubSpot batch update.
- Owned-eligible leads must satisfy **all**: `lifecyclestage = lead`, confirmed website/HQ phone present, `1000 ≤ total_monthly_visits ≤ 60000` (the pipeline's traffic band is tighter than the scraper's 100–500k gate), no current owner, and lead status not in the 12-value unqualified list.
- Eligible leads are fetched in pages (rate-limited to 1 page / 10 s) and only as many as the combined shortfall requires.

### 8.3 Pipeline: Lead Scoring (in progress / WIP)

- **Still being worked on.** Intended to score each lead (e.g. from enrichment data — Apollo/Seamless/Ahrefs traffic, revenue, B2C/B2B fit) so leads can be prioritized or routed.
- Details of the scoring model and thresholds will be added here once finalized.

---

## 9. Operational Notes

1. **No duplicate domains** in the sheets — duplicates corrupt row/result alignment.
2. **SingleSheet** requires Company Name + URL per row; **Production** only needs the URL.
3. **Do not edit sheets while the pipeline is running** — the regular workflow writes back by row position.
4. To change sheet data safely: wait for the run to finish, or **disable the EventBridge scheduler first and wait 30 minutes** for in-flight executions.
5. Staging objects under `s3://…/staging/` are currently retained so a failed job can be re-saved from S3.
6. The traffic gate runs unconditionally today (`SIMILARWEBAPI_ENABLED` is defined but not yet enforced in `process_domain`).

---

## 10. TODO

- Delete staging files after successful save (currently commented out in `lambdas/cleanup.py`).
- Wire `SIMILARWEBAPI_ENABLED` into `process_domain`.
- Alerting mechanism.
