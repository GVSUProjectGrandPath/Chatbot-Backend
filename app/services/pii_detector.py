from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, PatternRecognizer, Pattern

# Custom recognizers for GVSU-specific identifiers not covered by Presidio's default set
gvsu_id_recognizer = PatternRecognizer(
    supported_entity="GVSU_ID",
    patterns=[Pattern("G-number", r"\bG\d{6,8}\b", score=0.95)],
    context=["student", "id", "number", "gvsu"],
)

gpa_recognizer = PatternRecognizer(
    supported_entity="GPA_DISCLOSURE",
    patterns=[
        Pattern("gpa_my", r"\b(my|I\s+have(\s+a)?)\s+G\s*P\s*A\b", score=0.85),
        Pattern("gpa_is", r"\bG\s*P\s*A\s+(is|was|of)\s+\d", score=0.90),
    ],
    context=["grades", "academic", "score"],
)

finaid_recognizer = PatternRecognizer(
    supported_entity="FINAID_DISCLOSURE",
    patterns=[
        Pattern("finaid_my", r"\b(my|I\s+(have|received|got))\s+(financial\s+aid|scholarship|fafsa)\b", score=0.85),
        Pattern("loan_balance", r"\bmy\s+(loan|debt)\s+balance\b", score=0.90),
        Pattern("i_owe", r"\bI\s+owe\s+\$?\d", score=0.90),
    ],
    context=["aid", "scholarship", "loan", "debt", "fafsa"],
)

# Presidio entities that map to FERPA-protected information
ENABLED_ENTITIES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "US_SSN",
    "CREDIT_CARD",
    "LOCATION",
    "DATE_TIME",
    "US_BANK_NUMBER",
    "GVSU_ID",
    "GPA_DISCLOSURE",
    "FINAID_DISCLOSURE",
]

# Score thresholds — HIGH blocks immediately, BORDERLINE escalates to the LLM backstop
HIGH_CONFIDENCE = 0.85
BORDERLINE_LOW = 0.50

# Build the engine once at import time — spaCy model load (~1-3s) pays the cost on cold start,
# not on every request.
registry = RecognizerRegistry()
registry.load_predefined_recognizers()
registry.add_recognizer(gvsu_id_recognizer)
registry.add_recognizer(gpa_recognizer)
registry.add_recognizer(finaid_recognizer)

analyzer = AnalyzerEngine(registry=registry)


def analyze_pii(text: str) -> tuple[list, list]:
    """Returns (high_confidence_hits, borderline_hits) from Presidio analysis.

    High confidence (>=0.85): block immediately, no LLM needed.
    Borderline (0.50-0.85): escalate to LLM backstop — Presidio is uncertain.
    Below 0.50: treated as clean.
    """
    results = analyzer.analyze(text=text, entities=ENABLED_ENTITIES, language="en", score_threshold=BORDERLINE_LOW)
    high = [r for r in results if r.score >= HIGH_CONFIDENCE]
    borderline = [r for r in results if BORDERLINE_LOW <= r.score < HIGH_CONFIDENCE]
    return high, borderline
