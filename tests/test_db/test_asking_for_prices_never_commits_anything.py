"""Đi hỏi giá: thứ tự đúng, không rò ngân sách, và không để lại dấu vết nào.

Thứ tự là toàn bộ nội dung của cơ chế:

    hỏi TẤT CẢ đơn vị (không gửi max_price)
      → persist TỪNG báo giá
      → rồi mới lọc theo max_price
      → luật tất định chọn đề xuất

Đảo lên là hỏng. Gửi ngân sách đi thì đơn vị trả về một con số sát ngân sách, và
"đơn vị rẻ nhất" đo một thứ do chính P-118 tạo ra. Lọc trước khi persist thì các
báo giá bị loại không để lại dấu vết, và câu "không ai trong 500k, rẻ nhất là
620k" không có gì để dựa vào ngoài một lần chạy lại.

Và xin báo giá KHÔNG tạo cam kết: không đặt chỗ, không ghim hàng đợi duyệt,
không thu tiền. Nên khi không ai vừa ngân sách thì không có gì phải hoàn tác —
điều đó phải đúng theo THIẾT KẾ chứ không nhờ cẩn thận.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.common.results import StandardResult
from src.mock.service_providers import DON_VI_CHUYEN_NHA
from src.orchestration.quote import van_tay_yeu_cau
from src.orchestration.quote_service import xin_bao_gia_chuyen_nha

YEU_CAU = {
    "move_date": "2026-09-30",
    "move_time": "08:00",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}
GIA = {"MOV-01": 430_000, "MOV-02": 470_000, "MOV-03": 420_000}


class ConnectorGianDiep:
    """Ghi lại ĐÚNG payload từng đơn vị nhận được, rồi trả giá đã dàn dựng.

    Ghi lại payload là điểm chính: luật "ngân sách không rời khỏi P-118" chỉ
    kiểm được ở nơi thấy được thứ thật sự được gửi đi.
    """

    def __init__(self, gia: dict[str, int] | None = None, *, han_phut: int = 30) -> None:
        self.gia = GIA if gia is None else gia
        self.han_phut = han_phut
        self.da_goi: list[tuple[str, dict]] = []

    async def xin_bao_gia_chuyen_nha(self, service_provider_id, payload):
        self.da_goi.append((service_provider_id, dict(payload)))
        so_tien = self.gia.get(service_provider_id)
        if so_tien is None:
            return StandardResult.fail("NO_AVAILABILITY", "bận ngày đó")
        return StandardResult.ok(
            data={
                "external_quote_id": f"QMOV-{service_provider_id}",
                "service_provider_id": service_provider_id,
                "amount": so_tien,
                "currency": "VND",
                "valid_until": (datetime.now(UTC) + timedelta(minutes=self.han_phut)).isoformat(),
            }
        )


async def _workflow(pool) -> str:
    wid = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid, 'chuyển nhà', 'PENDING')", wid
    )
    return wid


async def _dem_quotes(pool, wid) -> int:
    return await pool.fetchval("SELECT count(*) FROM service_quotes WHERE workflow_id = $1::uuid", wid)


# ------------------------------------------------------------------ ngân sách
@pytest.mark.asyncio
async def test_the_budget_never_appears_in_any_provider_request(db_pool):
    """`max_price` không có mặt trong BẤT KỲ payload nào gửi đi.

    Kiểm ở mức payload thật, không ở mức "hàm allowlist trả về đúng": giữa hàm
    ấy và đường dây còn một đoạn mã, và đoạn ấy là chỗ ngân sách rò ra.
    """
    wid = await _workflow(db_pool)
    gian_diep = ConnectorGianDiep()

    await xin_bao_gia_chuyen_nha(
        db_pool,
        gian_diep,
        workflow_id=wid,
        task_id="T1",
        input_data={**YEU_CAU, "max_price": 450_000},
        max_price=450_000,
    )

    assert len(gian_diep.da_goi) == len(DON_VI_CHUYEN_NHA), "không hỏi hết các đơn vị"
    for don_vi, payload in gian_diep.da_goi:
        assert "max_price" not in payload, f"{don_vi} nhận được ngân sách của khách"
        assert set(payload) == {
            "move_date",
            "move_time",
            "move_vehicle",
            "needs_elevator",
            "needs_loading_support",
        }, f"{don_vi} nhận thêm field ngoài allowlist: {sorted(payload)}"


@pytest.mark.asyncio
async def test_every_quote_is_persisted_before_the_budget_filter_runs(db_pool):
    """Báo giá vượt ngân sách vẫn được ghim — nó là bằng chứng, không phải rác.

    Không có nó thì lời từ chối "không ai trong 450k" là một khẳng định không
    kiểm chứng được, và khách không biết mình đang thiếu bao nhiêu.
    """
    wid = await _workflow(db_pool)

    ket_qua = await xin_bao_gia_chuyen_nha(
        db_pool, ConnectorGianDiep(), workflow_id=wid, task_id="T1", input_data=YEU_CAU, max_price=425_000
    )

    assert len(ket_qua.tat_ca) == 3, "có báo giá bị loại trước khi kịp persist"
    assert await _dem_quotes(db_pool, wid) == 3
    assert [q.service_provider_id for q in ket_qua.trong_ngan_sach] == ["MOV-03"]
    assert ket_qua.gia_re_nhat == 420_000


@pytest.mark.asyncio
async def test_nothing_is_committed_when_no_quote_fits_the_budget(db_pool):
    """Không ai vừa ngân sách → không đề xuất, và KHÔNG side effect nào.

    Không hàng đợi duyệt, không đặt chỗ, không xác nhận báo giá. Chỉ còn lại
    các chứng từ ACTIVE — chúng là dữ liệu, không phải cam kết.
    """
    wid = await _workflow(db_pool)

    ket_qua = await xin_bao_gia_chuyen_nha(
        db_pool, ConnectorGianDiep(), workflow_id=wid, task_id="T1", input_data=YEU_CAU, max_price=100_000
    )

    assert ket_qua.trong_ngan_sach == []
    assert ket_qua.de_xuat is None
    assert ket_qua.gia_re_nhat == 420_000, "câu trả lời không nói được giá thật rẻ nhất"
    assert await db_pool.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id = $1::uuid", wid) == 0, (
        "xin báo giá đã ghim hàng đợi duyệt"
    )
    assert (
        await db_pool.fetchval(
            "SELECT count(*) FROM service_quotes WHERE workflow_id = $1::uuid AND status <> 'ACTIVE'", wid
        )
        == 0
    ), "xin báo giá đã chốt một chứng từ"


@pytest.mark.asyncio
async def test_no_budget_means_no_filter(db_pool):
    wid = await _workflow(db_pool)
    ket_qua = await xin_bao_gia_chuyen_nha(
        db_pool, ConnectorGianDiep(), workflow_id=wid, task_id="T1", input_data=YEU_CAU
    )
    assert len(ket_qua.trong_ngan_sach) == 3
    assert ket_qua.de_xuat.service_provider_id == "MOV-03", "không chọn đơn vị rẻ nhất"


# ------------------------------------------------------------------ đơn vị lỗi
@pytest.mark.asyncio
async def test_a_provider_that_fails_does_not_become_a_made_up_quote(db_pool):
    """Đơn vị ném lỗi → rớt khỏi danh sách, KHÔNG thành một con số đoán ra."""

    class ConnectorSap(ConnectorGianDiep):
        async def xin_bao_gia_chuyen_nha(self, service_provider_id, payload):
            if service_provider_id == "MOV-03":
                raise RuntimeError("đơn vị sập")
            return await super().xin_bao_gia_chuyen_nha(service_provider_id, payload)

    wid = await _workflow(db_pool)
    ket_qua = await xin_bao_gia_chuyen_nha(db_pool, ConnectorSap(), workflow_id=wid, task_id="T1", input_data=YEU_CAU)

    assert "MOV-03" not in {q.service_provider_id for q in ket_qua.tat_ca}
    assert ket_qua.tu_choi["MOV-03"] == "PROVIDER_ERROR"
    # Hai đơn vị còn lại vẫn phục vụ được: một đơn vị sập không kéo cả lượt.
    assert len(ket_qua.tat_ca) == 2
    assert ket_qua.de_xuat.service_provider_id == "MOV-01"


@pytest.mark.asyncio
async def test_a_malformed_quote_is_dropped_instead_of_persisted(db_pool):
    """Báo giá sai schema → không ghim, không đề xuất giả.

    Một dòng bị bỏ tệ hơn hẳn một dòng sai được ghi: dòng sai sẽ được đem ra
    thu tiền, và nó mang chữ ký của một đơn vị không báo con số ấy.
    """

    class ConnectorSaiSchema(ConnectorGianDiep):
        async def xin_bao_gia_chuyen_nha(self, service_provider_id, payload):
            if service_provider_id == "MOV-03":
                return StandardResult.ok(
                    data={
                        "external_quote_id": "QMOV-hong",
                        "service_provider_id": service_provider_id,
                        "amount": -1,
                        "currency": "VND",
                        "valid_until": "khong-phai-thoi-gian",
                    }
                )
            return await super().xin_bao_gia_chuyen_nha(service_provider_id, payload)

    wid = await _workflow(db_pool)
    ket_qua = await xin_bao_gia_chuyen_nha(
        db_pool, ConnectorSaiSchema(), workflow_id=wid, task_id="T1", input_data=YEU_CAU
    )

    assert ket_qua.tu_choi["MOV-03"] == "QUOTE_MALFORMED"
    assert await _dem_quotes(db_pool, wid) == 2
    assert ket_qua.de_xuat.service_provider_id == "MOV-01"


@pytest.mark.asyncio
async def test_a_provider_cannot_quote_on_behalf_of_another(db_pool):
    """Đơn vị trả về mã của HÀNG XÓM → bỏ. Chữ ký phải trùng người báo."""

    class ConnectorMaoDanh(ConnectorGianDiep):
        async def xin_bao_gia_chuyen_nha(self, service_provider_id, payload):
            ket_qua = await super().xin_bao_gia_chuyen_nha(service_provider_id, payload)
            if service_provider_id == "MOV-01" and ket_qua.success:
                ket_qua.data["service_provider_id"] = "MOV-02"
            return ket_qua

    wid = await _workflow(db_pool)
    ket_qua = await xin_bao_gia_chuyen_nha(
        db_pool, ConnectorMaoDanh(), workflow_id=wid, task_id="T1", input_data=YEU_CAU
    )

    assert ket_qua.tu_choi["MOV-01"] == "QUOTE_MALFORMED"
    assert {q.service_provider_id for q in ket_qua.tat_ca} == {"MOV-02", "MOV-03"}


@pytest.mark.asyncio
async def test_a_business_refusal_is_not_an_outage(db_pool):
    """ "Bận ngày đó" là câu trả lời hợp lệ — đơn vị rớt, cả lượt vẫn chạy."""
    wid = await _workflow(db_pool)
    ket_qua = await xin_bao_gia_chuyen_nha(
        db_pool,
        ConnectorGianDiep(gia={"MOV-01": 430_000, "MOV-02": 470_000}),
        workflow_id=wid,
        task_id="T1",
        input_data=YEU_CAU,
    )

    assert ket_qua.tu_choi == {"MOV-03": "NO_AVAILABILITY"}
    assert ket_qua.de_xuat.service_provider_id == "MOV-01"


@pytest.mark.asyncio
async def test_every_provider_failing_leaves_no_recommendation(db_pool):
    """Không ai báo giá → không có đề xuất. Không có "đơn vị mặc định" nào chen vào."""
    wid = await _workflow(db_pool)
    ket_qua = await xin_bao_gia_chuyen_nha(
        db_pool, ConnectorGianDiep(gia={}), workflow_id=wid, task_id="T1", input_data=YEU_CAU
    )

    assert ket_qua.tat_ca == [] and ket_qua.de_xuat is None
    assert await _dem_quotes(db_pool, wid) == 0


# ------------------------------------------------------------------ lượt sửa
@pytest.mark.asyncio
async def test_changing_the_request_starts_a_clean_round(db_pool):
    """Xin lại sau khi đổi yêu cầu: đời cũ SUPERSEDED, đời mới ACTIVE.

    Không dọn thì `bao_gia_dang_song` trả về cả hai đời và luật chọn lấy cái rẻ
    hơn — tức chọn theo một yêu cầu khách không còn hỏi.
    """
    wid = await _workflow(db_pool)
    await xin_bao_gia_chuyen_nha(db_pool, ConnectorGianDiep(), workflow_id=wid, task_id="T1", input_data=YEU_CAU)

    xe_tai = {**YEU_CAU, "move_vehicle": "truck"}
    lan_hai = await xin_bao_gia_chuyen_nha(
        db_pool, ConnectorGianDiep(), workflow_id=wid, task_id="T1", input_data=xe_tai
    )

    van_tay_moi = van_tay_yeu_cau(xe_tai)
    assert all(q.request_fingerprint == van_tay_moi for q in lan_hai.tat_ca)
    assert len(lan_hai.tat_ca) == 3, "đời cũ vẫn còn ACTIVE và lẫn vào lượt chọn"
    cu = await db_pool.fetchval(
        "SELECT count(*) FROM service_quotes WHERE workflow_id=$1::uuid AND status='SUPERSEDED'", wid
    )
    assert cu == 3


@pytest.mark.asyncio
async def test_asking_twice_for_the_same_request_does_not_double_the_quotes(db_pool):
    """Hỏi lại đúng yêu cầu cũ không được để lại hai đời ACTIVE cùng đơn vị.

    Ràng buộc duy nhất ở database chặn dòng thứ hai; tầng này phải đọc lỗi ấy
    thành "đơn vị đã báo giá rồi" chứ không để nó nổ ra ngoài.
    """
    wid = await _workflow(db_pool)
    await xin_bao_gia_chuyen_nha(db_pool, ConnectorGianDiep(), workflow_id=wid, task_id="T1", input_data=YEU_CAU)
    lan_hai = await xin_bao_gia_chuyen_nha(
        db_pool, ConnectorGianDiep(), workflow_id=wid, task_id="T1", input_data=YEU_CAU
    )

    assert await _dem_quotes(db_pool, wid) == 3, "hỏi lại cùng yêu cầu sinh ra báo giá trùng"
    assert len(lan_hai.tat_ca) == 3
    # Lý do phải nói đúng chuyện gì đã xảy ra. Gộp nó vào `QUOTE_MALFORMED` thì
    # lúc đọc log không phân biệt được "provider trả rác" với "ta hỏi hai lần".
    assert set(lan_hai.tu_choi.values()) == {"QUOTE_ALREADY_ISSUED"}
    assert lan_hai.de_xuat.service_provider_id == "MOV-03"


@pytest.mark.asyncio
async def test_quotes_of_one_step_never_leak_into_another(db_pool):
    """Hai bước trong cùng một yêu cầu giữ hai bộ chứng từ riêng."""
    wid = await _workflow(db_pool)
    await xin_bao_gia_chuyen_nha(db_pool, ConnectorGianDiep(), workflow_id=wid, task_id="T1", input_data=YEU_CAU)
    t5 = await xin_bao_gia_chuyen_nha(db_pool, ConnectorGianDiep(), workflow_id=wid, task_id="T5", input_data=YEU_CAU)

    assert len(t5.tat_ca) == 3
    assert {q.task_id for q in t5.tat_ca} == {"T5"}


# ------------------------------------------------------------------ luật chọn
@pytest.mark.asyncio
async def test_a_tie_is_broken_the_same_way_every_time(db_pool):
    """Bằng giá → đánh giá cao hơn thắng. Thiếu vế này thì kết quả nhấp nháy.

    MOV-02 đánh giá 4.8, MOV-01 4.6, MOV-03 4.3 — nên bằng giá thì MOV-02.
    """
    wid = await _workflow(db_pool)
    ket_qua = await xin_bao_gia_chuyen_nha(
        db_pool,
        ConnectorGianDiep(gia={"MOV-01": 500_000, "MOV-02": 500_000, "MOV-03": 500_000}),
        workflow_id=wid,
        task_id="T1",
        input_data=YEU_CAU,
    )
    assert ket_qua.de_xuat.service_provider_id == "MOV-02"


@pytest.mark.asyncio
async def test_the_recommendation_is_a_persisted_quote_not_a_computed_number(db_pool):
    """Thứ được đề xuất phải có chứng từ đọc lại được — đó là toàn bộ điểm của B."""
    wid = await _workflow(db_pool)
    ket_qua = await xin_bao_gia_chuyen_nha(
        db_pool, ConnectorGianDiep(), workflow_id=wid, task_id="T1", input_data=YEU_CAU
    )

    from src.db.quote_repository import doc_bao_gia

    doc_lai = await doc_bao_gia(db_pool, ket_qua.de_xuat.quote_id)
    assert doc_lai is not None, "đề xuất không tồn tại trong database"
    assert doc_lai.amount == ket_qua.de_xuat.amount
    assert doc_lai.external_quote_id, "chứng từ không mang mã của đơn vị"
