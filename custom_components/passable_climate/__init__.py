"""Passable Smart Climate Engine Integration.

Intelligent psychrometric window advisor, convective circulation engine,
and dual-zone thermal model trainer for Home Assistant.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, PLATFORMS
from .coordinator import SmartClimateCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_RETRAIN_MODELS = "retrain_models"
SERVICE_REFRESH_PLAN = "refresh_plan"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Passable Smart Climate Engine from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = SmartClimateCoordinator(
        hass=hass,
        entry_data=entry.data,
        entry_options=entry.options,
    )

    # Initialize coordinator: load cached models and circulation memory
    await coordinator.async_initialize()

    # Setup reactive listeners for window contacts and profile updates
    coordinator.async_setup_listeners()

    # Perform first refresh
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator instance
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # If no models are cached yet, trigger background retraining
    if not coordinator.models:
        _LOGGER.info("Passable Smart Climate: No cached models found; triggering initial background regression training.")
        hass.async_create_task(coordinator.async_retrain_models())

    # Register platforms (sensor, binary_sensor, button)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register update listener for options flow changes
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Register integration domain services if not already registered
    async def handle_retrain_models(call: ServiceCall) -> None:
        """Service call handler to retrain all models."""
        _LOGGER.info("Passable Smart Climate: Retrain models service invoked.")
        for coord in hass.data.get(DOMAIN, {}).values():
            if isinstance(coord, SmartClimateCoordinator):
                hass.async_create_task(coord.async_retrain_models())

    async def handle_refresh_plan(call: ServiceCall) -> None:
        """Service call handler to force immediate re-evaluation."""
        _LOGGER.info("Passable Smart Climate: Refresh plan service invoked.")
        for coord in hass.data.get(DOMAIN, {}).values():
            if isinstance(coord, SmartClimateCoordinator):
                await coord.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_RETRAIN_MODELS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RETRAIN_MODELS,
            handle_retrain_models,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_PLAN):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_PLAN,
            handle_refresh_plan,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: SmartClimateCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator:
        coordinator.async_unload()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # If no entries remain, clean up services
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_RETRAIN_MODELS)
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH_PLAN)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)
