from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import re

import tldextract
from yarl import URL
from config import settings
from scraper._types import PageRequest, PageResponse, ParsedResponse
from scraper.block_detector import ScrapeBlockDetector
from scraper.parser import load_tree, parser
from lxml.html import HtmlElement
from lxml.etree import tostring, ParserError
from fake_useragent import UserAgent
from logger import logger
from io_operations.s3_client import S3Client


class GenericScraper:
    HEADERS = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X x.y; rv:42.0) Gecko/20100101 Firefox/42.0",
        "Accept": "*/*",
        "Connection": "keep-alive",
    }

    def __init__(self, s3_bucket_name: str = None):
        self.ua = UserAgent()
        self.detector = ScrapeBlockDetector()
        if s3_bucket_name:
            self.s3_client = S3Client(bucket_name=s3_bucket_name)
        else:
            self.s3_client = None
        pass

    def get_request(
        self,
        url: str,
        use_proxy: bool = False,
        enable_browser_mode: bool = False,
        retry: bool = False,
        timeout: float | None = None,
    ) -> requests.Response | None:
        func_to_call = requests.get
        timeout = timeout if timeout else settings.global_http_timeout
        request_args = {
            "url": url,
            "timeout": timeout,
            "headers": self.HEADERS,
        }
        if settings.zyte_enabled or use_proxy:
            request_args["url"] = settings.zyte_url
            request_args["json"] = {"url": url}
            if enable_browser_mode:
                request_args["json"]["browserHtml"] = True
            else:
                request_args["json"].update({
                    "httpResponseBody": True,
                    "followRedirect": True,
                })
            request_args["auth"] = (settings.zyte_api_key, "")
            func_to_call = requests.post

        retry_count = 3
        while retry_count > 0:
            try:
                request_args["headers"]["User-Agent"] = self.ua.chrome
                response = func_to_call(**request_args)
                assert response.status_code == 200
                return response
            except Exception:
                logger.exception(
                    f"Exception while fetching URL: {url} with timeout={timeout}. "
                )
                if not retry:
                    break

                logger.info(f"Retrying to fetch URL: {url} with timeout={timeout}.")
                retry_count -= 1

        return None

    def fetch_htmls(
        self,
        page_requests: list[PageRequest],
        use_proxy: bool = False,
        enable_browser_mode: bool = False,
        retry: bool = False,
        timeout: float | None = None,
        get_first_successful_response: bool = False,
    ) -> tuple[list[PageResponse], str | None]:
        page_responses: list[PageResponse] = []
        domain_url: str | None = None

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_urls = {
                executor.submit(
                    self.get_request,
                    request.url,
                    use_proxy,
                    enable_browser_mode,
                    retry,
                    timeout,
                ): request.url
                for request in page_requests
            }
            for future in as_completed(future_to_urls):
                url = future_to_urls[future]
                data: requests.Response = future.result()
                if data:
                    response, domain_url = self.extract_response(url, data, use_proxy)
                    if "this site no longer supports insecure http" in response.content.lower():
                        continue

                    if get_first_successful_response:
                        return [response], domain_url
                else:
                    response = PageResponse(content="", url=url)

                page_responses.append(response)

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
        timeout: float | None = None,
        get_first_successful_response: bool = False,
        save_to_s3: bool = True,
    ):
        """
        - Get the HomePage and check if it has all the contents. If not, we cna get the about us and
        """
        parsed_responses: list[ParsedResponse] = []
        page_responses: list[PageResponse]
        domain_url: str | None = None
        page_responses, domain_url = self.fetch_htmls(
            page_requests,
            use_proxy,
            enable_browser_mode,
            retry,
            timeout,
            get_first_successful_response,
        )
        for page_response in page_responses:
            if page_response.content == "":
                continue

            blocked_status = self.detector.is_blocked(page_response.content)
            url_host, url_path = self.get_url_components(page_response.url)
            if self.s3_client and save_to_s3:
                self.s3_client.write_raw_content(
                    key=f"{url_host}/http-raw-{url_path}.html",
                    content=page_response.content,
                )

            try:
                tree: HtmlElement = load_tree(page_response)
            except ParserError:
                parsed_responses.append(
                    ParsedResponse(
                        body="",
                        url=page_response.url,
                        is_blocked=blocked_status["blocked"],
                    )
                )
                continue

            parser(tree)
            response_body = tostring(tree, encoding="unicode")
            response_body = re.sub(r"\n|\t", "", response_body)
            if self.s3_client and save_to_s3:
                self.s3_client.write_raw_content(
                    key=f"{url_host}/http-parsed-{url_path}.html",
                    content=response_body,
                )

            if len(response_body) < 1000 and not blocked_status["blocked"]:
                continue

            parsed_responses.append(
                ParsedResponse(
                    body=response_body,
                    url=page_response.url,
                    is_blocked=blocked_status["blocked"],
                )
            )

        return parsed_responses, domain_url

    def get_url_components(self, url: str) -> tuple[str, str]:
        url_host = tldextract.extract(url).top_domain_under_public_suffix
        url_obj = URL(url)
        url_path = url_obj.path.rstrip("/").lstrip("/")
        if not url_path:
            url_path = "homepage"

        return url_host, url_path
