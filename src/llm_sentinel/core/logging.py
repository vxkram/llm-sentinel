import logging


def setup_logging(level: int = logging.INFO) -> None:
    """uvicorn configures handlers on its own uvicorn.* loggers, not the
    root logger, so records from llm_sentinel.* loggers fell through to
    Python's WARNING-level "handler of last resort" and INFO messages were
    silently dropped (this is what hid Stage 5's circuit-breaker "skipping"
    log line). force=True guarantees this config wins regardless of
    whatever uvicorn already touched.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
