import pytest

from jit_access.common.policy import (
    MAX_INLINE_POLICY_CHARS,
    policy_fingerprint,
    validate_inline_policy,
    validate_managed_policy_arn,
)
from jit_access.common.usernames import normalize_username, same_user

from .conftest import policy_document


def test_valid_inline_policy_passes():
    validate_inline_policy(policy_document())


@pytest.mark.parametrize(
    "document, message",
    [
        ("not a dict", "JSON object"),
        ({"Version": "2008-10-17", "Statement": []}, "Version"),
        ({"Version": "2012-10-17", "Statement": []}, "non-empty list"),
        ({"Version": "2012-10-17", "Statement": "x"}, "non-empty list"),
        ({"Version": "2012-10-17", "Statement": ["x"]}, "must be an object"),
        ({"Version": "2012-10-17", "Statement": [{"Effect": "Maybe", "Action": "s3:*", "Resource": "*"}]}, "Effect"),
        ({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Resource": "*"}]}, "Action"),
        ({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "s3:*"}]}, "Resource"),
    ],
)
def test_invalid_inline_policies_are_rejected(document, message):
    with pytest.raises(ValueError, match=message):
        validate_inline_policy(document)


def test_oversized_inline_policy_is_rejected():
    document = policy_document(resource="arn:aws:s3:::" + "x" * MAX_INLINE_POLICY_CHARS)
    with pytest.raises(ValueError, match="larger than"):
        validate_inline_policy(document)


def test_managed_policy_arn_must_be_an_aws_managed_policy():
    validate_managed_policy_arn("arn:aws:iam::aws:policy/ReadOnlyAccess")
    validate_managed_policy_arn("arn:aws:iam::aws:policy/job-function/ViewOnlyAccess")
    for bad in ("ReadOnlyAccess", "arn:aws:iam::111122223333:policy/Mine", "arn:aws:iam::aws:role/Admin"):
        with pytest.raises(ValueError):
            validate_managed_policy_arn(bad)


def test_fingerprint_is_stable_and_ignores_key_order():
    first = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "*"}]}
    second = {"Statement": [{"Resource": "*", "Action": ["s3:GetObject"], "Effect": "Allow"}], "Version": "2012-10-17"}
    assert policy_fingerprint(None, first) == policy_fingerprint(None, second)
    assert policy_fingerprint(None, first).startswith("sha256:")
    assert len(policy_fingerprint(None, first)) == len("sha256:") + 32


def test_fingerprint_changes_when_the_policy_changes():
    base = policy_document()
    assert policy_fingerprint(None, base) != policy_fingerprint(None, policy_document(action="s3:PutObject"))
    assert policy_fingerprint(None, base) != policy_fingerprint(None, policy_document(resource="*"))


def test_managed_and_inline_fingerprints_do_not_collide():
    arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
    assert policy_fingerprint(arn, None) != policy_fingerprint(None, {"arn": arn})
    assert policy_fingerprint(arn, None) == policy_fingerprint(arn, None)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("alex@contoso.onmicrosoft.com", "alex"),
        ("alex@CONTOSO.onmicrosoft.com", "alex"),
        ("alex", "alex"),
        ("alex@other.com", "alex@other.com"),
        (" alex@contoso.onmicrosoft.com ", "alex"),
        ("", ""),
    ],
)
def test_usernames_lose_only_configured_domains(value, expected):
    assert normalize_username(value, ("contoso.onmicrosoft.com",)) == expected


def test_usernames_keep_everything_when_no_domain_is_configured():
    assert normalize_username("alex@contoso.onmicrosoft.com") == "alex@contoso.onmicrosoft.com"


def test_same_user_ignores_case_and_spaces():
    assert same_user("Alice", " alice ")
    assert not same_user("alice", "bob")
