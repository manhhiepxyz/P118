"""Công tắc chỉ bật bằng đúng chuỗi `"1"`. Mọi giá trị khác là TẮT.

Cờ này đụng vào tiền: nó chọn đơn vị cung cấp và mở đường cho một cam kết
thương mại. Một cấu hình gõ sai phải để hệ thống chạy như CŨ, chứ không phải
bật một đường mới chưa ai xem lại.

`"true"` bị từ chối là cố ý. Nhận nó nghĩa là phải trả lời "thế còn `True`,
`TRUE`, `yes`, `on`, `enabled`?" — và mỗi câu trả lời thêm một cách để hai môi
trường hiểu khác nhau về cùng một dòng trong `.env`.
"""

from __future__ import annotations

import pytest

from src.common.feature_flags import (
    SERVICE_PROVIDER_MATCHING,
    chon_don_vi_theo_bao_gia_bat,
)


def test_a_missing_variable_is_off(monkeypatch):
    """Thiếu biến là trạng thái của MỌI môi trường chưa ai cấu hình."""
    monkeypatch.delenv(SERVICE_PROVIDER_MATCHING, raising=False)
    assert chon_don_vi_theo_bao_gia_bat() is False


def test_exactly_one_turns_it_on(monkeypatch):
    monkeypatch.setenv(SERVICE_PROVIDER_MATCHING, "1")
    assert chon_don_vi_theo_bao_gia_bat() is True


@pytest.mark.parametrize(
    "gia_tri",
    [
        "",
        "0",
        "true",
        "True",
        "TRUE",
        "yes",
        "on",
        "01",
        "1.0",
        " 1",
        "1 ",
        "\t1",
        "enabled",
        "2",
        "-1",
    ],
)
def test_everything_else_is_off(monkeypatch, gia_tri):
    """ALLOWLIST, không phải blocklist.

    `" 1"` và `"1 "` cũng tắt: chuẩn hoá khoảng trắng nghĩa là chấp nhận rằng
    file `.env` được sinh ra bởi một công cụ không ai kiểm soát — và lúc ấy giá
    trị trong đó không còn là một ý định.
    """
    monkeypatch.setenv(SERVICE_PROVIDER_MATCHING, gia_tri)
    assert chon_don_vi_theo_bao_gia_bat() is False, f"{gia_tri!r} đã bật cờ"


def test_the_flag_is_read_fresh_every_time(monkeypatch):
    """Không cache. Một giá trị nhớ lại làm lượt kiểm thứ hai đo lại lượt đầu."""
    monkeypatch.setenv(SERVICE_PROVIDER_MATCHING, "1")
    assert chon_don_vi_theo_bao_gia_bat() is True
    monkeypatch.setenv(SERVICE_PROVIDER_MATCHING, "0")
    assert chon_don_vi_theo_bao_gia_bat() is False
    monkeypatch.delenv(SERVICE_PROVIDER_MATCHING, raising=False)
    assert chon_don_vi_theo_bao_gia_bat() is False


def test_the_shipped_configuration_keeps_it_off():
    """`.env.example` là thứ mọi môi trường mới chép ra — nó phải TẮT.

    Cờ mặc định bật ở file mẫu nghĩa là mọi lần dựng môi trường mới đều chạy
    đường chưa nghiệm thu, và không ai chủ động chọn điều đó.
    """
    from pathlib import Path

    mau = Path(__file__).resolve().parent.parent / ".env.example"
    dong = [d.strip() for d in mau.read_text(encoding="utf-8").splitlines()]
    khai_bao = [d for d in dong if d.startswith(f"{SERVICE_PROVIDER_MATCHING}=")]

    assert khai_bao, f"{SERVICE_PROVIDER_MATCHING} chưa có trong .env.example"
    assert khai_bao == [f"{SERVICE_PROVIDER_MATCHING}=0"], khai_bao


def test_docker_compose_does_not_turn_it_on():
    """Cấu hình demo cũng phải tắt — nó là thứ chạy trong buổi trình bày."""
    from pathlib import Path

    compose = (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text(encoding="utf-8")
    for dong in compose.splitlines():
        if SERVICE_PROVIDER_MATCHING in dong:
            assert ":-0}" in dong or dong.strip().endswith(': "0"') or dong.strip().endswith(": 0"), dong
