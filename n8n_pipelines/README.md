# n8n Pipelines

This directory holds the **n8n workflow exports** that act on scraper output after it lands in HubSpot. Each `.json` file is a full workflow export (nodes + connections + settings) that can be imported directly into n8n.

> See the main [`README.md`](../README.md) for the overall scraper architecture. This document goes into **step-by-step detail** for every node in each pipeline, including the exact JS logic each Code node runs.

## Workflows in this directory

| File | n8n workflow name | Trigger |
|---|---|---|
| [`enrich_traffic_data.json`](./enrich_traffic_data.json) | Enrich Traffic Data | Schedule (minutes) |
| [`clear_owner_unqualified_companies.json`](./clear_owner_unqualified_companies.json) | HubSpot - Clear Owner for Unqualified Companies | Executed by another workflow |
| [`lead_assignment_to_owners.json`](./lead_assignment_to_owners.json) | HubSpot - Lead Assignment to Owners | Schedule (every 30 min) |

## Shared credentials

All HubSpot HTTP calls use the n8n credential **"HubSpot Service Key account"** (`hubspotAppToken`). The SimilarWeb call uses the credential **"RapidAPI-SimilarWebAPI-Traffic"** (`httpHeaderAuth`).

All HubSpot property names below (`traffic_enrichment_status_request`, `total_monthly_visits`, `hubspot_owner_id`, `hs_lead_status`, `lifecyclestage`, `confirmed_website___headquarters_phone__`) are custom properties on the **Companies** object in the HubSpot portal.

---

## 1. Enrich Traffic Data

**File:** `enrich_traffic_data.json` — workflow name **"Enrich Traffic Data"**, `active: true`.

Fetches live SimilarWeb traffic metrics for companies that have been flagged for enrichment, then writes the metrics back to the HubSpot company record.

### Flow

```
Schedule Trigger
      │
      ▼
Get domains for Traffic Enrichment  (HubSpot companies/search: status = "requested")
      │
      ▼
Validate Domains                   (Code: regex-check the name is a real domain)
      │
      ▼
Split Out                          (one item per company)
      │
      ▼
Extract Data from RapidAPI-SimilarWebAPI  (GET similarweb-traffic-scale-plan.p.rapidapi.com/traffic)
      │
      ▼
Parse Item                         (Code: map response → HubSpot property payload)
      │
      ▼
Update HubSpot                     (PATCH companies/{companyId})
```

### Step-by-step

**1.1 Schedule Trigger** — `n8n-nodes-base.scheduleTrigger`, typeVersion 1.3.

- Rule: interval on `minutes`. (The export omits `minutesInterval`, so n8n uses its default; set the desired interval in the UI.)
- This pipeline is the polling loop that picks up companies waiting for traffic enrichment.

**1.2 Get domains for Traffic Enrichment** — HTTP Request, POST, typeVersion 4.4.

- URL: `https://api.hubapi.com/crm/v3/objects/companies/search`
- Body:
  ```json
  {
    "filterGroups": [
      { "filters": [ {
          "propertyName": "traffic_enrichment_status_request",
          "operator": "EQ",
          "value": "requested"
        } ] }
    ],
    "properties": ["name", "traffic_enrichment_status_request", "hubspot_owner_id"],
    "limit": 100
  }
  ```
- Selects only companies whose custom property `traffic_enrichment_status_request` equals `"requested"` — this is the flag that marks a company as queued for enrichment (the scraper or an operator is expected to set it).
- Returns up to 100 companies per page; note **no pagination is configured** on this node, so it only processes the first 100 matches per run.

**1.3 Validate Domains** — Code, typeVersion 2.

- For each company in `results`, takes `company.properties.name`, `trim()` + `toLowerCase()` it, and keeps it only if it matches the domain regex:
  ```js
  /^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$/
  ```
- Each valid company is emitted **as-is** (spread, so all original keys are retained) plus a new `domain` field.
- Purpose: a company `name` that is not a well-formed domain (e.g. a display name like "Acme Corp") is skipped before it can be sent to SimilarWeb.
- Output shape: `{ "results": [ company1, company2, ... ] }` where each `company` has `properties.name` and the added `domain`.

**1.4 Split Out** — `n8n-nodes-base.splitOut`, typeVersion 1.

- Field to split out: `results`.
- Turns the single array of results into **one n8n item per company**, so the downstream nodes run once per company.

**1.5 Extract Data from RapidAPI-SimilarWebAPI** — HTTP Request, GET, typeVersion 4.5.

- URL: `https://similarweb-traffic-scale-plan.p.rapidapi.com/traffic`
- Auth: `httpHeaderAuth` credential **"RapidAPI-SimilarWebAPI-Traffic"**.
- Headers:
  - `x-rapidapi-host`: `similarweb-traffic-scale-plan.p.rapidapi.com`
  - `Content-Type`: `application/json`
- Query parameter: `domain` = `{{ $json.properties.name }}` (the company name as stored in HubSpot).
- Runs once per company (because of Split Out). Returns the SimilarWeb traffic JSON document.

**1.6 Parse Item** — Code, typeVersion 2, mode `runOnceForEachItem`.

- Operates on `$json` (the SimilarWeb response) and extracts:
  - **Monthly visits:** `EstimatedMonthlyVisits` is an object keyed by date (`YYYY-MM-DD` → visits). `latestKey` is the newest key (max string comparison); `total_monthly_visits` = `monthlyVisits[latestKey]`, or `null` if empty.
  - **Engagement:** from `Engagments` (fallback `Engagements`):
    - `bounce_rate` = `parseFloat(BounceRate)` or `null`
    - `pages_per_visit` = `parseFloat(PagePerVisit)` or `null`
    - `time_on_site` = `TimeOnSite`
  - **Traffic sources:** from `TrafficSources`:
    - `traffic_source_search_organic` = `SearchOrganic ?? null`
    - `traffic_source_search_paid` = `SearchPaid ?? null`
    - `traffic_source_direct` = `Direct ?? null`
    - `traffic_source_referrals` = `Referrals ?? null`
  - **US share:** from `TopCountryShares`, finds the entry where `CountryCode === 'US'`, `usa_traffic` = its `Value` (a percentage), else `null`.
  - **Bookkeeping:**
    - `traffic_enrichment_date` = today's date as `YYYY-MM-DD`
    - `traffic_enrichment_status_request` = `"completed"` (this is how the pipeline marks the company as done, flipping the same flag used in step 1.2)
  - `companyId` is taken from the **Split Out** node (`$("Split Out").item.json.id`) so the update targets the right record.
- Output: a single item `{ companyId, payload: { total_monthly_visits, bounce_rate, pages_per_visit, time_on_site, traffic_source_search_organic, traffic_source_search_paid, traffic_source_direct, traffic_source_referrals, usa_traffic, traffic_enrichment_date, traffic_enrichment_status_request } }`.

**1.7 Update HubSpot** — HTTP Request, PATCH, typeVersion 4.5.

- URL: `https://api.hubapi.com/crm/v3/objects/companies/{{ $json.companyId }}`
- Body (JSON):
  ```json
  { "properties": {{ JSON.stringify($json.payload) }} }
  ```
- Writes the enriched metrics onto the company. After this, `traffic_enrichment_status_request` is `"completed"`, so the next run's search (step 1.2) will not pick the company up again.

### Field summary (written to HubSpot)

| HubSpot property | Source |
|---|---|
| `total_monthly_visits` | `EstimatedMonthlyVisits[latest date]` |
| `bounce_rate` | `Engagments.BounceRate` (float) |
| `pages_per_visit` | `Engagments.PagePerVisit` (float) |
| `time_on_site` | `Engagments.TimeOnSite` |
| `traffic_source_search_organic` | `TrafficSources.SearchOrganic` |
| `traffic_source_search_paid` | `TrafficSources.SearchPaid` |
| `traffic_source_direct` | `TrafficSources.Direct` |
| `traffic_source_referrals` | `TrafficSources.Referrals` |
| `usa_traffic` | `TopCountryShares` entry where `CountryCode === 'US'` |
| `traffic_enrichment_date` | today (YYYY-MM-DD) |
| `traffic_enrichment_status_request` | `"completed"` (was `"requested"`) |

---

## 2. HubSpot - Clear Owner for Unqualified Companies

**File:** `clear_owner_unqualified_companies.json` — workflow name **"HubSpot - Clear Owner for Unqualified Companies"**, `active: true`.

Clears the owner from every company whose lead status indicates the company is unqualified (so unqualified records never sit with an owner). This is the **Stage 1** cleanup that runs *before* leads are (re)assigned.

### Flow

```
When Executed by Another Workflow
      │
      ▼
Get Owners          (GET /crm/v3/owners?limit=100)
      │
      ▼
Find Owner ID       (Code: resolve "Gene/Ezzo LC Inside Sales" → ownerId)
      │
      ▼
Search Companies    (POST companies/search: hubspot_owner_id = ownerId AND hs_lead_status present)
      │
      ▼
Filter Unqualified Lead Status  (Code: keep hs_lead_status containing "unqualified" or exact matches)
      │
      ▼
Set Owner to No Owner           (PATCH companies/{companyId}: clear owner, set lifecyclestage)
```

### Step-by-step

**2.1 When Executed by Another Workflow** — `n8n-nodes-base.executeWorkflowTrigger`, typeVersion 1.2.

- Trigger is a call from the **Lead Assignment to Owners** workflow (via its `executeWorkflow` node). `callerPolicy` is `any`, so any workflow can invoke it.
- No data is passed in (`workflowInputs` empty) — the workflow needs no inputs.

**2.2 Get Owners** — HTTP Request, GET, typeVersion 4.4.

- URL: `https://api.hubapi.com/crm/v3/owners`
- Query: `limit=100`.
- Auth: HubSpot Service Key account.
- Fetches the current HubSpot user/owner list.

**2.3 Find Owner ID** — Code, typeVersion 2.

- Target list (trimmed + lowercased):
  ```js
  const targets = [
    'Gene LC Inside Sales',
    'Ezzo LC Inside Sales',
  ].map(name => name.trim().toLowerCase());
  ```
- Concatenates `results` from every input item into a single `owners` array.
- For each owner, builds `fullName = firstName lastName` and `email`, both trimmed + lowercased. An owner matches if `targets.includes(fullName)` **or** `targets.includes(email)`.
- **Error handling (fail-fast):**
  - If **no** matches at all → throws `No matching HubSpot owners found. Looking for: … Available owners: <comma-separated full names>`.
  - If **some** targets are missing → throws `These HubSpot owners were not found: … Available owners: …`.
  - These hard failures stop the run so a renamed owner or a typo is caught immediately instead of silently assigning to nobody.
- Output: one item per matched owner:
  ```js
  { json: { ownerId: String(owner.id), ownerName: "First Last", ownerEmail: owner.email } }
  ```

**2.4 Search Companies** — HTTP Request, POST, typeVersion 4.4.

- URL: `https://api.hubapi.com/crm/v3/objects/companies/search`
- Body:
  ```json
  {
    "filterGroups": [
      { "filters": [
          { "propertyName": "hubspot_owner_id", "operator": "EQ", "value": "{{ $json.ownerId }}" },
          { "propertyName": "hs_lead_status", "operator": "HAS_PROPERTY" }
        ] }
    ],
    "properties": ["name", "hs_lead_status", "hubspot_owner_id"],
    "limit": 200
  }
  ```
- Runs **once per owner** returned by Find Owner ID (n8n runs the HTTP node per incoming item).
- Finds companies currently owned by that owner **and** that have an `hs_lead_status` set (so the filter in 2.5 has something to evaluate).
- **Pagination:** custom — adds a `body` param `after` = `$response.body.paging?.next?.after`, stops when that value is `undefined`, with a `requestInterval` of `300` ms between pages.

**2.5 Filter Unqualified Lead Status** — Code, typeVersion 2.

- For every company in `results`, lowercases `hs_lead_status` and keeps it if:
  - **Substring match:** `status.includes("unqualified")` — catches all `Unqualified …` statuses (case-insensitive), or
  - **Exact match** (case-sensitive, against the original string):
    - `"dm located but cannot reach"`
    - `"# of leads changed to unqualified not interested from unknown"`
- Output: one item per qualifying company: `{ companyId, name, leadStatus }`.

**2.6 Set Owner to No Owner** — HTTP Request, PATCH, typeVersion 4.4, `retryOnFail: true`.

- URL: `https://api.hubapi.com/crm/v3/objects/companies/{{ $json.companyId }}`
- Body:
  ```json
  { "properties": { "hubspot_owner_id": "", "lifecyclestage": "136007941" } }
  ```
- Empties `hubspot_owner_id` (unassigns the owner) and sets `lifecyclestage` to `136007941` (the portal's **Unqualified** lifecycle-stage value).
- `retryOnFail: true` retries transient failures automatically.

---

## 3. HubSpot - Lead Assignment to Owners

**File:** `lead_assignment_to_owners.json` — workflow name **"HubSpot - Lead Assignment to Owners"**, `active: true`.

Main entry pipeline: runs every 30 minutes, first clears owners on unqualified companies (by invoking workflow #2), then top-up-assigns qualified, owned-eligible leads to the target owners so each owner ends up with a target quota.

### Flow

```
Schedule Trigger (every 30 min)
      │
      ▼
Call 'HubSpot - Clear Owner for Unqualified Companies'   (executeWorkflow → pipeline #2)
      │
      ▼
Total Leads to be Assigned to Each Owner   (Code: hardcoded target = 300)
      │
      ▼
Get Owners          (GET /crm/v3/owners?limit=100)
      │
      ▼
Find Owner ID       (Code: resolve targets → ownerId items)
      │
      ▼
Get Total leads assigned   (POST companies/search: count current leads per owner)
      │
      ▼
Get leads count to be assigned   (Code: shortfall = max(0, target − current) per owner)
      │
      ▼
If (totalLeads ≠ 0) ──true──► Get Leads with No Owner (POST companies/search, scored/eligible)
      │                                 │
     (false: stop)                      ▼
                              Map Owners to Leads  (Code: round-robin assign by shortfall, batch of 100)
                                    │
                                    ▼
                              Update Owners on Leads (POST companies/batch/update)
```

### Step-by-step

**3.1 Schedule Trigger** — `n8n-nodes-base.scheduleTrigger`, typeVersion 1.3.

- Rule: interval on `minutes` with `minutesInterval: 30` → runs **every 30 minutes**.

**3.2 Call 'HubSpot - Clear Owner for Unqualified Companies'** — `executeWorkflow` node, typeVersion 1.3.

- Invokes workflow id `UpHbY5v9QREENLR4` (the Clear-Owner workflow, pipeline #2) with empty inputs.
- Guarantees unqualified companies are cleaned **before** counting/assignment so the quota math starts from a clean slate.

**3.3 Total Leads to be Assigned to Each Owner** — Code, typeVersion 2.

- Body:
  ```js
  return [{ json: { totalLeadsToBeAssigned: 300 } }];
  ```
- Hardcoded target quota: **300** leads per owner. Change this constant here to change the quota.

**3.4 Get Owners** — HTTP Request, GET, typeVersion 4.4.

- Identical to pipeline #2's Get Owners: `GET https://api.hubapi.com/crm/v3/owners?limit=100`, HubSpot Service Key.

**3.5 Find Owner ID** — Code, typeVersion 2.

- **Identical code** to pipeline #2 step 2.3: targets `['Gene LC Inside Sales', 'Ezzo LC Inside Sales']`, matched against `firstName lastName` or `email`, with the same fail-fast errors, emitting `{ ownerId, ownerName, ownerEmail }`.

**3.6 Get Total leads assigned** — HTTP Request, POST, typeVersion 4.4.

- URL: `https://api.hubapi.com/crm/v3/objects/companies/search`
- Body:
  ```json
  {
    "filterGroups": [
      { "filters": [ { "propertyName": "hubspot_owner_id", "operator": "EQ", "value": "{{ $json.ownerId }}" } ] }
    ],
    "properties": ["name", "hs_lead_status", "hubspot_owner_id"],
    "limit": 100
  }
  ```
- Runs once per owner (from Find Owner ID). Counts how many leads each owner currently holds.
- **Pagination:** body `after` param from `paging.next.after`, stops when undefined, `requestInterval: 300` ms.

**3.7 Get leads count to be assigned** — Code, typeVersion 2.

- Initializes `ownerCounts[ownerId] = 0` for every owner from the **Find Owner ID** node.
- Counts records from **Get Total leads assigned**: for each record, `ownerId = record.properties?.hubspot_owner_id || "unassigned"` and increments.
- Reads the quota target: `targetTotal = Number($("Total Leads to be Assigned to Each Owner").all()[0]?.json?.totalLeadsToBeAssigned) || 0`.
- For each owner computes the shortfall **clamped at 0**:
  ```js
  const ownerTotal = Math.max(0, targetTotal - count);
  ```
  (an owner already at/over quota gets `0`).
- Sums all shortfalls into `grandTotalLeads` (also clamped to `≥ 0`).
- Output: a single item
  ```js
  { json: { totalLeads: grandTotalLeads, owners_info: [ { hubspot_owner_id, totalLeads }, … ] } }
  ```

**3.8 If** — `n8n-nodes-base.if`, typeVersion 2.3.

- Condition (strict, case-sensitive): `$json.totalLeads ≠ 0` (number comparison, `notEquals`).
- **True** → continues to **Get Leads with No Owner**. **False** (all owners at quota, nothing to assign) → workflow ends early. This prevents the expensive search + batch update from running when there is nothing to do.

**3.9 Get Leads with No Owner** — HTTP Request, POST, typeVersion 4.4, `retryOnFail: false`, `maxTries: 2`.

- URL: `https://api.hubapi.com/crm/v3/objects/companies/search`
- Body (this is the **owned-eligibility** query — it defines which leads are assignable):
  ```json
  {
    "filterGroups": [
      { "filters": [
          { "propertyName": "lifecyclestage", "operator": "EQ", "value": "lead" },
          { "propertyName": "confirmed_website___headquarters_phone__", "operator": "HAS_PROPERTY" },
          { "propertyName": "total_monthly_visits", "operator": "GTE", "value": 1000 },
          { "propertyName": "total_monthly_visits", "operator": "LTE", "value": 60000 },
          { "propertyName": "hubspot_owner_id", "operator": "NOT_HAS_PROPERTY" },
          { "propertyName": "hs_lead_status", "operator": "NOT_IN",
            "values": [
              "Unqualified Low Weight",
              "Unqualified Non US based",
              "Unqualified Bad Product Type",
              "Unqualified Revenue Plus 100 mil",
              "Unqualified Revenue Less 1 mil",
              "DM located BUT cannot reach",
              "Unqualified Cannot Reach",
              "Unqualified Website Down",
              "Unqualified Cannot Compete Pricing",
              "Unqualified Not Interested",
              "# of leads changed to unqualified not interested from UNKNOWN",
              "Unqualified Junk Lead / No shipping"
            ] }
        ] }
    ],
    "properties": ["name", "hs_lead_status", "hubspot_owner_id"],
    "limit": 200
  }
  ```
- An assignable lead must be **all** of:
  1. `lifecyclestage = lead` (a real lead),
  2. has a confirmed website / HQ phone (`confirmed_website___headquarters_phone__` present) — i.e. it passed validation/enrichment,
  3. `total_monthly_visits` between **1,000 and 60,000** (the tight "real company" traffic band; note the scraper's own gate is 100–500k, this pipeline is stricter),
  4. **no owner** yet (`hubspot_owner_id` NOT present),
  5. lead status **not** any of the 12 unqualified values above (these were just cleared by pipeline #2, so normally already absent).
- **Pagination (deliberately rate-limited):** `after` from `paging.next.after`, with `requestInterval: 10000` ms (10 s between pages — slows the search down to avoid HubSpot rate limits), and a completion expression that stops when either
  - `totalLeads === 0` (nothing to assign → stop immediately), **or**
  - the `after` offset has reached `ceil(totalLeads / 200) * 200` — i.e. only fetch roughly as many companies as the combined shortfall requires, no more.
  - `retryOnFail: false` + `maxTries: 2` means this node does not auto-retry (so the pagination math is not duplicated) but the overall run can still be retried up to 2 times.

**3.10 Map Owners to Leads** — Code, typeVersion 2.

- Pulls the per-owner shortfalls from `Get leads count to be assigned` (`owners_info`) and the fetched company ids from `Get Leads with No Owner` (`results`, flattened, `String(c.id)`).
- Walks the company list **in order** and, for each owner rule in `owners_info`, assigns that owner to the next `targetCount` companies (sequential round-robin by quota). A company is only taken if the owner still has quota (`assignedCount < targetCount`), and each company is assigned to at most one owner.
- Example: if owner A shortfall = 40 and owner B = 25, companies 0–39 → A, companies 40–64 → B.
- Chunks the resulting updates into batches of **100** (HubSpot's batch-update size limit):
  ```js
  batches.push({ json: { inputs: updates.slice(i, i + 100) } });
  ```
- Output: one item per batch: `{ json: { inputs: [ { id, properties: { hubspot_owner_id } }, … ] } }`.

**3.11 Update Owners on Leads** — HTTP Request, POST, typeVersion 4.4.

- URL: `https://api.hubapi.com/crm/v3/objects/companies/batch/update`
- Body: `{{ JSON.stringify($json) }}` (the raw `inputs` batch from 3.10).
- Runs once per batch. This is where the owner is actually written to the companies.

### Assignment logic summary

- Quota: **300 leads per owner** (`Total Leads to be Assigned to Each Owner`).
- Order: clear unqualified owners → count current load per owner → compute shortfall → fetch only the needed number of unowned, eligible leads → assign sequentially by owner shortfall → batch update.
- Eligibility (all required): `lifecyclestage = lead`, confirmed website/phone present, `1000 ≤ total_monthly_visits ≤ 60000`, no owner, and lead status not in the 12-value unqualified list.

---

## Cross-pipeline notes

- **Pipeline #3 depends on pipeline #2** (it calls it first). The `clear_owner_unqualified_companies.json` workflow is *not* scheduled on its own — it only runs when invoked.
- **Lead-status compatibility:** the scraper writes `hs_lead_status = Unqualified Revenue Less 1 mil` / `Unqualified Revenue Plus 100 mil` for traffic-rejected rows; those contain the substring `"unqualified"`, so pipeline #2 clears them and pipeline #3 excludes them from assignment. The other `Unqualified …` / `DM located …` variants are the manual/unqualified statuses entered by the team.
- **Enrichment status flag:** `traffic_enrichment_status_request` is the handshake between the scraper/ops and pipeline #1 — set it to `"requested"` to queue a company, pipeline #1 sets it to `"completed"` after writing metrics.
- All pipelines authenticate to HubSpot with the **"HubSpot Service Key account"** app-token credential.
- Workflow IDs referenced cross-workflow: pipeline #3 calls workflow id `UpHbY5v9QREENLR4` (pipeline #2).
