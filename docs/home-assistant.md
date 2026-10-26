# Home Assistant

A Wee Bawbee can tell Home Assistant when something needs a look, and Home Assistant can read a summary for a dashboard. Both are optional, and the app works the same without them.

## Notifications

The app POSTs JSON to a Home Assistant **webhook**:

- **After each sync** (06:00 and 18:00): anything new in the inbox goes out as one notification, such as a price change, a new regular payment, a missed payment, or a bill still paid after a cancel.
- **A bank link ending:** its own notification, with `person` set to the link's owner (`person_1` or `person_2`), so an automation can send Person 2's to their phone.
- **A what-if whose planned start has arrived:** a nudge to promote it. Nothing is ever promoted automatically.
- **Joint Billing projected below the cushion before the next payday:** £50 by default (`BAWBEE_HA_LOW_BALANCE_PENCE`).

Each is sent once. If Home Assistant is down, the attempt is recorded and the inbox still holds everything.

### Set it up

1. In Home Assistant, create an automation with a **Webhook** trigger. Give it a long, random webhook id, because the id is the only thing protecting it.
2. Put the webhook URL in the app's `.env` and restart:
   ```
   BAWBEE_HA_WEBHOOK_URL=http://homeassistant.lan:8123/api/webhook/<your-long-random-id>
   ```
3. In the app, open **Settings → Home Assistant → Send a test notification**.

The JSON the app sends:

```json
{
  "source": "bawbee",
  "kind": "inbox | consent | promotion_due | low_balance | test",
  "title": "Bawbee: 2 new in the inbox",
  "message": "Sky went from £104.75 to £108.50; new regular payment to Netflix, £10.99 monthly",
  "person": "person_2",
  "url": "https://bawbee.<your-domain>",
  "data": {}
}
```

An example automation:

```yaml
alias: Bawbee notifications
trigger:
  - platform: webhook
    webhook_id: <your-long-random-id>
    local_only: true
action:
  - choose:
      - conditions: "{{ trigger.json.person == 'person_2' }}"
        sequence:
          - service: notify.mobile_app_person_2s_phone
            data:
              title: "{{ trigger.json.title }}"
              message: "{{ trigger.json.message }}"
    default:
      - service: notify.mobile_app_person_1s_phone
        data:
          title: "{{ trigger.json.title }}"
          message: "{{ trigger.json.message }}"
mode: queued
```

## The summary sensor

`GET /api/ha/summary` returns a read-only summary:
- the inbox count by kind
- this payday's transfers done
- the month's surpluses
- Joint Billing's projected low point
- bank links and when the next consent ends

It needs a token made in **Settings → Home Assistant → Make a token**. The token is shown once and only its hash is kept. It can read the summary and nothing else. Revoke it from the same place.

```yaml
rest:
  - resource: https://bawbee.<your-domain>/api/ha/summary
    headers:
      Authorization: "Bearer bwb_..."
    scan_interval: 900
    sensor:
      - name: Bawbee inbox
        value_template: "{{ value_json.inbox.count }}"
      - name: Bawbee joint low point
        value_template: "{{ value_json.joint_low_point.pence / 100 }}"
        unit_of_measurement: GBP
      - name: Bawbee payday transfers done
        value_template: "{{ value_json.payday.done }}/{{ value_json.payday.transfers }}"
```

## Running the checks by hand

```sh
docker compose exec bawbee-app bawbee alerts
```

This runs recurring detection, missed payments, still paying and consents, then notifies Home Assistant. The worker runs the same after each sync and at 07:00.
