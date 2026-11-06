# zabbix-netbox-sync

One-way sync **Zabbix → NetBox**. Everything your Zabbix already knows about a device (name, IP, model,
serial, firmware, interfaces, Hikvision NVR cameras and HDDs) is written into the matching NetBox
objects. **Nothing is invented**: if Zabbix has no value, NetBox stays empty. Run it from cron and new
Zabbix hosts show up in NetBox on their own.

Two small Python scripts (only `requests` needed):

| Step | Script | What it does |
|---|---|---|
| 1 | `zabbix2netbox.py` | For every Zabbix host, creates a NetBox device + `mgmt` interface + primary IP. Role = first host group, site = `SITE_MAP` keyword in the host name. Existing devices/IPs are skipped. |
| 2 | `z2n_sync.py` | Enriches existing devices from Zabbix items: device type (manufacturer + model), serial, platform, asset tag, firmware/OS/location/contact in comments, interfaces (name, description, speed, type), and for Hikvision NVRs: one NetBox device per camera channel + HDD inventory items. |

**Safe by design:** only blank NetBox fields are filled. Existing model/serial/platform are never
overwritten (a difference is only reported). `DRY_RUN=1` writes nothing.

Tested with Zabbix 7.4 and NetBox 4.x (v1 and v2 API tokens).

## What maps where

| Zabbix | NetBox |
|---|---|
| host name, IP, status | device name, primary IP (on a `mgmt` interface), status |
| first host group | device role |
| keyword in host name (`SITE_MAP`) | site |
| items (model / serial / firmware) | device type + manufacturer, serial, comments |
| host inventory (os, location, contact, serial, asset tag) | platform, comments, serial, asset tag |
| `Interface X(alias): Operational status` / `Speed` items | interfaces (type guessed from name/speed) |
| Hikvision `hik.ch.online[N]`, `detect`, `pwd` | device `<NVR>-CHnn`, role `Camera`, status, comments |
| Hikvision `hik.hdd.status[N]`, `hik.hdd.capacity[N]` | inventory item `HDD N` (e.g. `4.0 TB, ok`) |

Vendors are recognised by **item key**, not template name (API users often cannot read templates).
Built in: Hikvision, MikroTik, Canon iR, HP, generic printers, Cisco SMB switches, Windows. Add your
own in `rules.example.json` (see below).

## Requirements

- Linux host that can reach both Zabbix and NetBox, Python 3.8+, `requests`
  (`sudo apt install python3-requests`)
- **Zabbix API token** (Users → API tokens). The user needs at least Read on all host groups.
- **NetBox API token** with *Write enabled*, user allowed to create devices, interfaces, IPs,
  device types, manufacturers, roles, platforms, inventory items.
- NetBox **sites should exist** (they are created if missing, but region/group cannot come from Zabbix).

## Quick start on a new server

```bash
git clone https://github.com/<you>/zabbix-netbox-sync.git
cd zabbix-netbox-sync
./install.sh            # first run: creates ~/.z2n.env from the template
nano ~/.z2n.env         # fill in URLs and tokens (all values in double quotes!)
./install.sh 30         # dry-run check + cron every 30 min, log in ~/z2n.log
```

Or by hand:

```bash
set -a; . ~/.z2n.env; set +a
DRY_RUN=1 python3 zabbix2netbox.py     # look at the output first
python3 zabbix2netbox.py               # create devices
DRY_RUN=1 python3 z2n_sync.py
python3 z2n_sync.py                    # fill details
```

**Back up NetBox before the first real run** (e.g. `pg_dump`).
Always run dry-run on its own line; pasting several commands at once can skip the `DRY_RUN`.

## Configuration (`~/.z2n.env`)

| Variable | Meaning | Default |
|---|---|---|
| `ZBX_URL`, `ZBX_TOKEN` | Zabbix API endpoint and token | required |
| `NB_URL`, `NB_AUTH` | NetBox URL; `Bearer nbt_<key>.<token>` (v2) or `Token <token>` (v1) | required |
| `NB_SITE` | fallback site | `Main Site` |
| `SITE_MAP` | `keyword=Site Name;keyword2=Site 2`, matched case-insensitively in the host name | empty |
| `SITE_FROM=host`, `SITE_SEP`, `SITE_PARTS` | derive site from leading name parts | off |
| `SKIP_IPS` | IPs not to import (e.g. the Zabbix server itself) | `127.0.0.1` |
| `PREFIXLEN` | mask for new IPs | `32` |
| `GROUP_FILTER` | only these Zabbix host groups | all |
| `CAMERAS=0` | disable NVR camera import | on |
| `RULES_FILE` | JSON vendor rules replacing the built-ins | built-in |
| `DRY_RUN=1` | write nothing | off |

**All values must be double-quoted** in the env file, otherwise spaces and `;` break the shell.

## Custom vendor rules

Copy `rules.example.json`, edit, and point `RULES_FILE` at it. A rule:

```json
{"name": "MyVendor",
 "detect": [{"key": "myvendor."}],
 "manufacturer": "MyVendor",
 "model":    [{"key": "myvendor.model", "re": "^(?:MyVendor\\s+)?(.+)$"}],
 "serial":   [{"key": "myvendor.serial"}],
 "firmware": [{"key": "myvendor.fw"}]}
```

`detect` picks the rule when a matching item key exists (optionally with `value_re`).
`from: "name"` reads from the item *name* instead of its value. `platform` sets a fixed NetBox platform.

## Running from cron

`z2n_run.sh` runs both steps under `flock` (no overlapping runs). Example crontab line:

```
*/30 * * * * /bin/bash /path/to/zabbix-netbox-sync/z2n_run.sh >> ~/z2n.log 2>&1
```

A new Zabbix host appears at the next run. Its model/serial/interfaces appear once Zabbix has polled
the items, usually by the next one or two runs.

## Limitations

- One-way, fill-blank only. Changing a value in Zabbix does not change an already-filled NetBox field.
- Hosts deleted in Zabbix are **not** deleted in NetBox.
- No cables (Zabbix has no link data; use LLDP/LibreNMS separately), no VLANs, MAC, interface IPs
  unless your templates provide items for them, no racks/locations.
- Vendors without a rule get name + IP only (plus Zabbix inventory fields, if filled).
- Cameras need Zabbix items named `Channel N (IP): online` (key `hik.ch.online[N]`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `... must be set` | env not loaded: `set -a; . ~/.z2n.env; set +a`; check the quotes |
| Zabbix `Session terminated, re-login` | wrong/expired API token |
| NetBox `403` `Source IP ::ffff:x.x.x.x is not permitted` | NetBox sees IPv4 clients as IPv4-mapped IPv6; leave the token's *Allowed IPs* empty or use `::ffff:x.x.x.x/128` |
| write fails with `permission` | token not *Write enabled* or user lacks permission |
| device skipped with `IP already assigned` | that IP is on another device in NetBox |
| model/serial missing | the Zabbix item is empty (host unreachable / not polled yet) or no rule for that vendor |
| duplicate interface errors | NetBox limits names to 64 chars; long names use the alias (or a hash), full name goes to description |

## Tests

```bash
python3 tests/test_mock.py     # offline, fake Zabbix/NetBox data
```

## License

MIT

---

## বাংলায় সংক্ষেপে

Zabbix-এ যে ডিভাইস ও তথ্য আছে (নাম, IP, মডেল, serial, firmware, interface, Hikvision NVR-এর ক্যামেরা ও HDD) তা
সরাসরি NetBox-এ তোলে। **Zabbix-এ যা নেই, তা বানানো হয় না।** cron-এ চালালে Zabbix-এ নতুন host যোগ হলে নিজে NetBox-এ আসে।

নতুন সার্ভারে সেটআপ:

```bash
git clone https://github.com/<you>/zabbix-netbox-sync.git && cd zabbix-netbox-sync
./install.sh          # ~/.z2n.env বানাবে
nano ~/.z2n.env       # URL ও টোকেন দিন (সব মান "..." এর ভেতরে)
./install.sh 30       # dry-run দেখাবে + প্রতি ৩০ মিনিটে cron বসাবে
```

প্রথমবার আসল চালানোর আগে NetBox-এর ব্যাকআপ নিন এবং `DRY_RUN=1` দিয়ে আলাদা লাইনে পরীক্ষা করুন।
স্ক্রিপ্ট শুধু ফাঁকা ঘর ভরে, আগে থেকে ভরা মডেল/serial বদলায় না। Zabbix থেকে host মুছলে NetBox-এর device মোছে না।
