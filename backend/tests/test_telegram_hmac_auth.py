"""Comprehensive test suite for Telegram Mini App HMAC-SHA256 authentication."""

import hashlib
import hmac
import json
import time
import urllib.parse
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import get_settings
from app.auth import validate_telegram_init_data, verify_telegram_bot_identity

client = TestClient(app)
settings = get_settings()
TEST_BOT_TOKEN = "9261050714:AAH_test_bot_token_for_hmac_auth_tests_46c"


def create_independent_telegram_init_data(
    params_dict: dict,
    bot_token: str,
    include_sig_in_hash: bool = False,
    extra_append: str = "",
) -> str:
    """
    Independently generate a Telegram-compatible HMAC initData query string.
    Strictly follows Telegram Mini App specification:
      1. Secret key = HMAC_SHA256("WebAppData", bot_token)
      2. Data-check-string = alphabetically sorted key=value joined by \n
      3. Hash = HMAC_SHA256(secret_key, data_check_string).hexdigest()
    """
    signing_params = {k: v for k, v in params_dict.items() if k != "hash"}
    if not include_sig_in_hash:
        signing_params = {k: v for k, v in signing_params.items() if k != "signature"}

    # Sort alphabetically by key
    sorted_items = sorted(signing_params.items(), key=lambda x: x[0])
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_items)

    secret_key = hmac.new(b"WebAppData", bot_token.strip().strip("'\"").encode("utf-8"), hashlib.sha256).digest()
    cal_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    all_params = dict(params_dict)
    all_params["hash"] = cal_hash

    query = urllib.parse.urlencode(all_params)
    if extra_append:
        query += extra_append
    return query


def test_telegram_hmac_valid_signature_direct_link_keys():
    """Verify validation with exact production direct-link keys: auth_date, chat_instance, chat_type, user."""
    user_payload = json.dumps({"id": 5551234, "first_name": "DirectLinkUser", "username": "direct_link"})
    params = {
        "auth_date": str(int(time.time())),
        "chat_instance": "-8912384712938471",
        "chat_type": "sender",
        "user": user_payload,
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN)

    user = validate_telegram_init_data(init_data, TEST_BOT_TOKEN)
    assert user["id"] == 5551234
    assert user["username"] == "direct_link"


def test_telegram_hmac_valid_signature_standard_keys():
    """Verify validation with standard keys: auth_date, query_id, user."""
    user_payload = json.dumps({"id": 7771234, "first_name": "StandardUser", "username": "standard_user"})
    params = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrP_V",
        "user": user_payload,
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN)

    user = validate_telegram_init_data(init_data, TEST_BOT_TOKEN)
    assert user["id"] == 7771234
    assert user["username"] == "standard_user"


def test_telegram_hmac_valid_signature_with_unicode_and_emojis():
    """Verify UTF-8 encoding stability with Unicode, Cyrillic, and emojis in user data."""
    user_payload = json.dumps({
        "id": 9998887,
        "first_name": "Елена 🎵 Night Owl",
        "last_name": "Smith & Wesson",
        "username": "elena_music",
        "language_code": "ru",
    })
    params = {
        "auth_date": str(int(time.time())),
        "chat_instance": "123456789",
        "chat_type": "private",
        "user": user_payload,
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN)

    user = validate_telegram_init_data(init_data, TEST_BOT_TOKEN)
    assert user["id"] == 9998887
    assert user["first_name"] == "Елена 🎵 Night Owl"


def test_telegram_hmac_with_bot_api_signature_parameter():
    """Verify initData containing Bot API 7.0+/8.0+ Ed25519 signature is accepted (signature excluded from HMAC)."""
    user_payload = json.dumps({"id": 4443322, "first_name": "SigUser", "username": "sig_user"})
    params = {
        "auth_date": str(int(time.time())),
        "chat_instance": "-1002345678",
        "chat_type": "sender",
        "user": user_payload,
        "signature": "AQABbW9jay1lZDI1NTE5LXNpZ25hdHVyZS1kYXRh",
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN, include_sig_in_hash=False)

    user = validate_telegram_init_data(init_data, TEST_BOT_TOKEN)
    assert user["id"] == 4443322


def test_telegram_hmac_signature_fallback_when_included():
    """Verify fallback if a client/proxy included 'signature' in the HMAC check string."""
    user_payload = json.dumps({"id": 4443323, "first_name": "SigUser2", "username": "sig_user2"})
    params = {
        "auth_date": str(int(time.time())),
        "chat_instance": "-1002345678",
        "chat_type": "sender",
        "user": user_payload,
        "signature": "AQABbW9jay1zaWduYXR1cmUtZGF0YS0y",
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN, include_sig_in_hash=True)

    user = validate_telegram_init_data(init_data, TEST_BOT_TOKEN)
    assert user["id"] == 4443323


def test_telegram_hmac_tampered_user_payload_rejected_401():
    """Verify that tampering with user payload causes HMAC signature failure (401)."""
    user_payload = json.dumps({"id": 1111111, "first_name": "Victim", "username": "victim"})
    params = {
        "auth_date": str(int(time.time())),
        "user": user_payload,
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN)

    # Attacker tampers with user ID in the query string
    tampered_init_data = init_data.replace("1111111", "9999999")
    with pytest.raises(Exception) as exc_info:
        validate_telegram_init_data(tampered_init_data, TEST_BOT_TOKEN)
    assert exc_info.value.status_code == 401
    assert "Invalid Telegram initData signature" in exc_info.value.detail


def test_telegram_hmac_tampered_hash_rejected_401():
    """Verify that a corrupted or forged 64-character hash is rejected."""
    user_payload = json.dumps({"id": 2222222, "first_name": "Bob", "username": "bob"})
    params = {
        "auth_date": str(int(time.time())),
        "user": user_payload,
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN)

    # Replace last 8 chars of hash
    tampered_init_data = init_data[:-8] + "00000000"
    with pytest.raises(Exception) as exc_info:
        validate_telegram_init_data(tampered_init_data, TEST_BOT_TOKEN)
    assert exc_info.value.status_code == 401


def test_telegram_hmac_wrong_bot_token_rejected_401():
    """Verify that initData generated with Bot Token A is rejected by Bot Token B."""
    wrong_token = "1234567890:ABC-DEF_wrong_bot_token_46chars_long123"
    user_payload = json.dumps({"id": 3333333, "first_name": "Charlie", "username": "charlie"})
    params = {
        "auth_date": str(int(time.time())),
        "user": user_payload,
    }
    init_data = create_independent_telegram_init_data(params, wrong_token)

    with pytest.raises(Exception) as exc_info:
        validate_telegram_init_data(init_data, TEST_BOT_TOKEN)
    assert exc_info.value.status_code == 401


def test_telegram_hmac_expired_auth_date_rejected_401():
    """Verify 5-minute freshness limit: auth_date > 300 seconds old is rejected."""
    user_payload = json.dumps({"id": 6666666, "first_name": "Old", "username": "old_session"})
    stale_auth_date = str(int(time.time()) - 301)  # 301 seconds old (> 5 minutes)
    params = {
        "auth_date": stale_auth_date,
        "user": user_payload,
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN)

    with pytest.raises(Exception) as exc_info:
        validate_telegram_init_data(init_data, TEST_BOT_TOKEN, max_age_seconds=300)
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


def test_telegram_hmac_fresh_auth_date_accepted_200():
    """Verify that auth_date within 5 minutes (e.g. 120s old) is accepted."""
    user_payload = json.dumps({"id": 7777777, "first_name": "Fresh", "username": "fresh_session"})
    fresh_auth_date = str(int(time.time()) - 120)  # 2 minutes old (< 5 minutes)
    params = {
        "auth_date": fresh_auth_date,
        "user": user_payload,
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN, extra_append="")

    user = validate_telegram_init_data(init_data, TEST_BOT_TOKEN, max_age_seconds=300)
    assert user["id"] == 7777777


def test_telegram_hmac_future_auth_date_rejected_401():
    """Verify that auth_date more than 60s in the future is rejected as invalid."""
    user_payload = json.dumps({"id": 8888888, "first_name": "Future", "username": "future_time"})
    future_auth_date = str(int(time.time()) + 120)  # 2 minutes into the future
    params = {
        "auth_date": future_auth_date,
        "user": user_payload,
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN)

    with pytest.raises(Exception) as exc_info:
        validate_telegram_init_data(init_data, TEST_BOT_TOKEN, max_age_seconds=300)
    assert exc_info.value.status_code == 401
    assert "invalid timestamp" in exc_info.value.detail.lower()


def test_telegram_hmac_duplicate_parameters_rejected_400():
    """Verify HTTP parameter pollution protection: duplicate parameter keys rejected."""
    user_payload = json.dumps({"id": 121212, "first_name": "Pollution", "username": "pollute"})
    params = {
        "auth_date": str(int(time.time())),
        "user": user_payload,
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN, extra_append="&auth_date=12345678")

    with pytest.raises(Exception) as exc_info:
        validate_telegram_init_data(init_data, TEST_BOT_TOKEN)
    assert exc_info.value.status_code == 400
    assert "duplicate parameters" in exc_info.value.detail.lower()


def test_telegram_hmac_tgwebAppData_wrapper_handled():
    """Verify that URL fragment/query wrapper #tgWebAppData= or ?tgWebAppData= is cleanly handled."""
    user_payload = json.dumps({"id": 343434, "first_name": "Fragment", "username": "frag_user"})
    params = {
        "auth_date": str(int(time.time())),
        "chat_instance": "987654",
        "chat_type": "sender",
        "user": user_payload,
    }
    clean_init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN)

    # Wrapped as Telegram Web passes in URL fragment
    wrapped = f"#tgWebAppData={urllib.parse.quote(clean_init_data)}&tgWebAppVersion=7.0"
    user = validate_telegram_init_data(wrapped, TEST_BOT_TOKEN)
    assert user["id"] == 343434


def test_telegram_hmac_via_api_auth_telegram_endpoint(monkeypatch):
    """End-to-end integration test through POST /api/auth/telegram."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", TEST_BOT_TOKEN)
    user_payload = json.dumps({"id": 909090, "first_name": "ApiTester", "username": "api_tester"})
    params = {
        "auth_date": str(int(time.time())),
        "chat_instance": "123456",
        "chat_type": "sender",
        "user": user_payload,
    }
    init_data = create_independent_telegram_init_data(params, TEST_BOT_TOKEN)

    res = client.post("/api/auth/telegram", json={"init_data": init_data})
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    body = res.json()
    assert "access_token" in body
    assert body["profile"]["username"] == "api_tester"
