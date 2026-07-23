"""Unit tests for app/services/tracing.py.

These are UNIT tests: they exercise the control flow of setup_tracing() and
get_tracer() in isolation. They deliberately do NOT perform a real export to
Application Insights — that needs live Azure credentials, real cloud resources,
and network access, which would make the suite slow, non-deterministic, and
capable of writing junk into production telemetry. That end-to-end path is
verified by actually running the app against Azure, not here.

So the only thing mocked is the Azure/OpenTelemetry SDK boundary we cannot run
offline (and only in the failure test). Everything we CAN run for real — the
"tracing disabled" path and get_tracer() — runs unmocked.
"""
import sys
from unittest.mock import MagicMock, patch

from app.services import tracing


def test_get_tracer_returns_usable_tracer():
    """get_tracer() must always hand back a tracer whose spans work as context
    managers, so callers can wrap code in a span without ever checking whether
    tracing is on. With no exporter configured this is OpenTelemetry's no-op
    tracer — using it should be completely harmless.
    """
    tracer = tracing.get_tracer()

    # The guarantee under test: this block must not raise even though
    # setup_tracing() was never called in this test.
    with tracer.start_as_current_span("test-span") as span:
        span.set_attribute("key", "value")


def test_setup_tracing_noop_when_endpoint_unset(monkeypatch):
    """The local-dev guarantee: with AZURE_AIPROJECT_ENDPOINT unset, setup_tracing()
    returns immediately without touching Azure and without raising, so the app
    runs untraced with zero configuration.
    """
    monkeypatch.delenv("AZURE_AIPROJECT_ENDPOINT", raising=False)

    # Returns None (early return) and does not raise.
    assert tracing.setup_tracing() is None


def test_setup_tracing_swallows_failures(monkeypatch):
    """The safety guarantee: if anything in the Azure/OTel setup fails,
    setup_tracing() logs it and returns — it must never propagate, or a tracing
    outage could take the chatbot down.

    We force a failure deterministically and offline by faking azure.ai.projects
    (the first thing setup imports) so that constructing AIProjectClient raises.
    In an environment where the real azure SDK isn't installed, the in-function
    import fails first instead — also a setup failure, and also one we must
    swallow — so this test asserts the guarantee holds either way, and never
    hits the network.
    """
    monkeypatch.setenv(
        "AZURE_AIPROJECT_ENDPOINT",
        "https://bogus.example/api/projects/x",
    )

    # AIProjectClient(...) raises the instant it's constructed.
    fake_projects = MagicMock()
    fake_projects.AIProjectClient = MagicMock(
        side_effect=RuntimeError("simulated Azure failure")
    )

    with patch.dict(sys.modules, {"azure.ai.projects": fake_projects}):
        # Must NOT raise, and returns None on the failure path.
        assert tracing.setup_tracing() is None
