FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
WORKDIR /app
COPY requirements-cloud.lock .
RUN python -m pip install --only-binary=:all: --require-hashes -r requirements-cloud.lock \
    && groupadd --gid 10001 valerion && useradd --uid 10001 --gid 10001 --no-create-home valerion
COPY --chown=10001:10001 . .
USER 10001:10001
EXPOSE 8080
CMD ["sh","-c","exec python -m uvicorn main:app --host 0.0.0.0 --port \"$PORT\" --no-proxy-headers --limit-concurrency 64 --timeout-keep-alive 5 --no-access-log --log-config logging.json"]
