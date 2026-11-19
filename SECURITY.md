# Security policy

## Reporting a vulnerability

**Please do not report security vulnerabilities as public issues.**

Use the **Security → Report a vulnerability** button on this repository to send a private report through GitHub. Only the maintainer can see it.

Please include:

- The affected component (kobra-screen, ACE driver, install package, ace-standalone…) and version (`open-kobra/VERSION`)
- Steps to reproduce
- The possible impact (e.g. unauthenticated network access, driving a heater to a dangerous temperature, deleting files)

Remove Wi-Fi names, IP addresses, serial numbers and passwords from any logs.

## Scope

Of particular interest:

- Network-reachable services: the ace-standalone web panel (listens on `127.0.0.1` only by default) and components that talk to Moonraker
- Behaviour that bypasses heater or motor safety limits
- Install or uninstall scripts deleting or overwriting the wrong files
- Bypassing the install package's integrity checks

Please report issues in Rinkhals, Klipper, Moonraker or the Anycubic stock software to those projects.

## Support

This is a volunteer project; response times are not guaranteed. Fixes for confirmed vulnerabilities are released and mentioned in the release notes.
