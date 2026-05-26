import ipaddress
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, HttpUrl, field_validator

from app.crawler.models import Classification, Content, Metadata

_BLOCKED_HOSTNAMES = {"localhost", "localhost."}


class CrawlRequest(BaseModel):
    url: HttpUrl
    respect_robots_txt: bool = True

    @field_validator("url")
    @classmethod
    def url_must_be_safe(cls, v: HttpUrl) -> HttpUrl:
        if v.scheme not in ("http", "https"):
            raise ValueError("URL must start with http:// or https://")

        hostname = urlparse(str(v)).hostname or ""

        if hostname.lower() in _BLOCKED_HOSTNAMES:
            raise ValueError("Crawling internal addresses is not permitted")

        # If the hostname is a raw IP address, block private/reserved ranges.
        # This covers 127.x (loopback), 169.254.x (GCP metadata / link-local),
        # and 10.x / 172.16.x / 192.168.x (RFC-1918 private ranges).
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError(
                    f"Crawling private or reserved IP addresses is not permitted ({hostname})"
                )
        except ValueError as e:
            if "not permitted" in str(e):
                raise
            # hostname is not an IP address — allow it through
        return v


class CrawlResponse(BaseModel):
    url: str
    status: str
    crawl_timestamp: datetime
    fetcher_used: Optional[str] = None
    robots_txt_checked: bool = False
    robots_txt_allowed: Optional[bool] = None
    metadata: Optional[Metadata] = None
    content: Optional[Content] = None
    classification: Optional[Classification] = None
    error_code: Optional[str] = None
    message: Optional[str] = None
    http_status_code: Optional[int] = None
