# Security policy

## Reporting a vulnerability

Please use GitHub's **Report a vulnerability** flow on this repository's
[Security advisories page](https://github.com/Craft-s/FinSight-AI/security/advisories/new).
Reports are intended for private discussion with the maintainer. If the private
reporting control is unavailable, do not publish exploit details or credentials
in an issue; open a minimal issue asking for a private contact channel.

Include the affected commit/version, reproduction steps, impact and a minimal
sanitized example. Never submit real API keys, administrator tokens, database
backups, private research histories or personal contact/location information.
Use synthetic test data when possible.

## Supported scope

This project is under active development. Security fixes target the latest
`main` branch; older commits are not maintained as separate supported releases.
There is no guaranteed response time or paid bug bounty.

The application currently uses a single-user authorization model for local
research. Making this source repository public does not turn the application
into a public multi-user service. Do not expose the local backend or model
services without appropriate authentication and network controls.

For the implemented controls and their verified limits, see
[docs/SECURITY.md](docs/SECURITY.md). Financial data quality, citation matching
and semantic correctness of model output are separate concerns; a successful
security check is not an audit of financial conclusions.
