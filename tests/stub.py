"""A stand-in for the Rampart model in tests: tags a few fictional-but-real-looking names, emails and SSNs."""
import re

NAMES = re.compile(r"\b(Priya Ramanathan|Hannah Castellanos)\b")
EMAIL = re.compile(r"\b[\w.]+@(?:outlook|gmail|protonmail)\.com\b")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def model(texts):
    out = []
    for t in texts:
        labels = []
        for pat, label in ((NAMES, "GIVEN_NAME"), (EMAIL, "EMAIL"), (SSN, "SSN")):
            if pat.search(t):
                labels.append(label)
                t = pat.sub(f"[{label}_1]", t)
        out.append((t, labels))
    return out
