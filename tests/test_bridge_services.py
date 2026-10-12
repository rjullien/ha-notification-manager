"""Tests for bridge diagnostic service schemas."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    from notification_manager import bridge_services


def test_bridge_logs_limit_coerces_numeric_string_and_rejects_invalid_text():
    hass = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock()
    coerce = MagicMock()
    range_validator = MagicMock()
    all_validator = MagicMock()

    with patch.object(bridge_services.vol, "Coerce", coerce), patch.object(
        bridge_services.vol, "Range", range_validator
    ), patch.object(bridge_services.vol, "All", all_validator):
        bridge_services.async_register_bridge_services(hass, MagicMock())

    registration = next(
        call
        for call in hass.services.async_register.call_args_list
        if call.args[1] == "whatsapp_bridge_logs"
    )
    assert registration.kwargs["schema"] is not None
    coerce.assert_any_call(int)
    assert range_validator.call_args_list[0] == ((), {"min": 1, "max": 500})
    assert all_validator.call_args_list[0] == (
        (coerce.return_value, range_validator.return_value),
        {},
    )

    converter = coerce.call_args_list[0].args[0]
    converted_limit = converter("100")
    assert converted_limit == 100
    assert isinstance(converted_limit, int)
    with pytest.raises(ValueError):
        converter("abc")
