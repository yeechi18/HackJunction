"""Reviewed public brand records. Never infer a pictured SKU's variant from its brand."""

import re
from decimal import Decimal


def known_brand(product_name: str) -> dict | None:
    name = product_name.casefold()
    if re.search(r"不是|非百岁山|仿|贴纸|杯|收纳|配件|holder|case|sticker", name):
        return None
    if "百岁山" not in name and not re.search(r"\bganten\b", name):
        return None
    return {
        "brand": "Ganten / 百岁山",
        "category": "bottled_water",
        "reviewed_on": "2026-11-08",
        "sources": [
            "https://www.ganten.com.cn/product01.html",
            "https://www.ganten.com.cn/p/about.html?lang=en",
        ],
        "facts": [
            {"key": "brand", "value": "Ganten / 百岁山", "confirmed": True},
            {"key": "product_type", "value": "天然矿泉水", "confirmed": True},
            {"key": "use_cases", "value": "饮用", "confirmed": True},
        ],
        "variant_options": ["348 mL", "570 mL", "1 L", "4.5 L"],
        "scope": "官网品牌与产品类别资料；不证明本瓶容量、包装材质、矿物质数值或销售授权。",
    }


def required_keys(order_input):
    if known_brand(str(order_input.get("product_name", ""))):
        return ("pack_quantity", "net_volume", "use_cases")
    return ()


def missing_delivery_fields(product_name: str, facts: list[dict]) -> list[str]:
    """Category-specific delivery checks; never block intake or infer a SKU variant."""
    missing = []
    for key in required_keys({"product_name": product_name}):
        records = [f for f in facts if f.get("key") == key and f.get("confirmed") is True]
        values = {(f.get("value", "").strip(), f.get("unit")) for f in records}
        if len(values) != 1:
            missing.append(key)
            continue
        value, unit = next(iter(values))
        valid = bool(value) and value.casefold() not in {"unknown", "n/a", "待确认", "未知"}
        if key == "net_volume":
            valid = (
                bool(re.fullmatch(r"\d+(?:\.\d+)?", value))
                and Decimal(value) > 0
                and unit in {"mL", "ml", "L", "l", "毫升", "升"}
            )
        if key == "pack_quantity":
            valid = bool(re.fullmatch(r"[1-9]\d*", value))
        if not valid:
            missing.append(key)
    return missing
