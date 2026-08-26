from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
import unicodedata
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HTML_TAG_RE = re.compile(r"<[^>]+>")
ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
PROMPT_INJECTION_RE = re.compile(
    r"(?i)\b("
    r"ignore\s+(all\s+)?previous\s+instructions|"
    r"system\s+prompt|developer\s+message|"
    r"reveal\s+(secrets?|tokens?|keys?)|"
    r"exfiltrate|jailbreak|"
    r"forget\s+(all\s+)?instructions"
    r")\b"
)


class SecurityValidationError(ValueError):
    """Raised when untrusted input violates a security boundary."""


def get_env(name: str, *fallback_names: str, default: str | None = None) -> str | None:
    for candidate in (name, *fallback_names):
        value = os.getenv(candidate)
        if value is not None and value.strip():
            return value.strip()
    return default


def require_env(name: str, *fallback_names: str) -> str:
    value = get_env(name, *fallback_names)
    if value is None:
        names = ", ".join((name, *fallback_names))
        raise RuntimeError(f"Variavel de ambiente obrigatoria ausente: {names}")
    return value


def build_retry_session(
    *,
    total_retries: int = 3,
    backoff_factor: float = 0.75,
    status_forcelist: Iterable[int] = (429, 500, 502, 503, 504),
) -> requests.Session:
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=tuple(status_forcelist),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
    session = requests.Session()
    session.mount("https://", adapter)
    return session


def ensure_https_url(url: str, *, allowed_hosts: Iterable[str] | None = None) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SecurityValidationError("Apenas URLs HTTPS sao permitidas.")
    if not parsed.hostname:
        raise SecurityValidationError("URL sem hostname valido.")
    if allowed_hosts and parsed.hostname not in set(allowed_hosts):
        raise SecurityValidationError(f"Host externo nao permitido: {parsed.hostname}")
    return url


def assert_response_size(response: requests.Response, max_bytes: int) -> None:
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > max_bytes:
        raise SecurityValidationError("Resposta remota excede o limite permitido.")
    if response.content and len(response.content) > max_bytes:
        raise SecurityValidationError("Payload remoto excede o limite permitido.")


def sanitize_text(
    value: object,
    *,
    max_length: int = 5000,
    allow_newlines: bool = False,
) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKC", text)
    text = html.unescape(text)
    text = ZERO_WIDTH_RE.sub("", text)
    text = HTML_TAG_RE.sub(" ", text)
    text = CONTROL_CHARS_RE.sub(" ", text)
    if allow_newlines:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
    else:
        text = re.sub(r"\s+", " ", text)
    return text.strip()[:max_length]


def sanitize_for_ai_context(value: object, *, max_length: int = 40000) -> str:
    text = sanitize_text(value, max_length=max_length, allow_newlines=True)
    return PROMPT_INJECTION_RE.sub("[instrucao externa removida]", text)


def pseudonymize_identifier(value: object, *, salt_env: str = "SELLAS_PATIENT_HASH_SALT") -> str:
    normalized = sanitize_text(value, max_length=256, allow_newlines=False).casefold()
    if not normalized:
        raise SecurityValidationError("Identificador vazio nao pode ser pseudonimizado.")
    salt = require_env(salt_env, "SKINNER_PATIENT_HASH_SALT").encode("utf-8")
    return hmac.new(salt, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def safe_filename(value: object, *, default: str = "arquivo", max_length: int = 80) -> str:
    text = sanitize_text(value, max_length=max_length, allow_newlines=False)
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")
    return text[:max_length] or default


def ensure_child_path(base_dir: str | Path, candidate: str | Path) -> Path:
    base_path = Path(base_dir).resolve()
    candidate_path = Path(candidate).resolve()
    if base_path != candidate_path and base_path not in candidate_path.parents:
        raise SecurityValidationError("Caminho fora do diretorio permitido.")
    return candidate_path
