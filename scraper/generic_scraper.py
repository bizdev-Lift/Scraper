import re
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Final, Optional

import requests
import tldextract
from fake_useragent import UserAgent
from lxml.etree import tostring
from yarl import URL

from config import settings
from io_operations.s3_client import S3Client
from logger import logger
from scraper._types import PageRequest, PageResponse, ParsedResponse
from scraper.block_detector import ScrapeBlockDetector
from scraper.parser import load_tree, parser


class ScraperError(Exception):
    """Custom exception for scraping related errors."""

    pass


class GenericScraper:
    DEFAULT_HEADERS: Final[dict[str, str]] = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Connection": "keep-alive",
    }

    def __init__(self, s3_bucket_name: Optional[str] = None):
        self.ua = UserAgent()
        self.detector = ScrapeBlockDetector()
        self.s3_client: Optional[S3Client] = (
            S3Client(bucket_name=s3_bucket_name) if s3_bucket_name else None
        )

    def _prepare_request_args(self, url: str, use_proxy: bool, enable_browser_mode: bool) -> dict:
        """Prepare arguments for request based on proxy/browser mode."""
        headers = self.DEFAULT_HEADERS.copy()
        headers["User-Agent"] = self.ua.chrome

        if settings.zyte_enabled or use_proxy:
            json_payload = {"url": url}
            if enable_browser_mode:
                json_payload["browserHtml"] = True
            else:
                json_payload.update({"httpResponseBody": True, "followRedirect": True})

            return {
                "url": settings.zyte_url,
                "json": json_payload,
                "auth": (settings.zyte_api_key, ""),
                "headers": headers,
            }

        return {
            "url": url,
            "headers": headers,
        }

    def get_request(
        self,
        url: str,
        use_proxy: bool = False,
        enable_browser_mode: bool = False,
        retry: bool = False,
        timeout: Optional[float] = None,
    ) -> Optional[requests.Response]:
        """Fetch a single URL with retries."""
        timeout = timeout or settings.global_http_timeout
        request_args = self._prepare_request_args(url, use_proxy, enable_browser_mode)
        request_args["timeout"] = timeout

        func_to_call = requests.post if (settings.zyte_enabled or use_proxy) else requests.get

        for attempt in range(3 if retry else 1):
            try:
                response = func_to_call(**request_args)
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                logger.warning(f"Attempt {attempt + 1} failed for {url}: {e}")
                if not retry:
                    break

        return None

    def fetch_htmls(
        self,
        page_requests: list[PageRequest],
        use_proxy: bool = False,
        enable_browser_mode: bool = False,
        retry: bool = False,
        timeout: Optional[float] = None,
        get_first_successful_response: bool = False,
    ) -> tuple[list[PageResponse], Optional[str]]:
        page_responses: list[PageResponse] = []
        domain_url: Optional[str] = None

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_urls = {
                executor.submit(
                    self.get_request,
                    req.url,
                    use_proxy,
                    enable_browser_mode,
                    retry,
                    timeout,
                ): req.url
                for req in page_requests
            }

            for future in as_completed(future_to_urls):
                url = future_to_urls[future]
                try:
                    data = future.result()
                    if data:
                        response, domain_url = self.extract_response(url, data, use_proxy)
                        if "this site no longer supports insecure http" in response.content.lower():
                            continue

                        if get_first_successful_response:
                            return [response], domain_url
                        page_responses.append(response)
                    else:
                        page_responses.append(PageResponse(content="", url=url))
                except Exception as e:
                    logger.error(f"Error processing future for {url}: {e}")
                    page_responses.append(PageResponse(content="", url=url))

        return page_responses, domain_url

    def extract_response(
        self, url: str, response: requests.Response, use_proxy: bool = False
    ) -> tuple[PageResponse, str]:
        if settings.zyte_enabled or use_proxy:
            response_json = response.json()
            if "httpResponseBody" in response_json:
                content = b64decode(response_json["httpResponseBody"]).decode("utf-8")
            elif "browserHtml" in response_json:
                content = response_json["browserHtml"].strip()
            else:
                content = ""
        else:
            content = response.text.strip()

        return PageResponse(content=content, url=url), response.url

    def run(
        self,
        page_requests: list[PageRequest],
        use_proxy: bool = False,
        enable_browser_mode: bool = False,
        retry: bool = False,
        timeout: Optional[float] = None,
        get_first_successful_response: bool = False,
        save_to_s3: bool = True,
    ) -> tuple[list[ParsedResponse], Optional[str]]:
        """Run scraping sequence: fetch, parse, and store."""
        parsed_responses: list[ParsedResponse] = []
        page_responses, domain_url = self.fetch_htmls(
            page_requests,
            use_proxy,
            enable_browser_mode,
            retry,
            timeout,
            get_first_successful_response,
        )

        for page_response in page_responses:
            if not page_response.content:
                continue

            blocked_status = self.detector.is_blocked(page_response.content)
            self._save_raw_to_s3(page_response, save_to_s3)

            try:
                tree = load_tree(page_response)
                parser(tree)
                response_body = re.sub(r"\n|\t", "", tostring(tree, encoding="unicode"))

                self._save_parsed_to_s3(page_response, response_body, save_to_s3)

                if len(response_body) < 1000 and not blocked_status["blocked"]:
                    continue

                parsed_responses.append(
                    ParsedResponse(
                        body=response_body,
                        url=page_response.url,
                        is_blocked=blocked_status["blocked"],
                    )
                )
            except Exception as e:
                logger.error(f"Failed to parse {page_response.url}: {e}")
                parsed_responses.append(
                    ParsedResponse(
                        body="", url=page_response.url, is_blocked=blocked_status["blocked"]
                    )
                )

        return parsed_responses, domain_url

    def _save_raw_to_s3(self, response: PageResponse, save_to_s3: bool) -> None:
        if self.s3_client and save_to_s3:
            host, path = self.get_url_components(response.url)
            self.s3_client.write_raw_content(
                key=f"{host}/http-raw-{path}.html", content=response.content
            )

    def _save_parsed_to_s3(self, response: PageResponse, content: str, save_to_s3: bool) -> None:
        if self.s3_client and save_to_s3:
            host, path = self.get_url_components(response.url)
            self.s3_client.write_raw_content(key=f"{host}/http-parsed-{path}.html", content=content)

    def get_url_components(self, url: str) -> tuple[str, str]:
        url_host = tldextract.extract(url).top_domain_under_public_suffix
        url_obj = URL(url)
        url_path = url_obj.path.rstrip("/").lstrip("/")
        if not url_path:
            url_path = "homepage"

        return url_host, url_path
