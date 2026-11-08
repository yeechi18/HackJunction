from enum import StrEnum


class OrderPhase(StrEnum):
    INTAKE = "INTAKE"
    NEEDS_INFO = "NEEDS_INFO"
    NEEDS_IMAGE = "NEEDS_IMAGE"
    AWAITING_ACCEPTANCE = "AWAITING_ACCEPTANCE"
    RESEARCH = "RESEARCH"
    GENERATION = "GENERATION"
    REVIEW = "REVIEW"
    AWAITING_RELEASE = "AWAITING_RELEASE"
    AWAITING_SELLER = "AWAITING_SELLER"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    NEEDS_PUBLISH_INFO = "NEEDS_PUBLISH_INFO"
    PUBLISHING = "PUBLISHING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


def allowed_actions(phase: str, *, internal: bool, pending: bool) -> list[str]:
    if pending:
        return []
    if internal:
        if phase == OrderPhase.AWAITING_ACCEPTANCE:
            return ["accept", "reject"]
        if phase == OrderPhase.AWAITING_RELEASE:
            return ["release", "veto"]
        return []
    if phase in {
        OrderPhase.INTAKE,
        OrderPhase.NEEDS_INFO,
        OrderPhase.AWAITING_ACCEPTANCE,
        OrderPhase.AWAITING_RELEASE,
    }:
        return ["message", "supplement"]
    if phase == OrderPhase.NEEDS_IMAGE:
        return ["upload_image"]
    if phase == OrderPhase.AWAITING_SELLER:
        return ["feedback", "confirm_publish"]
    return []
