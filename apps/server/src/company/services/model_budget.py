"""Check persisted per-order usage before actual model calls, including feedback requests."""

from sqlalchemy import func, select

from company.persistence.agent_runs import AgentRun
from company.persistence.production import DeliverableVersion, ProductionStepReceipt
from company.persistence.publication import PublishAttempt
from company.providers.mimo import ProviderError
from company.providers.usage import TOKEN_FIELDS, UsageTrackingProvider


class OrderBudgetExceeded(ProviderError):
    pass


def recorded_tokens(usage: dict) -> int:
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total
    parts = [
        usage.get("input_tokens", usage.get("prompt_tokens")),
        usage.get("output_tokens", usage.get("completion_tokens")),
    ]
    return sum(
        value
        for value in parts
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    )


async def check_budget(database, settings, order_id, *, new_candidate=False):
    async with database.sessions() as session:
        usages = []
        for column, model in [
            (AgentRun.usage, AgentRun),
            (ProductionStepReceipt.usage, ProductionStepReceipt),
            (PublishAttempt.plan_usage, PublishAttempt),
        ]:
            usages.extend(await session.scalars(select(column).where(model.order_id == order_id)))

        # This is a recorded-token limit, not an estimate of unknown tokens or monetary cost.
        def reported(value):
            return isinstance(value, int) and not isinstance(value, bool) and value >= 0

        for usage in usages:
            if usage.get("_model_calls") and not (
                reported(usage.get("total_tokens"))
                or all(reported(usage.get(key)) for key in TOKEN_FIELDS[:2])
            ):
                raise OrderBudgetExceeded(
                    "本单存在未返回完整 token 用量的调用，请负责人核查后继续。"
                )
        if sum(recorded_tokens(usage or {}) for usage in usages) >= settings.order_token_budget:
            raise OrderBudgetExceeded("本单已记录模型 token 用量达到预算，请负责人检查后调整预算。")
        if new_candidate:
            count = await session.scalar(
                select(func.count())
                .select_from(DeliverableVersion)
                .where(
                    DeliverableVersion.order_id == order_id,
                )
            )
            if count >= settings.order_candidate_budget:
                raise OrderBudgetExceeded("本单候选版本达到预算，已停止自动生成，请负责人处理。")


class BudgetedProvider(UsageTrackingProvider):
    def __init__(self, provider, database, settings, order_id):
        super().__init__(provider)
        self.database, self.configuration, self.order_id = database, settings, order_id

    async def generate_json(self, schema, messages, **kwargs):
        self.last_call = None
        await check_budget(
            self.database,
            self.configuration,
            self.order_id,
            new_candidate=schema.__name__ == "ListingDraft",
        )
        return await super().generate_json(schema, messages, **kwargs)
