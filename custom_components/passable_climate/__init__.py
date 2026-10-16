"""Passable Smart Climate Engine Integration.

Intelligent psychrometric window advisor, convective circulation engine,
and dual-zone thermal model trainer for Home Assistant.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started

from .const import (
    CONF_INFLUX_RETRAIN_HOURS,
    DEFAULT_INFLUX_RETRAIN_HOURS,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import SmartClimateCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_RETRAIN_MODELS = "retrain_models"
SERVICE_REFRESH_PLAN = "refresh_plan"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Passable Smart Climate Engine from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = SmartClimateCoordinator(hass=hass, entry=entry)

    # Initialize coordinator: load cached models, circulation memory, and latch
    await coordinator.async_initialize()

    # Setup reactive listeners for window contacts and profile updates
    coordinator.async_setup_listeners()

    # Store coordinator instance in runtime_data and domain registry
    entry.runtime_data = coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator

    entry.async_on_unload(coordinator.async_unload)

    # Register platforms (sensor, binary_sensor, button, number) before first refresh
    # so RestoreNumber entities populate comfort_settings prior to initial evaluation
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Perform first refresh
    await coordinator.async_config_entry_first_refresh()

    # If no models are cached yet, trigger background retraining
    if not coordinator.models:
        _LOGGER.info("Passable Smart Climate: No cached models found; triggering initial background regression training.")
        entry.async_create_background_task(
            hass, coordinator.async_retrain_models(), "passable_climate_initial_train"
        )

    # Scheduled retraining interval
    retrain_h = float(
        entry.options.get(
            CONF_INFLUX_RETRAIN_HOURS,
            entry.data.get(CONF_INFLUX_RETRAIN_HOURS, DEFAULT_INFLUX_RETRAIN_HOURS),
        )
    )
    if retrain_h > 0:
        async def _scheduled_retrain(_now: Any) -> None:
            await coordinator.async_retrain_models()

        entry.async_on_unload(
            async_track_time_interval(
                hass, _scheduled_retrain, datetime.timedelta(hours=retrain_h)
            )
        )

    # Enable actuation only once Home Assistant has fully started and restored all entities
    async def _enable_actuation(_hass: HomeAssistant) -> None:
        coordinator.actuation_enabled = True
        await coordinator.async_request_refresh()

    if unsub_started := async_at_started(hass, _enable_actuation):
        entry.async_on_unload(unsub_started)

    # Register update listener for options flow changes
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Register integration domain services if not already registered
    async def handle_retrain_models(call: ServiceCall) -> None:
        """Service call handler to retrain all models."""
        _LOGGER.info("Passable Smart Climate: Retrain models service invoked.")
        for coord in hass.data.get(DOMAIN, {}).values():
            if isinstance(coord, SmartClimateCoordinator):
                coord.entry.async_create_background_task(
                    hass, coord.async_retrain_models(), "passable_climate_service_retrain"
                )

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
