from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

TRACER_NAME = "llm_sentinel"


def setup_tracing(app: FastAPI, otlp_endpoint: str | None) -> None:
    """Auto-instrumentation covers the HTTP request/response boundary
    (FastAPIInstrumentor) and outbound provider calls (HTTPXClientInstrumentor)
    for free. Everything in between - auth, rate-limit check, fallback
    resolution, reconcile/charge - gets explicit spans added at the call
    sites, since those aren't separate HTTP boundaries auto-instrumentation
    can see.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": "llm-sentinel"}))

    if otlp_endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    else:
        # No collector configured (e.g. local dev without Jaeger running) -
        # spans still print to stdout so tracing is visibly exercised.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()


def get_tracer():
    return trace.get_tracer(TRACER_NAME)
