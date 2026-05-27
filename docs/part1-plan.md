# Part 1 — Crawler Service: Plan & Design Decisions

## Overview

A REST API that accepts any URL, fetches the page, extracts HTML metadata, classifies the page type, and returns a ranked list of relevant topics. Deployed on GCP Cloud Run, multi-worker. No shared state between requests — each worker holds only the KeyBERT model in memory, loaded once at startup.

---

## Tech Stack

| Concern | Chosen | Alternatives | Decision rationale |
|---|---|---|---|
| Static HTTP client | httpx | requests, aiohttp | Async-native; `requests` is sync-only which blocks worker threads under concurrency |
| JS rendering | Playwright | Selenium, Splash | Selenium is flaky in containers; Splash requires a separate sidecar service |
| HTML parsing | BeautifulSoup4 + lxml | html.parser, html5lib | lxml is 3-5x faster than html.parser; html5lib is too strict for malformed real-world HTML |
| Structured data | extruct | Manual JSON-LD parsing | One call extracts JSON-LD, OpenGraph, microdata, RDFa simultaneously |
| Boilerplate removal | trafilatura | readability-lxml, newspaper3k | Handles both article and product page layouts; readability-lxml weak on product pages, newspaper3k is news-specific |
| robots.txt | urllib.robotparser | reppy | reppy needs a C extension that breaks Alpine-based Docker builds; stdlib is sufficient here |
| Topic extraction | KeyBERT | YAKE, LLMs | See section below |
| Deployment | GCP Cloud Run | AWS Lambda, ECS | See section below |

---

## Hard Decision: Two-Layer Fetcher & Playwright Fallback Detection

### The Problem

Amazon (one of the test URLs) is heavily JS-rendered — a plain `httpx` GET returns a skeleton with no product data. REI and CNN are server-rendered and need no browser. Always using Playwright would add 5-10s and full Chromium overhead to every request that doesn't need it.

### Decision: httpx first, Playwright on detected failure

```
httpx fetch (~1-2s)
    → run fallback detection heuristic on the response HTML
    → if any signal triggers → re-fetch with Playwright (~5-10s)
    → else use httpx result
```

### Fallback Detection — Exact Checks

After the httpx fetch, we inspect the HTML with BeautifulSoup. Playwright is triggered if **any one** of these is true:

**1. Visible text length < 500 characters**
Strip all tags, measure remaining text. Any real content page (product, article, blog) has hundreds of words. A JS shell has navigation boilerplate at best. Threshold: 500 chars.

**2. SPA framework fingerprints**
These strings/attributes appear in the raw HTML before JS executes:
- `<div id="root">` — React
- `<div id="app">` — Vue / generic SPA
- `data-reactroot` attribute anywhere in the DOM
- `__NEXT_DATA__` in a `<script>` tag — Next.js
- `ng-version` attribute — Angular
- `window.__nuxt__` in a script block — Nuxt.js

**3. `<noscript>` has substantial content (> 50 chars) AND visible text is below threshold**
Both conditions must be true. News and e-commerce sites (e.g. CNN) use `<noscript>` tags for analytics even when the page is fully server-rendered — checking noscript alone causes false positives on 5MB static pages. The visible text check gates the noscript signal so it only fires when the page genuinely has little content.

**4. Explicit JS requirement strings in body**
Case-insensitive search for:
- `"enable javascript"`
- `"javascript is required"`
- `"javascript must be enabled"`

**5. Empty or generic `<title>`**
If `<title>` is missing, empty, or matches only the bare domain name (`"Amazon.com"` with no product name), the page-specific content hasn't loaded. We compare the title against the extracted hostname — exact or near-match triggers fallback.

**Playwright wait strategy:** `domcontentloaded` + 2.5s explicit wait, not `networkidle`. Ad-heavy sites (CNN, Amazon) fire continuous analytics/tracking requests that prevent `networkidle` from ever settling — the page times out after the full timeout. `domcontentloaded` fires as soon as the DOM is parsed; the 2.5s wait gives JS frameworks time to render their initial content.

**False positive behavior:** If a sparse-but-static page (e.g., a login page) triggers Playwright, it still crawls correctly — just slower. A false positive costs ~5s; a false negative (missing content) breaks the crawl. We accept false positives.

---

## Hard Decision: Topic Extraction — KeyBERT over YAKE

YAKE is a statistical extractor — it scores n-grams by position, frequency, and co-occurrence. It has no concept of meaning. On a noisy page like Amazon (navigation, "Add to Cart", "Ships from", "Customer Reviews"), YAKE surfaces UI chrome as topics because those strings are frequent and prominent.

KeyBERT embeds the full document and each candidate keyphrase into the same vector space using a pre-trained BERT sentence-transformer, then ranks candidates by cosine similarity to the document. It finds phrases that are semantically representative of the content, not just statistically frequent.

**Text cleaning pipeline before KeyBERT:**
KeyBERT embeds the entire input document — boilerplate in means bad topics out. The document vector ends up representing "Amazon shopping UI" rather than the actual product. Two-pass cleaning before the text reaches KeyBERT:

```
Raw HTML
    → BS4 removes <script>, <style>, <nav>, <header>, <footer>, <aside>
      and elements matching class/id patterns: "cookie", "banner", "ad", "sidebar", "menu"
    → trafilatura extracts the main content zone (semantic pass, handles obfuscated class names)
    → clean text → KeyBERT
```

This cleaning applies **only to the KeyBERT input**. Metadata fields (`title`, `description`, `h1`, `og_*`, etc.) are extracted from the raw HTML and returned as-is.

Concrete difference on the Amazon toaster URL:

| YAKE | KeyBERT |
|---|---|
| `add to cart` | `kitchen appliances` |
| `amazon.com` | `compact toaster` |
| `ships from` | `browning control` |
| `2-slice toaster` | `2-slice toaster` |
| `customer reviews` | `defrost function` |

The BERT model is ~400MB and loads once at container startup. Per-request extraction time is ~200ms after warm-up. Cold start on Cloud Run is ~30s for the first request after idle — acceptable for a demo.

KeyBERT is not an LLM. It is an encoder-only model with no generative capability, runs fully offline, and costs nothing per request.

---

## Hard Decision: robots.txt — Implement, No Cache, Configurable

### Why configurable
Amazon's `robots.txt` disallows most third-party crawlers. Strict enforcement blocks the demo crawl entirely. We implement the check but make it a per-request flag:

```json
{
  "url": "https://www.amazon.com/...",
  "respect_robots_txt": false
}
```

Default: `true` (production-honest). The demo sets it to `false`.

### Implementation flow
```
respect_robots_txt = true
    → GET https://<domain>/robots.txt
    → parse with urllib.robotparser.RobotFileParser
    → check if our User-Agent is allowed for the target path
    → disallowed → return error { robots_txt_allowed: false }
    → allowed → proceed with fetch
```

If robots.txt fetch fails (404, timeout, connection error) → treat as **allowed**. This is the industry-standard convention: absence of a robots.txt means open access.

### Why no cache in Part 1
An in-memory cache breaks under multi-worker deployments — each worker has isolated memory, so every worker re-fetches anyway. The correct solution is a shared Redis cache keyed by domain. Adding Redis infrastructure for a demo is not worth it; a fresh fetch per request is one cheap HTTP call.

**For Part 2 (design doc):** Shared Redis cache with 24h TTL per domain. Recommended: Upstash Redis — serverless, HTTPS endpoint, no VPC connector needed, free tier (10K commands/day) covers most crawl patterns, ~$0.20/100K commands beyond that. GCP Memorystore requires a Serverless VPC Access connector to reach Cloud Run and costs ~$35/month minimum — only worth it if VPC infrastructure already exists.

---

## Hard Decision: Deployment — GCP Cloud Run over AWS Lambda

AWS Lambda has a 250MB deployment package limit. The Playwright Chromium binary alone is ~300MB. Making Lambda work requires a custom Lambda layer, a container image deployment (which bypasses the limit but adds complexity), and workarounds for the ephemeral `/tmp` filesystem. It works but it's non-trivial.

Cloud Run accepts any Docker image up to 32GB. Our image (Playwright + BERT model) is ~1.5GB — deploy it with one command, get a public HTTPS endpoint, and it scales to zero when idle.

---

## API Design

**`POST /crawl`**
```json
// Request
{
  "url": "https://www.amazon.com/Cuisinart-CPT-122/dp/B009GQ034C",
  "respect_robots_txt": false
}

// Response
{
  "url": "https://www.amazon.com/...",
  "status": "success",
  "crawl_timestamp": "2026-05-26T10:00:00Z",
  "fetcher_used": "playwright",
  "robots_txt_checked": true,
  "robots_txt_allowed": true,
  "metadata": {
    "title": "Cuisinart CPT-122 Compact 2-Slice Toaster",
    "description": "Features 6 toasting shades...",
    "keywords": ["toaster", "cuisinart", "kitchen"],
    "og_title": "...",
    "og_description": "...",
    "og_image": "https://...",
    "canonical_url": "https://...",
    "lang": "en",
    "robots": "index,follow",
    "schema_type": "Product"
  },
  "content": {
    "h1": ["Cuisinart CPT-122 Compact 2-Slice Toaster"],
    "h2": ["Product details", "Customer reviews"],
    "h3": [],
    "body_text_preview": "First 500 characters of clean body text...",
    "word_count": 1240
  },
  "classification": {
    "page_type": "product",
    "topics": [
      "kitchen appliances",
      "compact toaster",
      "browning control",
      "2-slice toaster",
      "cuisinart",
      "stainless steel",
      "defrost function",
      "toasting shades",
      "slide-out tray",
      "breakfast appliance"
    ]
  }
}
```

**`GET /health`**
```json
{ "status": "ok", "model_loaded": true }
```

**Error responses — all return a consistent shape:**

robots.txt blocked:
```json
{
  "url": "https://www.amazon.com/...",
  "status": "blocked",
  "error_code": "robots_txt_disallowed",
  "message": "Crawling disallowed by robots.txt for this path",
  "robots_txt_checked": true,
  "robots_txt_allowed": false
}
```

Timeout (static or Playwright):
```json
{
  "url": "https://www.example.com/...",
  "status": "error",
  "error_code": "timeout",
  "message": "Page did not respond within 30s",
  "fetcher_used": "playwright"
}
```

Non-200 HTTP response:
```json
{
  "url": "https://www.example.com/...",
  "status": "error",
  "error_code": "http_error",
  "message": "Remote server returned HTTP 404",
  "http_status_code": 404
}
```

Non-HTML content type:
```json
{
  "url": "https://www.example.com/file.pdf",
  "status": "error",
  "error_code": "unsupported_content_type",
  "message": "Expected text/html, got application/pdf"
}
```

Invalid URL (request validation):
```json
{
  "status": "error",
  "error_code": "invalid_url",
  "message": "URL must start with http:// or https://"
}
```

---

## Page Type Classification

### Why Classification Matters

For an SEO platform like BrightEdge, page type determines what signals are meaningful. Topic density matters differently on a product page vs a news article. A category page shouldn't be analyzed for long-form content. A search results page should typically be noindexed and flagged as such. Classifying the page type correctly gates everything downstream.

### Taxonomy

Eight types, chosen to cover the full surface of what a crawler encounters on commercial and editorial sites:

| Type | Why It's Distinct |
|---|---|
| `product` | Has price, SKU, availability, reviews. Primary content is the item itself, not prose. Topic extraction targets features and specifications. |
| `article` | Long-form prose with a clear author and topic. Primary SEO signal is content depth and keyword coverage. Includes how-to guides and evergreen blog posts. |
| `news` | Like article but time-sensitive — has a `datePublished` and decays in relevance. Treated differently in freshness-weighted ranking. |
| `category` | A listing of products or content items. Not a content page itself — its SEO value is in internal linking and faceted navigation structure. |
| `homepage` | Root domain page. Brand-level content, different optimization targets than deep pages. |
| `search_results` | Dynamically generated listing (e.g., `amazon.com/s?k=toaster`). Should almost always be `noindex` — flagging this is a direct SEO audit signal. |
| `landing_page` | Campaign or conversion page. Single dominant CTA, hero image, minimal navigation. Not article structure, not product schema. |
| `utility` | Contact, About, FAQ, Login, Signup pages. Low SEO priority, typically thin content. Worth identifying so they're deprioritized in topic analysis. |

### Detection — Priority Order

**1. JSON-LD `@type` (highest confidence)**

| JSON-LD type | Maps to |
|---|---|
| `Product`, `IndividualProduct` | `product` |
| `Article`, `BlogPosting`, `TechArticle`, `HowTo` | `article` |
| `NewsArticle`, `ReportageNewsArticle` | `news` |
| `ItemList`, `CollectionPage` | `category` |
| `SearchResultsPage` | `search_results` |
| `WebSite` or `WebPage` at path `/` | `homepage` |
| `FAQPage` | `utility` |
| `ContactPage`, `AboutPage` | `utility` |

**2. URL pattern matching**

| Pattern | Type |
|---|---|
| Path is `/` or empty | `homepage` |
| `/search`, `/s?`, `?q=`, `?query=`, `?keyword=` | `search_results` |
| `/category/`, `/c/`, `/collections/`, `/dept/` | `category` |
| `/dp/`, `/product/`, `/item/`, `/sku/` | `product` |
| `/blog/`, `/article/`, `/post/`, `/guide/` | `article` |
| `/news/`, `/press/` | `news` |
| `/about`, `/contact`, `/faq`, `/login`, `/signup` | `utility` |

**3. HTML heuristics (fallback)**

- Price element present (`$`, `£`, `€` near a number, or `itemprop="price"`) → `product`
- `<article>` tag + byline or `datePublished` meta → `article`
- Grid of repeated item cards (3+ similar `<li>` or `<div>` blocks with images + links) → `category`
- Single dominant CTA button, hero image, no article tag, no price → `landing_page`
- None of the above → `webpage` (catch-all, not in the 8 above)

---

## Project Structure

```
crawler/
├── app/
│   ├── main.py                  # FastAPI init, startup event (load KeyBERT model once)
│   ├── api/
│   │   └── routes.py            # /crawl, /health
│   ├── crawler/
│   │   ├── fetcher.py           # httpx fetch + fallback detection + Playwright
│   │   ├── robots.py            # robots.txt fetch, parse, compliance check
│   │   ├── parser.py            # BeautifulSoup metadata + content extraction
│   │   └── classifier.py        # KeyBERT topics + page type classification
│   ├── models/
│   │   └── schemas.py           # Pydantic request/response models
│   └── config.py                # Timeouts, user-agent, model name, top-k, robots default
├── tests/
│   └── test_crawler.py
├── Dockerfile
├── requirements.txt
├── .dockerignore
└── README.md
```

---

## Edge Cases

| Case | Handling |
|---|---|
| JS-rendered page | Playwright fallback via detection heuristic |
| Timeout | 10s httpx, 30s Playwright; return error with `status: timeout` |
| Non-200 response | Return error with HTTP status code |
| Non-HTML content (PDF, image) | Detect via `Content-Type` header before parsing |
| Encoding issues | httpx and BeautifulSoup both auto-detect charset |
| Relative canonical URL | Resolved to absolute with `urllib.parse.urljoin` |
| Missing meta tags | All metadata fields nullable in schema |
| robots.txt fetch fails | Treat as allowed (missing = open, industry standard) |
| robots.txt disallows URL | Return `{ robots_txt_allowed: false }`, do not fetch page |

---

## What Is Not in Part 1

- No URL queue or batch processing (Part 2 design)
- No persistent storage (Part 2 design)
- No robots.txt caching of any kind — fresh fetch per request is intentional; in-memory cache is broken under multi-worker (isolated memory per worker), and adding Redis solely for this is unnecessary infra overhead for a demo. Shared Redis cache design is covered in Part 2.
- No API authentication
- No crawl rate limiting

---

## Summary of All Decisions

1. **httpx + Playwright fallback** — static fetch first, Playwright only when 5-point heuristic detects a JS-rendered shell
2. **KeyBERT over YAKE** — semantic similarity over statistical frequency; critical for noisy pages like Amazon where UI chrome dominates word counts; input is pre-cleaned via BS4 structural pass + trafilatura main content extraction before reaching the model
3. **robots.txt configurable** — implemented via `urllib.robotparser`, per-request `respect_robots_txt` flag, defaults to `true`; no cache in Part 1 (multi-worker safe), Redis (Upstash) deferred to Part 2
4. **extruct for structured data** — extracts JSON-LD, OpenGraph, microdata in one call; `@type` from JSON-LD is the primary page classification signal
5. **Rule-based page classification** — 8-type taxonomy (product, article, news, category, homepage, search_results, landing_page, utility); JSON-LD `@type` → URL patterns → HTML heuristics; deterministic and transparent. `search_results` detection is a direct SEO audit signal since those pages should be noindexed.
6. **GCP Cloud Run** — only viable serverless option; AWS Lambda's 250MB limit is incompatible with Playwright (~300MB) without significant workarounds
