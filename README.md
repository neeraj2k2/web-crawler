# Web Crawler API

A REST API that accepts any URL, fetches the page content, extracts HTML metadata, classifies the page type, and returns a ranked list of relevant topics. Built for the BrightEdge Engineering Candidate Assignment.

## Live Demo

**Public endpoint (GCP Cloud Run — asia-southeast1):**
```
https://web-crawler-807576498072.asia-southeast1.run.app
```

> The service is deployed on GCP Cloud Run which scales to zero when idle. The first request after a period of inactivity may take 30–60 seconds due to cold start. Subsequent requests will respond normally.

```bash
# Crawl the Amazon toaster page
curl -X POST https://web-crawler-807576498072.asia-southeast1.run.app/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "http://www.amazon.com/Cuisinart-CPT-122-Compact-2-Slice-Toaster/dp/B009GQ034C/ref=sr_1_1?s=kitchen&ie=UTF8&qid=1431620315&sr=1-1&keywords=toaster"}'

# Crawl the CNN article
curl -X POST https://web-crawler-807576498072.asia-southeast1.run.app/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"}'

# Crawl the REI blog post
curl -X POST https://web-crawler-807576498072.asia-southeast1.run.app/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/"}'

# Health check
curl https://web-crawler-807576498072.asia-southeast1.run.app/health
```

## Running Locally

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Copy and configure environment
cp .env.example .env

# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

## API

### POST /crawl

```bash
curl -X POST http://localhost:8080/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai", "respect_robots_txt": false}'
```

### GET /health

```bash
curl http://localhost:8080/health
```

## Running Tests

```bash
# Unit tests (no network required)
pytest tests/

# Integration tests (requires running server)
pytest tests/ -m integration
```

## Design Documentation

See `docs/part1-plan.md` for full architecture decisions, technology choices, and tradeoffs.

---

## AI Tools Used

**Tool:** Claude Code (Anthropic) — `claude-sonnet-4-6`
Used throughout the development lifecycle

