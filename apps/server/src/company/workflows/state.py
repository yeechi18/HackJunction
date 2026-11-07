from typing import TypedDict


class IntakeState(TypedDict, total=False):
    order_id: str
    command_id: str
    phase: str
    route: str
    production_round: int
    version_id: str
