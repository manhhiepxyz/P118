"""Tests cho thư viện ký/xác minh VNPay (src/connectors/vnpay.py).

Owner: Mạnh Hiệp (Executor layer)
File: tests/test_connectors_vnpay.py

Toàn bộ là unit test thuần — không DB, không HTTP. Thời gian được bấm cố
định để chữ ký deterministic: cùng input phải ra CÙNG hash ở mọi lần chạy.
"""

import urllib.parse
from datetime import UTC, datetime, timedelta, timezone

from src.connectors.vnpay import (
    IPN_RSP_INVALID_AMOUNT,
    VnPaySessionConfig,
    build_payment_url,
    build_sign_data,
    format_vnpay_date,
    ipn_response,
    parse_ipn_result,
    sanitize_order_info,
    sign,
    verify_signature,
)

SECRET = "UNITTESTSECRET"
FIXED_NOW = datetime(2026, 8, 26, 10, 0, 0, tzinfo=timezone(timedelta(hours=7)))
CONFIG = VnPaySessionConfig(
    tmn_code="TESTTMN",
    hash_secret=SECRET,
    payment_url="https://sandbox.vnpayment.vn/paymentv2/vpcpay.html",
    ttl_minutes=30,
)


def _base_params() -> dict[str, str]:
    return {
        "vnp_Version": "2.1.0",
        "vnp_Command": "pay",
        "vnp_TmnCode": "TESTTMN",
        "vnp_Amount": "15000000",
        "vnp_CurrCode": "VND",
        "vnp_TxnRef": "PAY-001",
        "vnp_OrderInfo": "Thanh toan phi dat cho BOOK-001",
        "vnp_OrderType": "other",
        "vnp_Locale": "vn",
        "vnp_CreateDate": "20260826100000",
    }


# ---------------------------------------------------------------------------
# format / sanitize
# ---------------------------------------------------------------------------


def test_format_vnpay_date_converts_to_gmt7():
    # 03:30 UTC ngày 26/08 → 10:30 GMT+7 cùng ngày.
    utc_moment = datetime(2026, 8, 26, 3, 30, 0, tzinfo=UTC)
    assert format_vnpay_date(utc_moment) == "20260826103000"


def test_sanitize_order_info_strips_diacritics_and_specials():
    assert sanitize_order_info("Thanh toán phí đặt chỗ A1201 (ZONE_A)") == ("Thanh toan phi dat cho A1201 ZONE A")


def test_sanitize_order_info_collapses_whitespace():
    assert sanitize_order_info("a!!!___b") == "a b"


# ---------------------------------------------------------------------------
# signing
# ---------------------------------------------------------------------------


def test_build_sign_data_sorts_alphabetically_and_drops_empty():
    data = build_sign_data({"vnp_B": "2", "vnp_A": "1", "vnp_C": "", "vnp_D": None})
    assert data == "vnp_A=1&vnp_B=2"


def test_sign_is_deterministic_hex_lowercase():
    first = sign(SECRET, _base_params())
    second = sign(SECRET, _base_params())
    assert first == second
    assert len(first) == 128  # SHA-512 hexdigest
    assert first == first.lower()


def test_sign_changes_when_secret_changes():
    assert sign(SECRET, _base_params()) != sign("OTHER-SECRET", _base_params())


def test_sign_changes_when_param_value_tampered():
    params = _base_params()
    tampered = dict(params, vnp_Amount="999000000")
    assert sign(SECRET, params) != sign(SECRET, tampered)


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------


def test_verify_signature_accepts_genuine_callback():
    params = _base_params()
    params["vnp_SecureHash"] = sign(SECRET, params)
    assert verify_signature(SECRET, params) is True


def test_verify_signature_ignores_secure_hash_type_field():
    # Một số môi trường trả thêm vnp_SecureHashType — phải bị loại khỏi dữ liệu ký.
    params = _base_params()
    params["vnp_SecureHash"] = sign(SECRET, params)
    with_type = dict(params, vnp_SecureHashType="SHA512")
    assert verify_signature(SECRET, with_type) is True


def test_verify_signature_rejects_forged_amount():
    params = _base_params()
    genuine_hash = sign(SECRET, params)
    forged = dict(params, vnp_Amount="999000000", vnp_SecureHash=genuine_hash)
    assert verify_signature(SECRET, forged) is False


def test_verify_signature_rejects_wrong_secret():
    params = _base_params()
    params["vnp_SecureHash"] = sign("OTHER-SECRET", params)
    assert verify_signature(SECRET, params) is False


def test_verify_signature_rejects_missing_or_empty_hash():
    assert verify_signature(SECRET, _base_params()) is False
    empty = dict(_base_params(), vnp_SecureHash="")
    assert verify_signature(SECRET, empty) is False


# ---------------------------------------------------------------------------
# build_payment_url
# ---------------------------------------------------------------------------


def test_build_payment_url_contains_signed_sorted_query():
    url = build_payment_url(
        CONFIG,
        txn_ref="PAY-001",
        amount_vnd=150000,
        order_info="Thanh toan phi dat cho",
        ip_addr="203.0.113.9",
        return_url="https://backend.example.com/api/v1/webhooks/vnpay/return",
        now=FIXED_NOW,
    )
    assert url.startswith(CONFIG.payment_url + "?")
    query = url.split("?", 1)[1]
    pairs = dict(urllib.parse.parse_qsl(query))
    assert pairs["vnp_Amount"] == "15000000"  # ×100 theo đặc tả
    assert pairs["vnp_TxnRef"] == "PAY-001"
    assert pairs["vnp_CurrCode"] == "VND"
    assert pairs["vnp_CreateDate"] == "20260826100000"
    assert pairs["vnp_ExpireDate"] == "20260826103000"  # +30 phút
    # Chữ ký trong URL khớp với phần query đứng trước nó.
    unsigned_query = query.rsplit("&vnp_SecureHash=", 1)[0]
    import hashlib
    import hmac as hmac_mod

    expected = hmac_mod.new(SECRET.encode(), unsigned_query.encode(), hashlib.sha512).hexdigest()
    assert pairs["vnp_SecureHash"] == expected


def test_build_payment_url_requires_credentials():
    broken = VnPaySessionConfig(tmn_code="", hash_secret="")
    try:
        build_payment_url(
            broken,
            txn_ref="PAY-001",
            amount_vnd=150000,
            order_info="x",
            ip_addr="127.0.0.1",
            return_url="https://b.example.com/r",
            now=FIXED_NOW,
        )
    except ValueError:
        pass
    else:  # pragma: no cover - pytest.raises cho gọn hơn nhưng giữ thông điệp rõ
        raise AssertionError("Thiếu TMN/hash_secret phải fail-fast bằng ValueError")


# ---------------------------------------------------------------------------
# parse_ipn_result
# ---------------------------------------------------------------------------


def _ipn_query(**overrides: str) -> dict[str, str]:
    base = {
        "vnp_TmnCode": "TESTTMN",
        "vnp_Amount": "15000000",
        "vnp_Command": "pay",
        "vnp_PayDate": "20260826100231",
        "vnp_ResponseCode": "00",
        "vnp_TransactionNo": "14288321",
        "vnp_TransactionStatus": "00",
        "vnp_TxnRef": "PAY-001",
        "vnp_BankCode": "NCB",
        "vnp_SecureHash": "a" * 128,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def test_parse_ipn_success_divides_amount_by_100():
    result = parse_ipn_result(_ipn_query())
    assert result is not None
    assert result.amount_vnd == 150000
    assert result.txn_ref == "PAY-001"
    assert result.transaction_no == "14288321"
    assert result.success is True


def test_parse_ipn_failure_when_transaction_status_not_zero():
    result = parse_ipn_result(_ipn_query(vnp_TransactionStatus="07"))
    assert result is not None
    assert result.success is False


def test_parse_ipn_failure_when_response_code_not_zero():
    result = parse_ipn_result(_ipn_query(vnp_ResponseCode="24"))
    assert result is not None
    assert result.success is False


def test_parse_ipn_returns_none_on_missing_required_fields():
    assert parse_ipn_result({}) is None
    assert parse_ipn_result(_ipn_query(**{"vnp_TxnRef": None})) is None


def test_parse_ipn_returns_none_on_non_numeric_amount():
    assert parse_ipn_result(_ipn_query(vnp_Amount="abc")) is None


# ---------------------------------------------------------------------------
# ipn_response
# ---------------------------------------------------------------------------


def test_ipn_response_codes_and_messages():
    assert ipn_response(IPN_RSP_INVALID_AMOUNT) == {"RspCode": "04", "Message": "Invalid Amount"}
    ok = ipn_response("00")
    assert ok == {"RspCode": "00", "Message": "Confirm Success"}
    unknown = ipn_response("97")
    assert unknown == {"RspCode": "97", "Message": "Unknown Error"}
