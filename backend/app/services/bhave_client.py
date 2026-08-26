from __future__ import annotations

import os
from typing import Any
from urllib.parse import urljoin

import requests


class BHaveClient:
    """Configurable bHave API adapter.

    The bHave deployment used by the clinical app is backed by Firebase/HTTP
    services, while future product environments may expose a REST facade. This
    adapter isolates those contract details behind environment-driven settings.
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        timeout: int = 30,
        *,
        records_path: str | None = None,
        auth_scheme: str | None = None,
        auth_header: str | None = None,
        records_key: str | None = None,
        page_param: str | None = None,
        page_size_param: str | None = None,
        next_page_key: str | None = None,
        max_pages: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout
        self.records_path = records_path or os.getenv("SELLAS_BHAVE_RECORDS_PATH", "/behavior-records")
        self.auth_scheme = (auth_scheme or os.getenv("SELLAS_BHAVE_AUTH_SCHEME", "bearer")).lower()
        self.auth_header = auth_header or os.getenv("SELLAS_BHAVE_AUTH_HEADER", "Authorization")
        self.records_key = records_key or os.getenv("SELLAS_BHAVE_RECORDS_KEY", "data,records,items,documents")
        self.page_param = page_param or os.getenv("SELLAS_BHAVE_PAGE_PARAM", "page")
        self.page_size_param = page_size_param or os.getenv("SELLAS_BHAVE_PAGE_SIZE_PARAM", "pageSize")
        self.next_page_key = next_page_key or os.getenv("SELLAS_BHAVE_NEXT_PAGE_KEY", "nextPageToken,next,next_cursor")
        self.max_pages = max_pages or int(os.getenv("SELLAS_BHAVE_MAX_PAGES", "20"))

    def fetch_behavior_records(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        if not self.base_url or not self.api_token:
            raise ValueError("SELLAS_BHAVE_BASE_URL e SELLAS_BHAVE_API_TOKEN precisam estar configurados.")

        records: list[dict[str, Any]] = []
        next_page: str | None = None
        for page in range(1, self.max_pages + 1):
            params = self._query_params(start_date, end_date, page, next_page)
            response = requests.get(
                self._records_url(),
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            batch = self._extract_records(payload)
            records.extend(batch)
            next_page = self._next_page(payload)
            if not next_page and len(batch) == 0:
                break
            if not next_page and page > 1:
                break
            if not next_page and len(batch) > 0:
                break
        return records

    def validate_contract(self) -> dict[str, Any]:
        missing = []
        if not self.base_url:
            missing.append("SELLAS_BHAVE_BASE_URL")
        if not self.api_token:
            missing.append("SELLAS_BHAVE_API_TOKEN")
        return {
            "ok": not missing,
            "missing": missing,
            "base_url_configured": bool(self.base_url),
            "records_path": self.records_path,
            "auth_scheme": self.auth_scheme,
            "records_keys": self._key_candidates(self.records_key),
            "pagination": {
                "page_param": self.page_param,
                "page_size_param": self.page_size_param,
                "next_page_keys": self._key_candidates(self.next_page_key),
                "max_pages": self.max_pages,
            },
        }

    def _records_url(self) -> str:
        return urljoin(f"{self.base_url}/", self.records_path.lstrip("/"))

    def _headers(self) -> dict[str, str]:
        if self.auth_scheme == "api-key":
            return {self.auth_header: self.api_token}
        if self.auth_scheme == "raw":
            return {self.auth_header: self.api_token}
        return {self.auth_header: f"Bearer {self.api_token}"}

    def _query_params(self, start_date: str | None, end_date: str | None, page: int, next_page: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
            self.page_size_param: int(os.getenv("SELLAS_BHAVE_PAGE_SIZE", "500")),
        }
        if next_page:
            params[self.page_param] = next_page
        else:
            params[self.page_param] = page
        return {key: value for key, value in params.items() if value not in (None, "")}

    def _extract_records(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in self._key_candidates(self.records_key):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            nested = payload.get("data")
            if isinstance(nested, dict):
                for key in self._key_candidates(self.records_key):
                    value = nested.get(key)
                    if isinstance(value, list):
                        return [item for item in value if isinstance(item, dict)]
        return []

    def _next_page(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in self._key_candidates(self.next_page_key):
            value = payload.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _key_candidates(value: str) -> list[str]:
        return [part.strip() for part in value.split(",") if part.strip()]
