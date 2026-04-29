from dataclasses import dataclass


@dataclass
class PageRequest:
    url: str


@dataclass
class PageResponse:
    url: str
    content: str


@dataclass
class ParsedResponse:
    body: str
    url: str
    is_blocked: bool


@dataclass
class ZyteProxy:
    pass
