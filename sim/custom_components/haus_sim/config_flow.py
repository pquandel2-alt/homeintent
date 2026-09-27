"""One-click config flow for the house simulation."""

from homeassistant.config_entries import ConfigFlow

from .entities import DOMAIN


class HausSimFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        self._async_abort_entries_match({})
        return self.async_create_entry(title="Haus-Simulation", data={})
