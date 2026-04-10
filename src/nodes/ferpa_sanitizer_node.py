import re

FERPA_RESPONSE = (
    "To protect your privacy under FERPA and GVSU policy, I can't process messages "
    "that contain personal identifiers, grades, or financial aid details. Please "
    "rephrase without that information — I'm happy to help with any financial "
    "education topic!"
)

def ferpa_sanitizer(message):
    FERPA_PATTERNS = [
    re.compile(r"@gvsu\.edu", re.IGNORECASE),
    re.compile(r"\bG\d{6,8}\b"),                          # G-Number
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),               # SSN digits
    re.compile(r"\bmy\s+(SSN|social\s+security(\s+number)?)\b", re.IGNORECASE),      # SSN words
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),               # credit/debit card number
    re.compile(r"\bmy\s+(GPA|financial\s+aid|scholarship|FAFSA)\b", re.IGNORECASE),
    re.compile(r"\bI\s+(got|received|have)\s+an?\s+[A-F][+\-]?\b", re.IGNORECASE),
                     ]

    for pattern in FERPA_PATTERNS:
        if pattern.search(message):
            return {"ferpa_blocked": True, "response": FERPA_RESPONSE}
    return {"ferpa_blocked": False}

print(ferpa_sanitizer("Hi This is Puneeth and my G-number is G0257128"))