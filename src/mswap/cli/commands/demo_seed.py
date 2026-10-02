"""Hidden developer command to seed a fake demo environment (MSWAP_DEMO=1).

Populates MSWAP_HOME with fake accounts, demo usage cache, and fake vault credentials.
Must never be used with real credentials or in production environments.
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta

from mswap.agy.tokens import fingerprint
from mswap.cli.context import AppContext
from mswap.core.models import Account
from mswap.core.store import LIVE_USER, live_target, slot_target


def _make_blob(refresh_token: str, access_token: str) -> bytes:
    data = {
        "token": {
            "refresh_token": refresh_token,
            "access_token": access_token,
            "expiry": "2099-01-01T00:00:00.000000Z",
            "token_type": "Bearer",
        }
    }
    return json.dumps(data).encode("utf-8")


def run(ctx: AppContext, _args: argparse.Namespace) -> int:
    """Seed the demo data directory with 3 fake accounts and usage cache."""
    now = ctx.clock.now()

    blob1 = _make_blob("demo-rt-alice", "ya29.FAKE-demo-alice")
    blob2 = _make_blob("demo-rt-bob", "ya29.FAKE-demo-bob")
    blob3 = _make_blob("demo-rt-carol", "ya29.FAKE-demo-carol")

    fp1 = fingerprint(blob1)
    fp2 = fingerprint(blob2)
    fp3 = fingerprint(blob3)

    accounts = [
        Account(
            slot=1,
            email="alice@example.com",
            fp=fp1,
            added_at=now,
            updated_at=now,
            alias=None,
            disabled=False,
            quarantined=None,
            plan="g1-pro-tier",
        ),
        Account(
            slot=2,
            email="bob@example.com",
            fp=fp2,
            added_at=now,
            updated_at=now,
            alias="work",
            disabled=False,
            quarantined=None,
            plan=None,
        ),
        Account(
            slot=3,
            email="carol@example.com",
            fp=fp3,
            added_at=now,
            updated_at=now,
            alias="spare",
            disabled=False,
            quarantined=None,
            plan=None,
        ),
    ]

    ctx.store.save(accounts)

    ctx.vault.write(live_target(), blob1, LIVE_USER)
    ctx.vault.write(slot_target(1), blob1, "alice@example.com")
    ctx.vault.write(slot_target(2), blob2, "bob@example.com")
    ctx.vault.write(slot_target(3), blob3, "carol@example.com")

    usage_data = {
        "schema": 1,
        "accounts": {
            fp1: {
                "fetched_at": now.isoformat(),
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "gemini-5h",
                                "window": "5h",
                                "remainingFraction": 0.92,
                                "resetTime": (now + timedelta(hours=4, minutes=12)).isoformat(),
                            },
                            {
                                "bucketId": "gemini-weekly",
                                "window": "weekly",
                                "remainingFraction": 0.98,
                                "resetTime": (now + timedelta(days=5, hours=18)).isoformat(),
                            },
                        ],
                    },
                    {
                        "displayName": "Claude and GPT models",
                        "buckets": [
                            {
                                "bucketId": "3p-5h",
                                "window": "5h",
                                "remainingFraction": 1.0,
                                "resetTime": None,
                            },
                            {
                                "bucketId": "3p-weekly",
                                "window": "weekly",
                                "remainingFraction": 1.0,
                                "resetTime": None,
                            },
                        ],
                    },
                ],
            },
            fp2: {
                "fetched_at": now.isoformat(),
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "gemini-5h",
                                "window": "5h",
                                "remainingFraction": 0.45,
                                "resetTime": (now + timedelta(hours=2, minutes=30)).isoformat(),
                            },
                            {
                                "bucketId": "gemini-weekly",
                                "window": "weekly",
                                "remainingFraction": 0.72,
                                "resetTime": (now + timedelta(days=3, hours=8)).isoformat(),
                            },
                        ],
                    },
                    {
                        "displayName": "Claude and GPT models",
                        "buckets": [
                            {
                                "bucketId": "3p-5h",
                                "window": "5h",
                                "remainingFraction": 0.85,
                                "resetTime": (now + timedelta(hours=3, minutes=10)).isoformat(),
                            },
                            {
                                "bucketId": "3p-weekly",
                                "window": "weekly",
                                "remainingFraction": 0.90,
                                "resetTime": (now + timedelta(days=4, hours=14)).isoformat(),
                            },
                        ],
                    },
                ],
            },
            fp3: {
                "fetched_at": now.isoformat(),
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "gemini-5h",
                                "window": "5h",
                                "remainingFraction": 0.15,
                                "resetTime": (now + timedelta(hours=1, minutes=5)).isoformat(),
                            },
                            {
                                "bucketId": "gemini-weekly",
                                "window": "weekly",
                                "remainingFraction": 0.35,
                                "resetTime": (now + timedelta(days=1, hours=20)).isoformat(),
                            },
                        ],
                    },
                    {
                        "displayName": "Claude and GPT models",
                        "buckets": [
                            {
                                "bucketId": "3p-5h",
                                "window": "5h",
                                "remainingFraction": 0.50,
                                "resetTime": (now + timedelta(hours=2, minutes=45)).isoformat(),
                            },
                            {
                                "bucketId": "3p-weekly",
                                "window": "weekly",
                                "remainingFraction": 0.60,
                                "resetTime": (now + timedelta(days=2, hours=12)).isoformat(),
                            },
                        ],
                    },
                ],
            },
        },
    }

    usage_file = ctx.store.root / "usage.json"
    usage_file.write_text(json.dumps(usage_data, indent=2), encoding="utf-8")

    print("Demo environment seeded successfully.", file=ctx.out)
    return 0
