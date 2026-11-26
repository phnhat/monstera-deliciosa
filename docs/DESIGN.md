# Design notes

Detail behind the [README](../README.md).

## Pipeline
`kbsync/pipeline.py` runs five phases. Each reads the previous phase's files from `out/`, so the phases can run in one process (`python main.py`) or one at a time (`python main.py <phase>`).

| Phase | Module | Output |
|---|---|---|
| fetch | `kbsync/zendesk.py` | `out/raw/articles.json`: every published article via the Zendesk Help Center API |
| convert | `kbsync/convert.py` | `articles/<slug>.md` |
| chunk | `kbsync/chunking.py` | `out/chunks/<slug>__NN.md` + `index.json` |
| plan | `kbsync/sync.py` | `out/plan.json`: what to add, update, skip, remove or defer |
| upload | `kbsync/vector_store.py` | `out/run_report.json` |

**Scraping.** The API returns article bodies only, so site nav, headers, footers and ads never enter the pipeline.

**Normalization.** HTML → Markdown with ATX headings, fenced code (language kept), tables and images.
- In-page `#anchor` links are rewritten to their heading slugs (1,366 of 1,373 resolve; the other 7 point at anchors missing in the source).
- Embeds (YouTube, Canva) become links, and one-column "TIP" tables become blockquotes.
- Inline styles, spans and decorative rules are stripped.

Each file starts with `# Title` and `Article URL: …`.

## Chunking strategy
Heading-aware and done client-side:
- Split at `##` / `###`.
- Merge small neighbouring sections up to ~700 tokens.
- Split oversized sections at paragraph boundaries. Tables and code are split by lines, repeating the header row or re-opening the fence.

Every chunk starts with `# <Title> › <Section>` and `Article URL: <url>`, so any retrieved chunk carries its context and a URL the bot can cite. Each chunk is uploaded as its own file with OpenAI `static` chunking (1024 max / 0 overlap), so OpenAI never re-splits it. One file is one chunk, which makes the logged chunk counts exact.

OpenAI's default 800/400-token windows would cut through lists and tables, and only the first window of each article would carry its title and URL. Result: 396 articles → 1,335 chunks (median ~560 tokens, max 700).

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

## Docker
The image is a multi-stage build: a `python:3.13-slim` builder installs the dependencies, and a `gcr.io/distroless/python3-debian13:nonroot` runtime runs them. It is 155 MB (down from 280 MB single-stage), with no shell and no pip, running as non-root. `docker run IMAGE` runs every phase once and exits (0 on success).

## Daily job
`.github/workflows/daily-sync.yml` runs daily at 06:17 UTC, plus manual runs. It is one job with one step per phase, so the run view shows which phase failed and how long each took:

```
Build image → Fetch articles → Convert to Markdown → Chunk → Compare with vector store → Upload delta
```

The phases take seconds each and run strictly in order, so they are steps rather than separate jobs: no per-job runner start-up, and no passing the image between machines. A 6-job version took 137 s per run; this one takes ~50 s. The workflow needs the repo secret `OPENAI_API_KEY` and the repo variable `VECTOR_STORE_ID`. GitHub pauses scheduled workflows in public repos after 60 days without commits.

Runs so far: the initial backfill, a demo of `updated`, then a run where nothing had changed upstream, so everything was skipped.

| Run | added | updated | skipped | deferred | chunks embedded |
|---|---|---|---|---|---|
| [1](https://github.com/phnhat/monstera-deliciosa/actions/runs/45815590893) | 100 | 0 | 0 | 296 | 280 |
| [2](https://github.com/phnhat/monstera-deliciosa/actions/runs/45816092830) | 100 | 0 | 100 | 196 | 271 |
| [3](https://github.com/phnhat/monstera-deliciosa/actions/runs/45816618603) | 100 | 0 | 200 | 96 | 277 |
| [4](https://github.com/phnhat/monstera-deliciosa/actions/runs/45817204477) | 96 | 0 | 300 | 0 | 507 |
| [5](https://github.com/phnhat/monstera-deliciosa/actions/runs/45817990308) (`mark_stale=3` demo) | 0 | 3 | 393 | 0 | 13 |
| [6](https://github.com/phnhat/monstera-deliciosa/actions/runs/45829964289) | 0 | 0 | 396 | 0 | 0 |

The store holds 396 articles / 1,335 chunks.

## Assistant
OptiBot is a Playground prompt (`gpt-6.2-astra`) with File Search attached to the `optibot-kb` vector store and this system prompt, verbatim:
```
You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply.
```
In the screenshot, the small boxes after step 2 are the model's inline citation markers (`filecite…`). The Playground shows them raw; the chunk files the model cited appear as chips below the answer. `python ask.py "<question>"` gives the same answer from the command line with the markers stripped, as OpenAI's [citation formatting guide](https://developers.openai.com/api/docs/guides/citation-formatting) recommends.
