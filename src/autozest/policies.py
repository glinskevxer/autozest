"""Building the policy objects from the configuration, in one place.

Four subsystems read the config file and three of them were doing it
themselves, which meant a fee card amendment reached the invoice and not the
reference printout. Anything that turns a settings file into a policy object
belongs here so there is one answer to what the rules currently are.

Nothing is cached. A long running process does not exist here, every command is
its own process, and a cache would only be there to be stale.
"""

from __future__ import annotations

from dataclasses import dataclass

from autozest.arbitration.policy import ArbitrationPolicy
from autozest.arbitration.policy import from_config as arbitration_from_config
from autozest.config import Settings
from autozest.settlement.fees import FeeSchedule
from autozest.settlement.fees import from_config as fees_from_config
from autozest.timing import BusinessCalendar


@dataclass(frozen=True)
class Policies:
    """Everything the settings file decides, resolved."""

    arbitration: ArbitrationPolicy
    fees: FeeSchedule
    calendar: BusinessCalendar

    def describe(self) -> list[str]:
        return [
            f"arbitration policy {self.arbitration.version}",
            f"fee card {self.fees.version}",
            f"open {self.calendar.opens} to {self.calendar.closes}",
            f"{len(self.calendar.extra_closures)} extra closures, "
            f"{len(self.calendar.extra_openings)} extra openings",
        ]


def build(settings: Settings | None = None) -> Policies:
    """Turn a settings file into the objects the services take."""
    config = settings or Settings()
    return Policies(
        arbitration=arbitration_from_config(config.section("arbitration")),
        fees=fees_from_config(config.section("fees")),
        calendar=config.calendar,
    )
