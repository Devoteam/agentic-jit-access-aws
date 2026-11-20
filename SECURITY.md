# Security policy

This project grants access to AWS accounts, so security reports matter.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately to **[SECURITY CONTACT EMAIL]**. Include what you found, how to reproduce it, and what
you think the impact is. You will get an acknowledgement within a few working days.

## What we consider a vulnerability

- A way to obtain access without an approval, or to approve a request without being an owner or deputy of the account.
- A way to request or approve access as another person.
- A way to make access outlive its approved duration, or to leave a permission set or assignment behind.
- A way to change what is granted after an approver reviewed it.
- A token that is accepted but should not be (wrong tenant, wrong audience, missing scope).
- Secrets or tokens written to logs.

## Hardening guidance

See [docs/security.md](docs/security.md) for the threat model, the guarantees, the known limits, and a
checklist to go through before using this to protect production accounts.
