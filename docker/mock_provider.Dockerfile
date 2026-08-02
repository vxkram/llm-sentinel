FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi "uvicorn[standard]" pydantic httpx

COPY mock_providers ./mock_providers

ARG MOCK_PROVIDER=openai
ENV MOCK_PROVIDER=${MOCK_PROVIDER}

EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn mock_providers.${MOCK_PROVIDER}_mock.main:app --host 0.0.0.0 --port 8000"]
