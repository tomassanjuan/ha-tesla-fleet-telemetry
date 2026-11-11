# Third-Party Notices

This project is licensed under the MIT License (see [LICENSE](LICENSE)).

It bundles **no** third-party source files. Home Assistant installs the runtime
dependencies listed in `custom_components/tesla_fleet_telemetry/manifest.json`
separately; they remain under their own licences:

| Package | Used for | Licence |
| --- | --- | --- |
| [paho-mqtt](https://github.com/eclipse/paho.mqtt.python) | MQTT client | EPL-2.0 OR BSD-3-Clause |
| [tesla-fleet-api](https://github.com/Teslemetry/python-tesla-fleet-api) | Tesla Fleet API client | Apache-2.0 |
| [qrcode](https://github.com/lincolnloop/python-qrcode) (with pypng) | Pairing QR code | BSD / MIT |

Licences were read from each package's published metadata and may change in
newer releases. Development-only tools (pytest, ruff, mypy, and so on) are not
distributed with this project. Home Assistant itself is Apache-2.0.

## Credits

Teslemetry and Bre77 were the main inspiration for this project, and their `tesla-fleet-api`
library is a dependency. Teslemetry, Tessie and Home Assistant Core's Tesla
integrations are independent projects; see the README for when to prefer them.

## Trademarks

Tesla, Model S, Model 3, Model X, Model Y, Cybertruck and the Tesla logo are
trademarks of Tesla, Inc. Home Assistant is a trademark of the Open Home
Foundation. Teslemetry and Tessie belong to their respective owners. This project is independent and is not affiliated with,
sponsored by, or endorsed by either. Names are used only to describe
compatibility. The icon in `custom_components/tesla_fleet_telemetry/brand/` is
original artwork and is not a Tesla mark.

## Disclaimer

This is not legal advice and the author is not a lawyer. If you redistribute
this project commercially, check the licences above yourself.
