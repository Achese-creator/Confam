import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module


client = TestClient(main_module.app)


class FakeUpdateQueue:
    def __init__(self):
        self.items = asyncio.Queue()

    async def put(self, update):
        await self.items.put(update)


class FakeTelegramApplication:
    def __init__(self):
        self.bot = object()
        self.update_queue = FakeUpdateQueue()


def configure_fake_bot(monkeypatch):
    fake = FakeTelegramApplication()
    monkeypatch.setattr(
        main_module.app.state,
        "telegram_application",
        fake,
        raising=False,
    )
    return fake


def test_webhook_returns_503_when_bot_is_not_configured(monkeypatch):
    monkeypatch.setattr(
        main_module.app.state,
        "telegram_application",
        None,
        raising=False,
    )
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test_secret")

    response = client.post("/telegram/webhook", json={"update_id": 1})

    assert response.status_code == 503
    assert response.json()["detail"] == "Telegram bot is not configured."


def test_webhook_returns_503_when_secret_is_missing(monkeypatch):
    configure_fake_bot(monkeypatch)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

    response = client.post("/telegram/webhook", json={"update_id": 1})

    assert response.status_code == 503
    assert "secret is not configured" in response.json()["detail"]


def test_webhook_rejects_missing_or_incorrect_secret(monkeypatch):
    configure_fake_bot(monkeypatch)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "expected_secret")

    missing = client.post("/telegram/webhook", json={"update_id": 1})
    incorrect = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret"},
    )

    assert missing.status_code == 403
    assert incorrect.status_code == 403
    assert missing.json()["detail"] == "Invalid webhook secret."


def test_webhook_rejects_malformed_json(monkeypatch):
    configure_fake_bot(monkeypatch)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "expected_secret")

    response = client.post(
        "/telegram/webhook",
        content="{not valid json",
        headers={
            "Content-Type": "application/json",
            "X-Telegram-Bot-Api-Secret-Token": "expected_secret",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid Telegram update."


def test_webhook_queues_valid_telegram_update(monkeypatch):
    fake = configure_fake_bot(monkeypatch)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "expected_secret")

    payload = {
        "update_id": 123456,
        "message": {
            "message_id": 1,
            "date": 1760000000,
            "chat": {"id": 12345, "type": "private"},
            "from": {
                "id": 12345,
                "is_bot": False,
                "first_name": "Test",
            },
            "text": "/start",
        },
    }

    response = client.post(
        "/telegram/webhook",
        json=payload,
        headers={
            "X-Telegram-Bot-Api-Secret-Token": "expected_secret",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    queued_update = fake.update_queue.items.get_nowait()
    assert queued_update.update_id == 123456
