"""Lifecycle of Swiss Meteo."""
import datetime
import logging
import pprint
import time

from async_timeout import timeout
from typing import Any
from homeassistant.core import Config, HomeAssistant
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.helpers.typing import HomeAssistantType
from hamsclientfork import meteoSwissClient
from homeassistant.helpers.issue_registry import IssueSeverity

from .const import (
    CONF_NAME,
    CONF_POSTCODE,
    CONF_STATION,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from homeassistant.const import Platform
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.helpers import issue_registry as ir

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.WEATHER]
MAX_CONTINUOUS_ERROR_TIME = 60 * 60


async def async_setup(hass: HomeAssistant, config: Config):
    """Setup via old entry in configuration.yaml."""
    _LOGGER.debug("Async setup meteo swiss")

    conf = config.get(DOMAIN)
    if conf is None:
        return True

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
        )
    )
    _LOGGER.debug("END Async setup meteo swiss")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    hass.data.setdefault(DOMAIN, {})

    _LOGGER.debug("Current configuration: %s", entry.data)
    client = await hass.async_add_executor_job(
        meteoSwissClient,
        entry.data[CONF_NAME],
        entry.data[CONF_POSTCODE],
        entry.data[CONF_STATION],
    )
    _LOGGER.debug("Client obtained")

    interval = datetime.timedelta(
        seconds=entry.data.get(
            CONF_UPDATE_INTERVAL,
            DEFAULT_UPDATE_INTERVAL,
        )
        * 60
    )
    coordinator = MeteoSwissDataUpdateCoordinator(
        hass,
        client,
        interval,
        entry.data[CONF_POSTCODE],
        entry.data[CONF_STATION],
    )
    await coordinator.async_config_entry_first_refresh()

    entry.async_on_unload(entry.add_update_listener(update_listener))

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(
        entry,
        PLATFORMS,
    )

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistantType, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    )

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class MeteoSwissDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching AccuWeather data API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: meteoSwissClient,
        update_interval: datetime.timedelta,
        post_code: int,
        station: str,
    ) -> None:
        """Initialize."""
        self.first_error = None
        self.error_raised = False
        self.hass = hass
        self.client = client
        self.post_code = post_code
        self.station = station
        _LOGGER.debug(
            "Data will be updated through %s at post code %s every %s",
            station,
            post_code,
            update_interval,
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""
        try:
            async with timeout(10):
                data = await self.hass.async_add_executor_job(
                    self.client.get_data,
                )
                _LOGGER.debug("Data obtained:\n%s", pprint.pformat(data))
                if "condition" not in data or not data["condition"]:
                    # Oh no.  We could not retrieve the URL.
                    # We try 20 times.  If it does not succeed,
                    # we will induce a config error.
                    _LOGGER.warning(
                        "Station %s provided us with no real-time data",
                        self.station,
                    )
                    if self.first_error is None:
                        self.first_error = time.time()

                    m = MAX_CONTINUOUS_ERROR_TIME
                    last_error = time.time() - self.first_error
                    if not self.error_raised and last_error > m:
                        ir.async_create_issue(
                            self.hass,
                            DOMAIN,
                            f"{self.station}_provides_no_data_{DOMAIN}",
                            is_fixable=False,
                            is_persistent=False,
                            severity=IssueSeverity.ERROR,
                            translation_key="station_no_data",
                            translation_placeholders={
                                "station": self.station,
                            },
                        )
                        self.error_raised = True
                else:
                    self.first_error = None
                    self.error_raised = False
        except Exception as exc:
            raise UpdateFailed(exc) from exc
        data[CONF_STATION] = self.station
        data[CONF_POSTCODE] = self.post_code
        return data
