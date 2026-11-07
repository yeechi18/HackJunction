"""One graph owns intake, human decisions and the bounded M4 production loop."""

from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from company.workflows.state import IntakeState

if TYPE_CHECKING:
    from company.services.workflow import WorkflowContext


def build_graph(context: "WorkflowContext", checkpointer):
    async def dispatch(state: IntakeState):
        return await context.dispatch(state)

    async def intake(state: IntakeState):
        return await context.intake(state)

    async def decision(state: IntakeState):
        return await context.decision(state)

    async def research(state: IntakeState):
        return await context.research(state)

    async def generation(state: IntakeState):
        return await context.generation(state)

    async def review(state: IntakeState):
        return await context.review(state)

    async def render_images(state: IntakeState):
        return await context.render_images(state)

    async def delivery_decision(state: IntakeState):
        return await context.delivery_decision(state)

    async def feedback(state: IntakeState):
        return await context.feedback(state)

    async def publish(state: IntakeState):
        return await context.publish(state)

    async def await_external(state: IntakeState):
        response = interrupt(
            {
                "order_id": state["order_id"],
                "phase": state["phase"],
                "wait_id": state["command_id"],
            }
        )
        if not isinstance(response, dict) or response.get("wait_id") != state["command_id"]:
            raise ValueError("Resume command does not match this workflow wait.")
        return {"command_id": response["command_id"], "production_round": 0, "version_id": ""}

    graph = StateGraph(IntakeState)
    graph.add_node("dispatch", dispatch)
    graph.add_node("intake", intake)
    graph.add_node("decision", decision)
    graph.add_node("research", research)
    graph.add_node("generation", generation)
    graph.add_node("review", review)
    graph.add_node("render_images", render_images)
    graph.add_node("delivery_decision", delivery_decision)
    graph.add_node("feedback", feedback)
    graph.add_node("publish", publish)
    graph.add_node("await_external", await_external)
    graph.add_edge(START, "dispatch")
    graph.add_conditional_edges(
        "dispatch",
        lambda state: state["route"],
        {
            "intake": "intake",
            "decision": "decision",
            "research": "research",
            "render_images": "render_images",
            "delivery_decision": "delivery_decision",
            "feedback": "feedback",
            "publish": "publish",
            "wait": "await_external",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "intake",
        lambda state: state["route"],
        {
            "wait": "await_external",
            "end": END,
        },
    )
    for node in (
        "decision",
        "research",
        "generation",
        "review",
        "render_images",
        "delivery_decision",
        "feedback",
        "publish",
    ):
        graph.add_conditional_edges(
            node,
            lambda state: state["route"],
            {
                "research": "research",
                "generation": "generation",
                "review": "review",
                "render_images": "render_images",
                "wait": "await_external",
                "end": END,
            },
        )
    graph.add_edge("await_external", "dispatch")
    return graph.compile(checkpointer=checkpointer)
