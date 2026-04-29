#!/usr/bin/env python3
"""Test script to verify block detector improvements"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from scraper.block_detector import ScrapeBlockDetector

# Test case: Page with "too many requests" in normal content
normal_page_html = """
<!DOCTYPE html>
<html>
<head>
    <title>Password Reset - MyApp</title>
</head>
<body>
    <header>
        <nav>
            <a href="/">Home</a>
            <a href="/about">About</a>
        </nav>
    </header>
    <main>
        <h1>Password Reset Information</h1>
        <p>We received too many requests for password resets. Please try again in a few minutes.</p>
        <p>This is a security measure to protect your account from unauthorized access attempts.</p>
        <div class="help-section">
            <h2>Need Help?</h2>
            <p>If you're having trouble resetting your password, contact support.</p>
        </div>
    </main>
    <footer>
        <p>&copy; 2024 MyApp. All rights reserved.</p>
    </footer>
</body>
</html>
"""

# Test case: Actual rate limit blocking page
blocking_page_html = """
<!DOCTYPE html>
<html>
<head>
    <title>429 Too Many Requests</title>
</head>
<body>
    <h1>Rate Limit Exceeded</h1>
    <p>Too many requests. Please try again later.</p>
</body>
</html>
"""

# Test case: Actual rate limit page with minimal content
minimal_blocking_page_html = """
<!DOCTYPE html>
<html>
<head>
    <title>Error</title>
</head>
<body>
    <div>Too many requests. Rate limit exceeded.</div>
</body>
</html>
"""

detector = ScrapeBlockDetector()

print("=" * 80)
print("Test 1: Normal page with 'too many requests' in content")
print("=" * 80)
result1 = detector.is_blocked(normal_page_html)
print(f"Blocked: {result1['blocked']}")
print(f"Reason: {result1['reason']}")
print(f"All signals: {result1['all_signals']}")
print(f"Confidence: {result1['confidence']:.2f}")
print()

print("=" * 80)
print("Test 2: Actual blocking page with 'too many requests' in title")
print("=" * 80)
result2 = detector.is_blocked(blocking_page_html)
print(f"Blocked: {result2['blocked']}")
print(f"Reason: {result2['reason']}")
print(f"All signals: {result2['all_signals']}")
print(f"Confidence: {result2['confidence']:.2f}")
print()

print("=" * 80)
print("Test 3: Minimal content blocking page")
print("=" * 80)
result3 = detector.is_blocked(minimal_blocking_page_html)
print(f"Blocked: {result3['blocked']}")
print(f"Reason: {result3['reason']}")
print(f"All signals: {result3['all_signals']}")
print(f"Confidence: {result3['confidence']:.2f}")
print()

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"✓ Test 1 (Normal page):    Blocked={result1['blocked']} (Expected: False)")
print(f"✓ Test 2 (Blocking title): Blocked={result2['blocked']} (Expected: True)")
print(f"✓ Test 3 (Minimal block):  Blocked={result3['blocked']} (Expected: True)")
