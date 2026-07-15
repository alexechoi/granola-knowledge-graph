"""Resilient client for the public Granola API."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic, sleep
from typing import TYPE_CHECKING, Protocol, Self, TypeVar

import httpx
from pydantic import ValidationError

from granola_kg.granola_models import (
    Folder,
    ListFoldersResponse,
    ListNotesQuery,
    ListNotesResponse,
    NoteDetail,
    NoteSummary,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

DEFAULT_BASE_URL = "https://public-api.granola.ai"
MAX_PAGE_SIZE = 30
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
ResponseModel = TypeVar("ResponseModel", ListNotesResponse, ListFoldersResponse, NoteDetail)


class Clock(Protocol):
    """Monotonic time source."""

    def __call__(self) -> float:
        """Return monotonic seconds."""
        ...


class Sleeper(Protocol):
    """Delay implementation."""

    def __call__(self, seconds: float, /) -> None:
        """Block for the requested duration."""
        ...


class GranolaApiError(RuntimeError):
    """Granola request or response validation failure."""


class RequestPacer:
    """Space requests to remain within Granola's sustained rate limit."""

    def __init__(
        self,
        *,
        requests_per_second: float = 5.0,
        clock: Clock = monotonic,
        sleeper: Sleeper = sleep,
    ) -> None:
        """Configure pacing with injectable time functions for deterministic tests."""
        if requests_per_second <= 0:
            msg = "requests_per_second must be positive"
            raise ValueError(msg)
        self._interval = 1.0 / requests_per_second
        self._clock = clock
        self._sleeper = sleeper
        self._last_request: float | None = None

    def wait(self) -> None:
        """Wait until the next request is permitted."""
        now = self._clock()
        if self._last_request is not None:
            delay = self._interval - (now - self._last_request)
            if delay > 0:
                self._sleeper(delay)
                now = self._clock()
        self._last_request = now


class GranolaClient:
    """Typed, paginated Granola API client with retry handling."""

    def __init__(  # noqa: PLR0913 - network dependencies are intentionally injectable.
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
        max_retries: int = 4,
        client: httpx.Client | None = None,
        pacer: RequestPacer | None = None,
        sleeper: Sleeper = sleep,
    ) -> None:
        """Configure authentication, retries, transport, and request pacing."""
        if not api_key:
            msg = "Granola API key cannot be empty"
            raise ValueError(msg)
        if max_retries < 0:
            msg = "max_retries cannot be negative"
            raise ValueError(msg)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )
        self._max_retries = max_retries
        self._pacer = pacer or RequestPacer(sleeper=sleeper)
        self._sleeper = sleeper

    def __enter__(self) -> Self:
        """Enter a managed client scope."""
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """Close an internally owned HTTP client."""
        self.close()

    def close(self) -> None:
        """Release network resources owned by this client."""
        if self._owns_client:
            self._client.close()

    def iter_notes(self, query: ListNotesQuery | None = None) -> Iterator[NoteSummary]:
        """Yield every note across cursor pages."""
        filters = query or ListNotesQuery()
        if not 1 <= filters.page_size <= MAX_PAGE_SIZE:
            msg = "page_size must be between 1 and 30"
            raise ValueError(msg)
        cursor = filters.cursor
        while True:
            params = self._note_params(filters, cursor)
            page = self._get_model("/v1/notes", params, ListNotesResponse)
            yield from page.notes
            if not page.has_more:
                return
            if page.cursor is None or page.cursor == cursor:
                msg = "Granola returned hasMore without a new cursor"
                raise GranolaApiError(msg)
            cursor = page.cursor

    def get_note(self, note_id: str, *, include_transcript: bool = True) -> NoteDetail:
        """Fetch one note, optionally including its transcript."""
        if not note_id.startswith("not_"):
            msg = "note_id must use Granola's not_ identifier"
            raise ValueError(msg)
        params = {"include": "transcript"} if include_transcript else {}
        return self._get_model(f"/v1/notes/{note_id}", params, NoteDetail)

    def iter_folders(self, *, page_size: int = 30) -> Iterator[Folder]:
        """Yield every accessible folder across cursor pages."""
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            msg = "page_size must be between 1 and 30"
            raise ValueError(msg)
        cursor: str | None = None
        while True:
            params = {"page_size": str(page_size)}
            if cursor is not None:
                params["cursor"] = cursor
            page = self._get_model("/v1/folders", params, ListFoldersResponse)
            yield from page.folders
            if not page.has_more:
                return
            if page.cursor is None or page.cursor == cursor:
                msg = "Granola returned hasMore without a new cursor"
                raise GranolaApiError(msg)
            cursor = page.cursor

    def _get_model(
        self,
        path: str,
        params: dict[str, str],
        model: type[ResponseModel],
    ) -> ResponseModel:
        response = self._request(path, params)
        try:
            return model.model_validate_json(response.text)
        except ValidationError as error:
            msg = f"Granola returned an invalid response for {path}"
            raise GranolaApiError(msg) from error

    def _request(self, path: str, params: dict[str, str]) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            self._pacer.wait()
            try:
                response = self._client.get(path, params=params)
            except httpx.TransportError as error:
                if attempt == self._max_retries:
                    msg = f"Granola request failed after {attempt + 1} attempts"
                    raise GranolaApiError(msg) from error
                self._sleeper(2.0**attempt)
                continue
            if response.status_code not in RETRYABLE_STATUS_CODES:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as error:
                    msg = f"Granola returned HTTP {response.status_code} for {path}"
                    raise GranolaApiError(msg) from error
                return response
            if attempt == self._max_retries:
                msg = f"Granola returned HTTP {response.status_code} after {attempt + 1} attempts"
                raise GranolaApiError(msg)
            self._sleeper(self._retry_delay(response, attempt))
        msg = "Granola retry loop ended unexpectedly"
        raise GranolaApiError(msg)

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after_values = response.headers.get_list("Retry-After")
        if retry_after_values:
            try:
                return max(float(retry_after_values[0]), 0.0)
            except ValueError:
                pass
        return min(2.0**attempt, 30.0)

    @staticmethod
    def _note_params(query: ListNotesQuery, cursor: str | None) -> dict[str, str]:
        params = {"page_size": str(query.page_size)}
        dates = {
            "created_before": query.created_before,
            "created_after": query.created_after,
            "updated_after": query.updated_after,
        }
        for name, value in dates.items():
            if value is not None:
                params[name] = _utc_isoformat(value)
        if query.folder_id is not None:
            params["folder_id"] = query.folder_id
        if cursor is not None:
            params["cursor"] = cursor
        return params


def _utc_isoformat(value: datetime) -> str:
    """Serialize a datetime using Granola's UTC query format."""
    if value.tzinfo is None:
        msg = "Granola date filters must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
