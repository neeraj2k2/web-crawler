import logging
import re
from typing import Optional
from urllib.parse import urlparse

import spacy
from bs4 import BeautifulSoup  # used in classify_page_type type hint
from keybert import KeyBERT

logger = logging.getLogger("app.crawler.classifier")


def extract_topics(
    model: KeyBERT,
    nlp: spacy.language.Language,
    metadata: dict,
    content: dict,
    extracted_text: Optional[str],
    top_k: int = 10,
    min_score: float = 0.3,
) -> list[str]:
    """
    Extracts topics using KeyBERT n-gram ranking.
    Input: title + description + h1s + h2s + trafilatura body (capped 1000 chars).
    Whitespace normalised before KeyBERT to avoid newline artefacts from trafilatura.
    """
    logger.info("Starting topic extraction (top_k=%d)", top_k)

    structured_parts = [
        metadata.get("title") or "",
        metadata.get("og_description") or "",   # og_description only — meta description is often a generic
        " ".join(content.get("h1", [])),        # category blurb ("Online Shopping for Blenders, Juicers...")
                                                # h2s excluded — on product pages they're navigation headings,
                                                # not content ("Similar items", "Customer reviews", etc.)
    ]
    structured_text = " ".join(p for p in structured_parts if p).strip()
    logger.info("Structured input: %d chars", len(structured_text))

    if extracted_text:
        body_excerpt = extracted_text[:1000]
        logger.info("Body excerpt: trafilatura %d total chars, using first 1000", len(extracted_text))
    else:
        body_excerpt = content.get("body_text_preview") or ""
        logger.info("Body excerpt: body_text_preview %d chars", len(body_excerpt))

    logger.info("── Body excerpt ─────────────────────────────")
    logger.info("%s", body_excerpt[:500])
    logger.info("────────────────────────────────────────────")

    combined = re.sub(r"\s+", " ", f"{structured_text} {body_excerpt}").strip()

    if len(combined) < 20:
        logger.warning("Combined input too short — returning empty topics")
        return []

    logger.info("── KeyBERT input (%d chars) ─────────────────", len(combined))
    logger.info("%s", combined[:500])
    logger.info("────────────────────────────────────────────")

    keywords = model.extract_keywords(
        combined,
        keyphrase_ngram_range=(1, 2),
        stop_words="english",
        top_n=top_k,
        use_mmr=True,
        diversity=0.3,
    )

    logger.info("── KeyBERT output ───────────────────────────")
    for phrase, score in keywords:
        logger.info("  %-40s score: %.4f", phrase, score)
    logger.info("────────────────────────────────────────────")

    topics = [kw for kw, score in keywords if score >= min_score]
    filtered = len(keywords) - len(topics)
    if filtered:
        logger.info("Score filter (min=%.2f): dropped %d low-scoring topics", min_score, filtered)
    logger.info("Final topics: %s", topics)
    return topics


def classify_page_type(url: str, schema_type: Optional[str], soup: BeautifulSoup) -> str:
    """Priority: JSON-LD @type → URL patterns → HTML heuristics."""
    logger.info("Classifying page type for %s (schema_type=%r)", url, schema_type)

    # 1. JSON-LD schema type
    if schema_type:
        schema_map = {
            "Product": "product",
            "IndividualProduct": "product",
            "Article": "article",
            "BlogPosting": "article",
            "TechArticle": "article",
            "HowTo": "article",
            "NewsArticle": "news",
            "ReportageNewsArticle": "news",
            "ItemList": "category",
            "CollectionPage": "category",
            "SearchResultsPage": "search_results",
            "FAQPage": "utility",
            "ContactPage": "utility",
            "AboutPage": "utility",
        }
        if schema_type in schema_map:
            result = schema_map[schema_type]
            logger.info("Classification via JSON-LD @type '%s' → '%s'", schema_type, result)
            return result
        if schema_type in ("WebSite", "WebPage"):
            parsed = urlparse(url)
            if parsed.path in ("", "/"):
                logger.info("Classification via JSON-LD WebSite/WebPage at root path → 'homepage'")
                return "homepage"

    # 2. URL pattern matching
    parsed = urlparse(url)
    path = parsed.path.lower()
    query = parsed.query.lower()

    if schema_type:
        logger.info("JSON-LD @type %r not in schema map — trying URL patterns (path=%r)", schema_type, path)
    else:
        logger.info("No JSON-LD schema type present — trying URL patterns (path=%r)", path)

    if path in ("", "/"):
        logger.info("Classification via URL: root path → 'homepage'")
        return "homepage"
    if any(p in path for p in ["/search", "/s/"]) or any(
        q in query for q in ["q=", "query=", "keyword=", "k="]
    ):
        logger.info("Classification via URL: search pattern → 'search_results'")
        return "search_results"
    if any(p in path for p in ["/category/", "/c/", "/collections/", "/dept/", "/department/"]):
        logger.info("Classification via URL: category pattern → 'category'")
        return "category"
    if any(p in path for p in ["/dp/", "/product/", "/item/", "/sku/"]):
        logger.info("Classification via URL: product pattern → 'product'")
        return "product"
    if any(p in path for p in ["/blog/", "/article/", "/post/", "/guide/"]):
        logger.info("Classification via URL: article pattern → 'article'")
        return "article"
    if any(p in path for p in ["/news/", "/press/"]):
        logger.info("Classification via URL: news pattern → 'news'")
        return "news"
    if any(p in path for p in ["/about", "/contact", "/faq", "/login", "/signup"]):
        logger.info("Classification via URL: utility pattern → 'utility'")
        return "utility"

    # 3. HTML heuristics — use the soup passed in from parser (already parsed, no re-parse)
    logger.info("URL pattern classification failed — trying HTML heuristics")

    if soup.find(attrs={"itemprop": "price"}) or re.search(
        r"[\$\£\€]\s*\d+[.,]\d{2}", soup.get_text()
    ):
        logger.info("Classification via heuristic: price signal → 'product'")
        return "product"

    if soup.find("article") and (
        soup.find("time") or soup.find(attrs={"itemprop": "datePublished"})
    ):
        logger.info("Classification via heuristic: <article> + date → 'article'")
        return "article"

    list_items = soup.find_all("li")
    image_link_items = [li for li in list_items if li.find("img") and li.find("a")]
    if len(list_items) >= 6 and len(image_link_items) >= 3:
        logger.info("Classification via heuristic: item grid (%d li, %d with img+link) → 'category'",
                    len(list_items), len(image_link_items))
        return "category"

    logger.info("No classification signal matched — defaulting to 'webpage'")
    return "webpage"
