import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Optional

import tldextract
from yarl import URL

from _types import ApolloResult, DomainInput, DomainResponse, SeamlessResult
from apollo.companies_search import ApolloAPI
from config import settings
from io_operations.google_sheets import (
    GoogleSheetsHandler,
    ProductionSheetStrategy,
    RegularSheetStrategy,
)
from llm.llm_helpers import LLMHelper
from logger import logger
from scraper._types import PageRequest
from scraper.bot_scraper import BotScraper
from scraper.generic_scraper import GenericScraper
from seamless.companies_search import SeamlessAPI


@dataclass
class ScrapeResult:
    body: Optional[str]
    domain_url: Optional[str]
    is_blocked: bool


class MainExecutor:
    def __init__(self, workflow_mode: str = "regular") -> None:
        bucket_name = os.environ.get("AWS_BUCKET_NAME")
        handler = GoogleSheetsHandler(settings.spreadsheet_info)
        self.strategy = (
            ProductionSheetStrategy(handler)
            if workflow_mode == "production"
            else RegularSheetStrategy(handler)
        )
        self.scraper = GenericScraper(s3_bucket_name=bucket_name)
        self.llm_helper = LLMHelper(handler)
        self.bot_scraper = BotScraper(s3_bucket_name=bucket_name)
        self.apollo_api = ApolloAPI()
        self.seamless_api = SeamlessAPI()

    def run(self) -> None:
        records = self.strategy.get_records()
        apollo_results = {}
        seamless_results = {}
        if self.strategy.pre_enrich:
            domains = [record.company_url for record in records]
            apollo_results = self.apollo_api.enrich_leads(domains)
            seamless_results = self.seamless_api.enrich_leads(domains)
        for record in records:
            if self.strategy.check_seen and self.strategy.is_seen(record):
                self.strategy.on_skip(record)
                continue
            try:
                output = self._process_record(record)
                if output:
                    if self.strategy.pre_enrich:
                        output.apollo_result = apollo_results.get(
                            record.company_url, ApolloResult()
                        )
                        output.seamless_result = seamless_results.get(
                            record.company_url, SeamlessResult()
                        )
                    self.strategy.on_success(record, output)
            except Exception:
                logger.exception(
                    f"Error while parsing domain {record.company_url}. Please visit it again."
                )

            time.sleep(10)

        self.strategy.on_complete(records)

    def _get_default_summary(self) -> DomainResponse:
        """Create default summary response for failed processing."""
        return DomainResponse(
            hq_phone_no="",
            website_availability="No",
            hq_address_listed="No",
            b2c_sales="No",
            b2b_sales="No",
            industry_classification="N/A",
            ecommerce_platform="N/A",
            lead_status="Unqualified - Website Down",
            apollo_result=ApolloResult(),
            seamless_result=SeamlessResult(),
        )

    def _process_record(self, record: DomainInput) -> Optional[DomainResponse]:
        """Process a single record and return the summary, or None on failure."""
        default_summary = self._get_default_summary()

        logger.info(f"Processing url={record.company_url}")

        page_requests = [
            PageRequest(url=f"http://{record.company_url}"),
            PageRequest(url=f"http://www.{record.company_url}"),
            PageRequest(url=f"https://{record.company_url}"),
            PageRequest(url=f"https://www.{record.company_url}"),
        ]
        result = self.process(page_requests, get_first_successful_response=True)
        domain_url = result.domain_url.rstrip("/") if result.domain_url else result.domain_url

        if not result.body:
            logger.error(f"Unable to scrape url={record.company_url}. Returning default summary.")
            self.strategy.on_error(record, default_summary)
            return None

        if result.is_blocked:
            logger.error(f"url={record.company_url} has been blocked. Returning default summary.")
            default_summary.lead_status = "Unqualified - Website Blocked"
            self.strategy.on_error(record, default_summary)
            return None

        website_url = self._determine_website_url(record.company_url, result.domain_url)
        redirected_to: Optional[str] = None
        if not website_url:
            logger.error(
                f"The website {record.company_url} redirected to "
                "another domain. Treating as wrong domain."
            )
            website_url = result.domain_url
            redirected_to = domain_url

        final_summary_json, links = self.llm_helper.process(website_url, result.body)

        if not final_summary_json or final_summary_json.website_availability == "No":
            logger.error(
                f"Unable to extract summary for url={record.company_url}. "
                "Returning default summary."
            )
            if not final_summary_json:
                default_summary.lead_status = "Error: LLM Failed"
            self.strategy.on_error(record, default_summary)
            return None

        about_contact_summary_json: Optional[DomainResponse] = None
        if self._needs_additional_scraping(final_summary_json):
            about_contact_summary_json = self._scrape_additional_pages(
                record, links, website_url, final_summary_json
            )

        revenue = self.fetch_revenue(company_url=website_url)
        merged_summary = self.merge_summary_outputs(
            final_summary_json, about_contact_summary_json, revenue
        )

        if self.strategy.track_old_lead_status:
            if isinstance(record.old_lead_status, str) and "LLM Failed" in record.old_lead_status:
                merged_summary.old_lead_status = record.old_lead_status

        if redirected_to:
            merged_summary.redirected_to = redirected_to

        logger.info(f"Summary extracted against url={website_url}.")
        return merged_summary

    def fetch_revenue(self, company_url: str) -> Optional[float]:
        base_url = URL("https://www.google.com/search")
        params = {"q": f"ZoomInfo annual revenue of {company_url}"}
        final_url = base_url.with_query(params)
        page_requests = [PageRequest(url=str(final_url))]
        parsed_responses, _ = self.scraper.run(
            page_requests,
            use_proxy=True,
            enable_browser_mode=True,
            retry=True,
            timeout=30,
            save_to_s3=False,
        )
        if not parsed_responses:
            logger.error(f"Unable to scrape url={final_url}.")
            return None

        return self.llm_helper.process_google_search(
            final_url, company_url, parsed_responses[0].body, "GoogleRevenueSearch"
        )

    def _needs_additional_scraping(self, summary: DomainResponse) -> bool:
        """Check if additional scraping is needed based on missing information."""
        return any(
            x in ["No", ""]
            for x in [
                summary.hq_phone_no,
                summary.hq_address_listed,
                summary.b2c_sales,
                summary.b2b_sales,
            ]
        )

    def _scrape_additional_pages(
        self,
        record: DomainInput,
        links: list[str],
        website_url: str,
        initial_summary: DomainResponse,
    ) -> DomainResponse:
        """Scrape additional pages (about us, contact us) for missing information."""
        logger.info("Some attributes missing from homepage — scraping about/contact pages.")

        if not links:
            logger.warning(f"No links extracted from homepage for url={website_url}.")
            return initial_summary

        page_requests = self._build_page_requests(links, website_url)
        result = self.process(page_requests)
        if not result.body or result.is_blocked:
            logger.error(f"Unable to scrape about/contact page for url={website_url}. Skipping.")
            return initial_summary

        about_contact_summary_json, _ = self.llm_helper.process(
            website_url,
            result.body,
            page_name="About Us, Contact Us",
        )

        return about_contact_summary_json

    def _build_page_requests(self, links: list[str], website_url: str) -> list[PageRequest]:
        """Build page requests from extracted links."""
        return [PageRequest(url=self._build_full_url(link, website_url)) for link in links]

    def _determine_website_url(self, company_url: str, domain_url: Optional[str]) -> Optional[str]:
        """Determine the base website URL; returns None if domain has changed."""
        if not domain_url:
            return company_url

        domain_obj = tldextract.extract(domain_url)
        company_obj = tldextract.extract(company_url)

        if domain_obj.domain.lower() != company_obj.domain.lower():
            return None

        return domain_url

    def _build_full_url(self, link: str, website_url: str) -> str:
        """Convert a relative link to a full URL."""
        if link.startswith("http"):
            return link
        elif link.startswith("/"):
            return f"{website_url}{link}"
        else:
            return f"{website_url}/{link}"

    def process(
        self,
        page_requests: list[PageRequest],
        use_bot: bool = False,
        retry: bool = True,
        get_first_successful_response: bool = False,
        _depth: int = 0,
    ) -> ScrapeResult:
        if _depth > 1:
            return ScrapeResult(body=None, domain_url=None, is_blocked=False)

        if use_bot:
            self.bot_scraper.start()
            parsed_responses, domain_url = self.bot_scraper.run(
                page_requests,
                get_first_successful_response=get_first_successful_response,
            )
            self.bot_scraper.close()
        else:
            parsed_responses, domain_url = self.scraper.run(
                page_requests,
                retry=retry,
                get_first_successful_response=get_first_successful_response,
            )
            if not parsed_responses:
                return self.process(
                    page_requests,
                    use_bot=True,
                    get_first_successful_response=get_first_successful_response,
                    _depth=_depth + 1,
                )

        bodies = [r.body for r in parsed_responses]
        is_blocked = all(r.is_blocked for r in parsed_responses)
        return ScrapeResult(body="\n".join(bodies), domain_url=domain_url, is_blocked=is_blocked)

    def merge_summary_outputs(
        self,
        summary1: DomainResponse,
        summary2: Optional[DomainResponse],
        revenue: Optional[float],
    ) -> DomainResponse:
        if not summary2:
            if revenue:
                summary1.revenue = revenue
            summary1.lead_status = self.compute_lead_status(summary1, summary2)
            return summary1

        final_summary = {}
        fields1 = asdict(summary1)
        fields2 = asdict(summary2)

        for key, value in fields1.items():
            if value in ["No", "", "N/A"]:
                final_summary[key] = fields2[key]
            elif key == "hq_phone_no" and "@" in value:
                final_summary[key] = fields2[key]
            elif (
                key == "industry_classification"
                and value == "Other"
                and fields2[key] not in ["N/A", "Other"]
            ):
                final_summary[key] = fields2[key]
            elif key == "lead_status":
                final_summary["lead_status"] = self.compute_lead_status(summary1, summary2)
            else:
                final_summary[key] = value

        if revenue:
            final_summary["revenue"] = revenue

        return DomainResponse(**final_summary)

    def process_domain(
        self,
        domain_url: str,
        apollo_result: Optional[ApolloResult] = None,
        seamless_result: Optional[SeamlessResult] = None,
    ) -> Optional[DomainResponse]:
        """Process a single domain URL without strategy side effects."""
        record = DomainInput(row_no=0, company_url=domain_url)
        result = self._process_record(record)
        if not result:
            return result

        if self.strategy.pre_enrich:
            if result.redirected_to:
                redirected_to_domain = re.sub(r"http(s)?://(www\.)?", "", result.redirected_to)
                apollo_result = self.apollo_api.enrich_leads([redirected_to_domain])
                seamless_result = self.seamless_api.enrich_leads([redirected_to_domain])
                result.apollo_result = apollo_result.get(redirected_to_domain, ApolloResult())
                result.seamless_result = seamless_result.get(redirected_to_domain, SeamlessResult())
            else:
                result.apollo_result = apollo_result
                result.seamless_result = seamless_result
        return result

    def compute_lead_status(
        self, homepage_summary: DomainResponse, other_summary: Optional[DomainResponse]
    ) -> str:
        """Compute lead status based on summary attributes."""

        if homepage_summary.website_availability in ["No", "N/A"]:
            return "Unqualified - Website Down"

        is_us_based = homepage_summary.is_us_based == "Yes" or (
            other_summary is not None and other_summary.is_us_based == "Yes"
        )
        if not is_us_based:
            return "Unqualified - Non-US Based"

        bad_product_type = homepage_summary.bad_product_type == "Yes" or (
            other_summary is not None and other_summary.bad_product_type == "Yes"
        )
        if bad_product_type:
            return "Unqualified - Bad Product Type"

        b2c = homepage_summary.b2c_sales == "Yes" or (
            other_summary is not None and other_summary.b2c_sales == "Yes"
        )
        b2b = homepage_summary.b2b_sales == "Yes" or (
            other_summary is not None and other_summary.b2b_sales == "Yes"
        )
        if not b2c and not b2b:
            return "Unqualified - Junk Lead / No Shipping"

        return "Lift Prime"
