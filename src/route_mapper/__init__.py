"""route_mapper: crawler modular para mapear las rutas internas de un sitio web."""

from __future__ import annotations

__version__ = "1.0.0"

from route_mapper.config import CrawlConfig
from route_mapper.crawler import Crawler
from route_mapper.models import CrawlResult, ExecutionMetadata, PageRecord

__all__ = [
    "CrawlConfig",
    "CrawlResult",
    "Crawler",
    "ExecutionMetadata",
    "PageRecord",
    "__version__",
]
