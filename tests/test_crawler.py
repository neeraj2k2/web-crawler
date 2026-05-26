"""
Unit tests: heuristic detection, parser, classifier — no network required.
Integration tests: marked with @pytest.mark.integration — require network + `pytest -m integration`.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bs4 import BeautifulSoup

from app.crawler.fetcher import _needs_playwright, _try_static, UnsupportedContentTypeError, HttpError
from app.crawler.parser import parse
from app.crawler.classifier import classify_page_type, extract_topics
from app.crawler.robots import is_allowed
from app.crawler.utils import resolve_redirect


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------------------
# Playwright fallback heuristic
# ---------------------------------------------------------------------------

STATIC_ARTICLE_HTML = """
<html lang="en">
<head><title>How to Introduce Your Indoorsy Friend to the Outdoors</title></head>
<body>
<article>
  <h1>How to Introduce Your Indoorsy Friend to the Outdoors</h1>
  <p>Getting outside can be daunting for someone who has never camped before.
  Here are ten practical tips to help ease your friend into the great outdoors
  without overwhelming them on the first trip. Start with a short day hike on
  a well-marked trail and bring plenty of snacks. Comfort matters more than
  challenge on the first outing. Choose a trail that is well-marked and not too
  strenuous for a first-timer. Bring layers of clothing since weather can change
  quickly in the mountains. Make sure to pack enough water and high-energy snacks
  like trail mix or granola bars. A good pair of hiking shoes makes a significant
  difference in comfort and safety on uneven terrain outdoors.</p>
</article>
</body>
</html>
"""

SPA_SHELL_HTML = """
<html>
<head><title>Amazon.com</title></head>
<body>
<div id="root"></div>
<script>window.__INITIAL_STATE__ = {};</script>
</body>
</html>
"""

NEXT_JS_SHELL_HTML = """
<html>
<head><title>Loading...</title></head>
<body>
<div id="__next"></div>
<script id="__NEXT_DATA__" type="application/json">{"props":{}}</script>
</body>
</html>
"""

NOSCRIPT_HTML = """
<html>
<head><title>Shop Toasters</title></head>
<body>
<noscript>You need to enable JavaScript to run this application and view products.</noscript>
</body>
</html>
"""


class TestNeedsPlaywright:
    def test_static_article_does_not_trigger(self):
        assert _needs_playwright(STATIC_ARTICLE_HTML, "http://blog.rei.com/camp/article") is False

    def test_react_root_triggers(self):
        assert _needs_playwright(SPA_SHELL_HTML, "https://amazon.com/dp/B009") is True

    def test_next_data_triggers(self):
        assert _needs_playwright(NEXT_JS_SHELL_HTML, "https://example.com/product") is True

    def test_noscript_with_content_triggers(self):
        assert _needs_playwright(NOSCRIPT_HTML, "https://example.com/shop") is True

    def test_short_body_triggers(self):
        thin_html = "<html><head><title>My Site</title></head><body><p>Hi</p></body></html>"
        assert _needs_playwright(thin_html, "https://example.com/page") is True

    def test_empty_title_triggers(self):
        html = "<html><head><title></title></head><body>" + ("word " * 600) + "</body></html>"
        assert _needs_playwright(html, "https://example.com/page") is True

    def test_domain_only_title_triggers(self):
        html = "<html><head><title>example.com</title></head><body>" + ("word " * 600) + "</body></html>"
        assert _needs_playwright(html, "https://example.com/page") is True

    def test_js_required_string_triggers(self):
        html = (
            "<html><head><title>App</title></head><body>"
            + ("word " * 600)
            + "<p>Please enable JavaScript to continue.</p></body></html>"
        )
        assert _needs_playwright(html, "https://example.com/app") is True


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

PRODUCT_HTML = """
<html lang="en">
<head>
  <title>Cuisinart CPT-122 Compact 2-Slice Toaster</title>
  <meta name="description" content="Features 6 toasting shades and a slide-out crumb tray.">
  <meta name="keywords" content="toaster, cuisinart, kitchen appliance">
  <meta property="og:title" content="Cuisinart CPT-122">
  <meta property="og:image" content="https://images.example.com/toaster.jpg">
  <link rel="canonical" href="https://www.amazon.com/dp/B009GQ034C">
  <script type="application/ld+json">
  {"@context":"https://schema.org","@type":"Product","name":"Cuisinart CPT-122"}
  </script>
</head>
<body>
  <h1>Cuisinart CPT-122 Compact 2-Slice Toaster</h1>
  <h2>Product Details</h2>
  <p>Compact design fits easily on any countertop. Six shade settings for perfect toasting.</p>
</body>
</html>
"""


class TestParser:
    def test_extracts_title(self):
        result = parse(PRODUCT_HTML, "https://www.amazon.com/dp/B009GQ034C")
        assert result["metadata"]["title"] == "Cuisinart CPT-122 Compact 2-Slice Toaster"

    def test_extracts_description(self):
        result = parse(PRODUCT_HTML, "https://www.amazon.com/dp/B009GQ034C")
        assert "6 toasting shades" in result["metadata"]["description"]

    def test_extracts_keywords(self):
        result = parse(PRODUCT_HTML, "https://www.amazon.com/dp/B009GQ034C")
        assert "toaster" in result["metadata"]["keywords"]

    def test_extracts_og_image(self):
        result = parse(PRODUCT_HTML, "https://www.amazon.com/dp/B009GQ034C")
        assert result["metadata"]["og_image"] == "https://images.example.com/toaster.jpg"

    def test_extracts_canonical(self):
        result = parse(PRODUCT_HTML, "https://www.amazon.com/dp/B009GQ034C")
        assert result["metadata"]["canonical_url"] == "https://www.amazon.com/dp/B009GQ034C"

    def test_extracts_schema_type(self):
        result = parse(PRODUCT_HTML, "https://www.amazon.com/dp/B009GQ034C")
        assert result["metadata"]["schema_type"] == "Product"

    def test_extracts_h1(self):
        result = parse(PRODUCT_HTML, "https://www.amazon.com/dp/B009GQ034C")
        assert "Cuisinart CPT-122 Compact 2-Slice Toaster" in result["content"]["h1"]

    def test_extracts_h2(self):
        result = parse(PRODUCT_HTML, "https://www.amazon.com/dp/B009GQ034C")
        assert "Product Details" in result["content"]["h2"]

    def test_word_count_positive(self):
        result = parse(PRODUCT_HTML, "https://www.amazon.com/dp/B009GQ034C")
        assert result["content"]["word_count"] > 0

    def test_missing_meta_returns_none(self):
        html = "<html><head><title>Test</title></head><body><p>" + "word " * 100 + "</p></body></html>"
        result = parse(html, "https://example.com/")
        assert result["metadata"]["description"] is None
        assert result["metadata"]["og_title"] is None

    def test_lang_extracted(self):
        result = parse(PRODUCT_HTML, "https://www.amazon.com/dp/B009GQ034C")
        assert result["metadata"]["lang"] == "en"


# ---------------------------------------------------------------------------
# Page type classifier
# ---------------------------------------------------------------------------

class TestClassifyPageType:
    def test_schema_product(self):
        assert classify_page_type("https://amazon.com/dp/B123", "Product", _soup("<html><body></body></html>")) == "product"

    def test_schema_article(self):
        assert classify_page_type("https://rei.com/blog/post", "BlogPosting", _soup("<html><body></body></html>")) == "article"

    def test_schema_news(self):
        assert classify_page_type("https://cnn.com/news/story", "NewsArticle", _soup("<html><body></body></html>")) == "news"

    def test_schema_search_results(self):
        assert classify_page_type("https://amazon.com/s", "SearchResultsPage", _soup("<html><body></body></html>")) == "search_results"

    def test_url_homepage(self):
        assert classify_page_type("https://amazon.com/", None, _soup("<html><body></body></html>")) == "homepage"

    def test_url_product_path(self):
        assert classify_page_type("https://shop.com/product/toaster-123", None, _soup("<html><body></body></html>")) == "product"

    def test_url_blog_path(self):
        assert classify_page_type("https://rei.com/blog/how-to-camp", None, _soup("<html><body></body></html>")) == "article"

    def test_url_search_query(self):
        assert classify_page_type("https://amazon.com/search?q=toaster", None, _soup("<html><body></body></html>")) == "search_results"

    def test_url_utility_faq(self):
        assert classify_page_type("https://example.com/faq", None, _soup("<html><body></body></html>")) == "utility"

    def test_heuristic_price_signal(self):
        html = '<html><body><span itemprop="price">$29.99</span></body></html>'
        assert classify_page_type("https://example.com/item", None, _soup(html)) == "product"

    def test_heuristic_article_tag(self):
        html = '<html><body><article><time datetime="2025-01-01">Jan 1</time><p>Content</p></article></body></html>'
        assert classify_page_type("https://example.com/story/slug", None, _soup(html)) == "article"


# ---------------------------------------------------------------------------
# SSRF protection — CrawlRequest URL validation
# ---------------------------------------------------------------------------

from app.api.models import CrawlRequest
from pydantic import ValidationError


class TestSSRFProtection:
    def _make_request(self, url: str):
        return CrawlRequest(url=url)

    def test_public_url_allowed(self):
        req = self._make_request("https://www.example.com/page")
        assert str(req.url).startswith("https://")

    def test_localhost_blocked(self):
        with pytest.raises(ValidationError, match="internal addresses"):
            self._make_request("http://localhost/admin")

    def test_loopback_ip_blocked(self):
        with pytest.raises(ValidationError, match="not permitted"):
            self._make_request("http://127.0.0.1/secret")

    def test_gcp_metadata_endpoint_blocked(self):
        with pytest.raises(ValidationError, match="not permitted"):
            self._make_request("http://169.254.169.254/computeMetadata/v1/")

    def test_private_range_blocked(self):
        with pytest.raises(ValidationError, match="not permitted"):
            self._make_request("http://192.168.1.1/admin")

    def test_non_http_scheme_blocked(self):
        with pytest.raises(ValidationError):
            self._make_request("ftp://example.com/file")


# ---------------------------------------------------------------------------
# robots.is_allowed
# ---------------------------------------------------------------------------

def _make_robots_response(status_code: int, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    return response


class TestRobots:
    @pytest.mark.asyncio
    async def test_allowed_when_no_disallow(self):
        client = AsyncMock()
        client.get.return_value = _make_robots_response(200, "User-agent: *\nDisallow: /secret/")
        assert await is_allowed("https://example.com/page", client) is True

    @pytest.mark.asyncio
    async def test_disallowed_when_path_blocked(self):
        client = AsyncMock()
        client.get.return_value = _make_robots_response(200, "User-agent: *\nDisallow: /")
        assert await is_allowed("https://example.com/page", client) is False

    @pytest.mark.asyncio
    async def test_fail_open_on_404(self):
        client = AsyncMock()
        client.get.return_value = _make_robots_response(404)
        assert await is_allowed("https://example.com/page", client) is True

    @pytest.mark.asyncio
    async def test_fail_open_on_network_error(self):
        client = AsyncMock()
        client.get.side_effect = Exception("DNS resolution failed")
        assert await is_allowed("https://example.com/page", client) is True

    @pytest.mark.asyncio
    async def test_allowed_path_not_in_disallow(self):
        client = AsyncMock()
        client.get.return_value = _make_robots_response(
            200, "User-agent: *\nDisallow: /secret/\nDisallow: /admin/"
        )
        assert await is_allowed("https://example.com/blog/post", client) is True


# ---------------------------------------------------------------------------
# utils.resolve_redirect
# ---------------------------------------------------------------------------

class TestResolveRedirect:
    def _no_redirect_response(self, url: str, status: int = 200) -> MagicMock:
        r = MagicMock()
        r.url = url
        r.status_code = status
        r.is_redirect = False
        return r

    def _redirect_response(self, location: str, status: int = 301) -> MagicMock:
        r = MagicMock()
        r.status_code = status
        r.is_redirect = True
        r.next_request = MagicMock()
        r.next_request.url = location
        r.headers = {"location": location}
        return r

    @pytest.mark.asyncio
    async def test_returns_original_when_no_redirect(self):
        client = AsyncMock()
        client.get.return_value = self._no_redirect_response("https://example.com/page", 200)

        url, status = await resolve_redirect("https://example.com/page", client)
        assert url == "https://example.com/page"
        assert status == 200

    @pytest.mark.asyncio
    async def test_returns_final_url_and_status_after_redirect(self):
        initial = self._redirect_response("https://www.example.com/new-path", 301)
        final = self._no_redirect_response("https://www.example.com/new-path", 200)
        client = AsyncMock()
        client.get.side_effect = [initial, final]

        url, status = await resolve_redirect("http://example.com/old-path", client)
        assert url == "https://www.example.com/new-path"
        assert status == 301

    @pytest.mark.asyncio
    async def test_returns_first_hop_when_follow_fails(self):
        initial = self._redirect_response("https://www.rei.com/blog/post", 301)
        client = AsyncMock()
        client.get.side_effect = [initial, Exception("ReadTimeout")]

        url, status = await resolve_redirect("http://blog.rei.com/post", client)
        assert url == "https://www.rei.com/blog/post"
        assert status == 301

    @pytest.mark.asyncio
    async def test_returns_original_and_none_on_exception(self):
        client = AsyncMock()
        client.get.side_effect = Exception("connection refused")

        url, status = await resolve_redirect("https://example.com/page", client)
        assert url == "https://example.com/page"
        assert status is None


# ---------------------------------------------------------------------------
# fetcher error paths and retry logic
# ---------------------------------------------------------------------------

def _make_httpx_response(status_code: int, content_type: str = "text/html", text: str = "<html></html>") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"content-type": content_type}
    response.text = text
    response.url = "https://example.com/page"
    response.history = []
    response.is_success = 200 <= status_code < 300
    response.is_client_error = 400 <= status_code < 500
    response.is_server_error = 500 <= status_code < 600
    return response


class TestFetcherErrorPaths:
    @pytest.mark.asyncio
    async def test_404_raises_http_error(self):
        client = AsyncMock()
        client.get.return_value = _make_httpx_response(404)
        with pytest.raises(HttpError) as exc_info:
            await _try_static("https://example.com/page", client)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_503_raises_http_error(self):
        client = AsyncMock()
        client.get.return_value = _make_httpx_response(503)
        with pytest.raises(HttpError) as exc_info:
            await _try_static("https://example.com/page", client)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_403_returns_none_for_playwright_fallback(self):
        client = AsyncMock()
        client.get.return_value = _make_httpx_response(403)
        html, status = await _try_static("https://example.com/page", client)
        assert html is None
        assert status is None

    @pytest.mark.asyncio
    async def test_429_returns_none_for_playwright_fallback(self):
        client = AsyncMock()
        client.get.return_value = _make_httpx_response(429)
        html, status = await _try_static("https://example.com/page", client)
        assert html is None
        assert status is None

    @pytest.mark.asyncio
    async def test_non_html_content_type_raises(self):
        client = AsyncMock()
        client.get.return_value = _make_httpx_response(200, content_type="application/pdf")
        with pytest.raises(UnsupportedContentTypeError):
            await _try_static("https://example.com/file.pdf", client)

    @pytest.mark.asyncio
    async def test_200_returns_html(self):
        client = AsyncMock()
        client.get.return_value = _make_httpx_response(200, text="<html><body>content</body></html>")
        html, status = await _try_static("https://example.com/page", client)
        assert html == "<html><body>content</body></html>"
        assert status == 200

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self):
        from app.config import settings
        client = AsyncMock()
        client.get.side_effect = Exception("connection reset")

        with patch("app.crawler.fetcher.asyncio.sleep", new_callable=AsyncMock):
            html, status = await _try_static("https://example.com/page", client)

        assert html is None
        assert client.get.call_count == settings.max_attempts

    @pytest.mark.asyncio
    async def test_no_retry_on_http_error(self):
        client = AsyncMock()
        client.get.return_value = _make_httpx_response(404)
        with pytest.raises(HttpError):
            await _try_static("https://example.com/page", client)
        assert client.get.call_count == 1


# ---------------------------------------------------------------------------
# classifier.extract_topics
# ---------------------------------------------------------------------------

class TestExtractTopics:
    def _make_mock_nlp(self, chunks: list[str]) -> MagicMock:
        mock_chunks = []
        for text in chunks:
            chunk = MagicMock()
            chunk.text = text
            mock_chunks.append(chunk)
        doc = MagicMock()
        doc.noun_chunks = mock_chunks
        nlp = MagicMock()
        nlp.return_value = doc
        return nlp

    def _make_mock_model(self, keywords: list[tuple[str, float]]) -> MagicMock:
        model = MagicMock()
        model.extract_keywords.return_value = keywords
        return model

    def test_returns_topics_from_noun_chunks(self):
        nlp = self._make_mock_nlp(["compact toaster", "shade settings", "crumb tray"])
        model = self._make_mock_model([("compact toaster", 0.85), ("shade settings", 0.72)])

        topics = extract_topics(
            model=model,
            nlp=nlp,
            metadata={"title": "Cuisinart Compact Toaster", "description": None,
                      "og_description": None, "h1": [], "h2": []},
            content={"h1": ["Cuisinart Compact Toaster"], "h2": [], "body_text_preview": ""},
            extracted_text="Compact toaster with 7 shade settings and a removable crumb tray.",
            top_k=5,
        )

        assert topics == ["compact toaster", "shade settings"]
        model.extract_keywords.assert_called_once()

    def test_falls_back_to_ngrams_when_no_noun_chunks(self):
        nlp = self._make_mock_nlp([])
        model = self._make_mock_model([("ai jobs", 0.80)])

        topics = extract_topics(
            model=model,
            nlp=nlp,
            metadata={"title": "AI and Jobs", "description": None,
                      "og_description": None, "h1": [], "h2": []},
            content={"h1": [], "h2": [], "body_text_preview": ""},
            extracted_text="Artificial intelligence is changing jobs.",
            top_k=5,
        )

        assert topics == ["ai jobs"]
        call_kwargs = model.extract_keywords.call_args
        assert "keyphrase_ngram_range" in call_kwargs.kwargs

    def test_returns_empty_when_combined_text_too_short(self):
        nlp = self._make_mock_nlp([])
        model = self._make_mock_model([])

        topics = extract_topics(
            model=model,
            nlp=nlp,
            metadata={"title": "", "description": None, "og_description": None,
                      "h1": [], "h2": []},
            content={"h1": [], "h2": [], "body_text_preview": ""},
            extracted_text=None,
            top_k=5,
        )

        assert topics == []
        model.extract_keywords.assert_not_called()

    def test_uses_body_preview_when_no_extracted_text(self):
        nlp = self._make_mock_nlp(["tech workers"])
        model = self._make_mock_model([("tech workers", 0.78)])

        topics = extract_topics(
            model=model,
            nlp=nlp,
            metadata={"title": "AI at Work", "description": None,
                      "og_description": None, "h1": [], "h2": []},
            content={"h1": ["AI at Work"], "h2": [], "body_text_preview": "Tech workers use AI daily."},
            extracted_text=None,
            top_k=5,
        )

        assert topics == ["tech workers"]

    def test_description_and_h2_never_included_in_keybert_input(self):
        """Description (meta) and h2 headings are excluded from KeyBERT input."""
        nlp = self._make_mock_nlp(["toaster"])
        model = self._make_mock_model([("toaster", 0.80)])

        extract_topics(
            model=model,
            nlp=nlp,
            metadata={
                "title": "Cuisinart CPT-122 Toaster",
                "description": "Online Shopping for Blenders, Juicers, Ovens and more",
                "og_description": None,
            },
            content={
                "h1": ["Cuisinart CPT-122 Toaster", "About this item"],
                "h2": ["Similar items", "Customer reviews", "Price"],
                "body_text_preview": "",
            },
            extracted_text="Compact toaster with 7 shade settings.",
            top_k=5,
        )

        combined_input = model.extract_keywords.call_args.args[0]
        # meta description excluded — og_description is None, meta desc is not used
        assert "Blenders" not in combined_input
        assert "Juicers" not in combined_input
        # h2 headings excluded
        assert "Similar items" not in combined_input
        assert "Customer reviews" not in combined_input
        # title is always included
        assert "Cuisinart CPT-122 Toaster" in combined_input


# ---------------------------------------------------------------------------
# Integration tests — run with: pytest -m integration
# ---------------------------------------------------------------------------

TEST_URLS = [
    ("https://www.amazon.com/Cuisinart-CPT-122-Compact-2-SliceToaster/dp/B009GQ034C", "product", None),
    (
        "http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/",
        "article",
        "www.rei.com returns HTTP 403 via Akamai Bot Manager for all non-browser clients including curl. "
        "Bypassing requires residential proxies — documented in Part 2 architecture.",
    ),
    ("https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai", "news", None),
]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("url,expected_type,xfail_reason", TEST_URLS)
async def test_crawl_live_url(url, expected_type, xfail_reason):
    if xfail_reason:
        pytest.xfail(xfail_reason)

    import httpx
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "http://localhost:8080/crawl",
            json={"url": url, "respect_robots_txt": False},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["classification"]["page_type"] == expected_type
    assert len(data["classification"]["topics"]) > 0
    assert data["metadata"]["title"] is not None
