from typing import TypedDict

# State
class BotState(TypedDict):
    messages: list[dict] 
    avatar: str | None
    onboarding_shown: bool
    ferpa_blocked : bool
    response: str | None
    rag_chunks: list[dict]
    intent: str | None #Boundary,Content


# Static responses
FERPA_RESPONSE = (
    "To protect your privacy under FERPA and GVSU policy, I can't process messages "
    "that contain personal identifiers, grades, or financial aid details. Please "
    "rephrase without that information - I'm happy to help with any financial "
    "education topic!"
)


# FERPA patterns
FERPA_PATTERNS = [
    re.compile(r"@gvsu\.edu", re.IGNORECASE),
    re.compile(r"\bG\d{6}\b"),                          # G-Number
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),               # SSN
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),               # credit/debit card number
    re.compile(r"\bmy\s+(GPA|financial\s+aid|scholarship|FAFSA)\b", re.IGNORECASE),
    re.compile(r"\bI\s+(got|received|have)\s+an?\s+[A-F][+\-]?\b", re.IGNORECASE),
]










