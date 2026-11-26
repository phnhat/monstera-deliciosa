FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken

WORKDIR /app
COPY requirements.txt .
# Install deps and bake the tokenizer into the image so runs need no extra download.
RUN pip install --no-cache-dir -r requirements.txt \
 && python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"

COPY kbsync ./kbsync
COPY main.py ask.py ./
RUN useradd --create-home --uid 1000 app && mkdir -p /app/out /app/articles && chown -R app /app
USER app

# `docker run IMAGE` and `docker run IMAGE main.py [--dry-run|--mark-stale N]` both work;
# `docker run IMAGE ask.py "question"` runs the sanity check.
ENTRYPOINT ["python"]
CMD ["main.py"]
