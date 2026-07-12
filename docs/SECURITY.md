# Security And Privacy

This integration touches vehicle controls, account authorization, telemetry, and location. Treat the stack like sensitive home infrastructure.

## Trust Boundaries

| Area | Main risk | Practical control |
| --- | --- | --- |
| Tesla private key | Unauthorized signed commands | Store only on the proxy host, mount read-only, rotate if exposed |
| Tesla refresh token | Long-lived account access | Keep in Home Assistant storage, reauthorize if revoked |
| Command proxy | Remote command abuse | Use HTTPS, firewall rules, rate limits, and logs |
| Telemetry receiver | Vehicle data loss or spoofing | Stable public hostname, valid TLS, certificate rotation |
| MQTT broker | Vehicle and location disclosure | Private network, authentication, topic ACLs |
| Home Assistant logs | Secret or location leakage | Redact before sharing, limit debug logging |

## Protect Secrets

Keep these private:

- Tesla client secret.
- OAuth access and refresh tokens.
- Tesla application private key.
- TLS private keys.
- MQTT usernames and passwords.
- Home Assistant backups.
- Service configuration files containing credentials.

Do not commit secrets to git, paste them into public issues, or include them in screenshots.

## Private Key Rules

The private key should live only where the command proxy can read it.

Recommended:

- Mount it read-only.
- Use `chmod 600`.
- Do not copy it into Home Assistant.
- Do not email it or store it in chat history.
- Rotate it if the host, backup, or repository containing it is exposed.

If the key changes, host the new public key and pair the virtual key again if Tesla reports key errors.

## MQTT Security

MQTT may include vehicle state, charging details, and location.

Recommended:

- Keep MQTT on a private network.
- Disable anonymous access unless this is a temporary lab setup.
- Use usernames and strong passwords.
- Use ACLs when your broker supports them.
- Avoid exposing MQTT to the public internet.
- Avoid retained messages for exact location unless you truly need them.

Example Mosquitto ACL:

```conf
user fleet_telemetry
topic write tesla/#

user homeassistant
topic read tesla/#
```

## Location Privacy

Location telemetry can reveal home, work, trips, charging stops, and habits.

Recommended:

- Enable location entities only if you use them.
- Redact exact coordinates before sharing logs.
- Be careful with wall dashboards visible to guests.
- Review backups before sharing them.
- Limit who can access Home Assistant history.

## Command Safety

Remote commands affect the real vehicle.

Recommended:

- Add cooldowns to command automations.
- Require strong conditions for unlock, trunk, window, horn, and climate actions.
- Avoid public dashboards with command buttons.
- Use safe test commands first, such as flash lights.
- Test automations while the vehicle is parked safely.

## TLS And Certificates

The telemetry receiver must present a certificate that matches the hostname configured in Home Assistant.

Recommended:

- Use a stable public DNS hostname.
- Renew certificates before expiry.
- Restart services after renewal.
- Keep TLS private keys restricted.
- Validate with:

```bash
openssl s_client -connect telemetry.example.com:19855 -servername telemetry.example.com </dev/null
```

## Logs And Diagnostics

Diagnostics should redact sensitive values, but still review before sharing.

Never share:

- Authorization headers.
- Access tokens.
- Refresh tokens.
- Private keys.
- Full certificate private keys.
- Full VINs if you want privacy.
- Exact coordinates.

For shared logs, prefer the last 6 characters of the VIN and approximate locations.

## If Something Leaks

Refresh token leaked:

1. Revoke app access from the Tesla account.
2. Reauthorize in Home Assistant.
3. Review logs for unexpected activity.

Private key leaked:

1. Stop the command proxy.
2. Generate a new key pair.
3. Host the new public key.
4. Restart the proxy with the new private key.
5. Pair the virtual key again if needed.
6. Review command logs.

MQTT exposed:

1. Close the public listener or firewall path.
2. Rotate MQTT credentials.
3. Review broker logs.
4. Consider exact location data exposed.

TLS key leaked:

1. Reissue the certificate.
2. Replace the key on the service host.
3. Restart the affected service.
4. Update Home Assistant options if needed.
