"""Verification-code service for register: Redis-backed storage, rate limiting,
and a pluggable provider so dev runs work without a real SMS gateway.

Two providers are supported:
- "console" (default): the code is logged and echoed back in the API response
  as ``dev_code`` so registration works end-to-end on a local machine.
- "aliyun": sends a real SMS via Aliyun Dysmsapi (phone accounts only).

Verification codes are keyed by the normalized account (phone or email), so the
same flow covers both. For emails in production, sending is not implemented and
the code is logged instead; verify still works.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import httpx
import redis as redis_lib

from config import get_settings
from security import normalize_login

logger = logging.getLogger("sms")

CODE_DIGITS = 6
DEFAULT_CODE_TTL = 300          # seconds a code stays valid
DEFAULT_RESEND_COOLDOWN = 60    # min seconds between sends to the same account
DEFAULT_DAILY_LIMIT = 10        # max send requests per account per day
VERIFY_MAX_ATTEMPTS = 5         # wrong guesses before the code is invalidated

_redis_client: redis_lib.Redis | None = None


def _client() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(get_settings().redis_url, decode_responses=True)
    return _redis_client


def _code_key(account: str) -> str:
    return f"sms:code:{account}"


def _cooldown_key(account: str) -> str:
    return f"sms:cooldown:{account}"


def _attempts_key(account: str) -> str:
    return f"sms:attempts:{account}"


def _daily_key(account: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"sms:daily:{account}:{today}"


def _is_phone(account: str) -> bool:
    return "@" not in account


def _generate_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))


class SmsError(RuntimeError):
    """Raised for validation/rate-limit failures; message is user-facing."""


def prepare_account(raw: str) -> str:
    """Normalize + validate the target account. Raises SmsError on bad input."""
    account = normalize_login(raw)
    if not account:
        raise SmsError("请输入手机号")
    phone = account.lstrip("+")
    if not phone.isdigit() or not (6 <= len(phone) <= 20):
        raise SmsError("手机号格式不正确")
    return phone


def send_code(raw_account: str) -> dict[str, Any]:
    """Generate and dispatch a verification code.

    Returns a dict for the API layer. In console/dev mode it includes
    ``dev_code`` so the client can display it for testing.
    """
    settings = get_settings()
    account = prepare_account(raw_account)

    client = _client()
    cooldown = getattr(settings, "sms_resend_cooldown_seconds", DEFAULT_RESEND_COOLDOWN)
    daily_limit = getattr(settings, "sms_daily_limit", DEFAULT_DAILY_LIMIT)
    ttl = getattr(settings, "sms_code_ttl_seconds", DEFAULT_CODE_TTL)

    # Resend cooldown: SET NX so the first request sets it, later ones fail.
    got = client.set(_cooldown_key(account), "1", ex=cooldown, nx=True)
    if not got:
        remaining = client.ttl(_cooldown_key(account))
        secs = int(remaining) if remaining and remaining > 0 else cooldown
        raise SmsError(f"发送过于频繁，请 {secs} 秒后再试")

    # Per-account daily cap to blunt abuse.
    count = client.incr(_daily_key(account))
    if count == 1:
        # expire roughly to end of day so keys don't leak forever.
        client.expire(_daily_key(account), 24 * 3600 + 600)
    if count > daily_limit:
        raise SmsError("今日验证码发送次数已达上限，请明天再试")

    code = _generate_code()
    client.set(_code_key(account), code, ex=ttl)
    client.delete(_attempts_key(account))

    channel = "phone" if _is_phone(account) else "email"
    provider = (getattr(settings, "sms_provider", "") or "console").strip().lower()

    if provider == "aliyun" and channel == "phone":
        _send_aliyun(account, code)
        logger.info("sms sent account=%s channel=phone", account)
        return {"sent": True, "channel": channel}

    # console / fallback: log and echo the code for local testing.
    logger.info("verification code account=%s channel=%s code=%s", account, channel, code)
    return {"sent": True, "channel": channel, "dev_code": code}


def verify_code(raw_account: str, code: str) -> bool:
    """Validate the code for the account.

    测试阶段: 短信尚未开通, 用共享注册口令代替。
    格式必须为 "TK" + 4 位数字(如 TK1234), TK 必须大写。
    开通短信后, 把下面的口令校验改回 Redis-backed 校验(见文件末注释)。
    """
    if not code:
        return False
    import re
    return bool(re.fullmatch(r"TK\d{4}", code.strip()))

    # --- 开通短信后恢复为真正的 Redis-backed 验证 ---
    # account = prepare_account(raw_account)
    # client = _client()
    # stored = client.get(_code_key(account))
    # if not stored:
    #     return False
    # if hmac.compare_digest(stored, code.strip()):
    #     client.delete(_code_key(account))
    #     client.delete(_attempts_key(account))
    #     return True
    # attempts = client.incr(_attempts_key(account))
    # if attempts == 1:
    #     client.expire(_attempts_key(account), DEFAULT_CODE_TTL)
    # if attempts >= VERIFY_MAX_ATTEMPTS:
    #     client.delete(_code_key(account))
    #     client.delete(_attempts_key(account))
    # return False


def _send_aliyun(phone: str, code: str) -> None:
    """Call Aliyun Dysmsapi (SendSms). Requires sms_* config keys."""
    settings = get_settings()
    access_key_id = getattr(settings, "sms_access_key_id", "") or ""
    access_key_secret = getattr(settings, "sms_access_key_secret", "") or ""
    sign_name = getattr(settings, "sms_sign_name", "") or ""
    template_code = getattr(settings, "sms_template_code", "") or ""
    if not (access_key_id and access_key_secret and sign_name and template_code):
        logger.warning("aliyun sms selected but not fully configured; skipping send")
        return

    params = {
        "PhoneNumbers": phone,
        "SignName": sign_name,
        "TemplateCode": template_code,
        "TemplateParam": f'{{"code":"{code}"}}',
    }
    body = _aliyun_sign(params, access_key_id, access_key_secret)
    try:
        resp = httpx.post("https://dysmsapi.aliyuncs.com/", data=body, timeout=10.0)
        data = resp.json()
    except Exception as exc:
        logger.error("aliyun sms request failed: %s", exc)
        return
    if str(data.get("Code", "")) != "OK":
        logger.error("aliyun sms error: %s", data)


def _aliyun_sign(params: dict[str, str], access_key_id: str, access_key_secret: str) -> dict[str, str]:
    """Build the signed common+request params for Aliyun POP API (RPC style)."""
    common = {
        "Format": "JSON",
        "Version": "2017-05-25",
        "AccessKeyId": access_key_id,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SignatureVersion": "1.0",
        "SignatureNonce": secrets.token_hex(8),
        "Action": "SendSms",
        "RegionId": "cn-hangzhou",
    }
    all_params = {**common, **params}
    sorted_items = sorted(all_params.items())
    canonical = "&".join(
        f"{urllib.parse.quote(k, safe='~')}={urllib.parse.quote(v, safe='~')}"
        for k, v in sorted_items
    )
    string_to_sign = "POST&" + urllib.parse.quote("/", safe="") + "&" + urllib.parse.quote(canonical, safe="")
    digest = hmac.new(
        (access_key_secret + "&").encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    signature = base64.b64encode(digest).decode("utf-8")
    all_params["Signature"] = signature
    return all_params
