import logging
import os

from opentelemetry import trace

logger = logging.getLogger("FinLit-Logger")


def trace_content_enabled() -> bool:
    """True when spans may carry raw student text (messages, prompts, answers).

    Defaults to OFF so that forgetting to set it is the safe outcome. Turn it on
    (FINLIT_TRACE_CONTENT=true) for internal testing, where the only people typing
    into the widget are the project team; leave it unset once real students are live,
    since Application Insights retains what it receives for 90 days by default.

    Read at call time rather than import time so tests and Azure App Settings changes
    both take effect without a code change.
    """
    return os.getenv("FINLIT_TRACE_CONTENT", "false").strip().lower() == "true"


def set_content(span: trace.Span, key: str, value: str) -> None:
    """Set a span attribute holding user/message text — a no-op unless content tracing is on.

    Every raw-text attribute in the app goes through here, so trace_content_enabled()
    is the single switch that governs all of them.
    """
    if trace_content_enabled():
        span.set_attribute(key, value)


def setup_tracing() -> None:
    """Initialize Azure AI Foundry tracing. Call once at app startup.

    Reads AZURE_AIPROJECT_ENDPOINT from the environment. If the variable is absent
    (e.g. local dev without credentials), tracing is silently disabled and all
    span calls throughout the app become no-ops — no code changes needed elsewhere.
    """
    endpoint = os.getenv("AZURE_AIPROJECT_ENDPOINT")
    if not endpoint:
        logger.info("AZURE_AIPROJECT_ENDPOINT not set — tracing disabled")
        return

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

        # OTel redacts prompt/response text from LLM spans by default. Opt in only when
        # FINLIT_TRACE_CONTENT is on, so this layer and set_content() below stay in sync.
        # Must be set before OpenAIInstrumentor().instrument() runs.
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = (
            "true" if trace_content_enabled() else "false"
        )

        # DefaultAzureCredential automatically uses managed identity when running on
        # Azure App Service, and falls back to `az login` credentials locally.
        client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

        # The Foundry Tracing UI reads from the Application Insights resource connected
        # to the project — fetch its connection string and point the OTel exporter there.
        connection_string = client.telemetry.get_application_insights_connection_string()
        configure_azure_monitor(connection_string=connection_string)

        # Auto-instrument the openai SDK so every chat/embedding call (including the
        # ones LangChain makes internally) emits a gen_ai span with prompt + response.
        OpenAIInstrumentor().instrument()

        logger.info("Azure AI Foundry tracing enabled")
    except Exception:
        # Tracing must never take the app down — log the failure and run untraced.
        logger.exception("tracing_setup_failed")


def get_tracer() -> trace.Tracer:
    """Return the app-wide OpenTelemetry tracer.

    Returns a no-op tracer if setup_tracing() was never called or failed, so
    callers never need to guard against None.
    """
    return trace.get_tracer("finlit-chatbot")
