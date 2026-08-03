import json
import logging
import os
import random
import time
from typing import Any, Final, Optional

import requests

from _types import DomainResponse
from config import settings
from io_operations.google_sheets import GoogleSheetsHandler
from llm._types import GeminiChatCompletion, GeminiResponse
from llm.image_parsing import create_image_parts
from llm.utils import clean_llm_response_json_data

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Custom exception for LLM operations."""

    pass


class LLMHelper:
    PROMPT_DIRECTORY: Final[str] = "llm/prompts"
    MAIN_PROMPT: Final[str] = "main_prompt"
    REVENUE_PROMPT: Final[str] = "revenue_prompt"

    def __init__(self, workflow_mode: str, sheets_handler: GoogleSheetsHandler):
        self.model_name = settings.model_name
        if workflow_mode == "regular" and settings.override_model_name:
            self.model_name = settings.override_model_name

        self.base_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent?key={settings.gemini_api_key}"
        )
        self.sheets_handler = sheets_handler

    def process(
        self,
        url: str,
        input_html: str,
        page_name: str = "Homepage",
    ) -> tuple[Optional[DomainResponse], Optional[list[str]]]:
        """Process content with LLM with retry logic."""
        completion: Optional[dict] = None
        for _ in range(2):
            completion = self.get_summary(
                input_html,
                self.MAIN_PROMPT,
                page_name=page_name,
                company_url=url,
                thinking_level="MEDIUM",
            )
            if not completion.get("error"):
                break

            logger.error(f"Gemini API failure for {url}. Retrying...")
            time.sleep(random.randint(5, 10))

        if not completion or completion.get("error"):
            logger.error(f"Max retries reached for {url}.")
            return None, None

        output_json, chat_completion = self.parse_llm_response(url, page_name, completion)
        if not output_json or not chat_completion:
            return None, None

        # Data cleanup
        if "reasoning" in output_json:
            logger.info(f"Reasoning: {output_json.pop('reasoning')}")

        links = output_json.pop("links", [])

        for key, value in output_json.items():
            if isinstance(value, list):
                output_json[key] = ",".join(value)

        self._log_usage(url, page_name, chat_completion)
        return DomainResponse(**output_json), links

    def _log_usage(self, url: str, page_name: str, chat_completion: Any) -> None:
        prompt_tokens = chat_completion.prompt_tokens()
        completion_tokens = chat_completion.completion_tokens()
        cost = chat_completion.cost(prompt_tokens, completion_tokens)
        if settings.environment != "dev":
            self.sheets_handler.update_stats(
                [url, page_name, prompt_tokens, completion_tokens, cost, self.model_name]
            )
        logger.info(
            f"Tokens used for {url} ({page_name}): P:{prompt_tokens}, C:{completion_tokens}"
        )

    def process_google_search(
        self, url: str, company_url: str, input_html: str, page_name: str
    ) -> Optional[float]:
        """Process Google Search results for revenue extraction."""
        completion = self.get_summary(input_html, self.REVENUE_PROMPT)
        if completion.get("error"):
            logger.error(f"Gemini API error for revenue search: {completion}")
            return None

        output_json, chat_completion = self.parse_llm_response(url, page_name, completion)
        if not output_json or not chat_completion:
            return None

        revenue = output_json.get("revenue")
        if not revenue or revenue == "null":
            return None

        self._log_usage(company_url, page_name, chat_completion)
        try:
            return float(revenue)
        except (ValueError, TypeError):
            logger.warning(f"Could not convert revenue to float: {revenue}")
            return None

    def parse_llm_response(
        self, url: str, page_name: str, completion: dict
    ) -> tuple[Optional[dict], Optional[GeminiChatCompletion]]:
        completion_response = GeminiResponse.from_dict(completion)
        if not completion_response:
            logger.error(f"Invalid Gemini response for {url} ({page_name}).")
            return None, None

        chat_completion = GeminiChatCompletion(completion_response)
        response_data = clean_llm_response_json_data(chat_completion.completion())
        try:
            return json.loads(response_data), chat_completion
        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to decode JSON for {url} ({page_name}): {e}\nResponse: {response_data}"
            )
            return None, None

    def get_summary(
        self,
        input_html: str,
        prompt_name: str,
        page_name: Optional[str] = None,
        company_url: Optional[str] = None,
        thinking_level: str = "MINIMAL",
        temperature: float = 1.0,
    ) -> dict:
        image_parts = (
            create_image_parts(company_url, input_html)
            if page_name == "Homepage" and company_url
            else []
        )
        prompt = self.read_prompt(prompt_name).replace("{{CONTENT}}", input_html)
        if company_url:
            prompt = prompt.replace("{{SITE_URL}}", company_url)

        response = self.make_gemini_request(prompt, image_parts, thinking_level, temperature)
        logger.info(f"Gemini response status: {response.status_code}")
        return response.json()

        # TODO: This can be used in case of debugging Purposes.
        # with open("response.json") as f:
        #     data = f.read()
        # f.close()
        # return json.loads(data)

    def read_prompt(self, prompt_name: str) -> str:
        prompt_path = os.path.join(self.PROMPT_DIRECTORY, prompt_name)
        with open(prompt_path, "r", encoding="utf-8") as file:
            return file.read()

    def make_gemini_request(
        self,
        input_content: str,
        image_parts: list[Any],
        thinking_level: str = "MINIMAL",
        temperature: float = 0.2,
    ) -> requests.Response:
        contents = [{"parts": [{"text": input_content}, *image_parts]}]
        payload = {
            "system_instruction": {
                "parts": [
                    {
                        "text": (
                            "You are a precise data extraction assistant. "
                            "You follow instructions exactly as written. "
                            "You never infer, assume, or fill in missing information. "
                            "If evidence is not explicitly present in the provided HTML, "
                            "you return the default negative value. "
                            "You output raw JSON only — "
                            "no markdown code fences, no json, no wrappers."
                        )
                    }
                ]
            },
            "contents": contents,
            "tools": [{"google_search": {}}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "text/plain",
                "thinkingConfig": {"thinkingLevel": thinking_level},
            },
        }
        return requests.post(
            self.base_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=60,
        )
