# Code signing policy

*Monitor for Amateur Radio (Monitor for BrandMeister)* – https://github.com/hannulma/monitor-for-amateur-radio

**Status:** the project is applying to the SignPath Foundation open-source program. Until the application is
approved, the Windows builds are **not signed**. Once approved, signed Windows releases will carry this notice:

> Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

## What is signed

- `MonitorForBrandMeister.exe` (the program) and `MonitorForBrandMeister-<version>-windows-setup.exe` (the installer),
  built by GitHub Actions on GitHub-hosted runners from this public repository
  ([`.github/workflows/release.yml`](.github/workflows/release.yml)).
- Bundled third-party open-source components (the Python runtime and libraries) are not signed with the project's
  certificate. The PyInstaller bootloader is compiled from source in the same workflow.
- macOS, Linux and Python (pip) packages are not signed by this program.

Every signing request is approved manually by the approver below. Product name and version are set in every build
and checked by the workflow before signing.

## Team and roles

| Role | Member |
|---|---|
| Author / committer | Hannu ([@hannulma](https://github.com/hannulma), OH2BEA) |
| Reviewer | Hannu ([@hannulma](https://github.com/hannulma), OH2BEA) |
| Approver | Hannu ([@hannulma](https://github.com/hannulma), OH2BEA) |

All team members use multi-factor authentication for GitHub and SignPath. Contributions from others are reviewed
before they are merged.

## Privacy

This program will not transfer any information to other networked systems unless specifically requested by the user
or the person installing or operating it. See the [privacy policy](PRIVACY.md) for the services the program contacts
when you enable the corresponding features, and how to switch each one off.
