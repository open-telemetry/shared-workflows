import re


PROBABLE_SECRET = re.compile(
    rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s+>]*[A-Za-z0-9+/]{24,}"
    rb"|\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{70,}|"
    rb"(?:AKIA|ASIA)[A-Z0-9]{16}|xox[baprs]-[0-9A-Za-z-]{16,}|AIza[0-9A-Za-z_-]{35})\b"
    rb"|(?i:\bbearer )[A-Za-z0-9._~-]{32,}"
)
URL_AUTH = re.compile(rb"""https?://[^\s:/"\\@]+:([^\s/"\\@]+)@""")
PLACEHOLDER = re.compile(rb"(?i)(?:pass(?:word)?|token|secret|xxx+|\$\{?[\w.-]+\}?|\*+)")
CODE_URL_AUTH = re.compile(rb"""(https?://)([^\s:/?#"'<>\\@]+):([^\s/?#"'<>\\@]+)@(?![^\s/?#"'<>\\]*@)""")


def redact_code_urls(text):
    def replace(match):
        scheme, user, password = match.groups()
        if PLACEHOLDER.fullmatch(password):
            return match[0]
        # Preserve evidence that the source itself redacted the credentials.
        label = b"redacted" if user.lower() == password.lower() == b"redacted" else b"omitted"
        return scheme + label + b":password@"

    return CODE_URL_AUTH.sub(replace, text.encode("utf-8")).decode("utf-8")


def safe_payload(value):
    def strings(value):
        if isinstance(value, str):
            yield value.encode("utf-8")
        elif isinstance(value, dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)

    def safe(text):
        return not PROBABLE_SECRET.search(text) and all(
            PLACEHOLDER.fullmatch(match.group(1)) for match in URL_AUTH.finditer(text)
        )

    return all(map(safe, strings(value)))
