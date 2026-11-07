"""Opt-in real MiMo acceptance, using only visibly fictional orders and publication.

Run with uv run --project apps/server python scripts/smoke-closed-loop.py --stage m5.
Later stages resume the same local case; this intentionally consumes model tokens.
The ignored local state contains an order token; never share it.
"""

import argparse
import io
import json
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
from company.config import REPO_ROOT, Settings
from PIL import Image, ImageDraw, ImageFont

STATE = REPO_ROOT / "var/local-acceptance.json"
REPORT = REPO_ROOT / "var/reports/closed-loop-live.json"
FACTS = [
    {"key": "material", "value": "silicone", "confirmed": True},
    {"key": "color", "value": "black", "confirmed": True},
    {"key": "pack_quantity", "value": "6", "unit": "pieces", "confirmed": True},
    {"key": "dimensions", "value": "4 x 3 x 2", "unit": "cm", "confirmed": True},
    {
        "key": "dimension_scope",
        "value": "each individual cable clip",
        "confirmed": True,
    },
    {"key": "cable_diameter", "value": "6", "unit": "mm", "confirmed": True},
    {
        "key": "use_cases",
        "value": "Organizing desk cables with a diameter up to 6 mm",
        "confirmed": True,
    },
]


def worker(order_id: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "company.worker", "--drain", "--order-id", order_id],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "The isolated worker did not complete; inspect internal safe diagnostics."
        )


def reference(quantity: int) -> bytes:
    canvas = Image.new("RGB", (1200, 900), "#f6f3ec")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=30)
    draw.text(
        (50, 40), "FICTIONAL DEMO - cable clip diagram", fill="#242424", font=font
    )
    for index in range(quantity):
        x, y = 120 + (index % 3) * 340, 200 + (index // 3) * 260
        draw.rounded_rectangle((x, y, x + 200, y + 120), radius=50, fill="#242424")
        draw.rectangle((x + 90, y, x + 110, y + 80), fill="#f6f3ec")
    draw.text(
        (50, 820),
        "Invented fixture. Not a product photograph.",
        fill="#242424",
        font=font,
    )
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def main(stage: str) -> None:
    settings = Settings()
    assert settings.mimo_mode == "live" and settings.publish_mode == "simulated"
    assert settings.app_env == "development", (
        "Run this opt-in probe only against local development."
    )
    checks = json.loads(REPORT.read_text())["checks"] if REPORT.exists() else []

    def record(name, **details):
        checks.append({"name": name, "passed": True, **details})
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now(UTC).isoformat(),
                    "is_demo": True,
                    "model_mode": "live",
                    "publish_mode": "simulated",
                    "order_id": order_id,
                    "checks": checks,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"PASS {name}", flush=True)

    with (
        httpx.Client(base_url="http://127.0.0.1:3000/api/v1", timeout=30) as seller,
        httpx.Client(base_url="http://127.0.0.1:3000/api/v1", timeout=30) as owner,
    ):
        seller_file = REPO_ROOT / "var/local-test-seller.txt"
        assert seller_file.exists(), "Register and verify a seller in the UI, then set email/password in var/local-test-seller.txt."
        seller_credentials = dict(line.split("=", 1) for line in seller_file.read_text().splitlines() if "=" in line)
        login = seller.post("/seller/auth/login", json=seller_credentials)
        assert login.status_code == 200, "Verified seller login failed."
        if STATE.exists():
            state = json.loads(STATE.read_text())
        else:
            assert stage == "m5", "Create the initial case using --stage m5 first."
            r = seller.post(
                "/orders",
                headers={"X-CSRF-Token": login.json()["csrf_token"]},
                json={

                    "product_name": "Fictional Silicone Desk Cable Clips",
                    "facts": FACTS,
                    "is_demo": True,
                    "notes": "明确标记的虚构模拟订单；非真实商品、实拍、成交或Amazon上架。",
                },
            )
            r.raise_for_status()
            state = r.json()
            STATE.write_text(json.dumps(state), encoding="utf-8")
        order_id = state["id"]
        token = parse_qs(urlsplit(state["private_url"]).fragment)["token"][0]
        r = seller.post(
            "/order-access/exchange", json={"order_id": order_id, "token": token}
        )
        r.raise_for_status()
        seller_headers = {"X-CSRF-Token": r.json()["csrf_token"]}
        credentials = dict(
            line.split("=", 1)
            for line in (REPO_ROOT / "var/local-admin.txt").read_text().splitlines()
        )
        r = owner.post("/internal/auth/login", json=credentials)
        r.raise_for_status()
        owner_headers = {"X-CSRF-Token": r.json()["csrf_token"]}
        private = f"/orders/{order_id}"
        internal = f"/internal/orders/{order_id}"

        def current():
            r = seller.get(private)
            r.raise_for_status()
            return r.json()

        def production():
            r = owner.get(internal + "/production")
            r.raise_for_status()
            return r.json()

        def upload(quantity):
            r = seller.post(
                private + "/assets",
                headers=seller_headers,
                files={
                    "file": (
                        "fictional-reference.png",
                        reference(quantity),
                        "image/png",
                    )
                },
            )
            r.raise_for_status()

        def release(version, key):
            r = owner.post(
                internal + "/decisions",
                headers={**owner_headers, "Idempotency-Key": key},
                json={
                    "action": "release",
                    "expected_revision": current()["revision"],
                    "version_id": version["id"],
                },
            )
            assert r.status_code == 202, (
                "Only a fully reviewed current version may be queued."
            )
            assert current()["phase"] == "AWAITING_RELEASE"
            worker(order_id)
            assert current()["phase"] == "AWAITING_SELLER"

        if stage == "m5":
            if current()["phase"] in {"INTAKE", "NEEDS_INFO"}:
                worker(order_id)
            assert current()["phase"] in {
                "AWAITING_ACCEPTANCE",
                "AWAITING_RELEASE",
                "AWAITING_SELLER",
            }
            if current()["phase"] == "AWAITING_ACCEPTANCE":
                upload(6)
                r = owner.post(
                    internal + "/decisions",
                    headers={
                        **owner_headers,
                        "Idempotency-Key": "closed-loop-initial-accept",
                    },
                    json={
                        "action": "accept",
                        "expected_revision": current()["revision"],
                    },
                )
                assert r.status_code == 202
                worker(order_id)
            data = production()
            version = data["versions"][0]
            assert version["text_passed"] and version["full_passed"], (
                "Real text/image review must pass."
            )
            assert any(
                run["role"] == "research" and run["model"] == settings.mimo_model
                for run in data["runs"]
            )
            record(
                "m4_real_research_generation_and_review",
                version_number=version["version_number"],
            )
            if current()["phase"] == "AWAITING_RELEASE":
                assert seller.get(private + "/deliverables").json()["items"] == []
                assert seller.get(private + "/download").status_code == 404
                record("m5_unreleased_draft_is_private")
                release(version, "closed-loop-initial-release")
            deliveries = seller.get(private + "/deliverables").json()["items"]
            assert deliveries and len(deliveries[0]["images"]) >= 2
            r = seller.get(private + "/download", params={"version_id": version["id"]})
            r.raise_for_status()
            archive = zipfile.ZipFile(io.BytesIO(r.content))
            assert any(name.endswith(".png") for name in archive.namelist())
            assert not any(
                "receipt" in name or "research" in name for name in archive.namelist()
            )
            (REPO_ROOT / "var/reports/delivered-demo.zip").write_bytes(r.content)
            with httpx.Client(base_url="http://127.0.0.1:3000/api/v1") as visitor:
                assert visitor.get(private + "/download").status_code in {401, 404}
            record(
                "m5_real_images_release_and_scoped_zip",
                image_count=len(deliveries[0]["images"]),
            )
        elif stage == "feedback":
            details = current()
            assert details["phase"] in {"AWAITING_SELLER", "AWAITING_RELEASE"}
            old = seller.get(private + "/deliverables").json()["items"][0]
            updates = [dict(fact) for fact in details["facts"]]
            next(fact for fact in updates if fact["key"] == "pack_quantity")[
                "value"
            ] = "4"
            if details["phase"] == "AWAITING_SELLER" and details["revision"] == 1:
                upload(4)
                r = seller.post(
                    private + "/feedback",
                    headers={
                        **seller_headers,
                        "Idempotency-Key": "closed-loop-quantity-feedback",
                    },
                    json={
                        "expected_revision": details["revision"],
                        "version_id": old["version_id"],
                        "content": "确认每包从6个改为4个理线夹。线径仍为6 mm，单件尺寸及其他事实保持不变。新参考图是4个。",
                        "fact_updates": updates,
                    },
                )
                assert r.status_code == 202
                worker(order_id)
            data = production()
            version = data["versions"][0]
            assert version["full_passed"]
            versions = {item["id"]: item for item in data["versions"]}
            ancestor = version
            seen = set()
            while ancestor["parent_version_id"] and ancestor["id"] not in seen:
                seen.add(ancestor["id"])
                ancestor = versions[ancestor["parent_version_id"]]
            assert old["version_id"] in seen | {ancestor["id"]}
            assert "4" in version["draft"]["title"]
            facts = current()["facts"]
            assert (
                next(fact for fact in facts if fact["key"] == "cable_diameter")["value"]
                == "6"
            )
            assert (
                next(fact for fact in facts if fact["key"] == "pack_quantity")["value"]
                == "4"
            )
            if current()["phase"] == "AWAITING_RELEASE":
                release(version, "closed-loop-feedback-release")
            record(
                "m6_real_fact_feedback_research_new_version",
                version_number=version["version_number"],
            )
        else:
            details = current()
            deliveries = seller.get(private + "/deliverables").json()["items"]
            assert details["phase"] in {"AWAITING_SELLER", "COMPLETED"}
            if details["phase"] != "COMPLETED":
                r = seller.post(
                    private + "/confirmations",
                    headers={
                        **seller_headers,
                        "Idempotency-Key": "closed-loop-confirm-publish",
                    },
                    json={
                        "expected_revision": details["revision"],
                        "version_id": deliveries[0]["version_id"],
                        "listing_price": "12.99",
                        "currency": "USD",
                        "seller_sku": "FICTIONAL-CLIPS-DEMO",
                    },
                )
                assert r.status_code == 202
                worker(order_id)
            assert current()["phase"] == "COMPLETED"
            publication = seller.get(private + "/publication").json()
            assert (
                publication["status"] == "SIMULATED"
                and publication["mode"] == "simulated"
            )
            assert publication["confirmation"]["listing_price"] == "12.99"
            assert publication["attempt"]["submit_count"] == 1
            demo_path = urlsplit(publication["listing_url"]).path
            r = httpx.get("http://127.0.0.1:3000/api/v1" + demo_path, timeout=30)
            r.raise_for_status()
            assert r.json()["mode"] == "simulated" and "模拟" in r.json()["notice"]
            record(
                "m6_real_publishing_agent_and_simulated_listing", listing_path=demo_path
            )
            before = len(production()["runs"])
            worker(order_id)
            assert len(production()["runs"]) == before
            record("idle_worker_restart_has_no_new_production_calls")
    print("Safe report: var/reports/closed-loop-live.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["m5", "feedback", "publish"], default="m5")
    main(parser.parse_args().stage)
