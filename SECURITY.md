# Security Policy

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue. Email
**varisinghsandhu@gmail.com** (replace with your contact) with a description, reproduction
steps, and impact. You'll get an acknowledgement, and we'll work on a fix and coordinated
disclosure.

## Design summary

Caliber runs only on loopback (`127.0.0.1`), validates the request `Host`/`Origin` to block
cross-site access (DNS-rebinding / CSRF), streams uploads with size limits, sanitises file
names and inputs, downloads only over HTTPS, and never logs or transmits your provider API
key except to the provider you choose. There is no telemetry.

A full review of findings and fixes is in [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md),
including the pre-release checklist (vendor front-end assets / add Subresource Integrity,
pin dependencies, code-sign the packaged builds).

## Supported versions

The latest release on the default branch is supported.
