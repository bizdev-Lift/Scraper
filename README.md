# 🤖 LLMDataExtraction

A tool for extracting structured data from websites using Large Language Models and automated browser interaction.

## 🔧 Prerequisites

- Python 3.12+
- [Google Cloud credentials](https://developers.google.com/sheets/api/quickstart/python) for Google Sheets API
- Gemini API key
- Internet connection for web browsing
- [Playwright](https://playwright.dev/docs/intro)
- [UV Package Manager](https://github.com/astral-sh/uv)
- [Docker](https://docs.docker.com/engine/install/)

## 🔑 Environment Variables Required

Create a `.prod_env` file in the project root (this is the file `./deploy.sh` loads) with the following variables:

```bash
ENVIRONMENT=production
AWS_BUCKET_NAME="domain-html-storage-bucket"
CREDENTIALS_PATH=credentials.json
GEMINI_API_KEY=<your_gemini_api_key>
MODEL_NAME=gemini-3-flash-preview
OVERRIDE_MODEL_NAME=gemini-3.1-flash-lite
MAX_INPUT_RECORDS=20
ZYTE_ENABLED=false
ZYTE_API_KEY=<your_zyte_api_key>
HUBSPOT_API_KEY=<your_hubspot_api_key>
APOLLO_API_KEY=<your_apollo_api_key>
SEAMLESS_API_KEY=<your_seamless_api_key>
AHREFS_API_KEY=<your_ahrefs_api_key>

# Enrichment providers (default: disabled). Set to true to enable each one.
APOLLO_ENABLED=false
SEAMLESS_ENABLED=false
AHREFS_ENABLED=false

# For Production Sheet
SPREADSHEET_ID=<your_production_spreadsheet_id>
SHEET_NAME="Scraper Tool INSERT HERE"
GOOD_RESULTS_SHEET="Scraper GOOD RESULTS History"
SKIP_RESULTS_SHEET="Scraper SKIP exist known lead History"
ERROR_RESULTS_SHEET="Scraper ERROR For Manual Scrapping History"
HUBSPOT_DATABASE_SHEET="Hubspot database March 2026"

# For Single Sheet
SINGLE_SPREADSHEET_ID=<your_single_sheet_spreadsheet_id>
SINGLE_SHEET_NAME="Ray -Matt Leads"

# Stats / history tracking
STATS_SPREADSHEET_ID=<your_stats_spreadsheet_id>
STATS_SHEET=Sheet1
```

`MAX_INPUT_RECORDS` is optional — it tells the script how many domains to parse in each execution.

`OVERRIDE_MODEL_NAME` is optional and **only affects the SingleSheet (regular) workflow**. If defined, the SingleSheet workflow will use this model instead of `MODEL_NAME`. This is useful for testing prompts against a different model without touching the production workflow. For example, to test the regular workflow with `gemini-3.1-flash-lite`, just set `OVERRIDE_MODEL_NAME=gemini-3.1-flash-lite` and the SingleSheet workflow will use it.

`APOLLO_ENABLED`, `SEAMLESS_ENABLED`, and `AHREFS_ENABLED` toggle each enrichment provider (all **default to disabled**). When a provider is disabled, the worker skips the API call and returns empty results for each domain; set to `true` to enable fetching.



## 🚀 How to Execute Locally

### Option 1: Direct Execution

1. Go inside the directory
   ```bash
   cd LLMDataExtraction
   ```

2. Setup the virtual environment.
    ```bash
    uv sync
    ```

3. Install chromium and related dependencies with playwright
    ```bash
    playwright install chromium --with-deps
    ```
4. Run the code using the following command.
    ```bash
    python main.py
    ```

## 🧪 Testing the Lambdas Locally

You can run each Lambda handler locally for debugging via `main.py`. The sample payloads are already defined there, mirroring what the Step Functions pipeline sends at runtime.

1. Make sure the environment variables are available (export them, or source your env file) — otherwise the script fails on import.
2. Set `TEST_HANDLER` in `main.py` to one of `splitter`, `worker`, or `cleanup`.
3. Run it:
   ```bash
   python main.py
   ```

### Sample inputs

**Splitter** — only receives the workflow mode:
```json
{"workflow_mode": "regular"}
```

**Worker** — each Map iteration hands the Worker a single chunk:
```json
{
  "chunk_id": 0,
  "domains": [
    {"domain": "dtidirect.com", "row_no": 99},
    {"domain": "accesstoindependence.com", "row_no": 301}
  ],
  "workflow_mode": "regular",
  "job_id": "577d8ab6-b0c3-466e-8be5-e6946cc9e6d8"
}
```

**Cleanup** — receives the full state-machine payload (all chunks plus the Map results):
```json
{
  "workflow_mode": "regular",
  "job_id": "577d8ab6-b0c3-466e-8be5-e6946cc9e6d8",
  "chunks": [
    {
      "chunk_id": 0,
      "domains": [
        {"domain": "dtidirect.com", "row_no": 99},
        {"domain": "accesstoindependence.com", "row_no": 301}
      ],
      "workflow_mode": "regular",
      "job_id": "577d8ab6-b0c3-466e-8be5-e6946cc9e6d8"
    }
  ],
  "results": [
    {"chunk_id": 0, "domain_count": 3, "status": "ok"}
  ]
}
```

## ⚙️ How the Code Works
This application performs automated data insights for each domain using generic scraper along with AI Agent. Important components in this are.
- `io_operations`: This module contains scripts for fetching and storing data from/to Google Sheets.
- `scraper`: This module contains an http generic scraper along with a bot scraper.
    - `generic_scraper`: An HTTP scraper that makes an HTTP call to fetch the HTML data.
    - `bot_scraper`: Fetch the HTML using playwright browser.
    - `parser`: This file contains the parsing functions which cleans out the HTML and remove unncessary tags.
- `llm`: This module contains all the interactions of the LLM along with the prompt.
    - `llm_helpers`: This file contains interaction with the Gemini. From making the LLM Call to fetching the response is done here.
    - `main_prompt`: This file contains the Prompt that is used to fetch the insights.
    - `revenue_prompt`: This file contains the Revenue Prompt that is used to fetch the revenue info for the domain.
- `config`: This file defines the config.
- `logger`: This file defines the logger used in the application.
- `executor`: This is the main file that executes the whole code.

## Architecture Diagram
![alt text](<architecture_diagram.png>)

## 🌐 Deployment

Deployment is handled entirely by a single script: `./deploy.sh`.

### Prerequisites
- AWS CLI configured with credentials for the target account
- Docker
- `node` and `npm` (the required serverless plugins are installed automatically)
- `uv`

### The `.prod_env` file
`deploy.sh` loads a `.${STAGE}_env` file from the project root, and the default stage is `prod`. So for the standard flow you need a **`.prod_env`** file populated with all the required environment variables listed in the [Environment Variables Required](#-environment-variables-required) section above. If `.prod_env` is missing, the script aborts with an error.

### Deploy (build & push & deploy)
```bash
./deploy.sh --build
```

This will:
1. Load `.prod_env`
2. Install the serverless plugins (`serverless@3`, `serverless-step-functions`, etc.)
3. Build the Docker image and push it to **ECR** (tagged with the current Git short SHA)
4. Deploy the **CloudFormation** stack defined in `serverless.yml` — this provisions the Lambda functions, S3 bucket, Step Functions state machines, and EventBridge schedulers

### Deploy an existing image (skip the build)
```bash
./deploy.sh --image-tag <git_sha>
```

### Destroy the stack
```bash
./deploy.sh --destroy
```



## 📄 How to switch Lambda between Single and Production Sheet.
- The `workflow_mode` is passed as **input** to the state machine — it is not read from an environment variable anymore.
- The value is **hardcoded inside each Step Functions state machine** (via the `Parameters` field on the `SplitDomains` state), so there are **two separate state machines**:
  - `DomainDataExtractorStateMachine` → `workflow_mode: "production"` → runs against the production sheet.
  - `DomainDataExtractorStateMachine_SingleSheet` → `workflow_mode: "regular"` → runs against the single sheet.
- Both are triggered on their own EventBridge schedulers every 15 minutes.

## 📄 How to Update the Sheets Used
Each workflow reads its sheet from its own set of environment variables:

**Production sheet** — uses the plain sheet variables:
```bash
SPREADSHEET_ID=<spreadsheet_id>
SHEET_NAME=<sheet_name>
```

**Single sheet** — uses the `SINGLE_` prefixed variables:
```bash
SINGLE_SPREADSHEET_ID=<spreadsheet_id>
SINGLE_SHEET_NAME=<sheet_name>
```

`SPREADSHEET_ID` can be extracted from the URL of the sheet. `SHEET_NAME` is the sheet name from inside the Google Sheet.

## 🖌️ Sheet Format
The format of the sheet would be like this.

| Company Name | Company Name URL | Confirmed Website / Headquarters Phone # | Lead : is Website live? | Lead : Address Listed on Website? | Lead : Do They Sell B2C? | Lead : Do they sell B2B? | Company : Industry type VERIFIED | Lead : Ecomm Platform | Lead Status | Annual Revenue | Redirected To |
|---|---|---|---|---|---|---|---|---|---|---|---|
|Bailey Blossom|baileysblossoms.com|---|---|---|---|---|---|---|---|---|---|
|Protect Products | protectoproducts.com|---|---|---|---|---|---|---|---|---|---|
|Beelart Embroidery | beelartembroidery.com|---|---|---|---|---|---|---|---|---|---|

## ⚠️ Important Notes Before Adding Domains

Please read these notes carefully before adding data to the sheets. Following them will prevent data corruption and processing issues.

1. **No duplicates** — Make sure you do not add duplicate domains to the sheet. Duplicate rows can cause the data to get corrupt (e.g., index mismatch, overwritten rows, or misaligned results).
2. **SingleSheet requirements** — The SingleSheet workflow requires both the **Company Name** and **Company URL** columns to be populated for every row. If either is missing, that row will not be processed.
3. **Production sheet requirements** — For the production workflow, only the **Company URL** column needs to be populated.
4. **Do not edit sheets while the pipeline is running** — Once you have added domains, do not alter the sheet (no reordering, no inserting/deleting rows, no editing cells). The pipeline relies on row positions to write results back.
5. **Making changes safely** — If you need to change the data in a sheet:
   1. Make sure the script is not currently running, or
   2. Disable the EventBridge scheduler first, wait **30 minutes** for any in-flight execution to finish, and only then make your changes to the sheet.




# TODO:
- Deployment needs to be automated.
- Alerting Mechanism.
