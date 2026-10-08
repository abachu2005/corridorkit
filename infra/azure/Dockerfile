FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CORRIDORKIT_JOB_ROOT=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src
RUN python -m pip install \
    ".[cloud]" \
    "TotalSegmentator==2.11.0"

EXPOSE 8080
CMD ["uvicorn", "corridorkit.cloud.service:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
