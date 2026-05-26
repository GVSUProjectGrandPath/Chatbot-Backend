import csv
import os
import re

INPUT_DIR = "data/raw"
OUTPUT_DIR = "data/cleaned"
MANIFEST = "data/video_manifest.csv"

# timestamps: (30 sec), (1:00 min), (1 min 30 sec), [0:00 - 0:15]
RE_TIMESTAMPS = re.compile(
    r"\(\d+(?:\s*[-–]\s*\d+)?\s*(sec|min|seconds|minutes)(\s+course)?\)"
    r"|\(\d+\s*min\s+\d+\s*sec\)"
    r"|\[\d+:\d+\s*[-–]\s*\d+:\d+\]",
    re.IGNORECASE,
)
RE_STAGE = re.compile(r"\[([A-Z][^\]\d]{2,40})\]")
RE_COMMENT_REF = re.compile(r"\[[a-z]\]")
RE_SEPARATOR = re.compile(r"^[_\-=]{3,}\s*$")
RE_COMMENT_THREAD = re.compile(r".+(reacted with|replied|commented|added a comment).+", re.IGNORECASE)
RE_SCRIPT_HEADER = re.compile(r"^\s*(Script\s*[:–-]?|Phone Video Script\s*[-–:]?)\s*$", re.IGNORECASE)
RE_SCRIPT_SECTION = re.compile(r"^\s*Script\s+\d+\s*$", re.IGNORECASE)  # "Script 1", "Script 2" etc — label only, keep content
RE_REFERENCES = re.compile(r"^\s*References?:?\s*$", re.IGNORECASE)
# Final Script sections are character dialogue used for video production — not educational content
RE_FINAL_SCRIPT = re.compile(r"^\s*Final\s+Script\s*[:–-]?\s*$", re.IGNORECASE)
RE_ARTIFACT = re.compile(r"would you like any (modifications|changes).+", re.IGNORECASE)
RE_CONVERSATIONAL = re.compile(
    r"^\s*(sounds?\s+good|looks?\s+good|great|nice|good\s+point|approved|lgtm|makes?\s+sense)[!.]?\s*$",
    re.IGNORECASE,
)
RE_EMOJI = re.compile("[\U00010000-\U0010ffff]", flags=re.UNICODE)


def clean_text(raw):
    lines = raw.splitlines()
    cleaned = []
    skip_rest = False

    for line in lines:
        if RE_REFERENCES.match(line):
            skip_rest = True
        if RE_FINAL_SCRIPT.match(line):
            skip_rest = True
        if RE_SCRIPT_HEADER.match(line):
            skip_rest = True
        if skip_rest:
            continue
        if RE_SCRIPT_SECTION.match(line):
            continue
        if RE_COMMENT_THREAD.search(line):
            continue
        if RE_ARTIFACT.search(line):
            continue
        if RE_CONVERSATIONAL.match(line):
            continue
        if RE_SEPARATOR.match(line):
            continue

        line = RE_TIMESTAMPS.sub("", line)
        line = RE_STAGE.sub("", line)
        line = RE_COMMENT_REF.sub("", line)
        line = RE_EMOJI.sub("", line)
        line = re.sub(r"  +", " ", line).rstrip()

        cleaned.append(line)

    return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    metadata = {}
    with open(MANIFEST, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            metadata[row["file_name"].strip()] = {
                "module": row["module"].strip(),
                "lesson": row["lesson"].strip(),
            }

    ok, skipped = [], []

    for file_name, meta in metadata.items():
        src_path = os.path.join(INPUT_DIR, file_name)

        if not os.path.exists(src_path):
            print(f"  MISSING  {file_name}")
            skipped.append(file_name)
            continue

        if not file_name.endswith(".txt"):
            print(f"  SKIP     {file_name}")
            skipped.append(file_name)
            continue

        with open(src_path, encoding="utf-8-sig", errors="replace") as f:
            raw = f.read()

        body = clean_text(raw)
        header = f"Module: {meta['module']}\nLesson: {meta['lesson']}\n{'─' * 40}\n\n"

        out_path = os.path.join(OUTPUT_DIR, os.path.splitext(file_name)[0] + ".txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(header + body)

        print(f"  OK       {file_name}")
        ok.append(file_name)

    print(f"\nDone. {len(ok)} files cleaned, {len(skipped)} skipped.")
    print(f"Output: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
