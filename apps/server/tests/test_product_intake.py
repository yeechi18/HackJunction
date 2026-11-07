import json

import pytest
from pydantic import ValidationError
from test_acquisition import StubProvider

from company.agents.acquisition import inspect_intake
from company.schemas.orders import SubmitOrder


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "file:///tmp/example",
        "ftp://example.com/a",
        "https://user:password@example.com/a",
        "https://example.com/a\nb",
    ],
)
def test_product_reference_rejects_non_http_or_credential_urls(value):
    with pytest.raises(ValidationError):
        SubmitOrder(product_name="test", domestic_product_url=value)


async def test_product_details_reach_agent_without_becoming_confirmed_facts():
    provider = StubProvider()
    details = "silicone clips, six pieces, 4x3x2 cm, desk use"
    url = "https://item.jd.com/100000000000.html"
    result = await inspect_intake(
        {
            "product_name": "Clips",
            "product_details": details,
            "domestic_product_url": url,
            "facts": [],
        },
        provider,
    )
    context = json.loads(provider.calls[0][0][1]["content"])
    assert context["product_details"] == details and context["domestic_product_url"] == url
    assert result.ready_for_acceptance is False
    assert set(result.missing_fields) == {"material", "dimensions", "pack_quantity", "use_cases"}
