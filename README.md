# Web Crawler API

A REST API that accepts any URL, fetches the page content, extracts HTML metadata, classifies the page type, and returns a ranked list of relevant topics. Built for the BrightEdge Engineering Candidate Assignment.

## Running Locally

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
playwright install chromium
python -m spacy download en_core_web_sm

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

