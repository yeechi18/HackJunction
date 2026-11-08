"""Ark image generation with inline references and bounded, credential-free errors."""

import base64
import binascii
import json
from dataclasses import dataclass

import httpx

from company.providers.mimo import ProviderError
from company.providers.storage import MAX_IMAGE_BYTES, validate_and_normalize

ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/images/generations"


class ImageCallUncertain(ProviderError):
    """The remote call may have completed; never automatically bill a second call."""


@dataclass(frozen=True)
class SeedreamResult:
    data: bytes
    model: str
    request_id: str | None
    usage: dict


async def generate_image(settings, prompt: str, references: list[bytes], *, transport=None):
    if not settings.seedream_enabled or not settings.seedream_api_key:
        raise ProviderError("Seedream 未启用或未配置图片 API 密钥。")
    if len(references) > 6:
        raise ProviderError("当前单次任务最多支持 6 张参考图，请先整理同一商品的参考素材。")
    body = {
        "model": settings.seedream_model,
        "prompt": prompt,
        "response_format": "b64_json",
        "size": "2K",
        "stream": False,
        "watermark": True,
    }
    if references:
        body["image"] = [
            "data:image/png;base64," + base64.b64encode(validate_and_normalize(data).data).decode()
            for data in references
        ]
    try:
        async with httpx.AsyncClient(
            timeout=settings.seedream_timeout_seconds, transport=transport, follow_redirects=False
        ) as client:
            async with client.stream(
                "POST",
                ENDPOINT,
                json=body,
                headers={
                    "Authorization": "Bearer " + settings.seedream_api_key.get_secret_value(),
                    "Content-Type": "application/json",
                },
            ) as response:
                if response.status_code >= 400:
                    guidance = {
                        400: "请检查模型名称、图片参数或内容限制",
                        401: "图片 API 密钥无效，请更新本地配置",
                        403: "请检查图片模型是否开通及密钥权限",
                        404: "模型或接口不存在，请检查指定模型是否可用",
                        429: "额度不足或限流，请核对账户额度后重试",
                    }.get(response.status_code, "绘图服务异常，请稍后手动重试")
                    raise ProviderError(f"Seedream HTTP {response.status_code}：{guidance}。")
                if response.status_code != 200:
                    raise ProviderError("Seedream 返回非预期响应，未保存图片。")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_IMAGE_BYTES * 2:
                        raise ProviderError("Seedream 响应超过本地安全大小限制，未保存图片。")
                request_id = response.headers.get("x-request-id")
    except (httpx.TimeoutException, httpx.TransportError):
        raise ImageCallUncertain(
            "Seedream 连接中断或超时，远端可能已计费；未自动重试。请核查调用记录后手动重试。"
        ) from None
    try:
        payload = json.loads(raw)
        rows = payload.get("data", [])
        if len(rows) != 1 or not rows[0].get("b64_json"):
            raise ValueError
        data = base64.b64decode(rows[0]["b64_json"], validate=True)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError
        validate_and_normalize(data)
    except (ValueError, KeyError, TypeError, binascii.Error, AttributeError):
        raise ProviderError(
            "Seedream 未返回可读取的单张图片，可能被内容审核拦截；请检查服务记录。"
        ) from None
    usage = {
        k: v
        for k, v in (payload.get("usage") or {}).items()
        if k in {"generated_images", "output_tokens", "total_tokens"} and isinstance(v, int)
    }
    return SeedreamResult(data, settings.seedream_model, request_id, usage)
