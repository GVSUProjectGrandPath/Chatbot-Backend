import re

FERPA_RESPONSE = (
    "To protect your privacy under FERPA and GVSU policy, I can't process messages "
    "that contain personal identifiers, grades, or financial aid details. Please "
    "rephrase without that information — I'm happy to help with any financial "
    "education topic!"
)

def ferpa_sanitizer(message):
    FERPA_PATTERNS = [

    # Student identity — GVSU email (direct and obfuscated)
    re.compile(r"(\[at\]|@)\s*gvsu\.edu", re.IGNORECASE),

    # Student identity — G-Number
    re.compile(r"\bG\d{6,8}\b"),

    # Student identity — name disclosure
    re.compile(r"\bmy\s+name\s+is\b", re.IGNORECASE),

    # Student identity — phone number (common US formats, with or without parens)
    re.compile(r"(\b\d{3}|\(\d{3}\))[-.\s]\d{3}[-.\s]\d{4}\b"),

    # Student identity — home address
    re.compile(r"\bmy\s+address\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+\w+\s+(street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|court|ct|place|pl|way)\b", re.IGNORECASE),

    # Student identity — date of birth
    re.compile(r"\bmy\s+(birthday|date\s+of\s+birth|dob|birth\s+date)\b", re.IGNORECASE),
    re.compile(r"\bborn\s+on\b", re.IGNORECASE),

    # Academic records — GPA
    re.compile(r"\b(my|I\s+have(\s+a)?)\s+G\s*P\s*A\b", re.IGNORECASE),

    # Academic records — specific letter grade received
    re.compile(r"\b(I\s+)?(got|received|have)\s+an?\s+[A-F][+\-]?\b", re.IGNORECASE),

    # Academic records — academic standing and probation
    re.compile(r"\b(my\s+)?academic\s+(standing|probation)\b", re.IGNORECASE),
    re.compile(r"\bdean'?s\s+list\b", re.IGNORECASE),

    # Academic records — enrollment status and credit hours
    re.compile(r"\bmy\s+(enrollment\s+status|credit\s+hours?|course\s+load)\b", re.IGNORECASE),
    re.compile(r"\bI('?m|\s+am)\s+(enrolled|registered)\s+.{0,15}\d+\s+credits?\b", re.IGNORECASE),

    # Academic records — degree program, major, minor, graduation
    re.compile(r"\bI('?m|\s+am)\s+(majoring|minoring|graduating)\b", re.IGNORECASE),
    re.compile(r"\bmy\s+(major|minor)\s+(is|was|will\s+be)\b", re.IGNORECASE),
    re.compile(r"\bmy\s+(degree\s+program|graduation\s+date)\b", re.IGNORECASE),

    # Financial records — financial aid, scholarship, FAFSA
    re.compile(r"\b(my|I\s+(have(\s+a)?|received|got))\s+(financial\s+aid|scholarship|fafsa)\b", re.IGNORECASE),

    # Financial records — loan and debt balances
    re.compile(r"\bmy\s+(loan|debt)\s+balance\b", re.IGNORECASE),
    re.compile(r"\bI\s+owe\s+\$?\d", re.IGNORECASE),

    # Financial records — bank account
    re.compile(r"\bmy\s+(bank\s+account|account\s+number|routing\s+number)\b", re.IGNORECASE),

    # Financial records — SSN digit pattern
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),

    # Financial records — SSN word phrases
    re.compile(r"\bmy\s+(ssn|social\s+security(\s+number)?)\b", re.IGNORECASE),

    # Financial records — credit/debit card formatted (4-4-4-4)
    re.compile(r"\b(?:\d{4}[- ]){3}\d{4}\b"),

    # Financial records — card number with context keyword before it
    re.compile(r"\b(my|use|card|credit|debit|pay)\b.{0,30}\b\d{13,16}\b", re.IGNORECASE),

    # Financial records — card number with context keyword after it
    re.compile(r"\b\d{13,16}\b.{0,30}\b(payment|card|credit|debit|charge)\b", re.IGNORECASE),

    ]

    for pattern in FERPA_PATTERNS:
        if pattern.search(message):
            return True
    return False


if __name__ == "__main__":
    print(ferpa_sanitizer("Hi my G-number is G02527285"))
