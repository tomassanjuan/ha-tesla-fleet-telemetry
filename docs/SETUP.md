# Setup

This guide gets the Tesla services and the Home Assistant integration working together.

## How The Pieces Fit

```text
Tesla vehicle
  -> Fleet Telemetry receiver
  -> MQTT
  -> Home Assistant

Home Assistant
  -> Vehicle Command proxy
  -> Tesla
  -> Tesla vehicle
```

You run the proxy, telemetry receiver, and MQTT broker. Home Assistant connects to MQTT for vehicle data and to the proxy for signed commands.

## Requirements

- Home Assistant 2024.1 or newer.
- Tesla account with access to the vehicle.
- Tesla application credentials with the vehicle permissions you need.
- Public DNS name for the telemetry receiver.
- TLS certificate for the telemetry receiver.
- TLS certificate for the command proxy if it is public.
- MQTT broker reachable from Home Assistant and the telemetry receiver.
- Docker Compose or another service manager.

## 1. Create Key Material

Generate an application key pair:

```bash
openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem
openssl ec -in private-key.pem -pubout -out public-key.pem
```

Host the public key here, replacing `example.com` with your registered domain:

```text
https://example.com/.well-known/appspecific/com.tesla.3p.public-key.pem
```

Keep `private-key.pem` secret. It belongs on the command proxy host only.

## 2. Prepare The Service Directory

Example layout:

```text
tesla-fleet/
  compose.yml
  private-key.pem
  fleet-telemetry.json
  mosquitto.conf
  certs/
    proxy/
      fullchain.pem
      privkey.pem
    telemetry/
      fullchain.pem
      privkey.pem
  mosquitto-data/
```

Recommended permissions:

```bash
chmod 600 private-key.pem
chmod 600 certs/proxy/privkey.pem
chmod 600 certs/telemetry/privkey.pem
```

## 3. Run The Services

Minimal Docker Compose:

```yaml
services:
  vehicle-command:
    image: tesla/vehicle-command:latest
    restart: unless-stopped
    command:
      - "-key-file=/config/private-key.pem"
      - "-host=0.0.0.0"
      - "-port=4443"
      - "-tls-cert=/certs/fullchain.pem"
      - "-tls-key=/certs/privkey.pem"
    ports:
      - "4443:4443"
    volumes:
      - ./private-key.pem:/config/private-key.pem:ro
      - ./certs/proxy/fullchain.pem:/certs/fullchain.pem:ro
      - ./certs/proxy/privkey.pem:/certs/privkey.pem:ro
    read_only: true
    security_opt:
      - no-new-privileges:true

  fleet-telemetry:
    image: tesla/fleet-telemetry:latest
    restart: unless-stopped
    command: ["/fleet-telemetry", "-config=/etc/fleet-telemetry/config.json"]
    ports:
      - "19855:19855"
    volumes:
      - ./fleet-telemetry.json:/etc/fleet-telemetry/config.json:ro
      - ./certs/telemetry/fullchain.pem:/certs/fullchain.pem:ro
      - ./certs/telemetry/privkey.pem:/certs/privkey.pem:ro
    read_only: true
    security_opt:
      - no-new-privileges:true

  mqtt:
    image: eclipse-mosquitto:2
    restart: unless-stopped
    ports:
      - "1883:1883"
    volumes:
      - ./mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
      - ./mosquitto-data:/mosquitto/data

volumes:
  mosquitto-data:
```

Example `fleet-telemetry.json`:

```json
{
  "host": "0.0.0.0",
  "port": 19855,
  "log_level": "info",
  "json_log_enable": true,
  "reliable_ack": true,
  "tls": {
    "server_cert": "/certs/fullchain.pem",
    "server_key": "/certs/privkey.pem"
  },
  "mqtt": {
    "broker": "tcp://mqtt:1883",
    "client_id": "fleet_telemetry",
    "topic_base": "tesla",
    "qos": 1
  },
  "records": {
    "connectivity": ["logger", "mqtt"],
    "errors": ["logger", "mqtt"],
    "alerts": ["logger", "mqtt"],
    "V": ["mqtt"]
  }
}
```

Simple local `mosquitto.conf`:

```conf
listener 1883
protocol mqtt
persistence true
persistence_location /mosquitto/data/
log_dest stdout
allow_anonymous false
password_file /mosquitto/config/passwd
```

If you use MQTT authentication, mount the password file into the Mosquitto container and use matching credentials in Home Assistant and Fleet Telemetry.

Start the stack:

```bash
docker compose pull
docker compose up -d
docker compose ps
```

## 4. Pair The Virtual Key

Open this URL with the Tesla account that has access to the vehicle:

```text
https://tesla.com/_ak/example.com
```

Replace `example.com` with the domain hosting the public key. Follow the Tesla app prompt to add the key to the vehicle.

Pairing is required for many modern commands and telemetry configuration changes.

## 5. Install The Integration

Install through HACS as a custom repository, or copy this folder into Home Assistant:

```text
custom_components/tesla_fleet_telemetry
```

Restart Home Assistant.

## 6. Add It In Home Assistant

1. Open **Settings > Devices & services**.
2. Choose **Add integration**.
3. Search for **Tesla Fleet Telemetry**.
4. Sign in with Tesla.
5. Select the vehicle.
6. Enter connection settings.

Example values:

| Setting | Example |
| --- | --- |
| MQTT broker | `mqtt.local:1883` |
| Command proxy | `https://proxy.example.com:4443` |
| Telemetry server | `telemetry.example.com:19855` |

If Home Assistant cannot fetch the telemetry certificate automatically, paste the PEM certificate when asked.

## 7. Validate The Setup

Check containers:

```bash
docker compose ps
docker compose logs --tail=100 vehicle-command
docker compose logs --tail=100 fleet-telemetry
docker compose logs --tail=100 mqtt
```

Check telemetry TLS:

```bash
openssl s_client -connect telemetry.example.com:19855 -servername telemetry.example.com </dev/null
```

Check proxy TLS:

```bash
openssl s_client -connect proxy.example.com:4443 -servername proxy.example.com </dev/null
```

Check MQTT:

```bash
mosquitto_sub -h mqtt.local -t 'tesla/#' -v
```

In Home Assistant:

- Open the vehicle device.
- Enable a few important entities.
- Wait for new telemetry.
- Try a low-risk command such as flashing lights, only when safe.

## Certificate Renewal

Renew certificates before they expire. After renewal:

```bash
docker compose restart vehicle-command fleet-telemetry
```

Then reload the integration or update options if the telemetry certificate changed.

## Multiple Vehicles

Multiple vehicles can share the same proxy, telemetry receiver, and MQTT broker. Add each vehicle as its own Home Assistant config entry and enable only the entities you need.
