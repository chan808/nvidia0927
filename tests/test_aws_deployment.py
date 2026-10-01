"""Release validation boundaries and persistence of generated server credentials."""
import importlib.util
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pilot_runtime", ROOT / "deploy/aws/prepare_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def test_credentials_are_preserved_across_release_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.os, "chown", lambda *args: None, raising=False)
    path = tmp_path / "private" / "token"
    assert runtime.secret_file(path, "first-token") == "first-token"
    assert runtime.secret_file(path, "replacement-token") == "first-token"
    assert path.read_text() == "first-token"


@pytest.mark.parametrize("revision,image", [
    ("main;touch /tmp/injected", "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/test@sha256:" + "0" * 64),
    ("0" * 40, "tracebridge:latest"),
    ("0" * 40, "another-registry/test@sha256:" + "0" * 64),
])
def test_runtime_rejects_mutable_or_untrusted_release_input_before_writing(tmp_path, revision, image):
    with pytest.raises(ValueError):
        runtime.prepare({}, revision, image, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_deploy_role_targets_only_the_new_instance_and_dedicated_document():
    template = yaml.safe_load((ROOT / "deploy/aws/pilot.yaml").read_text())
    policies = template["Resources"]["GithubRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    ssm = next(s for s in policies if "ssm:SendCommand" in s["Action"])
    assert ssm["Resource"] == [
        {"Fn::Sub": "arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:document/${DeployDocument}"},
        {"Fn::Sub": "arn:aws:ec2:${AWS::Region}:${AWS::AccountId}:instance/${Instance}"},
    ]
    trust = template["Resources"]["GithubRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    subjects = trust["Condition"]["StringEquals"]["token.actions.githubusercontent.com:sub"]
    assert subjects == [
        {"Fn::Sub": "${GithubSubjectPrefix}:ref:refs/heads/main"},
        {"Fn::Sub": "${GithubSubjectPrefix}:ref:refs/heads/${DeploymentBranch}"},
    ]
    assert "*" not in str(subjects)
    assert template["Parameters"]["GithubSubjectPrefix"]["AllowedValues"] == ["repo:chan808@177499146/nvidia0927@1390222127"]


def test_ec2_has_no_ssh_and_no_unlimited_cpu_charge_mode():
    template = yaml.safe_load((ROOT / "deploy/aws/pilot.yaml").read_text())
    resources = template["Resources"]
    assert {r["FromPort"] for r in resources["SecurityGroup"]["Properties"]["SecurityGroupIngress"]} == {80, 443}
    assert resources["Instance"]["Properties"]["CreditSpecification"]["CPUCredits"] == "standard"
    assert resources["Instance"]["Properties"]["MetadataOptions"]["HttpTokens"] == "required"
    params = resources["DeployDocument"]["Properties"]["Content"]["parameters"]
    assert re.fullmatch(params["Revision"]["allowedPattern"], "0" * 40)
    assert not re.fullmatch(params["Revision"]["allowedPattern"], "main';echo exposed")
    assert not re.fullmatch(params["Digest"]["allowedPattern"], "latest")


def test_pilot_limits_leave_room_for_os_and_never_publish_db_or_runner_ports():
    pilot = yaml.safe_load((ROOT / "deploy/compose.pilot.yaml").read_text())
    total = sum(int(pilot["services"][name]["mem_limit"].removesuffix("m"))
                for name in ["web", "control", "postgres", "guard", "proxy"])
    assert total <= 1536
    pg = yaml.safe_load((ROOT / "deploy/postgres.compose.yaml").read_text())["services"]["postgres"]
    control = yaml.safe_load((ROOT / "deploy/control-plane.compose.yaml").read_text())["services"]["control"]
    assert "ports" not in pg and "ports" not in control
    assert pilot["services"]["control"]["environment"]["FORWARDED_ALLOW_IPS"] == "172.28.70.6"
