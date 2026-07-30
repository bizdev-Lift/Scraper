import time
import requests
from config import settings
from logger import logger
from _types import SeamlessResult

class SeamlessAPI:
    BASE_API_ENDPOINT = "https://api.seamless.ai/api/client"
    SEAMLESS_BASE_API_VERSION = "v1"
    COMPANIES_ENRICH_ENDPOINT = f"{BASE_API_ENDPOINT}/{SEAMLESS_BASE_API_VERSION}/companies/research"
    COMPANIES_POLL_ENDPOINT = f"{BASE_API_ENDPOINT}/{SEAMLESS_BASE_API_VERSION}/companies/research/poll"
    HEADERS = {
        "Content-Type": "application/json",
        "Token": settings.seamless_api_key
    }
    DELAY_SECONDS      = 0.5   # pause between requests
    SEAMLESS_POLL_WAIT = 3     # seconds between each poll attempt
    SEAMLESS_MAX_POLLS = 10    # give up after this many polls per domain

    def __init__(self):
        pass
        
    def enrich_leads(self, domains) -> dict[str, SeamlessResult]:
        if not domains:
            return {}

        request_id = self.seamless_submit(domains)
        if not request_id:
            return self._empty_seamless(domains)
    
        # Step 3: poll for results (give research a head start first)
        time.sleep(self.SEAMLESS_POLL_WAIT)
        companies = self.seamless_poll(request_id, domains)
        if not companies:
            return self._empty_seamless(domains)
        
        logger.info(companies)
        return companies
        
        
    def seamless_submit(self, domains: list[str]) -> list:
        try:
            r = requests.post(
                self.COMPANIES_ENRICH_ENDPOINT,
                headers=self.HEADERS,
                json={"companies": [{"domain": domain} for domain in domains]}
            )     
            if r.status_code == 401:
                logger.error("[Seamless] 401 — check your API key")
            elif r.status_code == 422:
                logger.error(f"[Seamless] 422 — {r.text[:120]}")
            
            r.raise_for_status()
            return r.json().get("requestIds") or []
        except Exception:
            logger.exception("[Seamless] Research submit error")
    
    def seamless_poll(self, request_ids: str, domains: list[str]) -> dict[str, SeamlessResult]:
        """Poll until status is terminal, validate domain match, return company dict."""
        companies = {}
        retry = False
        for attempt in range(1, self.SEAMLESS_MAX_POLLS + 1):
            retry = False
            companies = {}
            try:
                r = requests.get(
                    self.COMPANIES_POLL_ENDPOINT,
                    headers=self.HEADERS,
                    params={"requestIds": request_ids},
                    timeout=15,
                )
                r.raise_for_status()
    
                response_json = r.json()
                
                if response_json.get("success", False):
                    results = r.json().get("data") or []
                    if not results:
                        break
        
                    for item, requested_domain in zip(results, domains):
                        status = item.get("status")
            
                        if status == "done":
                            c = item.get("company") or {}
                            returned_domain = c.get("domain")
                            if not self._domains_match(requested_domain, returned_domain):
                                logger.info(f"[Seamless] Domain mismatch — asked for '{requested_domain}', "
                                    f"got '{returned_domain}'. Discarding result.")

                            companies[requested_domain] = self._format_seamless(c)
            
                        elif status == "missing": 
                            logger.info(f"[Seamless] Still researching... (poll {attempt}/{self.SEAMLESS_MAX_POLLS})")
                            time.sleep(self.SEAMLESS_POLL_WAIT)
                            retry = True
                            break
                        
                        elif status == "error":
                            logger.error(f"[Seamless] Research status: {status} (check credits/license if 'error') for {requested_domain}")
                            companies[requested_domain] = self._format_seamless({})
                    if retry:
                        continue
                    break
        
                logger.info(f"[Seamless] Still researching... (poll {attempt}/{self.SEAMLESS_MAX_POLLS})")
                time.sleep(self.SEAMLESS_POLL_WAIT)
    
            except Exception:
                logger.exception("[Seamless] Poll error")
                break
    
    
        # Guarantee every requested domain is represented, even if we
        # exhausted retries, errored out, or never got a "done" for it.
        for requested_domain in domains:
            companies.setdefault(requested_domain, self._format_seamless({}))

        return companies
    
           
    def _domains_match(self, requested: str, returned: str | None) -> bool:
        """Strip www. and trailing slashes before comparing domains."""
        if not returned:
            return False
        
        def clean(d):
            return d.lower().strip().lstrip("www.").rstrip("/")
        
        return clean(requested) == clean(returned)
  
    def _empty_seamless(self, domains: list[str]) -> dict:
        return {
            domain: SeamlessResult()         
            for domain in domains
        }
    
        
    @staticmethod
    def _format_seamless(output: dict) -> SeamlessResult:
        return SeamlessResult(            
            company_name=output.get("name"),
            website=output.get("domain"),
            industry=output.get("industries"),
            num_of_employees=output.get("staffCount"),
            revenue_range=output.get("revenueRange"),
            annual_revenue=output.get("annualRevenue"),
            company_state_abbr=output.get("location", {}).get("state"),
            company_postal_code=output.get("location", {}).get("postCode"),
            company_country=output.get("location", {}).get("country"),
            company_linkedin_url= output.get("linkedInProfileUrl"),
            short_description=output.get("description"),
        )
