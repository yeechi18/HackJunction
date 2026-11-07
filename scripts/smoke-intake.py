"""Real MiMo M3 acceptance. Creates only new, explicitly fictional orders."""

import json
import subprocess
import sys
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx

from company.config import REPO_ROOT

FACTS = [
    {"key": "material", "value": "silicone", "confirmed": True},
    {"key": "pack_quantity", "value": "6", "confirmed": True},
    {"key": "use_cases", "value": "Organizing cables on a desk", "confirmed": True},
]


def worker(order_id):
    result = subprocess.run(
        [sys.executable, "-m", "company.worker", "--drain", "--order-id", order_id, "--intake-only"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    if result.returncode:
        raise RuntimeError("The isolated worker process did not complete successfully.")


def main():
    checks = []
    with httpx.Client(base_url="http://127.0.0.1:3000/api/v1", timeout=20) as client:
        seller_file = REPO_ROOT / "var/local-test-seller.txt"
        assert seller_file.exists(), "Register and verify a seller in the UI, then set email/password in var/local-test-seller.txt."
        seller_credentials = dict(line.split("=", 1) for line in seller_file.read_text().splitlines() if "=" in line)
        login = client.post("/seller/auth/login", json=seller_credentials)
        assert login.status_code == 200, "Verified seller login failed."
        created = client.post("/orders", headers={"X-CSRF-Token": login.json()["csrf_token"]}, json={
            "seller_name": "M3验收模拟卖家", "product_name": "[M3验收]虚构桌面理线夹",
            "facts": FACTS, "is_demo": True,
        })
        assert created.status_code == 201, "Could not create the fictional intake order."
        order = created.json()
        order_id = order["id"]
        token = parse_qs(urlsplit(order["private_url"]).fragment)["token"][0]
        exchange = client.post("/order-access/exchange", json={"order_id": order_id, "token": token})
        assert exchange.status_code == 200
        seller_csrf = {"X-CSRF-Token": exchange.json()["csrf_token"]}

        worker(order_id)
        current = client.get(f"/orders/{order_id}").json()
        assert current["phase"] == "NEEDS_INFO", "Missing dimensions must block acceptance."
        messages = client.get(f"/orders/{order_id}/messages").json()["items"]
        assert any(message["role"] == "assistant" and message["content"] for message in messages)
        checks.append("real_intake_requests_missing_dimensions")

        complete_facts = [*FACTS, {
            "key": "dimensions", "value": "4 x 3 x 2", "unit": "cm", "confirmed": True,
        }]
        response = client.post(f"/orders/{order_id}/supplements", headers=seller_csrf, json={
            "expected_revision": current["revision"], "facts": complete_facts,
        })
        assert response.status_code == 200
        worker(order_id)  # A new OS process reconstructs the persistent graph and resumes it.
        current = client.get(f"/orders/{order_id}").json()
        assert current["phase"] == "AWAITING_ACCEPTANCE"
        checks.append("new_worker_process_resumes_after_supplement")

        credentials = dict(line.split("=", 1) for line in
                           (REPO_ROOT / "var/local-admin.txt").read_text().splitlines())
        login = client.post("/internal/auth/login", json=credentials)
        assert login.status_code == 200
        owner_csrf = {"X-CSRF-Token": login.json()["csrf_token"],
                      "Idempotency-Key": f"m3-accept:{order_id}"}
        response = client.post(f"/internal/orders/{order_id}/decisions", headers=owner_csrf,
                               json={"action": "accept", "expected_revision": current["revision"]})
        assert response.status_code == 202
        assert client.get(f"/orders/{order_id}").json()["phase"] == "AWAITING_ACCEPTANCE"
        checks.append("queued_approval_is_not_reported_as_applied")
        worker(order_id)
        detail = client.get(f"/internal/orders/{order_id}").json()
        assert detail["phase"] == "RESEARCH"
        assert len(detail["agent_runs"]) == 2
        assert all(run["status"] == "succeeded" for run in detail["agent_runs"])
        checks.append("owner_approval_moves_to_research")
        worker(order_id)
        repeated = client.get(f"/internal/orders/{order_id}").json()
        assert len(repeated["agent_runs"]) == 2
        checks.append("idle_restart_does_not_repeat_model_calls")

        report = {
            "created_at": datetime.now(UTC).isoformat(), "is_demo": True,
            "model_mode": "live", "order_id": order_id,
            "checks": [{"name": name, "passed": True} for name in checks],
            "total_tokens": sum(run["usage"].get("total_tokens", 0)
                                for run in detail["agent_runs"]),
            "phase": detail["phase"],
        }
        (REPO_ROOT / "var/reports/m3-intake.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"M3 real model + separate worker process acceptance: {len(checks)} checks passed.")
        print("Safe report: var/reports/m3-intake.json")


if __name__ == "__main__":
    main()
