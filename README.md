# monstera-deliciosa

Support-center articles → clean Markdown → OpenAI vector store, kept in sync by a daily job. It backs **OptiBot**, an assistant that answers only from the docs and cites article URLs.

**Daily job logs:** [GitHub Actions runs](https://github.com/phnhat/monstera-deliciosa/actions/workflows/daily-sync.yml). Each run's summary shows added / updated / skipped counts. Its artifacts (`1-raw-articles` … `5-run-report`) hold each phase's output and log, ending with `run_report.json`.

## How it works
1. **Scrape** (`kbsync/zendesk.py`): every published article via the Zendesk Help Center API. The API returns article bodies only, so site nav, headers, footers and ads never enter the pipeline.
2. **Normalize** (`kbsync/convert.py`): HTML → Markdown with ATX headings, fenced code, tables and images. In-page `#anchor` links are rewritten to their heading slugs, embeds (YouTube, Canva) become links, one-column "TIP" tables become blockquotes, and inline styles are stripped. Output: [`articles/<slug>.md`](articles/), each starting with `# Title` and `Article URL: …`.
3. **Chunk** (`kbsync/chunking.py`) and **sync** (`kbsync/sync.py`, `kbsync/vector_store.py`), described below.

`kbsync/pipeline.py` runs these as five phases: `fetch → convert → chunk → plan → upload`. Each phase reads the previous phase's files from `out/`. `python main.py` runs all of them; `python main.py <phase>` runs one.

## Chunking strategy
Heading-aware and done client-side:
- Split at `##` / `###`.
- Merge small neighbouring sections up to ~700 tokens.
- Split oversized sections at paragraph boundaries. Tables and code are split by lines, repeating the header row or re-opening the fence.

Every chunk starts with `# <Title> › <Section>` and `Article URL: <url>`, so any retrieved chunk carries its context and a URL the bot can cite. Each chunk is uploaded as its own file with OpenAI `static` chunking (1024 max / 0 overlap), so OpenAI never re-splits it. One file is one chunk, which makes the logged chunk counts exact.

OpenAI's default 800/400-token windows would cut through lists and tables, and only the first window of each article would carry its title and URL. Result: 396 articles → 1,335 chunks (median ~560 tokens).

## Delta detection
State lives on the vector-store files themselves, as attributes (`article_id`, `content_hash`, `chunk_index`, `chunk_count`, …), so the job is stateless: no database or volume. Each run hashes every article's Markdown (sha256 + chunker version) and compares it with the store:

| Article state | Result |
|---|---|
| Not in the store | **added** |
| Hash or chunk set differs | **updated**: new chunks are uploaded first; old ones are deleted only once all new chunks are attached |
| Same hash and chunks | **skipped** |
| Gone from the help center | **removed**, guarded against mass deletion |

Hashing content instead of trusting `updated_at` matters here: in one day the help center bumped `updated_at` on 384 of its 396 articles.

Two controls help demonstrate the job:
- `MAX_NEW_PER_RUN` (default 100) throttles the initial backfill.
- `--mark-stale N` (also an input on manual runs) is a demo switch. It marks N articles stale so a run shows `updated`, and the run log labels it as a demo.

## Setup & run locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.sample .env                      # add OPENAI_API_KEY
python main.py --dry-run                 # scrape + convert + chunk, no API calls
python main.py                           # sync the delta to the vector store
python main.py fetch                     # or one phase: fetch|convert|chunk|plan|upload
python ask.py "How do I add a YouTube video?"
pytest -q
```
Docker: the job runs once and exits 0.
```bash
docker build -t kbsync .
docker run --rm -e API_KEY=sk-... kbsync    # same as: kbsync main.py
```
The image is a multi-stage build, from a `python:3.13-slim` builder to a `distroless/python3` runtime: 155 MB, no shell, non-root.

## Daily job
`.github/workflows/daily-sync.yml` runs daily at 06:17 UTC, plus manual runs. Each phase is its own job, so the run graph shows which phase failed and how long each took:

```
Build image → Fetch articles → Convert to Markdown → Chunk → Compare with vector store → Upload delta
```

The image is built once and handed to the later jobs. Phase outputs pass between jobs as artifacts. The workflow needs the repo secret `OPENAI_API_KEY` and the repo variable `VECTOR_STORE_ID`. GitHub pauses scheduled workflows in public repos after 60 days without commits.

First runs (single-job workflow at the time): the initial backfill, then a demo of `updated`.

| Run | added | updated | skipped | deferred | chunks embedded |
|---|---|---|---|---|---|
| [1](https://github.com/phnhat/monstera-deliciosa/actions/runs/45815590893) | 100 | 0 | 0 | 296 | 280 |
| [2](https://github.com/phnhat/monstera-deliciosa/actions/runs/45816092830) | 100 | 0 | 100 | 196 | 271 |
| [3](https://github.com/phnhat/monstera-deliciosa/actions/runs/45816618603) | 100 | 0 | 200 | 96 | 277 |
| [4](https://github.com/phnhat/monstera-deliciosa/actions/runs/45817204477) | 96 | 0 | 300 | 0 | 507 |
| [5](https://github.com/phnhat/monstera-deliciosa/actions/runs/45817990308) (`mark_stale=3` demo) | 0 | 3 | 393 | 0 | 13 |

The store now holds 396 articles / 1,335 chunks.

## Assistant
OptiBot is a Playground prompt with File Search attached to the vector store and this system prompt, verbatim:
```
You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply.
```
