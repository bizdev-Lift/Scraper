"""
Tags to remove

- svg
- style
- link
- noscript
- script tags with src defined.
"""

from clear_html.formatted_text.cleaner import BodyCleaner
from lxml.html import HtmlElement, HTMLParser, fromstring

from scraper._types import PageResponse

cleaner = BodyCleaner(
    scripts=False,
    meta=False,
    style=True,
    inline_style=True,
    add_nofollow=True,
    kill_tags=(
        "input",
        "video",
        "cite",
        "symbol",
        "br",
        "svg",
        "noscript",
    ),
    allow_tags=(
        "img",
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "code",
        "pre",
        "ul",
        "ol",
        "table",
        "dl",
        "script",
        "link",
        "aside",
        "footer",
        "li",
        "a",
    ),
)

# TODO: Remove script tags manually that have src defined.
html_parser = HTMLParser(encoding="utf-8", collect_ids=False)


def load_tree(response: PageResponse) -> HtmlElement:
    tree = fromstring(html=response.content.encode("utf-8"), parser=html_parser)
    return tree


def parser(tree: HtmlElement) -> HtmlElement:
    tree = remove_script_tags(tree)
    tree = remove_hidden_elements(tree)
    cleaner(tree)
    return tree


def remove_script_tags(tree: HtmlElement) -> HtmlElement:
    """
    Remove script tags from the HTML tree.
    """
    for script in tree.xpath("//script"):
        script.getparent().remove(script)

    return tree


def remove_hidden_elements(tree: HtmlElement) -> HtmlElement:
    """
    Removes elements that are hidden via:
    1. HTML 'hidden' attribute
    2. aria-hidden='true' attribute
    3. Inline styles (display: none or visibility: hidden)
    """

    # This XPath covers:
    # - Literal 'hidden' attribute
    # - aria-hidden='true'
    # - Inline styles (using translate to handle case-insensitivity and spacing)
    xpath_query = (
        "//*["
        "@hidden or "
        "@aria-hidden='true' or "
        "contains(translate(@style, ' ', ''), 'display:none') or "
        "contains(translate(@style, ' ', ''), 'visibility:hidden')"
        "]"
    )

    for element in tree.xpath(xpath_query):
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    return tree
