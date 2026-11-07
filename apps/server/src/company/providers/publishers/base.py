from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublisherResult:
    status: str
    mode: str = "simulated"
    listing_url: str | None = None
    external_id: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class ListingPublisher(Protocol):
    mode: str

    async def validate(self, snapshot: dict) -> ValidationResult: ...
    async def submit(self, snapshot: dict, *, operation_key: str) -> PublisherResult: ...
    async def get_status(self, *, operation_key: str) -> PublisherResult: ...
