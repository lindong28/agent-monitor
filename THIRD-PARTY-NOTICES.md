# Third-party notices

agent-monitor's own code is distributed under [MIT](LICENSE). Incorporated third-party source and dependencies keep their own licenses. The source distribution excludes downloaded Python environments, Electron binaries, Chart.js bundles and font binaries; the installer obtains them from their upstream distributions.

## Incorporated source

| Local files | Upstream source and adaptations | Included license |
| --- | --- | --- |
| `parsers/claude.py`, `parsers/codex.py`, `pricing_fetcher.py` | Adapted from stormzhang/token-tracker's `src/adapters/claude.py`, `src/adapters/codex.py` and `src/analyzer/cost.py`; dependency removal and local parsing/accounting/cache integration | [MIT, Copyright (c) 2026 stormzhang](LICENSES/token-tracker-MIT.txt) |
| `ip_check/cli.py` | Adapted from the `ai-ipcheck` 0.2.0 CLI by stormzhang; structured output, network-policy routing and dashboard integration | [Apache License 2.0](LICENSES/ipcheck-Apache-2.0.txt) |

The token-tracker attribution is bound to [snapshot ae15a0262150d083d9f1b51b55598329375f5b05](https://github.com/stormzhang/token-tracker/tree/ae15a0262150d083d9f1b51b55598329375f5b05), which includes an explicit MIT license. The three upstream source files match their earlier import source; this identifies a licensed source snapshot without claiming that the license file was present at the original import date.

The `ai-ipcheck` 0.2.0 CLI matches [ipcheck snapshot efd9d1a446de2bae9e5304f53dd96439368cadbe](https://github.com/stormzhang/ipcheck/tree/efd9d1a446de2bae9e5304f53dd96439368cadbe). The package metadata says MIT, but the bundled license and corresponding repository license are Apache 2.0; this distribution retains the Apache 2.0 text. Neither the examined wheel nor that source snapshot contains an upstream NOTICE file.

## Installed dependencies

| Component | Use | Upstream license material |
| --- | --- | --- |
| Requests | Python HTTP client | [Apache 2.0](https://github.com/psf/requests/blob/main/LICENSE) |
| Colorama | Terminal output | [BSD 3-Clause](https://github.com/tartley/colorama/blob/master/LICENSE.txt) |
| Chart.js 4.4.0 | Browser charts | [MIT](https://github.com/chartjs/Chart.js/blob/v4.4.0/LICENSE.md); its downloaded bundle retains the upstream header |
| IBM Plex Sans / Mono | Browser fonts | [SIL Open Font License 1.1](https://github.com/IBM/plex/blob/master/LICENSE.txt); the installer attempts to save the license beside downloaded fonts |
| Electron 45.0.0 | Chromium system-proxy runtime | [Electron license](https://github.com/electron/electron/blob/v45.0.0/LICENSE) and the license/Chromium notice files inside its release archive |

Redistributors who bundle these generated artifacts must include the corresponding notices from the actual downloaded versions. This source distribution does not grant ownership of third-party components or guarantee license compliance for a separately built binary bundle.

The implementation originated in the author's `tt-web` project in ai-agent-config, extracted at commit `3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2`. Parser comments retain their token-tracker provenance, and `token_cost.py` retains its shared arithmetic contract. These are source attributions, not runtime installation prerequisites.
