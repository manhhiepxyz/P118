"""Nguồn sự thật DUY NHẤT cho contract của 10 tool provider.

Mười tool là tập PROVIDER — mọi thứ hệ thống có connector phục vụ. Agent chỉ
lập kế hoạch được với 8 trong số đó; xem `src/common/agent_tool_policy.py`.

Trước file này, luật của một tool nằm rải ở ba nơi và không nơi nào biết nơi
kia: `TaskPlanValidator` giữ required field + vài enum, Connector giữ danh sách
output field, provider mock giữ Pydantic model. Hệ quả là Validator chấp nhận
những plan mà provider chắc chắn từ chối — ví dụ `transaction_type="hack"`,
`max_price=-1`, `currency="USD"`, `needs_elevator="yes"`.

Bảng dưới đây chép đúng theo `shared_contracts.md` mục 4 và các Pydantic model
trong `src/mock/schemas.py`. KHÔNG đổi tên tool hay tên field: đây là mô tả
contract đang có, không phải contract mới.

Ai dùng:
  - `src/agents/validator.py` — chặn plan sai trước khi tới Executor.
  - test ma trận — chứng minh schema, Validator và Connector cùng một luật.

Output spec dùng để kiểm `InputRef`: một task chỉ được tham chiếu tới field
mà tool nguồn THẬT SỰ trả về, và kiểu của field đó phải dùng được ở chỗ đích.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Literal

# `bool` là subclass của `int` trong Python, nên mọi kiểm tra số phải loại bool
# ra một cách tường minh; nếu không `needs_elevator=True` sẽ lọt qua ô integer
# và `amount=True` trở thành số tiền 1 đồng.
Kind = Literal["string", "integer", "boolean", "enum", "date", "time", "list", "object"]

# Kiểu output nào dùng được cho kiểu input nào khi nối bằng InputRef.
# Chỉ khai báo các cặp CHẮC CHẮN tương thích; cặp không có ở đây bị từ chối.
_COMPATIBLE_KINDS: Mapping[Kind, frozenset[Kind]] = MappingProxyType(
    {
        "string": frozenset({"string", "enum"}),
        "enum": frozenset({"enum", "string"}),
        "integer": frozenset({"integer"}),
        "boolean": frozenset({"boolean"}),
        "date": frozenset({"date", "string"}),
        "time": frozenset({"time", "string"}),
        "list": frozenset({"list"}),
        "object": frozenset({"object"}),
    }
)


@dataclass(frozen=True)
class FieldSpec:
    """Luật của một field. Bất biến để không ai sửa contract lúc chạy."""

    kind: Kind
    enum: frozenset[str] | None = None
    # Chặn dưới. `exclusive` phân biệt "không nhỏ hơn N" với "lớn hơn N".
    # Tiền (amount) và ngân sách (max_price) đều dùng exclusive: 0 đồng không
    # phải một giao dịch, cũng không phải một truy vấn tìm kiếm.
    minimum: int | None = None
    exclusive_minimum: bool = False
    # `consent` phải đúng literal True; False là từ chối, không phải một lựa chọn.
    must_be_true: bool = False

    def describe(self) -> str:
        """Mô tả luật để đưa vào message lỗi.

        Chỉ nêu LUẬT, không bao giờ nêu giá trị thực tế nhận được: giá trị đó
        có thể là tên, số điện thoại, CCCD hay token người dùng dán nhầm.
        """
        if self.must_be_true:
            return "phải là true"
        if self.enum is not None:
            return "phải thuộc: " + ", ".join(sorted(self.enum))
        if self.kind == "integer" and self.minimum is not None:
            return f"phải là số nguyên {'lớn hơn' if self.exclusive_minimum else 'không nhỏ hơn'} {self.minimum}"
        return {
            "string": "phải là chuỗi không rỗng",
            "integer": "phải là số nguyên",
            "boolean": "phải là true hoặc false",
            "date": "phải theo định dạng YYYY-MM-DD",
            "time": "phải theo định dạng HH:MM",
            "list": "phải là danh sách",
            "object": "phải là object",
        }[self.kind]

    def check(self, value: Any) -> str | None:
        """Trả None nếu hợp lệ, ngược lại trả mô tả luật bị vi phạm."""
        if self.must_be_true:
            return None if value is True else self.describe()

        if self.kind == "boolean":
            # Chỉ chấp nhận bool thật. "yes"/"no"/1/0 đều bị từ chối.
            return None if isinstance(value, bool) else self.describe()

        if self.kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                return self.describe()
            if self.minimum is not None:
                too_small = value <= self.minimum if self.exclusive_minimum else value < self.minimum
                if too_small:
                    return self.describe()
            return None

        if self.kind == "enum":
            if not isinstance(value, str) or (self.enum is not None and value not in self.enum):
                return self.describe()
            return None

        if self.kind == "string":
            # Chuỗi toàn khoảng trắng không phải là câu trả lời.
            return None if isinstance(value, str) and value.strip() else self.describe()

        if self.kind == "date":
            if not isinstance(value, str):
                return self.describe()
            try:
                date.fromisoformat(value)
            except ValueError:
                return self.describe()
            return None

        if self.kind == "time":
            if not isinstance(value, str):
                return self.describe()
            try:
                datetime.strptime(value, "%H:%M")
            except ValueError:
                return self.describe()
            return None

        if self.kind == "list":
            return None if isinstance(value, list) else self.describe()

        return None if isinstance(value, dict) else self.describe()


@dataclass(frozen=True)
class ToolContract:
    """Contract đầy đủ của một tool: nhận gì, bắt buộc gì, trả ra gì."""

    inputs: Mapping[str, FieldSpec]
    required: frozenset[str]
    outputs: Mapping[str, FieldSpec]

    def __post_init__(self) -> None:
        unknown = set(self.required) - set(self.inputs)
        if unknown:
            raise ValueError(f"required field không có trong inputs: {sorted(unknown)}")


def _contract(
    inputs: dict[str, FieldSpec],
    outputs: dict[str, FieldSpec],
    optional: frozenset[str] = frozenset(),
) -> ToolContract:
    """Mặc định MỌI input đều bắt buộc; optional phải khai tường minh.

    Chọn chiều mặc định này để thêm một field mới mà quên khai sẽ làm test đỏ,
    chứ không âm thầm trở thành field tuỳ chọn không ai kiểm.
    """
    return ToolContract(
        inputs=MappingProxyType(dict(inputs)),
        required=frozenset(inputs) - optional,
        outputs=MappingProxyType(dict(outputs)),
    )


_STRING = FieldSpec(kind="string")
_DATE = FieldSpec(kind="date")
_TIME = FieldSpec(kind="time")
_INTEGER = FieldSpec(kind="integer")

# MVP chỉ dùng VND. Đây là luật nghiệp vụ, không phải giới hạn kỹ thuật:
# đổi tiền tệ kéo theo tỉ giá, làm tròn và đối soát — ngoài phạm vi Gate 2.
_CURRENCY = FieldSpec(kind="enum", enum=frozenset({"VND"}))

TOOL_CONTRACTS: Mapping[str, ToolContract] = MappingProxyType(
    {
        "search_properties": _contract(
            inputs={
                "transaction_type": FieldSpec(kind="enum", enum=frozenset({"rent", "buy"})),
                "property_type": FieldSpec(kind="enum", enum=frozenset({"apartment", "room"})),
                "residential_area": _STRING,
                # "lớn hơn 0" theo shared_contracts.md: ngân sách 0 đồng không
                # phải một truy vấn tìm kiếm có nghĩa.
                "max_price": FieldSpec(kind="integer", minimum=0, exclusive_minimum=True),
            },
            outputs={"properties": FieldSpec(kind="list"), "result_count": _INTEGER},
        ),
        "schedule_property_viewing": _contract(
            inputs={"project_id": _STRING, "viewing_date": _DATE, "viewing_time": _TIME},
            outputs={
                "viewing_id": _STRING,
                "project_id": _STRING,
                "project_name": _STRING,
                "viewing_date": _DATE,
                "viewing_time": _TIME,
                "viewing_status": FieldSpec(kind="enum", enum=frozenset({"SCHEDULED"})),
                "contact_name": _STRING,
                "contact_phone": _STRING,
                # 4 thông tin người đón tiếp do provider xác nhận — nguồn sự thật
                # nằm ở mock tour provider, không phải Agent tự dựng.
                "receptionist_name": _STRING,
                "receptionist_phone": _STRING,
                "reception_area": _STRING,
                "reception_time": _TIME,
            },
        ),
        # Huỷ một lịch ĐÃ ĐẶT. Chỉ `viewing_id` — mã do provider cấp, đọc từ
        # kết quả bước đã chạy. Không ô nào người dùng gõ được, và đó là chủ ý:
        # nhận thêm bất kỳ ô nào ở đây là mở đường cho một mã lịch bịa ra.
        # Huỷ một chỗ đỗ ĐÃ GIỮ. `refunded_amount` do provider quyết theo luật
        # 24 giờ; tầng trên chỉ NÓI LẠI con số ấy, không tính lại.
        "cancel_maintenance": _contract(
            inputs={"maintenance_id": _STRING},
            outputs={
                "maintenance_id": _STRING,
                "maintenance_status": FieldSpec(kind="enum", enum=frozenset({"CANCELLED"})),
            },
        ),
        "cancel_move": _contract(
            inputs={"move_request_id": _STRING},
            outputs={
                "move_request_id": _STRING,
                "move_status": FieldSpec(kind="enum", enum=frozenset({"CANCELLED"})),
            },
        ),
        "cancel_shuttle": _contract(
            inputs={"shuttle_id": _STRING},
            outputs={
                "shuttle_id": _STRING,
                "shuttle_status": FieldSpec(kind="enum", enum=frozenset({"CANCELLED"})),
            },
        ),
        "cancel_parking": _contract(
            inputs={"booking_id": _STRING},
            outputs={
                "booking_id": _STRING,
                "booking_status": FieldSpec(kind="enum", enum=frozenset({"CANCELLED"})),
                "refunded_amount": FieldSpec(kind="int"),
                "refund_denied": FieldSpec(kind="bool"),
            },
        ),
        "cancel_property_viewing": _contract(
            inputs={"viewing_id": _STRING},
            outputs={
                "viewing_id": _STRING,
                "viewing_status": FieldSpec(kind="enum", enum=frozenset({"CANCELLED"})),
            },
        ),
        "register_property_interest": _contract(
            inputs={
                "project_id": _STRING,
                "interest_type": FieldSpec(kind="enum", enum=frozenset({"buy", "rent", "consultation"})),
                # GIỜ CỤ THỂ, không phải buổi.
                #
                # "afternoon" tới tay nhân viên tư vấn vẫn không nói được nên
                # gọi lúc mấy giờ, còn người dùng muốn hẹn 14:30 thì không có
                # cách nào diễn đạt. Cả hai đầu cùng mất thông tin.
                "preferred_contact_time": _TIME,
                # consent=False không phải "đã trả lời là không" — nó nghĩa là
                # chưa có sự đồng ý, nên plan không được phép chạy.
                "consent": FieldSpec(kind="boolean", must_be_true=True),
            },
            outputs={
                "interest_id": _STRING,
                "project_id": _STRING,
                "project_name": _STRING,
                "interest_status": FieldSpec(kind="enum", enum=frozenset({"RECEIVED"})),
                "contact_channel": _STRING,
            },
        ),
        "create_maintenance_request": _contract(
            inputs={
                "issue_type": FieldSpec(
                    kind="enum",
                    enum=frozenset({"air_conditioning", "electrical", "plumbing", "other"}),
                ),
                "description": _STRING,
                "location": _STRING,
                "preferred_date": _DATE,
                "preferred_time": _TIME,
            },
            outputs={
                "maintenance_id": _STRING,
                "maintenance_status": FieldSpec(kind="enum", enum=frozenset({"SCHEDULED"})),
                "appointment_date": _DATE,
                "appointment_time": _TIME,
            },
        ),
        "schedule_move": _contract(
            inputs={
                "move_date": _DATE,
                "move_time": _TIME,
                "needs_elevator": FieldSpec(kind="boolean"),
                "needs_loading_support": FieldSpec(kind="boolean"),
                "move_vehicle": FieldSpec(kind="enum", enum=frozenset({"none", "van", "truck"})),
            },
            outputs={
                "move_request_id": _STRING,
                "move_status": FieldSpec(kind="enum", enum=frozenset({"SCHEDULED"})),
                "move_date": _DATE,
                "move_time": _TIME,
                "elevator_slot": _STRING,
            },
        ),
        "register_resident": _contract(
            inputs={"full_name": _STRING, "apartment_code": _STRING, "residential_area": _STRING},
            outputs={"resident_id": _STRING},
        ),
        "register_vehicle": _contract(
            inputs={
                "resident_id": _STRING,
                "plate_number": _STRING,
                "vehicle_type": FieldSpec(kind="enum", enum=frozenset({"car", "motorcycle"})),
            },
            outputs={"vehicle_id": _STRING},
        ),
        "book_parking": _contract(
            inputs={
                "vehicle_id": _STRING,
                "booking_date": _DATE,
                "parking_zone": FieldSpec(kind="enum", enum=frozenset({"ZONE_A", "ZONE_B"})),
            },
            outputs={
                "booking_id": _STRING,
                "parking_zone": FieldSpec(kind="enum", enum=frozenset({"ZONE_A", "ZONE_B"})),
                "booking_date": _DATE,
                # `amount` và `currency` là báo giá authoritative do provider
                # trả về. Chúng đi tiếp sang pay_fee bằng InputRef, không bao
                # giờ do người dùng hay LLM tự khai.
                # Báo giá luôn là số dương: một chỗ đỗ 0 đồng không phải báo giá.
                "amount": FieldSpec(kind="integer", minimum=0, exclusive_minimum=True),
                "currency": _CURRENCY,
            },
        ),
        # Đổi khu cho một chỗ ĐÃ GIỮ. Một thao tác, không phải huỷ-rồi-đặt:
        # hai lời gọi tách rời để lại khoảng trống, và trong khoảng ấy chỗ ở
        # khu mới có thể bị người khác lấy — khách vào có chỗ, ra tay trắng.
        #
        # `amount` là ĐẦU RA, y như `book_parking`: giá do bên bán quyết theo
        # khu (`ZONE_A` 150.000 / `ZONE_B` 100.000). Cho caller khai giá là cho
        # caller tự định giá dịch vụ.
        "change_parking_zone": _contract(
            inputs={
                "booking_id": _STRING,
                "parking_zone": FieldSpec(kind="enum", enum=frozenset({"ZONE_A", "ZONE_B"})),
            },
            outputs={
                "booking_id": _STRING,
                "parking_zone": FieldSpec(kind="enum", enum=frozenset({"ZONE_A", "ZONE_B"})),
                "booking_date": _DATE,
                "amount": FieldSpec(kind="integer", minimum=0, exclusive_minimum=True),
                "currency": _CURRENCY,
            },
        ),
        "pay_fee": _contract(
            inputs={
                "booking_id": _STRING,
                # Giao dịch thanh toán phải > 0. Luật này khớp đúng constraint
                # ck_payments_amount_positive trong database — Tool Contract
                # không được lỏng hơn tầng dưới, nếu không plan qua được
                # Validator rồi mới vỡ ở INSERT.
                "amount": FieldSpec(kind="integer", minimum=0, exclusive_minimum=True),
                "currency": _CURRENCY,
            },
            outputs={
                "payment_id": _STRING,
                "payment_status": FieldSpec(kind="enum", enum=frozenset({"PENDING", "PAID", "FAILED", "REFUNDED"})),
            },
        ),
        "book_shuttle": _contract(
            inputs={
                # `viewing_id` đến từ InputRef của task schedule_property_viewing
                # — id nội bộ do provider cấp, KHÔNG phải field người dùng khai.
                "viewing_id": _STRING,
                "tour_date": _DATE,
                # Sức chứa xe tham quan: tối thiểu 1 người. Cận trên 30 ép ở
                # provider (`BookShuttleRequest.ge=30`); FieldSpec không có
                # maximum nên đây là luật tầng dưới, không phải tầng contract.
                "passenger_count": FieldSpec(kind="integer", minimum=1),
            },
            outputs={
                "shuttle_id": _STRING,
                "viewing_id": _STRING,
                "tour_date": _DATE,
                "passenger_count": FieldSpec(kind="integer", minimum=1),
                # 4 thông tin tài xế deterministic do provider tự sinh (roster
                # theo shuttle_id) — xác nhận xe phải hiện rõ cho người đặt.
                "driver_name": _STRING,
                "license_plate": _STRING,
                "vehicle_type": _STRING,
                "pickup_time": _STRING,
            },
        ),
    }
)

ALLOWED_TOOLS: frozenset[str] = frozenset(TOOL_CONTRACTS)


def output_spec(tool: str, field: str) -> FieldSpec | None:
    """Spec của một output field, hoặc None nếu tool không trả field đó."""
    contract = TOOL_CONTRACTS.get(tool)
    if contract is None:
        return None
    return contract.outputs.get(field)


def kinds_are_compatible(source: FieldSpec, target: FieldSpec) -> bool:
    """Output của task nguồn có dùng được ở ô input đích hay không.

    Chỉ trả True cho các cặp chắc chắn hợp lệ. Cặp chưa biết bị coi là không
    tương thích — nối sai kiểu thì hỏng ở Executor hoặc ở provider, muộn hơn
    nhiều so với chặn tại đây.
    """
    return target.kind in _COMPATIBLE_KINDS.get(source.kind, frozenset())
