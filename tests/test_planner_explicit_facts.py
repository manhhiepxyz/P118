"""Điều người dùng nói rõ được HIỂU bởi Planner, và bị RÀNG BUỘC bởi code.

Vì sao đổi kiến trúc
--------------------
Bản trước đọc ba boolean bằng regex trong `src/common/goal_facts.py`. Nó hỏng
theo một kiểu không vá được: mỗi vòng vá đóng được vài câu và mở ra vài câu
khác, vì thứ nó đang cố làm là HIỂU TIẾNG VIỆT.

Bốn câu cuối cùng nó vẫn đọc sai, sau ba vòng vá:

    "tôi không thực sự cần người bốc xếp"          → True
    "tôi chưa hoàn toàn sẵn sàng đồng ý cho tư vấn" → True
    "tôi không hoàn toàn muốn cần thang máy"        → True
    "tôi đồng ý nhưng xin đừng bao giờ tư vấn"      → True

Mỗi câu chèn thêm một trạng từ giữa từ phủ định và từ neo. Nới cửa sổ thì bắt
được chúng và bắt nhầm câu khác; thêm từ vào regex thì đó là whitelist trá
hình. Không có cách nào đúng ở tầng đó.

Ranh giới mới
-------------
    Planner (LLM)   HIỂU câu — đó là việc nó vốn đang làm, trong CÙNG một lượt
                    gọi đang đọc goal. Không thêm lượt LLM nào.

    Code            RÀNG BUỘC HẬU QUẢ — và không bao giờ tự sinh ra một fact.

Code kiểm được sáu điều, tất định, không cần hiểu tiếng Việt nào:

    1. Chỉ ba field. `Literal` ở schema, nên `resident_id` không dựng được.
    2. `value` phải là boolean thật.
    3. `evidence` phải là một đoạn KHÔNG RỖNG có thật trong goal.
    4. Hai kết luận trái nhau cho cùng field → mâu thuẫn.
    5. Một field vừa được nhận vừa bị hỏi lại → mâu thuẫn.
    6. Mâu thuẫn → đúng MỘT lượt sửa; sai lần hai → PlannerError.

Điều code KHÔNG còn hứa: rằng "tôi đồng ý nhưng xin đừng liên hệ" sẽ không
thành `True`. Việc ấy giờ thuộc về model. Cái code hứa là nó KHÔNG TỰ BỊA ra
fact nào, và mọi fact model đưa ra đều phải trích dẫn được từ chính goal.
"""

from __future__ import annotations

import pytest

from src.agents.planner import Planner, PlannerError, _PlannerResponse
from src.common.task_plan import Task, TaskPlan

GOAL_NHIEU_DICH_VU = (
    "Đặt lịch tham quan Vinhomes Green Paradise ngày 2026-09-04 lúc 10:30 xe đưa đón cho 2 khách "
    "tại ABCD liên hệ 09999822. Đăng ký quan tâm / nhận tư vấn Vinhomes Golden City nhu cầu "
    "Tìm hiểu thêm gọi lúc 09:30 tôi đồng ý được liên hệ. Đăng ký phương tiện và chỗ đỗ xe bắt "
    "đầu từ ngày 2026-08-22 Xe máy biển số 12M-88923 chỗ đỗ Khu A. Báo bảo trì / sửa chữa ngày "
    "2026-08-27 hạng mục Nước lúc 10:00 ở ad hư. Đặt lịch chuyển nhà ngày 2026-09-02 lúc 10:30 "
    "phương tiện Xe van cần người bốc xếp"
)

GOAL_MOT_DICH_VU = "Đặt lịch chuyển nhà ngày 2026-09-02 lúc 10:30 phương tiện Xe van cần người bốc xếp"

# Bốn câu mà regex đọc sai. Chúng ở đây làm DỮ LIỆU, không phải whitelist:
# không dòng nào trong `src/` được biết tới chúng.
CAU_DOI_KHANG = [
    "tôi không thực sự cần người bốc xếp",
    "tôi chưa hoàn toàn sẵn sàng đồng ý cho tư vấn liên hệ",
    "tôi không hoàn toàn muốn cần thang máy",
    "tôi đồng ý nhưng xin đừng bao giờ tư vấn liên hệ",
]


class _Model:
    """LLM giả trả sẵn kịch bản; đếm số lượt được gọi.

    Đếm lượt là phần quan trọng nhất: cả thiết kế này đứng trên lời hứa "không
    thêm lượt gọi model nào".
    """

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def with_structured_output(self, _schema, **_kwargs):
        return self

    async def ainvoke(self, _messages):
        self.calls += 1
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]


def _fact(field: str, value: bool, evidence: str) -> dict:
    return {"field": field, "value": value, "evidence": evidence}


def _needs(*fields: str, facts: list[dict] | None = None) -> _PlannerResponse:
    return _PlannerResponse(
        status="NEEDS_INFORMATION",
        plan=None,
        missing_fields=list(fields),
        explicit_facts=list(facts or []),
    )


def _ready(facts: list[dict] | None = None) -> _PlannerResponse:
    plan = TaskPlan(
        goal="x",
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=[],
                input={"vehicle_id": "VEH-1", "parking_zone": "ZONE_A", "booking_date": "2029-01-15"},
            )
        ],
    )
    return _PlannerResponse(status="READY", plan=plan, missing_fields=[], explicit_facts=list(facts or []))


def _as_dict(result) -> dict:
    return {f.field: f.value for f in result.explicit_facts}


# ---------------------------------------------------------------------------
# Điều model hiểu đúng thì đi tới nơi — trong ĐÚNG một lượt gọi
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_what_the_goal_states_is_carried_out_of_the_planner():
    model = _Model(
        [
            _needs(
                "description",
                "needs_elevator",
                facts=[
                    _fact("consent", True, "tôi đồng ý được liên hệ"),
                    _fact("needs_loading_support", True, "cần người bốc xếp"),
                ],
            )
        ]
    )

    result = await Planner(model).plan(GOAL_NHIEU_DICH_VU, existing_context={})

    assert _as_dict(result) == {"consent": True, "needs_loading_support": True}
    assert model.calls == 1, "đọc goal và đọc fact phải nằm trong CÙNG một lượt gọi"


@pytest.mark.asyncio
async def test_facts_come_back_on_a_ready_plan_too():
    """READY cũng mang fact — kế hoạch đã validate KHÔNG bị đụng tới."""
    model = _Model([_ready(facts=[_fact("needs_loading_support", True, "cần người bốc xếp")])])

    result = await Planner(model).plan(GOAL_MOT_DICH_VU, existing_context={})

    assert result.is_ready
    assert [t.task_id for t in result.plan.tasks] == ["T1"]
    assert _as_dict(result) == {"needs_loading_support": True}
    assert model.calls == 1


@pytest.mark.asyncio
async def test_one_service_and_five_services_obey_the_same_contract():
    """Contract không phụ thuộc số dịch vụ trong goal."""
    fact = [_fact("needs_loading_support", True, "cần người bốc xếp")]
    for goal in (GOAL_MOT_DICH_VU, GOAL_NHIEU_DICH_VU):
        model = _Model([_needs("description", facts=fact)])
        result = await Planner(model).plan(goal, existing_context={})
        assert _as_dict(result) == {"needs_loading_support": True}, goal[:40]
        assert model.calls == 1


@pytest.mark.asyncio
async def test_a_goal_that_states_nothing_yields_nothing():
    model = _Model([_needs("description")])

    result = await Planner(model).plan("Báo bảo trì hạng mục Nước", existing_context={})

    assert _as_dict(result) == {}, "code tự sinh ra một fact không ai nói"


# ---------------------------------------------------------------------------
# Code không bao giờ TỰ BỊA — bốn câu đối kháng
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cau", CAU_DOI_KHANG)
@pytest.mark.asyncio
async def test_code_never_invents_a_fact_for_a_sentence_the_model_left_alone(cau):
    """Model không kết luận gì → context KHÔNG được có boolean nào.

    Đây là điều regex không làm được: nó luôn phải trả lời, kể cả khi câu vượt
    quá khả năng của nó. Tầng này im lặng khi model im lặng.
    """
    model = _Model([_needs("description")])

    result = await Planner(model).plan(cau, existing_context={})

    assert _as_dict(result) == {}, f"tự bịa fact cho {cau!r}"


# ---------------------------------------------------------------------------
# Ràng buộc tất định
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bay", ["resident_id", "booking_id", "amount", "owner_user_id", "workflow_id", "vehicle_id"])
def test_a_trusted_identifier_cannot_even_be_expressed_as_a_fact(bay):
    """Chặn ở SCHEMA, không phải ở một danh sách kiểm sau đó.

    `Literal` nghĩa là một `_PlannerResponse` mang `resident_id` không dựng
    được — mọi đường dùng nó đều được chặn theo, kể cả đường viết sau này.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _PlannerResponse(
            status="NEEDS_INFORMATION",
            plan=None,
            missing_fields=["description"],
            explicit_facts=[_fact(bay, True, "x")],
        )


@pytest.mark.parametrize("gia_tri", ["true", 1, "có", None])
def test_a_value_that_is_not_a_real_boolean_is_refused(gia_tri):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _PlannerResponse(
            status="NEEDS_INFORMATION",
            plan=None,
            missing_fields=["description"],
            explicit_facts=[{"field": "consent", "value": gia_tri, "evidence": "tôi đồng ý được liên hệ"}],
        )


@pytest.mark.parametrize("evidence", ["", "   ", "\n"])
def test_an_empty_evidence_is_refused_at_the_schema(evidence):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _PlannerResponse(
            status="NEEDS_INFORMATION",
            plan=None,
            missing_fields=["description"],
            explicit_facts=[_fact("consent", True, evidence)],
        )


@pytest.mark.asyncio
async def test_a_fact_whose_evidence_is_not_in_the_goal_is_rejected():
    """Model phải TRÍCH DẪN được. Không trích được nghĩa là nó đang bịa.

    Đây là ràng buộc mạnh nhất mà code giữ được mà không cần hiểu tiếng Việt:
    một lời đồng ý phải tồn tại trong chính câu người dùng viết ra.
    """
    bia = [_fact("consent", True, "tôi đồng ý cho gọi điện bất cứ lúc nào")]
    model = _Model([_needs("description", facts=bia), _needs("description", facts=bia)])

    with pytest.raises(PlannerError):
        await Planner(model).plan(GOAL_NHIEU_DICH_VU, existing_context={})

    assert model.calls == 2, "phải sửa đúng một lần rồi dừng"


@pytest.mark.asyncio
async def test_a_hallucinated_evidence_is_corrected_once_and_then_accepted():
    """Sửa được thì đi tiếp — vòng sửa không phải một cái bẫy."""
    model = _Model(
        [
            _needs("description", facts=[_fact("consent", True, "câu này không có trong goal")]),
            _needs("description", facts=[_fact("consent", True, "tôi đồng ý được liên hệ")]),
        ]
    )

    result = await Planner(model).plan(GOAL_NHIEU_DICH_VU, existing_context={})

    assert _as_dict(result) == {"consent": True}
    assert model.calls == 2


@pytest.mark.asyncio
async def test_two_opposite_conclusions_for_one_field_are_refused():
    doi_nghich = [
        _fact("needs_loading_support", True, "cần người bốc xếp"),
        _fact("needs_loading_support", False, "cần người bốc xếp"),
    ]
    model = _Model([_needs("description", facts=doi_nghich), _needs("description", facts=doi_nghich)])

    with pytest.raises(PlannerError):
        await Planner(model).plan(GOAL_NHIEU_DICH_VU, existing_context={})

    assert model.calls == 2


@pytest.mark.asyncio
async def test_the_same_field_cannot_be_both_understood_and_asked_about():
    """Vừa nói "tôi đã hiểu" vừa hỏi lại là một response mâu thuẫn với chính nó."""
    mau_thuan = [_fact("consent", True, "tôi đồng ý được liên hệ")]
    model = _Model(
        [_needs("consent", facts=mau_thuan), _needs("consent", facts=mau_thuan)],
    )

    with pytest.raises(PlannerError):
        await Planner(model).plan(GOAL_NHIEU_DICH_VU, existing_context={})

    assert model.calls == 2


@pytest.mark.asyncio
async def test_that_same_contradiction_is_fixable_in_one_retry():
    model = _Model(
        [
            _needs("consent", facts=[_fact("consent", True, "tôi đồng ý được liên hệ")]),
            _needs("description", facts=[_fact("consent", True, "tôi đồng ý được liên hệ")]),
        ]
    )

    result = await Planner(model).plan(GOAL_NHIEU_DICH_VU, existing_context={})

    assert _as_dict(result) == {"consent": True}
    assert list(result.missing_fields) == ["description"]
    assert model.calls == 2


@pytest.mark.asyncio
async def test_the_error_never_echoes_what_the_user_wrote():
    """Message lỗi và prompt sửa đi vào log — dữ liệu người dùng thì không."""
    bia = [_fact("consent", True, "tôi đồng ý cho gọi điện bất cứ lúc nào")]
    model = _Model([_needs("description", facts=bia), _needs("description", facts=bia)])

    with pytest.raises(PlannerError) as excinfo:
        await Planner(model).plan(GOAL_NHIEU_DICH_VU, existing_context={})

    for ro_ri in ("12M-88923", "09999822", "Golden City", "bất cứ lúc nào"):
        assert ro_ri not in str(excinfo.value), f"lỗi rò {ro_ri!r}"


# ---------------------------------------------------------------------------
# Fact phải rời khỏi Planner và vào được STATE — chỗ tầng trên đọc
# ---------------------------------------------------------------------------


async def _run_plan_node(planner_result, *, goal: str):
    """Chạy ĐÚNG `plan_node` của graph production, trả state nó sinh ra.

    Không có test này thì một `plan_node` quên đính fact vào state sẽ đi lọt:
    mọi test persistence đều thay `run_demo_workflow` bằng stub, nên chúng
    không bao giờ chạm tới graph. Đo được — mutation "graph không trả fact" đi
    qua toàn bộ suite mà không test nào đỏ.
    """
    from src.agents.graph import build_planner_graph

    class _Planner:
        async def plan(self, *_args, **_kwargs):
            return planner_result

    class _Boundary:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("plan_node không được chạm tầng thực thi")

    graph = build_planner_graph(_Planner(), _Boundary())
    # Lấy CHÍNH hàm `plan_node` production ra khỏi graph đã compile, rồi gọi nó.
    #
    # Không gọi qua `graph.nodes[...].ainvoke`: bước đó kèm ChannelWrite của
    # LangGraph và cần runtime context, nên nó đo hạ tầng graph chứ không đo
    # đoạn mã ta đang kiểm. Không tự dựng lại closure: bản sao sẽ trôi khỏi bản
    # thật, và test sẽ xanh trên một hàm không ai chạy.
    plan_node = graph.nodes["plan"].node.steps[0].afunc
    return await plan_node({"goal": goal, "existing_context": {}})


@pytest.mark.asyncio
async def test_facts_reach_the_state_when_more_information_is_needed():
    from src.agents.planner import ExplicitFact, PlannerResult

    state = await _run_plan_node(
        PlannerResult(
            status="NEEDS_INFORMATION",
            missing_fields=("description",),
            explicit_facts=(ExplicitFact(field="consent", value=True),),
        ),
        goal=GOAL_NHIEU_DICH_VU,
    )

    assert state.get("explicit_facts") == {"consent": True}, "fact không rời khỏi graph — tầng trên không có gì để ghim"


@pytest.mark.asyncio
async def test_facts_reach_the_state_on_a_ready_plan_as_well():
    from src.agents.planner import ExplicitFact, PlannerResult

    plan = TaskPlan(
        goal=GOAL_MOT_DICH_VU,
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=[],
                input={"vehicle_id": "VEH-1", "parking_zone": "ZONE_A", "booking_date": "2029-01-15"},
            )
        ],
    )
    state = await _run_plan_node(
        PlannerResult(
            status="READY",
            plan=plan,
            explicit_facts=(ExplicitFact(field="needs_loading_support", value=True),),
        ),
        goal=GOAL_MOT_DICH_VU,
    )

    assert state["planner_status"] == "READY"
    assert state.get("explicit_facts") == {"needs_loading_support": True}

    # Kế hoạch KHÔNG bị fact đụng vào. `_ensure_payment_is_offered` vẫn thêm
    # bước thanh toán như mọi lượt READY khác — đó là hành vi sẵn có, và test
    # này không được che nó đi. Điều phải đúng: bước gốc còn nguyên cả danh
    # tính lẫn input, và không có bước nào sinh ra từ fact.
    theo_id = {t.task_id: t for t in state["plan"].tasks}
    assert theo_id["T1"].tool == "book_parking"
    assert theo_id["T1"].input["parking_zone"] == "ZONE_A"
    assert {t.tool for t in state["plan"].tasks} <= {"book_parking", "pay_fee"}


@pytest.mark.asyncio
async def test_only_a_checked_fact_object_can_become_context():
    """Dict thô không đi qua được — nó chưa từng qua lớp kiểm trích dẫn."""
    from src.agents.graph import _facts_as_context

    assert _facts_as_context(({"field": "consent", "value": True},)) == {}
