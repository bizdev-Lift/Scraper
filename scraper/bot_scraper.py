import random
import re
import time
from typing import List, Optional, Tuple

import tldextract
from lxml.etree import tostring
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from yarl import URL

from io_operations.s3_client import S3Client
from logger import logger
from scraper._types import PageRequest, PageResponse, ParsedResponse
from scraper.block_detector import ScrapeBlockDetector
from scraper.parser import load_tree, parser


class BotScraper:
    """
    A class to scrape data from a website using Playwright.
    """

    def __init__(self, s3_bucket_name: str = None) -> None:
        self.playwright = None
        self.detector = ScrapeBlockDetector()
        if s3_bucket_name:
            self.s3_client = S3Client(bucket_name=s3_bucket_name)
        else:
            self.s3_client = None

    def start(self) -> None:
        """Initialize Playwright instance."""
        self.playwright = sync_playwright().start()

    def close(self) -> None:
        """
        Close the browser and Playwright instance.
        """
        if self.playwright:
            self.playwright.stop()

    def run(
        self,
        page_requests: List[PageRequest],
        get_first_successful_response: bool = False,
    ) -> Tuple[List[ParsedResponse], Optional[str]]:
        """
        Get the content of the page.
        """
        try:
            browser = self._create_browser()
            domain_url: Optional[str] = None
            page = self._setup_page(browser)
        except Exception as e:
            logger.exception(f"Exception while creating browser or context. Exception is: {e}")
            return [], None

        page_responses: List[ParsedResponse] = []
        for page_request in page_requests:
            response_body, blocked_status = self._scrape_single_page(page, page_request)
            if not response_body:
                continue

            if len(response_body) < 1000 and not blocked_status["blocked"]:
                continue

            if not domain_url:
                domain_url = page.url

            page_responses.append(
                ParsedResponse(
                    url=page.url,
                    body=response_body,
                    is_blocked=blocked_status["blocked"],
                )
            )

            if get_first_successful_response:
                break

        browser.close()
        return page_responses, domain_url

    def _create_browser(self) -> Browser:
        """Create and configure browser instance."""
        return self.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--single-process",
            ],
        )

    def _create_context(self, browser: Browser) -> BrowserContext:
        """Create browser context with viewport settings."""
        return browser.new_context(viewport={"width": 1920, "height": 1080})

    def _setup_page(self, browser: Browser) -> Page:
        """Setup page with stealth configuration."""
        page = browser.new_page()
        return page

    def _scrape_single_page(self, page: Page, page_request: PageRequest) -> tuple[str | None, dict]:
        """Scrape content from a single page."""
        page_content = ""
        url = page_request.url
        url_host, url_path = self.get_url_components(url)
        blocked_status = {"blocked": False}
        try:
            page.goto(url, wait_until="load")
            page_content = self._get_page_content(page)
            if self.s3_client:
                self.s3_client.write_raw_content(
                    key=f"{url_host}/bot-raw-{url_path}.html",
                    content=page_content,
                )
            blocked_status = self.detector.is_blocked(page_content)
            page_content = self._process_html_content(url, page_content)
            if self.s3_client:
                self.s3_client.write_raw_content(
                    key=f"{url_host}/bot-parsed-{url_path}.html",
                    content=page_content,
                )
            return page_content, blocked_status
        except Exception as e:
            logger.error(f"Exception while loading page content for url={url}. Exception is: {e}")

        return page_content, blocked_status

    def get_url_components(self, url: str) -> tuple[str, str]:
        url_host = tldextract.extract(url).top_domain_under_public_suffix
        url_obj = URL(url)
        url_path = url_obj.path.rstrip("/").lstrip("/")
        if not url_path:
            url_path = "homepage"

        return url_host, url_path

    def _get_page_content(self, page: Page) -> str:
        """Get page content with retry logic for short responses."""
        time.sleep(random.randint(5, 10))
        page_content = page.content()
        # if len(page_content) < 1000:
        #     time.sleep(random.randint(5, 10))
        #     page_content = page.content()
        return page_content

    def _process_html_content(self, url: str, page_content: str) -> str | None:
        """Process and clean HTML content."""
        tree = load_tree(PageResponse(url=url, content=page_content))
        # title = tree.xpath("//title/text()")
        # if title and "attention required" in title[0].lower():
        #     return None

        parser(tree)
        response_body = tostring(tree, encoding="unicode")
        response_body = re.sub(r"\n|\t", "", response_body)

        # if not response_body:
        #     logger.error(
        #         f"Failed to load HTML through playwright browser for url={url}"
        #     )
        #     return None

        return response_body
