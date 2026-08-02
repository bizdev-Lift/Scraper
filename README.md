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

Create a `.env` file in the project root with the following variables:

```bash
CREDENTIALS_PATH=path_to_your_credentials.json
GEMINI_API_KEY=your_gemini_api_key
AWS_BUCKET_NAME="domain-html-storage-bucket"
CREDENTIALS_PATH=credentials.json
GEMINI_API_KEY=<key>
MODEL_NAME=gemini-3-flash-preview
ZYTE_ENABLED=false
ZYTE_API_KEY=<api_key>
STATS_SPREADSHEET_ID=1fifyC7tsAwOR8f8osHnKL-CsFJYstYzJcIIwlwj5MXU
STATS_SHEET=Sheet1


# For Single Sheet
SPREADSHEET_ID=<your_spreadsheet_id>
SHEET_NAME=<sheet_name>

# For Production Sheet
SPREADSHEET_ID=<your_spreadsheet_id>
SHEET_NAME=<sheet_name>
GOOD_RESULTS_SHEET="Scraper GOOD RESULTS ready to import"
SKIP_RESULTS_SHEET="Scraper SKIP exist known lead"
ERROR_RESULTS_SHEET="Scraper ERROR For Manual Scrapping"
```

There is another optional env called `MAX_INPUT_RECORDS` that you can set to tell the script how many domains should it parse in each execution.



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
1. Build the image using the following command.
   ```bash
   docker buildx build -f Dockerfile --provenance=false -t llm-data-extractor . --platform=linux/amd64 --target=llm-extractor
   ```
2. Setup the Repository in the Elastic Container Registry. For more info check [this](https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html) link.

3. Tag and push the image to the repository
   ```bash
   docker tag llm-data-extractor:latest <repository_url>
   docker push <repository_url>
   ```

   For example, if my repository url is `383488877661.dkr.ecr.us-east-1.amazonaws.com/llm-data-extractor:latest`, then the commands will be.
   ```bash
   docker tag llm-data-extractor:latest 383488877661.dkr.ecr.us-east-1.amazonaws.com/llm-data-extractor:latest
   docker push 383488877661.dkr.ecr.us-east-1.amazonaws.com/llm-data-extractor:latest
   ```

4. Create the Lambda on AWS. For more info follow [this](https://docs.aws.amazon.com/lambda/latest/dg/images-create.html) link.

5. Following Image Configuration need to be set.
    - **CMD**: executor.lambda_handler

6. Following configurations need to be set on AWS Lambda.
    - **Memory**: 256 MB
    - **Timeout**: 15 minutes


## 📄 How to switch Lambda between Single and Production Sheet.
- This switch is being controlled through an environment variable called `WORKFLOW_MODE`. If you set it to production, it will run against production sheet (NOTE: All the env variables needed by production sheet are mentioned above and should be defined for this to work)
- If you remove WORKFLOW_MODE variable, it will run by default for a single sheet.


## 📄 How to Update Lambda to Use different Sheet
- If you want to change the sheet with which Lambda will interact, you would have to update two variables. The `SPREADSHEET_ID` can be extracted from the URL of the the sheet. The `SHEET_NAME` is the sheet name from inside the Google Sheet.
```bash
SPREADSHEET_ID=<spreadsheet_id>
SHEET_NAME=<sheet_name>
```

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
