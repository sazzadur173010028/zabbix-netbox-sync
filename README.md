# Zabbix থেকে NetBox অটো ইন্টিগ্রেশন

[English README](README.en.md)

Zabbix-এ থাকা প্রতিটা ডিভাইসের নাম, IP, মডেল, serial, firmware, interface এবং Hikvision NVR-এর ক্যামেরা ও HDD সরাসরি
NetBox-এ তোলে। **শুধু Zabbix-এ যা আছে ও ফাঁকা নয়, সেটুকুই যায়। বানানো কোনো তথ্য যোগ হয় না।**
cron-এ চালালে Zabbix-এ নতুন host যোগ হলে সেটা নিজে NetBox-এ চলে আসে।

শুধু Python `requests` লাগে। দুটো স্ক্রিপ্ট দুই ধাপে কাজ করে:

| ধাপ | স্ক্রিপ্ট | কাজ |
|---|---|---|
| ১ | `zabbix2netbox.py` | Zabbix-এর প্রতিটা host-এর জন্য NetBox-এ device, `mgmt` interface ও primary IP বানায়। Role আসে প্রথম host group থেকে, site আসে host-নামের শব্দ (`SITE_MAP`) থেকে। আগে থেকে থাকা device/IP বাদ যায়। |
| ২ | `z2n_sync.py` | Zabbix item থেকে মডেল (manufacturer সহ), serial, platform, asset tag, firmware/OS/location/contact (comments-এ) ও interface (নাম, description, speed, ধরন) বসায়। Hikvision NVR হলে প্রতিটা ক্যামেরা আলাদা device ও HDD inventory item হিসেবে আনে। |

**নিরাপদ নিয়ম:** শুধু ফাঁকা জায়গা ভরে। NetBox-এ আগে থেকে ভরা মডেল, serial, platform বদলায় না, আলাদা হলে আউটপুটে জানায়।
`DRY_RUN=1` দিলে কিছুই লেখে না। পরীক্ষিত: Zabbix 7.4, NetBox 4.x।

## ১. Zabbix-এর কোন তথ্য NetBox-এ কোথায় যায়

| Zabbix-এ যা আছে | NetBox-এ যেখানে যায় |
|---|---|
| host নাম, IP, status | device নাম, primary IP (`mgmt` interface-সহ), status |
| প্রথম host group | device role |
| host-নামের শব্দ (`SITE_MAP`) | site |
| item: মডেল, serial, firmware | device type (manufacturer সহ), serial, comments |
| inventory: OS, location, contact, serial, asset tag | platform, comments, serial, asset tag |
| `Interface X(alias): Operational status` ও `Speed` item | interface (নাম, description, speed, ধরন) |
| Hikvision `hik.ch.online[N]`, `detect`, `pwd` | device `<NVR>-CHnn`, role `Camera`, status, comments |
| Hikvision `hik.hdd.status[N]`, `hik.hdd.capacity[N]` | inventory item `HDD N` (যেমন `4.0 TB, ok`) |

ভেন্ডর চেনা হয় **item key** দিয়ে, template-এর নাম দিয়ে নয় (API ইউজারের template পড়ার পারমিশন না থাকলে নাম ফাঁকা আসে)।
বিল্ট-ইন: Hikvision, MikroTik, Canon iR, HP, অন্য প্রিন্টার, Cisco SMB switch, Windows। নিজের ভেন্ডর `rules.example.json`-এর মতো যোগ করা যায় (ধাপ ৭)।

## ২. আগে যা লাগবে

1. Linux হোস্ট, যেখান থেকে Zabbix ও NetBox দুটোতেই পৌঁছানো যায়। Python 3.8+ ও `requests`:
   `sudo apt install -y python3-requests`
2. **Zabbix API টোকেন:** Zabbix ওয়েবে *Users → API tokens → Create API token*। টোকেন শুধু একবার দেখায়, তখনই কপি করুন।
   ইউজারের সব host group-এ অন্তত Read পারমিশন থাকতে হবে।
3. **NetBox API টোকেন:** *Admin → API Tokens → Add*, **Write enabled** চালু রাখুন।
   v2 টোকেন হলে `Bearer nbt_<কী>.<টোকেন>`, v1 হলে `Token <টোকেন>`।
4. **NetBox-এ site আগে থেকে তৈরি** (region/group সহ)। না থাকলে স্ক্রিপ্ট নাম দিয়ে বানায়, কিন্তু region/group Zabbix থেকে জানা যায় না।

**Allowed IPs-এর ফাঁদ:** NetBox আপনার সার্ভারের IPv4 অনুরোধকেও IPv4-mapped IPv6 (`::ffff:172.16.1.5`) হিসেবে দেখে।
তাই টোকেনের Allowed IPs-এ সাধারণ IPv4 দিলে মেলে না। ঘরটা ফাঁকা রাখুন অথবা `::ffff:<সার্ভারের IP>/128` দিন।

**শুরুর আগে NetBox-এর ব্যাকআপ নিন** (netbox-docker হলে):

```bash
cd ~/netbox-docker
sudo docker compose exec -T postgres pg_dump -U netbox netbox | gzip > ~/netbox-before-sync-$(date +%F).sql.gz
```

## ৩. নতুন সার্ভারে সেটআপ (সবচেয়ে সহজ)

```bash
git clone https://github.com/sazzadur173010028/zabbix-netbox-sync.git
cd zabbix-netbox-sync
./install.sh          # প্রথমবার ~/.z2n.env বানাবে
nano ~/.z2n.env       # URL ও টোকেন দিন (সব মান "..." এর ভেতরে)
./install.sh 30       # dry-run দেখাবে + প্রতি ৩০ মিনিটে cron বসাবে
```

`install.sh` নিজে `requests` না থাকলে ইনস্টল করে, `~/.z2n.env`-এর পারমিশন `600` করে, আর cron-এ `z2n_run.sh` যোগ করে।

## ৪. সেটিং ফাইল (`~/.z2n.env`)

```bash
cp .env.example ~/.z2n.env
chmod 600 ~/.z2n.env
nano ~/.z2n.env
```

```
ZBX_URL="http://zabbix.example.com/zabbix/api_jsonrpc.php"
ZBX_TOKEN="আপনার_zabbix_টোকেন"
NB_URL="http://netbox.example.com:8000"
NB_AUTH="Bearer nbt_আপনার_পুরো_টোকেন"
NB_SITE="Main Site"
SKIP_IPS="127.0.0.1"
SITE_MAP="Branch1=Site One;HQ=Site Two"
```

**সব মান `"..."`-এর ভেতরে লিখতে হবে।** না হলে space ও `;` থাকা মান (যেমন `Bearer ...` বা `SITE_MAP`) shell ভেঙে ফেলে।
লোড ও যাচাই (মান দেখায় না, শুধু সেট হয়েছে কিনা):

```bash
set -a; . ~/.z2n.env; set +a
for v in ZBX_URL ZBX_TOKEN NB_URL NB_AUTH NB_SITE SITE_MAP; do [ -n "${!v}" ] && echo "$v ঠিক আছে" || echo "$v ফাঁকা"; done
```

নতুন টার্মিনাল খুললে প্রথমে আবার `set -a; . ~/.z2n.env; set +a` চালাতে হবে। এই ফাইল কখনো Git-এ তুলবেন না।

| ভ্যারিয়েবল | কাজ | ডিফল্ট |
|---|---|---|
| `ZBX_URL`, `ZBX_TOKEN` | Zabbix API ঠিকানা ও টোকেন | আবশ্যক |
| `NB_URL`, `NB_AUTH` | NetBox ঠিকানা ও টোকেন | আবশ্যক |
| `NB_SITE` | কিছু না মিললে যে site-এ যাবে | `Main Site` |
| `SITE_MAP` | `শব্দ=Site নাম` জোড়া, `;` দিয়ে আলাদা। host-নামে শব্দ (ছোট-বড় হাতের ভেদ নেই) থাকলে সেই site। প্রথম মিল জেতে | ফাঁকা |
| `SITE_FROM=host`, `SITE_SEP`, `SITE_PARTS` | নামের প্রথম অংশ থেকে site বের করা | বন্ধ |
| `SKIP_IPS` | বাদ দেওয়া IP, কমা দিয়ে (যেমন Zabbix server `127.0.0.1`) | `127.0.0.1` |
| `PREFIXLEN` | নতুন IP-র mask | `32` |
| `GROUP_FILTER` | শুধু এই Zabbix host group-গুলো | সব |
| `CAMERAS=0` | NVR ক্যামেরা আমদানি বন্ধ | চালু |
| `RULES_FILE` | নিজের ভেন্ডর নিয়মের JSON ফাইল | বিল্ট-ইন |
| `DRY_RUN=1` | কিছু লেখে না, শুধু দেখায় | বন্ধ |

## ৫. ধাপ ১: device ও IP তৈরি (`zabbix2netbox.py`)

**প্রথমে সবসময় dry-run, আলাদা লাইনে:**

```bash
set -a; . ~/.z2n.env; set +a
DRY_RUN=1 python3 zabbix2netbox.py
```

আউটপুট ঠিক দেখালে আসল চালান:

```bash
python3 zabbix2netbox.py
```

| আউটপুট | কী বোঝায় |
|---|---|
| `create: নাম IP role=...` | নতুন device তৈরি হবে |
| `skip (device exists)` | এই নামের device NetBox-এ আছে |
| `skip (IP already assigned)` | এই IP কোনো device-এ বসানো আছে (বসানো না থাকলে device বানিয়ে সেই IP-ই বসায়) |
| `skip (SKIP_IPS)` | `SKIP_IPS`-এ থাকা IP |
| `Done. created=N skipped=M failed=K` | শেষ হিসেব |

Device type এই ধাপে `Generic Device` থাকে, আসল মডেল ধাপ ২ বসায়।

## ৬. ধাপ ২: মডেল, serial, firmware, interface, ক্যামেরা (`z2n_sync.py`)

```bash
DRY_RUN=1 python3 z2n_sync.py     # আগে দেখুন
python3 z2n_sync.py               # তারপর আসল
```

- **Comments-এ** একটা লাইন যায় এবং প্রতিবার নতুন করে লেখা হয়: `[Zabbix sync] Firmware: ... | OS: ... | Location: ... | Contact: ...`
- **Interface:** `Interface <নাম>(<alias>): Operational status` item থেকে। alias হয় description, `Speed` item থাকলে speed। ধরন নাম ও speed দেখে ঠিক হয় (`gi*` → 1000base-t, `sfp-sfpplus*` → 10G SFP+, `vlan*` → virtual ইত্যাদি)।
- NetBox-এ interface নাম ৬৪ অক্ষরের বেশি হয় না। লম্বা নাম (Windows NIC) হলে alias বা ছোট নাম + hash ব্যবহার হয়, পুরো নাম description-এ যায়।
- NetBox-এ আগে থেকে থাকা interface মেলানোর সময় `GigabitEthernet1/0/1` আর `gi1/0/1` একই ধরা হয়। মিললে শুধু ফাঁকা description/speed ভরে।

### NVR-এর ক্যামেরা ও HDD (Hikvision, ISAPI)

NVR host-এ `Channel N (IP): online` নামের item (`hik.ch.online[N]`) থাকলে:

| Zabbix item | NetBox-এ |
|---|---|
| `hik.ch.online[N]` | device `<NVR নাম>-CHnn`, role `Camera`, primary IP, status (online = active, নইলে offline) |
| `detect result`, `password status` | device-এর comments (দুর্বল পাসওয়ার্ড চোখে পড়ে) |
| `hik.hdd.status[N]`, `hik.hdd.capacity[N]` | NVR device-এর inventory item `HDD N`, যেমন `4.0 TB, ok` |

- ক্যামেরার IP আগে থেকে অন্য device-এ বসানো থাকলে (যেমন দুই NVR-এ একই IP) সেটা বাদ যায়।
- আগে থেকে থাকা ক্যামেরার শুধু status ও `[Zabbix sync]` comment লাইন বদলায়।
- আউটপুট: `cameras: new X, updated Y, skipped Z (in Zabbix N)`।

## ৭. নিজের ভেন্ডর নিয়ম যোগ করা

`rules.example.json` কপি করে বদলান, তারপর `RULES_FILE="/path/rules.json"` দিন। একটা নিয়ম:

```json
{"name": "MyVendor",
 "detect": [{"key": "myvendor."}],
 "manufacturer": "MyVendor",
 "model":    [{"key": "myvendor.model", "re": "^(?:MyVendor\\s+)?(.+)$"}],
 "serial":   [{"key": "myvendor.serial"}],
 "firmware": [{"key": "myvendor.fw"}]}
```

- `detect`: কোন item key থাকলে নিয়মটা খাটবে (চাইলে `value_re` দিয়ে মান মিলিয়ে)।
- `model`/`serial`/`firmware`: item key-র শুরু, প্রয়োজনে `re` regex দিয়ে অংশ বের করা। `"from": "name"` দিলে item-এর মানের বদলে নাম পড়ে।
- `platform`: NetBox platform ঠিক করে দিতে।

## ৮. নতুন host অটো আসা (cron)

`install.sh` এটা বসিয়ে দেয়। হাতে করতে চাইলে:

```bash
chmod +x z2n_run.sh
( crontab -l 2>/dev/null | grep -v 'z2n_run.sh'; echo "*/30 * * * * /bin/bash $PWD/z2n_run.sh >> $HOME/z2n.log 2>&1" ) | crontab -
crontab -l
tail -30 ~/z2n.log
```

`z2n_run.sh` দুটো স্ক্রিপ্ট পরপর চালায় এবং `flock` দিয়ে দুটো রান একসাথে চলা আটকায়। শেষে `Done. created=0 skipped=N failed=0` দেখালে ঠিক আছে।

**পরীক্ষা:** Zabbix-এ ডামি host যোগ করুন (নামে `SITE_MAP`-এর শব্দ, একটা অব্যবহৃত IP, group `Network Devices`)। পরের রানে NetBox-এ `create: ...` আসবে। দেখা হলে NetBox ও Zabbix দুই জায়গা থেকেই ডামি মুছুন।

নতুন host-এর মডেল, serial, interface Zabbix-এর প্রথম poll-এর পর পরের এক-দুই রানে ভরে।

## ৯. সীমাবদ্ধতা

- একমুখী ও শুধু ফাঁকা-ভরা। Zabbix-এ মান বদলালে NetBox-এর আগে-ভরা ফিল্ড বদলায় না।
- Zabbix থেকে host মুছলে NetBox-এর device মোছে না, হাতে করতে হবে।
- Cable হয় না (Zabbix-এ LLDP নেই, LLDP/LibreNMS আলাদা ধাপে)। VLAN, MAC, interface-এর IP আসে না (template-এ item না থাকলে)। Rack ও location আসে না।
- নিয়ম নেই এমন ভেন্ডরের শুধু নাম ও IP আসে (Zabbix inventory ভরা থাকলে তার তথ্যও)।
- ক্যামেরার জন্য `Channel N (IP): online` ধরনের item লাগে।

## ১০. সমস্যা ও সমাধান

| লক্ষণ | কারণ ও সমাধান |
|---|---|
| `ZBX_URL, ZBX_TOKEN, NB_URL, NB_AUTH must be set` | ভ্যারিয়েবল লোড হয়নি (নতুন টার্মিনাল) বা `.z2n.env`-এ `"..."` নেই। `set -a; . ~/.z2n.env; set +a` চালান, তারপর যাচাইয়ের লুপ দিন |
| Zabbix: `Session terminated, re-login, please` | টোকেন ভুল, মেয়াদ শেষ, বা উদাহরণের নাম লিখে ফেলেছেন। আসল টোকেন বানান |
| NetBox `403 Forbidden` | কারণ দেখতে: `curl -s -H "Authorization: $NB_AUTH" "$NB_URL/api/dcim/sites/?limit=1"` |
| `Source IP ::ffff:... is not permitted to authenticate using this token` | টোকেনের Allowed IPs-এ IPv4 বাঁধা। ঘর ফাঁকা করুন বা `::ffff:<সার্ভারের IP>/128` দিন |
| `You do not have permission` বা write ব্যর্থ | টোকেনে Write enabled নেই, বা ইউজারের পারমিশন নেই |
| `Interface with this Device and Name already exists` | লম্বা নাম ৬৪ অক্ষরে কেটে এক হয়ে গিয়েছিল। নতুন সংস্করণে alias/hash ব্যবহার হয়। আগের কাটা-নামের interface হাতে মুছুন |
| device তৈরি হয়নি, `skip (IP already assigned)` | সেই IP NetBox-এ অন্য device-এ বসানো। CSV-তে বানানো কিন্তু বসানো নয় এমন IP হলে নতুন সংস্করণ device বানিয়ে সেটা বসায় |
| মডেল বা serial আসেনি | সেই host-এর Zabbix item ফাঁকা (পৌঁছানো যায়নি বা প্রথম poll হয়নি), অথবা সেই ভেন্ডরের নিয়ম নেই (নিয়ম যোগ করুন) |
| `templates: []` (API থেকে) | API ইউজারের template group-এ Read নেই। স্ক্রিপ্ট item key দিয়ে ভেন্ডর চেনে, তাই ক্ষতি নেই |
| দুই host-এর একই IP | Zabbix-এ ভুল IP বসানো। Zabbix-এ ঠিক করুন |
| আসল মোডে চলে গেছে | একসাথে কয়েকটা কমান্ড পেস্ট করলে `DRY_RUN` ছাড়ারটাও চলে। dry-run সবসময় আলাদা লাইনে চালান |
| Brother প্রিন্টারের মডেল `NC-9800h` | Zabbix-এ শুধু নেটওয়ার্ক কার্ডের নাম আছে। NetBox-এ হাতে বদলান, স্ক্রিপ্ট আর বদলাবে না |
| Zabbix server `127.0.0.1` device হয়ে যাচ্ছে | `SKIP_IPS="127.0.0.1"` সেট করুন |

## ১১. টেস্ট

```bash
python3 tests/test_mock.py     # নকল Zabbix/NetBox ডেটায়, নেটওয়ার্ক লাগে না
```

## লাইসেন্স

MIT
