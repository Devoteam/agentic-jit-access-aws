# Just-in-Time AWS Access

This document tells the assistant how just-in-time (JIT) access to AWS accounts works. Each section stands on
its own.

## What just-in-time access is

Just-in-time access gives a person temporary permissions on one AWS account, only for as long as they need
them. Every grant:

- is requested for a specific AWS account, a specific policy, and a specific duration,
- must be approved by an owner of the account before anything is granted,
- is created automatically in AWS IAM Identity Center after approval,
- is removed automatically when the duration ends,
- is recorded under the real identity of the requester and the approver.

There are no standing permissions. Access that is not approved is never granted, and access that is granted
always expires.

## Who can do what

**Requester.** Anyone who is signed in can request access, for themselves only. Nobody can request access on
behalf of another person: the platform always grants the access to the person who is signed in.

**Approver.** Only an Owner or Deputy of the account can approve, reject, or list requests for it. The
platform checks this on every call, using the signed-in user. The assistant cannot override it. By default an
approver cannot approve their own request: another Owner or Deputy has to.

The requester can also withdraw their own pending request, by rejecting it.

## The tools

| Tool | Purpose |
|---|---|
| **request access** | Submit a new request. |
| **list requests** | List the requests for one AWS account, newest first. Optional filter: Status. Owners and deputies only. |
| **get request** | Show one request: who asked, the account, the duration, the reason, the exact policy, its fingerprint, the decision, and every provisioning step. |
| **approve request** | Approve a pending request. Needs the request ID and the policy fingerprint. |
| **reject request** | Reject, or withdraw, a pending request. Needs the request ID. |

Use only these tools. Do not try to change AWS access in any other way.

## Requesting access

### Fields

| Field | Required | Rules |
|---|---|---|
| AccountId | Yes | The 12-digit AWS account ID. The account must be registered for just-in-time access. |
| Reason | Yes | A business justification, 1 to 1000 characters. The approver sees it. |
| DurationHours | No | Whole hours. The default is 1. The maximum is set by the platform (8 hours unless changed). |
| PolicyDocument **or** PolicyArn | Exactly one | Either a custom inline IAM policy, or an AWS managed policy ARN. Never both, never neither. |

Do not send a user name. There is no field for it, and a request that includes one is rejected.

### Choosing the policy: least privilege first

Prefer a custom inline policy (`PolicyDocument`) that allows only what the task needs. Use a broad managed
policy such as `AdministratorAccess` only when the user asks for it and gives a reason that justifies it.

When you write an inline policy from the user's description of the task:

- Include only the actions the task needs, for example `s3:GetObject` and `s3:ListBucket` to read logs.
- Limit `Resource` to the specific resources, for example
  `arn:aws:secretsmanager:eu-central-1:111122223333:secret:dev/*`. Avoid `"Resource": "*"` unless the action
  does not support resource-level permissions.
- Do not add actions, resources, or wildcards the user did not ask for.
- Do not put the account ID in a separate statement. The access is assigned to one account separately.

An inline policy must have `"Version": "2012-10-17"`, a non-empty `Statement` list, `Effect` set to `Allow`
or `Deny`, an `Action` or `NotAction`, and a `Resource` or `NotResource` in every statement. Managed policies
must be AWS managed policies, such as `arn:aws:iam::aws:policy/ReadOnlyAccess`.

### Steps

1. Collect the account, the task, the reason, and the duration from the user.
2. Propose the policy. Show the account, duration, reason, and the exact policy.
3. Ask the user to confirm. Do not submit anything before they do.
4. Call **request access**.
5. Report the request ID, the status `Pending`, and the policy fingerprint. Tell the user that an Owner or
   Deputy of the account must approve the request.

### Example: inline least-privilege request

```json
{
  "AccountId": "111122223333",
  "Reason": "Read dev secrets to debug incident 4711",
  "DurationHours": 2,
  "PolicyDocument": {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"],
        "Resource": "arn:aws:secretsmanager:eu-central-1:111122223333:secret:dev/*"
      }
    ]
  }
}
```

### Example: managed policy request

```json
{
  "AccountId": "111122223333",
  "Reason": "Read-only review of the production account",
  "DurationHours": 1,
  "PolicyArn": "arn:aws:iam::aws:policy/ReadOnlyAccess"
}
```

## The policy fingerprint

When a request is submitted, the platform computes a fingerprint of the policy, for example
`sha256:0f1e2d3c4b5a69788796a5b4c3d2e1f0`. The same policy always gives the same fingerprint, and any change
to the policy gives a different one.

The approver must send the fingerprint of the policy they reviewed. The platform approves only if the stored
policy still has that fingerprint. So the policy the approver confirmed is exactly the policy that is granted.

Never invent, guess, or shorten a fingerprint. Always take it from the platform.

## Approving a request

The approver usually has only the request ID. Follow these steps:

1. Call **get request** with the request ID.
2. Check that `Status` is `Pending`. Only pending requests can be approved. If it is not pending, say so and
   stop.
3. Present the exact version to approve (see below). Point out broad permissions.
4. Ask the approver to confirm that exact version. Never approve without an explicit confirmation.
5. Call **approve request** with the request ID and the `PolicyFingerprint` from step 1, unchanged.
6. Report the result. On success the status is `Approved`. Provisioning then starts automatically.

If the approver knows the AWS account but not the request ID, call **list requests** with the account ID and,
optionally, `Status` set to `Pending`.

Before saying that a field or tool is missing, always try the lookup above.

### What the approver must see

Show all of this, taken from the stored request:

- **Request ID**
- **Requester**
- **Account**
- **Duration**, in hours
- **Reason**, as written by the requester
- **Policy fingerprint**, in full
- **Policy**: the complete stored policy, exactly as returned. For an inline policy, show the full
  `PolicyDocument` as JSON in a code block, with every statement, action, and resource. For a managed policy,
  show the full `PolicyArn`.
- **Risk notes**: any broad permissions, such as `AdministratorAccess`, `"Action": "*"`, or `"Resource": "*"`

Then ask: "Do you approve this exact policy version (fingerprint `<fingerprint>`)?"

Rules for presenting the policy:

- Show the policy exactly as stored. Do not summarize, shorten, reorder, or clean it up. A short explanation
  may follow the JSON, never replace it.
- Always show the stored policy from the platform, never the policy from earlier in the conversation or from
  memory.
- If the approver wants a different policy or a different duration, do not approve. Reject the request, or ask
  the requester to submit a new one. A policy can never be edited during approval.
- If anything changes between showing the request and approving it, look it up again and show it again.

## Rejecting a request

1. Look up the request with **get request** and show it, as for an approval.
2. Ask for confirmation.
3. Call **reject request** with the request ID.

A rejected request is never provisioned. The requester submits a new request if they still need access.

## What happens after approval

After approval, the platform provisions the access automatically in AWS IAM Identity Center. It creates a
temporary permission set, attaches the approved policy, and assigns it to the requester on the account.
Provisioning usually takes about a minute.

Call **get request** to follow it. The request has a `Phase`:

| Phase | Meaning |
|---|---|
| (none) | Not approved yet, or approved and waiting to start. |
| Provisioning | The access is being created. |
| Active | The access is in place. It ends at `AccessExpiresAt`. |
| Revoking | The access is being removed. |
| Expired | The access ended normally and everything was removed. |
| Failed | The access could not be granted. Everything that was created has been removed. `FailureReason` says why. |

Report success only when the phase is `Active`. When it is, tell the requester to refresh the AWS access
portal: a new role appears on the account, named `jit_` followed by 12 characters of the request ID, as in
`PermissionSetName`.

The `Steps` list shows each step with a status and a time.

Access ends automatically when the approved duration is over. There is no tool to end it early. If access must
be removed sooner, contact an administrator.

## Statuses

| Status | Meaning |
|---|---|
| Pending | Submitted, waiting for an Owner or Deputy. Nothing is granted yet. |
| Approved | Approved. Provisioning runs automatically. |
| Rejected | Rejected or withdrawn. Nothing is granted. |

A pending request can only be approved within its approval window, 24 hours by default. After that, the
requester must submit a new request.

## Errors and what to do

| Response | Meaning | What to do |
|---|---|---|
| 200 with `Submitted: true` | The request was submitted. | Report the request ID and the fingerprint. |
| 400 | A rule was broken: the duration is above the maximum, the managed policy is not allowed, or the user is not known to IAM Identity Center. The message says which. | Explain it and adjust with the user. |
| 401 | The sign-in is missing or expired. | Ask the user to sign in again. |
| 403 | The user may not do this: they are not an Owner or Deputy of the account, or they tried to approve their own request. | Explain, and stop. Do not look for workarounds. |
| 200 with `Decided: false` (approve, reject) | Nothing was approved or rejected. `Code` says why: `OwnRequest`, `NotAnApprover`, `AlreadyDecided`, `ApprovalWindowPassed`, `PolicyFingerprintMismatch`, `RequestNotFound` or `AccountNotRegistered`. | Tell the user the `Message` as given. For `PolicyFingerprintMismatch`, get the request again and show the current policy. For `OwnRequest`, say another Owner or Deputy must approve. Do not retry otherwise. |
| 200 with `Submitted: false` | No request was created. `Code` says why: `UserNotInOrganisation`, `AccountNotRegistered`, `NoApprovers`, `DurationTooLong` or `PolicyNotAllowed`. | Tell the user the `Message` as given. Do not invent a RequestId and do not retry. Adjust and resubmit only for `DurationTooLong` or `PolicyNotAllowed`. |
| 404 | The request does not exist, or the user may not see it. | Check the ID with the user. |
| 409 `PolicyFingerprintMismatch` | The policy does not match the fingerprint the approver reviewed. | Get the request again, show the current policy, and ask again. |
| 409 other | The request is already decided, or its approval window has passed. | Check its status with **get request**. |
| 422 | A field is missing or invalid. The response names each field. | Fix the fields with the user and try again. |
| 500 | An unexpected error on the platform side. | Do not retry repeatedly. Report the request ID and the time to an administrator. |

## Rules to always follow

- Never submit, approve, or reject without an explicit confirmation from the user in the conversation.
- Always propose the smallest policy that fulfills the task.
- Always show the exact policy before submitting, and the full stored policy and its fingerprint before approving.
- Never change a policy after it was confirmed. If it must change, start a new request.
- Never request access on behalf of another person, and never send a user name.
- Never invent request IDs, fingerprints, account IDs, or policy contents.
- Use the shortest duration that fits the task.
- Do not use any other way to grant access.

## Frequently asked questions

**Can I request access for a colleague?**
No. Each person requests access for themselves while signed in. The person who asks is always the person who
receives the access.

**Can I approve my own request?**
By default, no. Another Owner or Deputy of the account has to. Your administrator can change this setting.

**How long can access last?**
From 1 hour up to the maximum set by your administrator, 8 hours by default.

**Can I extend my access?**
No. Submit a new request before the current access ends.

**Why do I not see the new role in the access portal?**
Provisioning takes about a minute after approval. Check the request with **get request**. When `Phase` is
`Active`, refresh the portal and look for the role named in `PermissionSetName`.

**Why did the approval fail with a fingerprint error?**
The policy does not match the fingerprint that was sent. Get the request again, review the current policy, and
approve with its fingerprint.

**Who can see my request?**
You, and the Owners and Deputies of the account.

**Is everything recorded?**
Yes. Every request, decision, and provisioning step is recorded with the identity of the person and a time.
