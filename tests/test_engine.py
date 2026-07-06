"""Regression tests for the cfn-name-check engine.

Pins the exact behavior of: Description-based stack-name extraction, exact
condition pruning (unreachable !If branches), strict mode, budget mode,
deploy-time parameter warnings, resource-reference resolution, unnamed
resources, nested paths, and the generated limits DB.
"""
import os
import pytest
from cfn_name_check.engine import check_template

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "regression.yml")


def _run(**kw):
    with open(FIXTURE, encoding="utf-8") as f:
        return check_template(f.read(), filename="regression.yml", **kw)


def _by_key(result):
    return {f"{f['resource']}.{f['property']}": f for f in result["findings"]}


def test_stack_name_from_description():
    r = _run()
    assert r["stackName"] == "pipeline-example-sample-service-api-dev"
    assert r["budgetMode"] is False


def test_exact_mode_prunes_unreachable_branch():
    """AllowedValues force dev/prod; the !Ref fallback ('staging', 7 chars) is unreachable."""
    f = _by_key(_run())["OverLimitRole.RoleName"]
    assert f["severity"] == "error"
    assert f["worstCase"].endswith("-prod")
    assert f["length"] == 69  # 39-char stack + 26-char infix + 4-char suffix
    assert f["maxStackNameLen"] == 34


def test_strict_mode_takes_longest_branch():
    f = _by_key(_run(strict=True))["OverLimitRole.RoleName"]
    assert f["worstCase"].endswith("-staging")
    assert f["length"] == 72


def test_free_form_condition_param_does_not_disable_pruning():
    """HasEmail references a free-form param; EnvSuffix pruning must still work."""
    f = _by_key(_run())["NearLimitRule.Name"]
    assert f["worstCase"].endswith("-prod")


def test_near_limit_is_warning():
    f = _by_key(_run())["NearLimitRule.Name"]
    assert f["severity"] == "warning"
    assert 0 <= f["margin"] <= 5


def test_safe_name_is_info():
    f = _by_key(_run())["SafeFunction.FunctionName"]
    assert f["severity"] == "info"


def test_nested_paths_checked():
    keys = _by_key(_run())
    assert "OverLimitRole.Policies.[0].PolicyName" in keys
    assert "NearLimitRule.Targets.[0].Id" in keys


def test_unnamed_resource_produces_no_finding():
    assert not any(f["resource"] == "UnnamedBucket" for f in _run()["findings"])


def test_stack_name_override():
    f = _by_key(_run(stack_name="short"))["OverLimitRole.RoleName"]
    assert f["severity"] == "info"
    assert f["worstCase"].startswith("short-")


def test_budget_mode_warns_when_stack_could_overflow():
    """Description regex that matches nothing -> budget mode; 64-limit props warn."""
    r = _run(description_regex="NOPE=(\\S+)")
    assert r["budgetMode"] is True
    f = _by_key(r)["OverLimitRole.RoleName"]
    assert f["severity"] == "warning"
    assert f["maxStackNameLen"] == 34


def test_deploy_time_parameter_warning():
    tpl = """
Parameters:
  ProjectName: {Type: String}
Resources:
  R:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub "${ProjectName}-application-execution-role"
      AssumeRolePolicyDocument: {Version: "2012-10-17", Statement: []}
"""
    f = check_template(tpl)["findings"][0]
    assert f["severity"] == "warning"
    assert "ProjectName" in f["message"]
    assert "leaving only" in f["message"]


def test_deploy_time_fixed_part_over_limit_is_error():
    tpl = """
Parameters:
  P: {Type: String}
Resources:
  R:
    Type: AWS::Events::Rule
    Properties:
      Name: !Sub "${P}-this-fixed-part-is-really-extremely-unreasonably-long-notification-rule-x"
      EventPattern: {source: [x]}
"""
    f = check_template(tpl)["findings"][0]
    assert f["severity"] == "error"
    assert "fixed part alone" in f["message"]


def test_param_maxlength_is_exact_not_deploy_time():
    tpl = """
Parameters:
  P: {Type: String, MaxLength: 10}
Resources:
  R:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub "${P}-role"
      AssumeRolePolicyDocument: {Version: "2012-10-17", Statement: []}
"""
    f = check_template(tpl)["findings"][0]
    assert f["unresolved"] is False
    assert f["length"] == 15


def test_generated_registry_coverage():
    """A type absent from the curated set must still be checked (SageMaker 63)."""
    tpl = """
Description: StackName=analytics-platform-customer-behaviour-tracking-dev
Resources:
  E:
    Type: AWS::SageMaker::Endpoint
    Properties:
      EndpointName: !Sub "${AWS::StackName}-inference-endpoint"
      EndpointConfigName: cfg
"""
    f = {x["property"]: x for x in check_template(tpl)["findings"]}["EndpointName"]
    assert f["severity"] == "error"
    assert f["limit"] == 63


def test_lowercase_constraint():
    tpl = """
Resources:
  B:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: MyMixedCaseBucket
"""
    fs = check_template(tpl)["findings"]
    assert any("lowercase" in f["message"] for f in fs)


def test_non_template_ignored():
    r = check_template("just: some\nrandom: yaml\n")
    assert r["isTemplate"] is False and r["findings"] == []
