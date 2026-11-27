# Trenchcoat — Detective Board + agent runtime
# $0 deploy target: Hugging Face Spaces (Docker SDK, port 7860) or any ~$5 VPS.
#
#   docker build -t trenchcoat .
#   docker run -p 7860:7860 --env-file .env -v trenchcoat-data:/data trenchcoat
#
# HF Spaces: set app_port: 7860 in the Space README front-matter, add your keys
# as Space secrets, and set BOARD_TOKEN (the board is public on a free Space).

FROM python:3.11-slim

# Playwright Chromium needs system libs; INSTALL_BROWSER=0 skips them for a
# lighter image (text-only evidence, no screenshots).
ARG INSTALL_BROWSER=1
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TRENCHCOAT_DATA_DIR=/data \
    BOARD_HOST=0.0.0.0 \
    BOARD_PORT=7860

WORKDIR /app

COPY requirements.txt pyproject.toml README.md ./
COPY trenchcoat ./trenchcoat
RUN pip install --upgrade pip && pip install -r requirements.txt && pip install -e .

RUN if [ "$INSTALL_BROWSER" = "1" ]; then \
      python -m playwright install --with-deps chromium; \
    fi

# HF Spaces runs containers as uid 1000; make /data writable by it.
RUN useradd -m -u 1000 trench && mkdir -p /data && chown -R trench:trench /data /app
USER trench

VOLUME ["/data"]
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/healthz')" || exit 1

CMD ["trenchcoat", "board", "--host", "0.0.0.0", "--port", "7860"]
