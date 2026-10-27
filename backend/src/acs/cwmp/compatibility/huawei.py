"""Inventory mapping supported by the reviewed EG8145X6-10 laboratory evidence."""

from dataclasses import replace

from acs.domain.cpe.inform import Inform
from acs.domain.cpe.inventory import NormalizedInventory, TextObservation


class HuaweiEG8145X6Profile:
    profile_id = "huawei-eg8145x6-10-v5r022c00s364"
    profile_version = "1"
    priority = 100
    product_class = "EG8145X6-10"
    firmware = "V5R022C00S364"

    def matches(self, inform: Inform, generic: NormalizedInventory) -> bool:
        device = inform.device
        return (
            inform.version == "1.0"
            and device.manufacturer == "Huawei Technologies Co., Ltd"
            and device.oui == "00259E"
            and device.product_class == self.product_class
            and generic.firmware.state == "valid"
            and generic.firmware.value == self.firmware
            and any(
                parameter.name == "InternetGatewayDevice.DeviceInfo.SoftwareVersion"
                for parameter in inform.parameters
            )
            and (
                generic.model.state != "valid"
                or generic.model.value == device.product_class
            )
        )

    def apply(
        self, inform: Inform, generic: NormalizedInventory
    ) -> NormalizedInventory:
        model = generic.model
        if model.state == "not_reported":
            model = TextObservation(
                "valid", inform.device.product_class, "DeviceId.ProductClass"
            )
        return replace(
            generic,
            model=model,
            profile_id=self.profile_id,
            profile_version=self.profile_version,
        )


class HuaweiEG8145V5Profile(HuaweiEG8145X6Profile):
    """Same basic mapping, independently bound to the reviewed V5-V2 evidence."""

    profile_id = "huawei-eg8145v5-v2-v5r021c00s210"
    priority = 110
    product_class = "EG8145V5-V2"
    firmware = "V5R021C00S210"
