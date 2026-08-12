from src.common.projects import find_project_id, project_name, resolve_project_id


def test_public_name_maps_to_internal_id() -> None:
    assert resolve_project_id("Vinhomes Ocean Park") == "PRJ-007"
    assert resolve_project_id("  vinhomes   ocean park  ") == "PRJ-007"


def test_controlled_common_alias_maps_without_fuzzy_guessing() -> None:
    assert resolve_project_id("vinhome ocean park") == "PRJ-007"
    assert resolve_project_id("Ocean Park") is None


def test_free_text_resolves_exactly_one_supported_project() -> None:
    assert find_project_id("Tôi muốn tham quan Vinhomes Golden City ngày mai") == "PRJ-006"
    assert find_project_id("Tôi muốn xem Vinhomes") is None
    assert find_project_id("So sánh Vinhomes Golden City và Vinhomes Ocean Park") is None


def test_internal_id_resolves_to_public_name() -> None:
    assert project_name("PRJ-007") == "Vinhomes Ocean Park"
    assert project_name("PRJ-999") is None
