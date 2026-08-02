FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e .

COPY configs ./configs

EXPOSE 8010

CMD ["uvicorn", "llm_sentinel.main:app", "--host", "0.0.0.0", "--port", "8010"]
