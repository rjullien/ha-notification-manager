"""Regression — reload : le listen_once de boot recovery ne doit pas être retiré deux fois.

`async_listen_once` se retire tout seul après émission ; retirer le handle à
nouveau au unload fait logger HA core « Unable to remove unknown job listener »
à chaque reload de l'entrée (rapporté sur la 1.12.0).
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    import notification_manager as nm
    import notification_manager.runtime as runtime


def _make_entry(entry_id="entry1", bridge_url="http://bridge:8080", **data):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {"bridge_url": bridge_url, **data}
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=MagicMock())
    return entry


def _make_hass():
    hass = MagicMock()
    hass.data = {}
    hass.is_running = False
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock()
    hass.services.async_remove = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
    return hass


def _run_unload_callbacks(entry):
    """Simule l'exécution des callbacks async_on_unload par la config entry."""
    for call in entry.async_on_unload.call_args_list:
        cb = call.args[0]
        if callable(cb):
            cb()


class TestBootRecoveryListener:
    async def test_unsub_apres_emission_nest_pas_appele(self):
        hass = _make_hass()
        entry = _make_entry()

        with (
            patch.object(runtime, "_CONST_ALEXA_PLAYERS", ["media_player.salon"]),
            patch.object(runtime, "_CONST_ALEXA_LOCAL_PLAYERS", []),
            patch.object(nm.er, "async_get", return_value=MagicMock()),
            patch.object(nm, "Store") as store,
            patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()),
        ):
            store.return_value.async_load = AsyncMock(return_value=None)
            assert await nm.async_setup_entry(hass, entry) is True

        unsub = hass.bus.async_listen_once.return_value
        listener = hass.bus.async_listen_once.call_args.args[1]

        # l'événement tire : le listen_once s'auto-retire
        listener(MagicMock())

        # l'entry est déchargée ensuite (reload) : ne PAS retirer à nouveau
        _run_unload_callbacks(entry)
        unsub.assert_not_called()

    async def test_unsub_avant_emission_est_appele(self):
        hass = _make_hass()
        entry = _make_entry()

        with (
            patch.object(runtime, "_CONST_ALEXA_PLAYERS", ["media_player.salon"]),
            patch.object(runtime, "_CONST_ALEXA_LOCAL_PLAYERS", []),
            patch.object(nm.er, "async_get", return_value=MagicMock()),
            patch.object(nm, "Store") as store,
            patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()),
        ):
            store.return_value.async_load = AsyncMock(return_value=None)
            assert await nm.async_setup_entry(hass, entry) is True

        unsub = hass.bus.async_listen_once.return_value

        # déchargement AVANT homeassistant_started : le listener doit être retiré
        _run_unload_callbacks(entry)
        unsub.assert_called_once()
