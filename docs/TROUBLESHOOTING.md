# Troubleshooting

Use this guide to find whether the problem is Home Assistant, MQTT, the command proxy, Fleet Telemetry, TLS, Tesla authorization, or the vehicle.

## Fast Checks

| Question | Check |
| --- | --- |
| Are the services running? | `docker compose ps` |
| Is MQTT receiving vehicle data? | `mosquitto_sub -h mqtt.local -t 'tesla/#' -v` |
| Is the telemetry receiver reachable? | `openssl s_client -connect telemetry.example.com:19855 -servername telemetry.example.com </dev/null` |
| Is the proxy reachable? | `openssl s_client -connect proxy.example.com:4443 -servername proxy.example.com </dev/null` |
| Is Home Assistant connected to MQTT? | Check integration settings and MQTT logs |
| Is the vehicle asleep? | Wait, wake from Home Assistant, or check Tesla app |
| Is the virtual key paired? | Pair again at `https://tesla.com/_ak/example.com` |

## Logs To Check

Home Assistant:

```yaml
logger:
  default: info
  logs:
    custom_components.tesla_fleet_telemetry: debug
```

Turn debug logging off after the issue is fixed.

Service logs:

```bash
docker compose logs --tail=200 vehicle-command
docker compose logs --tail=200 fleet-telemetry
docker compose logs --tail=200 mqtt
```

## No Telemetry In Home Assistant

Likely causes:

- Vehicle has not connected to the telemetry receiver yet.
- Vehicle is asleep.
- Telemetry receiver is not reachable from the internet.
- TLS certificate is invalid or does not match the telemetry hostname.
- MQTT is down or unreachable.
- Home Assistant is connected to the wrong MQTT broker.
- No useful entities are enabled.
- VIN in MQTT does not match the configured vehicle.

Steps:

1. Subscribe to MQTT:

   ```bash
   mosquitto_sub -h mqtt.local -t 'tesla/#' -v
   ```

2. Check telemetry receiver logs:

   ```bash
   docker compose logs --tail=200 fleet-telemetry
   ```

3. Validate public TLS:

   ```bash
   openssl s_client -connect telemetry.example.com:19855 -servername telemetry.example.com </dev/null
   ```

4. Enable one simple entity such as battery level.
5. Reload the integration.
6. Wake the vehicle only if you need immediate validation.

## Commands Fail

Likely causes:

- Vehicle is asleep, offline, or has poor signal.
- Virtual key is not paired.
- Command proxy cannot read the private key.
- Tesla authorization expired or was revoked.
- Command is unsupported by that vehicle.
- Too many commands were sent too quickly.

Steps:

1. Confirm Home Assistant can reach the proxy.
2. Check Home Assistant logs for auth, rate limit, key, or unavailable messages.
3. Check proxy logs:

   ```bash
   docker compose logs --tail=200 vehicle-command
   ```

4. If key errors appear, pair again:

   ```text
   https://tesla.com/_ak/example.com
   ```

5. If authorization errors appear, reauthorize the integration in Home Assistant.
6. If the vehicle is asleep, wait for the wake retry or send one manual wake.

## Setup Cannot Fetch The Certificate

Likely causes:

- DNS points to the wrong server.
- Port `19855` is blocked.
- Certificate hostname does not match.
- Full certificate chain is missing.
- Reverse proxy or load balancer is not passing TLS correctly.

Checks:

```bash
dig telemetry.example.com
nc -vz telemetry.example.com 19855
openssl s_client -connect telemetry.example.com:19855 -servername telemetry.example.com </dev/null
```

If automatic fetch still fails, paste the PEM certificate manually in Home Assistant.

## Common Error Meanings

| Error | Meaning | Fix |
| --- | --- | --- |
| `missing_key` | Vehicle does not have the virtual key | Host public key, open pairing URL, pair in Tesla app |
| `unsupported_firmware` | Vehicle firmware does not support the requested behavior | Update vehicle, disable unsupported entities |
| `limit_reached` | Too many telemetry configurations exist for the vehicle | Remove stale configs and use one active setup |
| `ca_fetch_failed` | Home Assistant could not fetch telemetry certificate | Fix DNS, port, SNI, certificate chain, or paste PEM |
| `vehicle unavailable` | Vehicle is asleep, offline, or unreachable | Wait, wake once, avoid command loops |
| `rate limited` | Too many requests or setting changes | Wait, reduce automation frequency |

## MQTT Has Data But Entities Are Stale

Check:

- VIN in MQTT matches the configured vehicle.
- Entity is enabled.
- MQTT broker address is correct in the integration options.
- Home Assistant can subscribe to the Tesla topics.
- Integration was reloaded after settings changed.
- Payload topic base is `tesla` unless you changed it everywhere.

Expected topic shapes:

```text
tesla/5YJ.../v/BatteryLevel 72
tesla/5YJ.../v/Location {"latitude": 1.23, "longitude": 4.56}
tesla/5YJ.../connectivity {"Status": "CONNECTED"}
tesla/5YJ.../errors/unsupported_interval {...}
```

## TLS Certificate Rotation

Use this when telemetry or proxy certificates change.

1. Issue the new certificate.
2. Install it on the service host.
3. Restart the affected service:

   ```bash
   docker compose restart vehicle-command fleet-telemetry
   ```

4. Validate with `openssl s_client`.
5. Update Home Assistant options if the telemetry certificate changed.
6. Reload the integration.
7. Confirm telemetry resumes.

Rollback: restore the previous certificate and key pair, then restart the service.

## OAuth Reauthorization

Use this when Home Assistant says Tesla authorization is invalid.

1. Open **Settings > Devices & services**.
2. Select **Tesla Fleet Telemetry**.
3. Complete the reauthorization prompt.
4. Confirm vehicle selection still works.
5. Test a low-risk command when safe.

If reauthorization loops, confirm the Tesla application credentials and permissions.

## Rate Limit Recovery

1. Stop the automation or manual action causing repeated commands.
2. Wait for the retry window shown in logs.
3. Add cooldowns or state checks to the automation.
4. Avoid rapidly enabling and disabling many entities.

## What To Collect For Help

Collect and sanitize:

- Home Assistant version.
- Integration version.
- Vehicle firmware version.
- Whether the virtual key is paired.
- Home Assistant logs for `tesla_fleet_telemetry`.
- Proxy logs.
- Telemetry receiver logs.
- MQTT sample topics with VIN and location redacted.

Remove tokens, private keys, full VINs, exact location, email addresses, and private hostnames before sharing.
