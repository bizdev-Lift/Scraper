import re
from typing import List, Dict, Set
from dataclasses import dataclass


@dataclass
class BlockingPattern:
    keywords: List[str]
    min_matches: int = 1
    case_sensitive: bool = False


class ScrapeBlockDetector:
    def __init__(self):
        # Patterns that can be checked in full HTML (very specific to blocking pages)
        self.blocking_patterns = {
            "cloudflare": BlockingPattern(
                [
                    "cloudflare",
                    "cf-ray",
                    "checking your browser",
                    "enable javascript and cookies",
                    "ddos protection",
                    "please wait while we verify",
                ],
                min_matches=2,
            ),
        }

        # Context-sensitive patterns - only checked in title/h1 or minimal content
        # These keywords can appear in normal page content so need strict context
        self.context_sensitive_patterns = {
            "bot_detection": BlockingPattern(
                [
                    "bot detected",
                    "automated traffic detected",
                    "suspicious activity detected",
                    "verify you are human",
                    "verifying you are human",
                    "prove you are human",
                    "attention required! | cloudflare",
                    "sorry, you have been blocked",
                ],
                min_matches=1,
            ),
            "rate_limiting": BlockingPattern(
                [
                    "too many requests",
                    "rate limit exceeded",
                    "rate limit",
                    "request limit exceeded",
                ],
                min_matches=1,
            ),
        }

        # Only check these in title/h1 tags - more specific phrases
        self.title_indicators = [
            "access denied",
            "403 forbidden",
            "403 error",
            "429 too many requests",
            "just a moment",
            "attention required",
            "security check required",
            "under maintenance",
            "site unavailable",
            "attention required! | cloudflare",
        ]

    def is_blocked(
        self, html_content: str, url: str = "", status_code: int = 200
    ) -> Dict:
        if not html_content or len(html_content.strip()) < 50:
            return {"blocked": True, "reason": "empty_response", "confidence": 0.9}

        html_lower = html_content.lower()

        # Check HTTP status codes
        if status_code in [403, 429, 503, 521, 522, 523, 524]:
            return {
                "blocked": True,
                "reason": f"http_status_{status_code}",
                "confidence": 1.0,
            }

        blocking_signals = []
        confidence_score = 0.0

        # Check blocking patterns (safe to check in full HTML - very specific)
        for pattern_name, pattern in self.blocking_patterns.items():
            matches = self._count_pattern_matches(html_lower, pattern)
            if matches >= pattern.min_matches:
                blocking_signals.append(pattern_name)
                confidence_score += 0.3 * min(matches / pattern.min_matches, 2.0)

        # Extract title and H1 for context-sensitive checking
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html_lower, re.DOTALL)
        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html_lower, re.DOTALL)

        title_or_h1_text = ""
        if title_match:
            title_or_h1_text += title_match.group(1).strip() + " "
        if h1_match:
            title_or_h1_text += h1_match.group(1).strip()

        # Check if page has minimal content (to allow context-sensitive patterns on full page)
        body_match = re.search(r"<body[^>]*>(.*?)</body>", html_lower, re.DOTALL)
        body_text_content = ""
        if body_match:
            body_text_content = re.sub(r"<[^>]+>", "", body_match.group(1)).strip()

        is_minimal_page = len(body_text_content) < 200

        # Check context-sensitive patterns (only in title/h1 or minimal content pages)
        for pattern_name, pattern in self.context_sensitive_patterns.items():
            # First check in title/h1
            if title_or_h1_text:
                matches = self._count_pattern_matches(title_or_h1_text, pattern)
                if matches >= pattern.min_matches:
                    blocking_signals.append(f"{pattern_name}_title")
                    confidence_score += 0.4
                    continue  # Don't double-count

            # If page is minimal (< 200 chars), also check in full content
            if is_minimal_page:
                matches = self._count_pattern_matches(html_lower, pattern)
                if matches >= pattern.min_matches:
                    blocking_signals.append(f"{pattern_name}_minimal")
                    confidence_score += 0.3

        # Check title indicators (already have title_or_h1_text from above)
        if title_or_h1_text:
            title_signals = [
                indicator
                for indicator in self.title_indicators
                if indicator in title_or_h1_text
            ]
            if title_signals:
                blocking_signals.extend(
                    [f"title_{signal.replace(' ', '_')}" for signal in title_signals]
                )
                confidence_score += 0.5

        # # Check for minimal content (already extracted body_text_content above)
        # if len(body_text_content) < 1000:
        #     blocking_signals.append("minimal_content")
        #     confidence_score += 0.3

        # Check for JavaScript-heavy pages (potential bot detection)
        script_count = len(re.findall(r"<script[^>]*>", html_lower))
        total_content = len(re.sub(r"<[^>]+>", "", html_lower).strip())

        if script_count > 5 and total_content < 500:
            blocking_signals.append("js_heavy_minimal_content")
            confidence_score += 0.3

        # Check for common blocking page structures
        if self._has_blocking_page_structure(html_lower):
            blocking_signals.append("blocking_page_structure")
            confidence_score += 0.4

        is_blocked = len(blocking_signals) > 0 and confidence_score >= 0.3

        return {
            "blocked": is_blocked,
            "reason": blocking_signals[0] if blocking_signals else "none",
            "all_signals": blocking_signals,
            "confidence": min(confidence_score, 1.0),
        }

    def _count_pattern_matches(
        self, html_content: str, pattern: BlockingPattern
    ) -> int:
        matches = 0
        content = html_content if pattern.case_sensitive else html_content.lower()

        for keyword in pattern.keywords:
            search_term = keyword if pattern.case_sensitive else keyword.lower()
            if search_term in content:
                matches += 1

        return matches

    def _has_blocking_page_structure(self, html_content: str) -> bool:
        """
        Strict detection of blocking page structures to minimize false positives.
        Only returns True when multiple strong indicators are present.
        """
        score = 0

        # Strong indicators (higher weight) - These are very specific to blocking pages
        strong_patterns = [
            # Cloudflare challenge page specific elements
            (r'<div[^>]*id="cf-wrapper"', 2),
            (r'<div[^>]*id="challenge-(?:running|error-text|form)"', 2),
            (r'class="[^"]*cf-browser-verification[^"]*"', 2),
            (r'<script[^>]*src="[^"]*\/cdn-cgi\/challenge-platform\/', 2),
            # Full-page captcha (not just form elements)
            (r"<body[^>]*>\s*<(?:div|iframe)[^>]*(?:recaptcha|hcaptcha)", 2),
            (r'<div[^>]*class="[^"]*g-recaptcha[^"]*"[^>]*data-callback', 2),
            # Perimeter X / PerimeterX bot detection
            (r'<script[^>]*src="[^"]*perimeterx\.net', 2),
            (r"_pxAppId", 2),
            # DataDome detection
            (r'<script[^>]*src="[^"]*datadome\.co', 2),
            (r"window\.ddjskey", 2),
            # Generic bot challenge pages (very minimal content)
            (
                r"<body[^>]*>\s*<(?:div|main)[^>]*>\s*<(?:h1|h2)[^>]*>(?:just a moment|checking|verifying|please wait)",
                2,
            ),
        ]

        # Moderate indicators - Need to be combined with other signals
        moderate_patterns = [
            # Cloudflare-specific classes that are less common in regular sites
            (r'class="[^"]*cf-(?:error-details|alert-box|highlight)', 1),
            # Challenge/verify in specific contexts (not just any form)
            (r'<div[^>]*id="challenge-[^"]*"[^>]*>.*?(?:cloudflare|verify|bot)', 1),
            (r'<form[^>]*id="challenge-form"[^>]*action="[^"]*challenge', 1),
            # Meta refresh with very short delay (0-5 seconds) combined with minimal content
            (r'<meta[^>]*http-equiv="refresh"[^>]*content="[0-5];', 1),
        ]

        # Check strong patterns
        for pattern, weight in strong_patterns:
            if re.search(pattern, html_content, re.IGNORECASE | re.DOTALL):
                score += weight

        # Check moderate patterns
        for pattern, weight in moderate_patterns:
            if re.search(pattern, html_content, re.IGNORECASE | re.DOTALL):
                score += weight

        # Additional context checks - verify it's truly a blocking page
        # Check if page lacks normal content elements (legitimate pages usually have these)
        has_navigation = bool(
            re.search(
                r'<(?:nav|header)[^>]*>|<[^>]*(?:class|id)="[^"]*(?:nav|menu|header)',
                html_content,
                re.IGNORECASE,
            )
        )
        has_footer = bool(
            re.search(
                r'<footer[^>]*>|<[^>]*(?:class|id)="[^"]*footer',
                html_content,
                re.IGNORECASE,
            )
        )
        has_main_content = bool(
            re.search(
                r'<(?:main|article)[^>]*>|<[^>]*(?:class|id)="[^"]*(?:main|content|article)',
                html_content,
                re.IGNORECASE,
            )
        )

        # Get actual text content (excluding scripts and styles)
        text_content = re.sub(
            r"<script[^>]*>.*?</script>",
            "",
            html_content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text_content = re.sub(
            r"<style[^>]*>.*?</style>",
            "",
            text_content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text_content = re.sub(r"<[^>]+>", "", text_content)
        text_content = text_content.strip()

        # If page has very little text content and no normal page structure, increase score
        if len(text_content) < 300 and not (
            has_navigation or has_footer or has_main_content
        ):
            score += 1

        # Require a score of at least 2 to indicate blocking
        # This means we need either:
        # - One strong indicator (weight 2)
        # - Two moderate indicators (weight 1 each)
        # - One moderate indicator + minimal content context
        return score >= 2

    def add_custom_pattern(self, name: str, pattern: BlockingPattern):
        self.blocking_patterns[name] = pattern

    def remove_pattern(self, name: str):
        if name in self.blocking_patterns:
            del self.blocking_patterns[name]
