"""Conservative, vendor-neutral mapping of basic DeviceInfo observations."""

from datetime import datetime

from acs.domain.cpe.inform import Inform, Parameter
from acs.domain.cpe.inventory import InvalidReason, NormalizedInventory, TextObservation


class GenericInventoryNormalizer:
    profile_id = "generic-device-info"
    profile_version = "1"

    def normalize(self, inform: Inform, received_at: datetime) -> NormalizedInventory:
        return self.normalize_parameters(inform.parameters, received_at)

    def normalize_parameters(
        self, parameters: tuple[Parameter, ...], received_at: datetime
    ) -> NormalizedInventory:
        return NormalizedInventory(
            model=self._text(parameters, "ModelName"),
            firmware=self._text(parameters, "SoftwareVersion"),
            hardware=self._text(parameters, "HardwareVersion"),
            profile_id=self.profile_id,
            profile_version=self.profile_version,
            received_at=received_at,
        )

    @staticmethod
    def _text(parameters: tuple[Parameter, ...], suffix: str) -> TextObservation:
        paths = (
            f"Device.DeviceInfo.{suffix}",
            f"InternetGatewayDevice.DeviceInfo.{suffix}",
        )
        # Fixed ordering makes the result independent of ParameterList order.
        candidates = sorted(
            (parameter for parameter in parameters if parameter.name in paths),
            key=lambda parameter: (paths.index(parameter.name), parameter.value),
        )
        if not candidates:
            return TextObservation("not_reported")
        values: set[str] = set()
        for candidate in candidates:
            reason: InvalidReason | None = None
            if any(ord(char) < 32 or ord(char) == 127 for char in candidate.value):
                reason = "control_character"
            elif not candidate.value.strip():
                reason = "empty"
            elif len(candidate.value) > 256:
                reason = "too_long"
            if reason is not None:
                return TextObservation(
                    "invalid", source_parameter=candidate.name, reason=reason
                )
            values.add(candidate.value.strip())
        if len(values) != 1:
            return TextObservation("invalid", reason="conflicting_values")
        return TextObservation("valid", values.pop(), candidates[0].name)
