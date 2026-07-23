import logging
import os

from opentelemetry import trace

logger = logging.getLogger("FinLit-Logger")


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

        # OTel redacts prompt/response text from LLM spans by default; opt in so the
        # raw system prompt (with retrieved chunks) and answers appear in traces.
        # Must be set before OpenAIInstrumentor().instrument() runs.
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")

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
