import requests
import base64
import json
import random
import os
import time

from typing import Union
from _types import DomainResponse
from config import settings
from llm._types import GeminiResponse, GeminiChatCompletion
from llm.image_parsing import create_image_parts
from llm.utils import _clean_llm_response_json_data
from logger import logger
from io_operations.google_sheets import GoogleSheetsHandler


class LLMHelper:
    PROMPT_DIRECTORY_PATH = "llm/prompts"
    MAIN_PROMPT = "main_prompt"
    REVENUE_PROMPT = "revenue_prompt"
    MODEL_NAME = ""

    def __init__(self, sheets_handler: GoogleSheetsHandler):
        self.setup_model()
        self.base_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.MODEL_NAME}:generateContent?key={settings.gemini_api_key}"
        )
        self.sheets_handler = sheets_handler

    def setup_model(self):
        if os.environ.get("MODEL_NAME"):
            self.MODEL_NAME = os.environ["MODEL_NAME"]
        else:
            self.MODEL_NAME = "gemini-2.0-flash"

    def process(
        self,
        url: str,
        input_html: str,
        page_name: str = "Homepage",
    ) -> Union[tuple[DomainResponse, list[str]], DomainResponse]:
        count = 2
        while count > 0:
            completion = self.get_summary(
                input_html,
                self.MAIN_PROMPT,
                page_name=page_name,
                company_url=url,
                thinking_level="MEDIUM",
            )
            completion_error = completion.get("error")
            if not completion_error:
                break

            logger.error(
                f"Gemini API is not available for url {url} against ({page_name}).\nError details: {completion}.\nTrying one more time."
            )
            time.sleep(
                random.randint(5, 10)
            )  # Wait for a random time between 5 to 10 seconds
            count -= 1
        else:
            logger.error(
                f"Max tries reached in trying Gemini for {url}. Skipping this record and moving on to next one!"
            )
            return None, None

        output_json, chat_completion = self.parse_llm_response(
            url, page_name, completion
        )

        if not chat_completion:
            return None, None

        if "reasoning" in output_json:
            reasoning = output_json.pop("reasoning")
            print(reasoning)

        if "links" in output_json:
            links = output_json.pop("links")
        else:
            links = []
            logger.warning(f"Unable to extract links from the response for url={url}.")

        prompt_tokens = chat_completion.prompt_tokens()
        completion_tokens = chat_completion.completion_tokens()
        cost = chat_completion.cost(prompt_tokens, completion_tokens)
        if settings.environment != "dev":
            self.sheets_handler.update_stats(
                [url, page_name, prompt_tokens, completion_tokens, cost]
            )
        logger.info(
            f"Total tokens used for url {url} against ({page_name}):\n"
            f"Prompt Tokens: {prompt_tokens}, Completion Tokens: {completion_tokens}"
        )
        return DomainResponse(**output_json), links

    def process_google_search(
        self, url: str, company_url: str, input_html: str, page_name: str
    ) -> Union[DomainResponse, None]:
        completion = self.get_summary(input_html, self.REVENUE_PROMPT)
        completion_error = completion.get("error")
        if completion_error:
            logger.error(
                f"Gemini API is not available for url {url} against ({page_name}).\nError details: {completion}.\nTrying one more time."
            )
            return None

        output_json, chat_completion = self.parse_llm_response(
            url, page_name, completion
        )

        if not output_json:
            logger.error(
                f"Gemini API returned an invalid response for url {url} against ({page_name})."
            )
            return None

        revenue = output_json.get("revenue")
        if not revenue or revenue == "null":
            return None

        prompt_tokens = chat_completion.prompt_tokens()
        completion_tokens = chat_completion.completion_tokens()
        cost = chat_completion.cost(prompt_tokens, completion_tokens)
        self.sheets_handler.update_stats(
            [company_url, page_name, prompt_tokens, completion_tokens, cost]
        )
        logger.info(
            f"Total tokens used for google_url {url}:\n"
            f"Prompt Tokens: {prompt_tokens}, Completion Tokens: {completion_tokens}"
        )
        return revenue

    def parse_llm_response(self, url, page_name, completion):
        completion_response = GeminiResponse.from_dict(completion)
        if not completion_response:

            logger.error(
                f"Gemini API returned an invalid response for url {url} against ({page_name})."
            )
            return None, None

        chat_completion = GeminiChatCompletion(completion_response)
        response_data = _clean_llm_response_json_data(chat_completion.completion())
        try:
            output_json = json.loads(response_data)
        except json.JSONDecodeError as e:
            logger.error(
                f"Gemini API returned an invalid JSON response for url {url} against ({page_name})."
            )
            logger.error(f"Response: {response_data}\nError: {e}")
            return None, None

        return output_json, chat_completion

    def get_summary(
        self,
        input_html: str,
        prompt_name: str,
        page_name: str | None = None,
        company_url: str | None = None,
        thinking_level: str = "MINIMAL",
        temperature: float = 1.0,
    ) -> dict:
        image_parts = []
        if page_name and page_name == "Homepage":
            image_parts = create_image_parts(company_url, input_html)

        prompt = self.read_prompt(prompt_name)
        prompt = prompt.replace("{{CONTENT}}", input_html)

        if company_url:
            prompt = prompt.replace("{{SITE_URL}}", company_url)

        response = self.make_gemini_request(
            prompt, image_parts, thinking_level=thinking_level, temperature=temperature
        )

        logger.info(response.status_code)
        logger.info(response.text)

        return response.json()

    def enforce_consistency(self, data: dict) -> dict:
        """
        Parses the model's JSON output and enforces that top-level b2c_sales
        and b2b_sales match their conclusion fields inside reasoning.
        Also enforces lead_status consistency with the corrected values.
        """

        reasoning = data.get("reasoning", {})
        changes = []

        # --- Enforce b2c_sales ---
        b2c_reasoning = reasoning.get("b2c_sales", {})
        if isinstance(b2c_reasoning, dict):
            b2c_conclusion = b2c_reasoning.get("b2c_sales_conclusion", "").strip()
            if b2c_conclusion in ("Yes", "No"):
                if data.get("b2c_sales") != b2c_conclusion:
                    changes.append(
                        f"b2c_sales overridden: '{data.get('b2c_sales')}' → '{b2c_conclusion}' "
                        f"(source: reasoning.b2c_sales.b2c_sales_conclusion)"
                    )
                    data["b2c_sales"] = b2c_conclusion

        # --- Enforce b2b_sales ---
        b2b_reasoning = reasoning.get("b2b_sales", {})
        if isinstance(b2b_reasoning, dict):
            b2b_keyword = b2b_reasoning.get("b2b_sales_keyword_found", "None").strip()
            b2b_verbatim = b2b_reasoning.get(
                "b2b_sales_verbatim_surrounding_text", "N/A"
            ).strip()
            b2b_conclusion = b2b_reasoning.get("b2b_sales_conclusion", "No").strip()

            # Contradiction check: keyword claimed found but no verbatim text to prove it
            # This means the model hallucinated the keyword — force everything to No
            if b2b_keyword != "None" and b2b_verbatim in ("N/A", "", "null", "None"):
                changes.append(
                    f"b2b_sales hallucination detected: keyword_found='{b2b_keyword}' "
                    f"but verbatim_surrounding_text='{b2b_verbatim}' — "
                    f"keyword cannot be found without verbatim proof. Forcing to No."
                )
                b2b_conclusion = "No"
                data["reasoning"]["b2b_sales"]["b2b_sales_keyword_found"] = "None"
                data["reasoning"]["b2b_sales"][
                    "b2b_sales_verbatim_surrounding_text"
                ] = "N/A"
                data["reasoning"]["b2b_sales"]["b2b_sales_location"] = "N/A"
                data["reasoning"]["b2b_sales"][
                    "b2b_sales_not_in_blog_or_product_name"
                ] = "N/A"
                data["reasoning"]["b2b_sales"]["b2b_sales_not_hidden"] = "N/A"
                data["reasoning"]["b2b_sales"]["b2b_sales_conclusion"] = "No"

            # Also check: verbatim text does not actually contain the claimed keyword
            elif b2b_keyword != "None" and b2b_verbatim != "N/A":
                if b2b_keyword.lower() not in b2b_verbatim.lower():
                    changes.append(
                        f"b2b_sales self-invalidation: keyword_found='{b2b_keyword}' "
                        f"not found as substring in verbatim_text='{b2b_verbatim}'. Forcing to No."
                    )
                    b2b_conclusion = "No"
                    data["reasoning"]["b2b_sales"]["b2b_sales_keyword_found"] = "None"
                    data["reasoning"]["b2b_sales"][
                        "b2b_sales_verbatim_surrounding_text"
                    ] = "N/A"
                    data["reasoning"]["b2b_sales"]["b2b_sales_location"] = "N/A"
                    data["reasoning"]["b2b_sales"][
                        "b2b_sales_not_in_blog_or_product_name"
                    ] = "N/A"
                    data["reasoning"]["b2b_sales"]["b2b_sales_not_hidden"] = "N/A"
                    data["reasoning"]["b2b_sales"]["b2b_sales_conclusion"] = "No"

            if data.get("b2b_sales") != b2b_conclusion:
                changes.append(
                    f"b2b_sales overridden: '{data.get('b2b_sales')}' → '{b2b_conclusion}' "
                    f"(source: reasoning.b2b_sales.b2b_sales_conclusion)"
                )
                data["b2b_sales"] = b2b_conclusion

        # --- Re-derive lead_status if b2b or b2c changed ---
        if changes:
            lead_status = data.get("lead_status", "")
            b2c = data["b2c_sales"]
            b2b = data["b2b_sales"]
            website_up = data.get("website_availability") == "Yes"

            # Only fix lead_status if it was Lift Prime but now both sales are No,
            # or if it was Junk Lead but now at least one sales field is Yes
            if b2c == "No" and b2b == "No" and lead_status == "Lift Prime":
                data["lead_status"] = "Unqualified – Junk Lead / No Shipping"
                changes.append(
                    "lead_status overridden: 'Lift Prime' → 'Unqualified – Junk Lead / No Shipping'"
                )
            elif (
                (b2c == "Yes" or b2b == "Yes")
                and website_up
                and lead_status == "Unqualified – Junk Lead / No Shipping"
            ):
                data["lead_status"] = "Lift Prime"
                changes.append(
                    "lead_status overridden: 'Unqualified – Junk Lead / No Shipping' → 'Lift Prime'"
                )

        return data, changes

    def read_prompt(self, prompt_name) -> str:
        with open(f"{self.PROMPT_DIRECTORY_PATH}/{prompt_name}", "r") as file:
            prompt = file.read()
        return prompt

    def make_gemini_request(
        self,
        input_content: str,
        image_parts: list[str],
        thinking_level: str = "MINIMAL",
        temperature: float = 0.2,
    ) -> requests.Response:
        contents = [{"parts": [{"text": input_content}, *image_parts]}]

        response = requests.post(
            self.base_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "system_instruction": {
                        "parts": [
                            {
                                "text": "You are a precise data extraction assistant. You follow instructions exactly as written. You never infer, assume, or fill in missing information. If evidence is not explicitly present in the provided HTML, you return the default negative value. You do not hallucinate keywords or text that are not literally present in the input. You output raw JSON only — no markdown code fences, no json, no  wrappers of any kind."
                            }
                        ]
                    },
                    "contents": contents,
                    "tools": [{"google_search": {}}],
                    "generationConfig": {
                        "temperature": temperature,
                        "responseMimeType": "text/plain",
                        "thinkingConfig": {
                            "thinkingLevel": thinking_level,
                        },
                    },
                }
            ),
        )
        return response
