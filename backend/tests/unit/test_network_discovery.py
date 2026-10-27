"""Discovery is bounded and cannot be converted into arbitrary value reads."""

from dataclasses import replace

import pytest

from acs.cwmp.compatibility.queries import V5_TARGET, HuaweiQueryPolicy
from acs.domain.operations.query import ParameterInfo, QueryConflict, QueryReply


@pytest.mark.parametrize(
    "path",
    [
        "InternetGatewayDevice.",
        "InternetGatewayDevice.WANDevice.",
        "InternetGatewayDevice.WANDevice.2.WANConnectionDevice.3.WANPPPConnection.4.",
    ],
)
def test_reviewed_identity_and_discovery_paths(path: str) -> None:
    plan = HuaweiQueryPolicy().plan(replace(V5_TARGET, model=None), "discovery", path)
    assert plan.scope == "discovery" and plan.next_level and plan.names == ()


@pytest.mark.parametrize(
    "path",
    [
        "",
        "InternetGatewayDevice.LANDevice.1.",
        "InternetGatewayDevice.ManagementServer.",
        "InternetGatewayDevice.WANDevice.1.Password",
        "InternetGatewayDevice.WANDevice." + "a." * 10,
    ],
)
def test_unrelated_or_unbounded_paths_rejected(path: str) -> None:
    with pytest.raises(QueryConflict):
        HuaweiQueryPolicy().plan(V5_TARGET, "discovery", path)


def test_projection_excludes_unapproved_leaf_names() -> None:
    policy = HuaweiQueryPolicy()
    path = "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1."
    plan = policy.plan(V5_TARGET, "discovery", path)
    names = (
        ParameterInfo(path + "ExternalIPAddress", False),
        ParameterInfo(path + "Password", True),
        ParameterInfo(path + "Username", True),
    )
    assert (
        policy.discovery_names(
            QueryReply("id", "get_parameter_names", names=names), plan
        )
        == names[:1]
    )
