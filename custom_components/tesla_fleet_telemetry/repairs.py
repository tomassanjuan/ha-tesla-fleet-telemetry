"""Repairs platform for Tesla Fleet Telemetry integration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import async_delete_issue

from .const import (
    CONF_DOMAIN,
    CONF_TELEMETRY_SERVER_CA,
    CONF_TELEMETRY_SERVER_HOSTNAME,
    CONF_TELEMETRY_SERVER_PORT,
    DOMAIN,
)
from .entity_registry import _fields_for_unique_id
from .utils import async_fetch_server_ca, normalize_pem_cert_bundle

_LOGGER = logging.getLogger(__name__)

_VIN_LENGTH = 17


def _find_entry_for_vin(hass: HomeAssistant, vin: str) -> tuple[str, Any] | None:
    """Return (entry_id, entry_data) for a given VIN, or None if not found."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        entry_data = getattr(entry, "runtime_data", None)
        if entry_data is not None and getattr(entry_data, "vin", None) == vin:
            return entry.entry_id, entry_data
    return None


def _pairing_url_for_entry(hass: HomeAssistant, entry_id: str) -> str | None:
    """Rebuild the virtual-key pairing URL from the entry's registered domain."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return None
    merged = {**entry.data, **entry.options}
    domain = merged.get(CONF_DOMAIN) or merged.get(CONF_TELEMETRY_SERVER_HOSTNAME)
    return f"https://tesla.com/_ak/{domain}" if domain else None


def _extract_key_paired(config: object) -> bool | None:
    """Pull key_paired bool from a telemetry config response dict."""
    if not isinstance(config, dict):
        return None
    response = config.get("response", config)
    if isinstance(response, dict):
        return response.get("key_paired")
    return None


class VKeyNotPairedRepairFlow(RepairsFlow):
    """Repair flow for virtual key not paired issue."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the first step of the repair flow."""
        vin = self.issue_id.replace("key_not_paired_", "")
        found = _find_entry_for_vin(self.hass, vin)
        if found is None:
            _LOGGER.error("Could not find entry for VIN %s to repair key pairing", vin)
            return self.async_abort(reason="entry_not_found")
        _, entry_data = found

        try:
            _LOGGER.info("Checking virtual key pairing status for %s", vin)
            config = await entry_data.get_telemetry_config()
            key_paired = _extract_key_paired(config)
            _LOGGER.debug("Virtual key status check for %s: key_paired=%s", vin, key_paired)

            if key_paired is True:
                _LOGGER.info("Virtual key confirmed paired for %s", vin)
                async_delete_issue(self.hass, DOMAIN, f"key_not_paired_{vin}")
                try:
                    push_result = await entry_data.push_telemetry_config(reason="manual")
                    if push_result.get("error"):
                        _LOGGER.warning(
                            "Config push after virtual key repair failed for %s: %s",
                            vin,
                            push_result.get("error"),
                        )
                    else:
                        _LOGGER.info(
                            "Config push triggered after virtual key repair for %s",
                            vin,
                        )
                except Exception as err:
                    _LOGGER.debug(
                        "Config push after virtual key repair exception for %s: %s",
                        vin,
                        err,
                    )
                return self.async_create_entry(title="", data={})
            return await self.async_step_confirm()
        except Exception as err:
            _LOGGER.error("Repair check failed for %s: %s", vin, err)
            return self.async_abort(reason="repair_failed")

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show pairing instructions and verify when user submits."""
        vin = self.issue_id.replace("key_not_paired_", "")

        issue_registry = ir.async_get(self.hass)
        issue = issue_registry.async_get_issue(DOMAIN, self.issue_id)
        description_placeholders = {"vin": vin}
        if issue and issue.translation_placeholders:
            description_placeholders = issue.translation_placeholders

        if user_input is not None:
            found = _find_entry_for_vin(self.hass, vin)
            if found is None:
                return self.async_abort(reason="entry_not_found")
            entry_id, entry_data = found

            try:
                config = await entry_data.get_telemetry_config()
                key_paired = _extract_key_paired(config)

                if key_paired is True:
                    _LOGGER.info("Virtual key confirmed paired for %s", vin)
                    async_delete_issue(self.hass, DOMAIN, f"key_not_paired_{vin}")
                    try:
                        await entry_data.push_telemetry_config(reason="manual")
                    except Exception as err:
                        _LOGGER.debug("Config push after virtual key repair exception: %s", err)
                    return self.async_create_entry(title="", data={})

                pairing_url = (
                    description_placeholders.get("pairing_url")
                    or _pairing_url_for_entry(self.hass, entry_id)
                    or "https://tesla.com/_ak/"
                )
                return self.async_abort(
                    reason="still_not_paired",
                    description_placeholders={"vin": vin, "pairing_url": pairing_url},
                )
            except Exception as err:
                _LOGGER.error("Repair check failed for %s: %s", vin, err)
                return self.async_abort(reason="repair_failed")

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=description_placeholders,
            last_step=True,
        )


class UnknownFieldRepairFlow(RepairsFlow):
    """Repair flow for unknown telemetry field issue."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the first step of the repair flow."""
        issue_registry = ir.async_get(self.hass)
        issue = issue_registry.async_get_issue(DOMAIN, self.issue_id)
        issue_data = issue.data if issue and isinstance(issue.data, dict) else {}
        prefix = "unknown_field_"
        vin = str(issue_data.get("vin") or self.issue_id[len(prefix) : len(prefix) + _VIN_LENGTH])
        field = str(issue_data.get("field") or self.issue_id[len(prefix) + _VIN_LENGTH + 1 :])

        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({}),
                description_placeholders={"vin": vin, "field": field},
            )

        found = _find_entry_for_vin(self.hass, vin)
        if found is None:
            _LOGGER.error("Could not find entry for VIN %s to repair unknown field %s", vin, field)
            return self.async_abort(reason="entry_not_found")
        entry_id, _ = found

        registry = er.async_get(self.hass)
        disabled = 0
        for entity_entry in er.async_entries_for_config_entry(registry, entry_id):
            if field not in _fields_for_unique_id(entity_entry.unique_id, vin):
                continue
            if entity_entry.disabled_by is not None:
                continue
            # Disabled as USER (the user confirmed this fix): an INTEGRATION
            # disable is undone at the next setup by the default-enabled sync
            # for default-on entities, and is ignored by the fleet-config
            # resync listener, so the unknown field would stay in the push.
            registry.async_update_entity(
                entity_entry.entity_id, disabled_by=er.RegistryEntryDisabler.USER
            )
            disabled += 1

        if disabled:
            _LOGGER.info(
                "Disabled %s entity/entities for unknown field %s on %s",
                disabled,
                field,
                vin,
            )
        async_delete_issue(self.hass, DOMAIN, self.issue_id)
        return self.async_create_entry(title="", data={})


async def _server_ca_changed(hass: HomeAssistant, entry_id: str) -> bool:
    """Whether the telemetry server's trusted CA differs from the stored one."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return False
    opts = {**entry.data, **entry.options}
    hostname = opts.get(CONF_TELEMETRY_SERVER_HOSTNAME)
    port = opts.get(CONF_TELEMETRY_SERVER_PORT)
    if not hostname or not isinstance(port, int):
        return False
    try:
        pem, trusted = await async_fetch_server_ca(hass, hostname, port)
    except (OSError, ValueError):
        return False
    try:
        current = normalize_pem_cert_bundle(opts.get(CONF_TELEMETRY_SERVER_CA, ""))
    except ValueError:
        current = ""
    return trusted and pem != current


class BlockerRepairFlow(RepairsFlow):
    """Repair flow for blocking problems: explain, then reload on Submit.

    Reloading re-reads the server's certificate chain and pushes the config
    again. The result says whether the stored CA changed; if the problem
    persists the issue reappears by itself.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Explain the problem; on Submit reload the integration."""
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        data = issue.data if issue and isinstance(issue.data, dict) else {}
        placeholders = dict(issue.translation_placeholders or {}) if issue else {}
        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({}),
                description_placeholders=placeholders,
            )
        vin = str(data.get("vin") or "")
        found = _find_entry_for_vin(self.hass, vin)
        if found is None:
            return self.async_abort(reason="entry_not_found")
        ca_changed = await _server_ca_changed(self.hass, found[0])
        # Only errors newer than the reload count, so history from before the
        # user's fix does not re-raise the issue.
        self.hass.data.setdefault(DOMAIN + "_blocker_retry", {})[vin] = datetime.now(UTC)
        async_delete_issue(self.hass, DOMAIN, self.issue_id)
        await self.hass.config_entries.async_reload(found[0])
        return self.async_abort(
            reason="reloaded_ca_changed" if ca_changed else "reloaded",
            description_placeholders=placeholders,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any],
) -> RepairsFlow | None:
    """Create a repair flow for Tesla Fleet Telemetry issues."""
    if issue_id.startswith("key_not_paired_"):
        return VKeyNotPairedRepairFlow()
    if issue_id.startswith("blocker_"):
        return BlockerRepairFlow()
    if issue_id.startswith("unknown_field_"):
        return UnknownFieldRepairFlow()
    return None
