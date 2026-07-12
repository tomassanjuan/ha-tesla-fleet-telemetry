# Usage

This guide covers the day-to-day behavior after setup.

## Entity Strategy

The integration can create many entities. Keep the useful ones enabled and leave the rest disabled to reduce clutter and telemetry traffic.

Good first entities:

| Area | Useful entities |
| --- | --- |
| Charging | Battery level, charging state, charge limit, plug state, time to full |
| Climate | Inside temperature, outside temperature, climate power, target temperature |
| Access | Lock state, doors, trunk, frunk, windows, charge port |
| Location | Vehicle tracker, heading, destination details if you use them |
| Tires | Four tire pressure sensors and warnings |
| Vehicle | Firmware version, update availability, vehicle name, odometer |

Enable entities from **Settings > Devices & services > Entities**. Search for the vehicle name or `tesla`.

## Entity Categories

Charging:

- Battery percentage and estimated range.
- Charge power, voltage, amps, and time remaining.
- Start and stop charging.
- Charge limit and charge amps where supported.
- Charge port state and charge port commands.

Climate:

- Interior and exterior temperature.
- Climate on/off.
- Cabin target temperature.
- Fan speed where supported.
- Seat heat and seat cooling where supported.
- Defrost and preconditioning state.

Access:

- Lock entity.
- Door, trunk, frunk, window, and charge port covers where supported.
- Sentry mode switch where supported.

Location:

- Device tracker.
- Heading.
- Destination name, arrival distance, and arrival time where available.

Media:

- Media player.
- Current artist, album, track, station, and volume where available.

Tires and vehicle health:

- Tire pressure per wheel.
- Tire pressure warnings.
- Firmware update state.
- Vehicle details and diagnostics.

## Commands

Common commands include:

- Wake vehicle.
- Lock and unlock.
- Start and stop climate.
- Set cabin temperature.
- Start and stop charging.
- Set charge limit.
- Open or close charge port.
- Open trunk or frunk when supported.
- Vent or close windows when supported.
- Flash lights.
- Honk horn.
- Media controls.

Command safety:

- Avoid automations that unlock the vehicle without strong conditions.
- Avoid repeated horn, light, window, or trunk commands.
- Add cooldowns to automations.
- Use clear conditions such as vehicle location, charging state, and household presence.
- Test commands while the vehicle is parked safely.

## Telemetry Modes

Use the quietest mode that still fits your dashboard or automation.

| Mode | Best for |
| --- | --- |
| Realtime | Active dashboards, charging sessions, short-term monitoring |
| Normal | Everyday Home Assistant use |
| Low | Parked vehicle, battery-friendly monitoring |

Location entities may require additional Tesla permissions and may show location access indicators in the vehicle.

## Sleep Behavior

Tesla vehicles sleep to save energy. That is normal.

- Some values can pause while the car sleeps.
- A command may wake the car first and retry.
- New telemetry settings may not apply until the car wakes.
- Repeated wake commands can waste energy.

## Automation Ideas

Useful examples:

- Notify if charge drops below a set level while unplugged.
- Notify if the vehicle is left unlocked at home overnight.
- Start climate before a regular departure time when the vehicle is at home.
- Alert if tire pressure warning appears.
- Announce when a software update is available.
- Stop charging at a desired time or price window.

Keep automations conservative. A good Tesla automation should check vehicle location, current state, and recent command history before acting.

## Maintenance

Weekly:

- Check Home Assistant warnings for the integration.
- Check telemetry receiver logs for repeated errors.
- Confirm MQTT is receiving messages.

Monthly:

- Update Tesla service containers during a quiet window.
- Check certificate expiry.
- Review enabled entities and disable unused ones.
- Review automations that send commands.

Before major changes:

- Back up Home Assistant config.
- Back up Tesla service config.
- Confirm you have the private key and certificate files stored safely.

## Backups

Back up:

- Home Assistant config entries.
- Private key for command signing.
- Public key hosting configuration.
- TLS certificate automation settings.
- Fleet Telemetry configuration.
- MQTT credentials and persistence if you depend on retained state.

Do not share backups publicly. They can contain tokens, location, hostnames, and secrets.
