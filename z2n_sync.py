#!/usr/bin/env python3
"""
Zabbix -> NetBox detail sync (rule driven).
Rule: only data that exists in Zabbix and is non-empty is written to NetBox.
Already-filled NetBox fields (serial, platform, device type) are never overwritten.
Devices must already exist (run zabbix2netbox.py first).
DRY_RUN=1 writes nothing.
RULES_FILE=/path/rules.json replaces the built-in RULES below.
"""
import os, re, sys, json, hashlib, requests

ZBX_URL = os.environ.get("ZBX_URL", "").strip()
ZBX_TOKEN = os.environ.get("ZBX_TOKEN", "").strip()
NB_URL = os.environ.get("NB_URL", "").rstrip("/")
NB_AUTH = os.environ.get("NB_AUTH", "").strip()
DRY = os.environ.get("DRY_RUN", "0") == "1"
CAMERAS = os.environ.get("CAMERAS", "1") != "0"
PREFIXLEN = os.environ.get("PREFIXLEN", "32")
if not (ZBX_URL and ZBX_TOKEN and NB_URL and NB_AUTH):
    sys.exit("ZBX_URL, ZBX_TOKEN, NB_URL, NB_AUTH must be set (see .env.example)")

# ---- rules: which Zabbix item feeds which NetBox field ----
# detect: item key prefix that selects the rule | model/serial/firmware: item key prefix (+ optional regex)
RULES = [
    {"name": "Hikvision", "detect": [{"key": "hik."}], "manufacturer": "Hikvision",
     "model": [{"key": "hik.model"}], "serial": [{"key": "hik.serial"}],
     "firmware": [{"key": "hik.firmware"}]},
    {"name": "MikroTik", "detect": [{"key": "mikrotik."}], "manufacturer": "MikroTik",
     "platform": "RouterOS",
     "model": [{"key": "mikrotik.sysdescr", "re": r"^(?:RouterOS\s+)?(.+)$"}],
     "firmware": [{"key": "mikrotik.version"}]},
    {"name": "Canon iR", "detect": [{"key": "canon."}], "manufacturer": "Canon",
     "model": [{"key": "canon.model"}], "serial": [{"key": "canon.serial"}]},
    {"name": "HP", "detect": [{"key": "hp."}], "manufacturer": "HP",
     "model": [{"key": "hp.model", "re": r"^(?:HP\s+)?(.+)$"}], "serial": [{"key": "hp.serial"}]},
    {"name": "Printer", "detect": [{"key": "printer."}], "manufacturer_from_model": True,
     "model": [{"key": "printer.model", "re": r"^(.+?)(?:,|$)"}],
     "serial": [{"key": "printer.serial"}],
     "firmware": [{"key": "printer.firmware.main"},
                  {"key": "printer.model", "re": r"Firmware Ver\.\s*([\d.]+)"}]},
    {"name": "Cisco SMB", "detect": [{"key": "system.descr[sysDescr",
                                      "value_re": r"(?i)CBS|SG\d|Catalyst|Cisco|C1[23]00"}],
     "manufacturer": "Cisco",
     "model": [{"key": "system.hw.serialnumber[", "from": "name", "re": r"^([^\s:]+)"}],
     "serial": [{"key": "system.hw.serialnumber["}]},
    {"name": "Windows", "detect": [{"key": "system.uname", "value_re": r"^Windows"}],
     "manufacturer": "Microsoft", "platform_from_os": r"^(Windows Server \d+ \w+)"},
]
if os.environ.get("RULES_FILE"):
    RULES = json.load(open(os.environ["RULES_FILE"]))

nb = requests.Session()
nb.headers.update({"Authorization": NB_AUTH, "Content-Type": "application/json",
                   "Accept": "application/json"})


def slug(s):
    return (re.sub(r"[^a-z0-9_-]+", "-", s.lower()).strip("-") or "x")[:50]


def zbx(method, params):
    r = requests.post(ZBX_URL, json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
                      headers={"Content-Type": "application/json-rpc",
                               "Authorization": "Bearer " + ZBX_TOKEN}, timeout=300)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        sys.exit("Zabbix error: " + json.dumps(d["error"]))
    return d["result"]


def nb_get(path, **p):
    p.setdefault("limit", 1000)
    r = nb.get(NB_URL + path, params=p, timeout=60)
    r.raise_for_status()
    return r.json().get("results", [])


def nb_write(method, path, payload):
    r = nb.request(method, NB_URL + path, data=json.dumps(payload), timeout=60)
    if r.status_code >= 300:
        raise RuntimeError("%s %s -> %s %s" % (method, path, r.status_code, r.text[:300]))
    return r.json()


def pick(its, specs):
    for sp in specs:
        for it in its:
            if not it["key_"].startswith(sp["key"]):
                continue
            src = (it["name"] if sp.get("from") == "name" else it["lastvalue"]) or ""
            src = src.strip()
            if not src:
                continue
            if sp.get("re"):
                m = re.search(sp["re"], src)
                if not m:
                    continue
                src = (m.group(1) if m.groups() else m.group(0)).strip()
            if src:
                return src
    return ""


def detects(rule, its):
    for d in rule["detect"]:
        for it in its:
            if it["key_"].startswith(d["key"]):
                if d.get("value_re") and not re.search(d["value_re"], it["lastvalue"] or ""):
                    continue
                return True
    return False


def norm(n):
    n = n.lower().strip()
    for a, b in (("tengigabitethernet", "te"), ("gigabitethernet", "gi"),
                 ("fastethernet", "fa"), ("port-channel", "po")):
        if n.startswith(a):
            n = b + n[len(a):]
    return n


def iftype(n, speed_bps=0):
    l = n.lower()
    if re.match(r"^(lo|loopback)", l) or l.startswith(("vlan", "pppoe", "l2tp", "ovpn", "wg")):
        return "virtual"
    if l.startswith(("bridge", "br")):
        return "bridge"
    if l.startswith(("po", "port-channel", "bond")):
        return "lag"
    if l.startswith(("te", "xe", "sfp-sfpplus", "sfpplus")):
        return "10gbase-x-sfpp"
    if l.startswith("sfp"):
        return "1000base-x-sfp"
    if re.match(r"^(gi|ge|ether|eth)", l):
        return "1000base-t"
    if speed_bps >= 10_000_000_000:
        return "10gbase-t"
    if speed_bps >= 1_000_000_000:
        return "1000base-t"
    if speed_bps >= 100_000_000:
        return "100base-tx"
    return "other"


def nbname(ifn, alias):
    """NetBox names max 64 chars. Longer: use alias, or truncated name + hash."""
    if len(ifn) <= 64:
        return ifn, alias[:200]
    short = alias if alias and len(alias) <= 64 else ifn[:55] + "~" + hashlib.md5(ifn.encode()).hexdigest()[:6]
    return short, (ifn + (" | " + alias if alias else ""))[:200]


IF1 = re.compile(r"^Interface (.+?)\s*\((.*)\): (.+)$")
IF2 = re.compile(r"^Interface (.+?): (.+)$")


def parse_ifs(its):
    ifs = {}
    for it in its:
        m = IF1.match(it["name"])
        if m:
            name, alias, what = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        else:
            m = IF2.match(it["name"])
            if not m:
                continue
            name, alias, what = m.group(1).strip(), "", m.group(2).strip()
        d = ifs.setdefault(name, {"alias": alias, "speed": 0, "has": False})
        if what == "Operational status":
            d["has"] = True
            d["alias"] = alias or d["alias"]
        elif what == "Speed":
            try:
                d["speed"] = int(float(it["lastvalue"]))
            except (ValueError, TypeError):
                pass
    return {k: v for k, v in ifs.items() if v["has"]}


def find_device(h):
    name = h.get("name") or h["host"]
    r = [d for d in nb_get("/api/dcim/devices/", name=name) if d["name"] == name]
    if r:
        return r[0]
    for i in h.get("interfaces", []):
        ip = i.get("ip")
        if not ip or ip == "0.0.0.0":
            continue
        for a in nb_get("/api/ipam/ip-addresses/", address=ip):
            if a["address"].split("/")[0] != ip:
                continue
            dev = (a.get("assigned_object") or {}).get("device")
            if dev:
                return nb_get("/api/dcim/devices/", id=dev["id"])[0]
    return None


def ensure_by_name(path, name, extra=None):
    f = [x for x in nb_get(path, name=name) if x.get("name") == name]
    if f:
        return f[0]["id"]
    if DRY:
        return None
    p = {"name": name, "slug": slug(name)}
    p.update(extra or {})
    return nb_write("POST", path, p)["id"]


def ensure_type(manufacturer, model):
    mf = ensure_by_name("/api/dcim/manufacturers/", manufacturer)
    if mf is None:
        return None
    f = [x for x in nb_get("/api/dcim/device-types/", model=model, manufacturer_id=mf)
         if x["model"] == model]
    if f:
        return f[0]["id"]
    if DRY:
        return None
    return nb_write("POST", "/api/dcim/device-types/",
                    {"manufacturer": mf, "model": model, "slug": slug(manufacturer + "-" + model)})["id"]


CH_RE = re.compile(r"^Channel (\d+) \(([\d.]+)\): (online|detect result|password status)$")


def parse_channels(its):
    chs = {}
    for it in its:
        m = CH_RE.match(it["name"])
        if not m or not it["key_"].startswith("hik.ch."):
            continue
        n, ip, what = int(m.group(1)), m.group(2), m.group(3)
        v = (it["lastvalue"] or "").strip()
        d = chs.setdefault(n, {"ip": ip})
        if what == "online" and v in ("0", "1"):
            d["online"] = (v == "1")
        elif what == "detect result" and v:
            d["detect"] = v
        elif what == "password status" and v:
            d["pwd"] = v
    return {n: d for n, d in chs.items() if "online" in d}


def sync_cameras(dev, nvr_name, chs):
    ctype = ensure_type("Generic", "IP Camera")
    role = ensure_by_name("/api/dcim/device-roles/", "Camera", {"color": "9e9e9e"})
    created = updated = skipped = 0
    for n, c in sorted(chs.items()):
        name = ("%s-CH%02d" % (nvr_name, n))[:64]
        status = "active" if c["online"] else "offline"
        line = "[Zabbix sync] NVR: %s | Channel: %d | Detect: %s | Password: %s" % (
            nvr_name, n, c.get("detect", "?"), c.get("pwd", "?"))
        try:
            cur = [d for d in nb_get("/api/dcim/devices/", name=name) if d["name"] == name]
            if cur:
                d0 = cur[0]
                old = d0.get("comments") or ""
                kept = [l for l in old.splitlines() if not l.startswith("[Zabbix sync]")]
                new = "\n".join(kept + [line]).strip()
                up = {}
                if d0["status"]["value"] != status:
                    up["status"] = status
                if new != old.strip():
                    up["comments"] = new
                if up:
                    updated += 1
                    if not DRY:
                        nb_write("PATCH", "/api/dcim/devices/%d/" % d0["id"], up)
                continue
            hits = [x for x in nb_get("/api/ipam/ip-addresses/", address=c["ip"])
                    if x["address"].split("/")[0] == c["ip"]]
            if any((x.get("assigned_object") or {}).get("device") for x in hits):
                skipped += 1
                continue
            created += 1
            if DRY:
                continue
            nd = nb_write("POST", "/api/dcim/devices/", {
                "name": name, "device_type": ctype, "role": role, "site": dev["site"]["id"],
                "status": status, "comments": line})
            ifc = nb_write("POST", "/api/dcim/interfaces/",
                           {"device": nd["id"], "name": "mgmt", "type": "other"})
            if hits:
                addr = nb_write("PATCH", "/api/ipam/ip-addresses/%d/" % hits[0]["id"], {
                    "assigned_object_type": "dcim.interface", "assigned_object_id": ifc["id"]})
            else:
                addr = nb_write("POST", "/api/ipam/ip-addresses/", {
                    "address": "%s/%s" % (c["ip"], PREFIXLEN), "status": "active",
                    "assigned_object_type": "dcim.interface", "assigned_object_id": ifc["id"]})
            nb_write("PATCH", "/api/dcim/devices/%d/" % nd["id"], {"primary_ip4": addr["id"]})
        except Exception as e:  # noqa: BLE001
            print("  FAILED camera:", name, "->", e)
    return created, updated, skipped


def sync_hdds(dev, its):
    caps, stat = {}, {}
    for it in its:
        m = re.match(r"^hik\.hdd\.(status|capacity)\[(\d+)\]$", it["key_"])
        if m:
            (stat if m.group(1) == "status" else caps)[int(m.group(2))] = (it["lastvalue"] or "").strip()
    if not (caps or stat):
        return 0
    exist = {i["name"]: i for i in nb_get("/api/dcim/inventory-items/", device_id=dev["id"])}
    changed = 0
    for k in sorted(set(caps) | set(stat)):
        name = "HDD %d" % k
        try:
            tb = "%.1f TB" % (int(caps[k]) / 1e12) if caps.get(k) else ""
        except ValueError:
            tb = ""
        desc = ", ".join(x for x in (tb, stat.get(k, "")) if x)
        cur = exist.get(name)
        if cur and cur.get("description") == desc:
            continue
        changed += 1
        if DRY:
            continue
        try:
            if cur:
                nb_write("PATCH", "/api/dcim/inventory-items/%d/" % cur["id"], {"description": desc})
            else:
                nb_write("POST", "/api/dcim/inventory-items/",
                         {"device": dev["id"], "name": name, "description": desc})
        except Exception as e:  # noqa: BLE001
            print("  FAILED HDD:", name, "->", e)
    return changed


def main():
    hosts = zbx("host.get", {"output": ["hostid", "host", "name"], "selectInterfaces": ["ip"],
                             "selectInventory": "extend"})
    items = zbx("item.get", {"output": ["hostid", "name", "key_", "lastvalue"]})
    by_host = {}
    for it in items:
        by_host.setdefault(it["hostid"], []).append(it)

    for h in hosts:
        name = h.get("name") or h["host"]
        dev = find_device(h)
        if not dev:
            print("not in NetBox, skip:", name)
            continue
        its = by_host.get(h["hostid"], [])
        inv = h.get("inventory") or {}
        if not isinstance(inv, dict):
            inv = {}
        rule = next((r for r in RULES if detects(r, its)), None)
        patch, notes = {}, []

        # --- model / manufacturer ---
        manufacturer = model = serial = fw = ""
        platform_name = ""
        if rule:
            model = pick(its, rule.get("model", []))
            serial = pick(its, rule.get("serial", []))
            fw = pick(its, rule.get("firmware", []))
            manufacturer = rule.get("manufacturer", "")
            if rule.get("manufacturer_from_model") and model:
                manufacturer = model.split()[0]
            if manufacturer and model.lower().startswith(manufacturer.lower() + " "):
                model = model[len(manufacturer) + 1:].strip()
            platform_name = rule.get("platform", "")
            if rule.get("platform_from_os"):
                m = re.search(rule["platform_from_os"], inv.get("os", ""))
                platform_name = m.group(1) if m else ""
        if not model and inv.get("model"):
            model = re.sub(r"^RouterOS\s+", "", inv["model"]).strip()
        if not serial and inv.get("serialno_a"):
            serial = inv["serialno_a"].strip()

        cur_type = dev["device_type"]["model"]
        if model:
            if cur_type == "Generic Device":
                tid = ensure_type(manufacturer or "Generic", model)
                if tid:
                    patch["device_type"] = tid
                notes.append("model: %s %s" % (manufacturer, model))
            elif cur_type.lower() != model.lower():
                notes.append("model already '%s', Zabbix says '%s' (unchanged)" % (cur_type, model))
        if serial:
            if not dev.get("serial"):
                patch["serial"] = serial[:50]
                notes.append("serial: " + serial)
            elif dev["serial"] != serial[:50]:
                notes.append("serial differs: NetBox=%s Zabbix=%s (unchanged)" % (dev["serial"], serial))
        if platform_name and not dev.get("platform"):
            pid = ensure_by_name("/api/dcim/platforms/", platform_name)
            if pid:
                patch["platform"] = pid
            notes.append("platform: " + platform_name)
        if inv.get("asset_tag") and not dev.get("asset_tag"):
            patch["asset_tag"] = inv["asset_tag"][:50]
            notes.append("asset tag: " + inv["asset_tag"])

        # --- comments: firmware / OS / location / contact ---
        parts = []
        if fw:
            parts.append("Firmware: " + fw[:100])
        if inv.get("os") and not (rule and rule.get("platform")):
            parts.append("OS: " + inv["os"][:120])
        if inv.get("location"):
            parts.append("Location: " + inv["location"][:80])
        if inv.get("contact"):
            parts.append("Contact: " + inv["contact"][:80])
        if parts:
            old = dev.get("comments") or ""
            kept = [l for l in old.splitlines()
                    if not l.startswith(("[Zabbix sync]", "Firmware/OS:"))]
            new = "\n".join(kept + ["[Zabbix sync] " + " | ".join(parts)]).strip()
            if new != old.strip():
                patch["comments"] = new
                notes.append("comments: " + " | ".join(parts)[:90])

        if patch and not DRY:
            try:
                nb_write("PATCH", "/api/dcim/devices/%d/" % dev["id"], patch)
            except Exception as e:  # noqa: BLE001
                print("  FAILED device update:", name, "->", e)

        # --- interface ---
        zif = parse_ifs(its)
        added = updated = 0
        if zif:
            exist = {norm(i["name"]): i for i in nb_get("/api/dcim/interfaces/", device_id=dev["id"])}
            for ifn, d in zif.items():
                nm, desc = nbname(ifn, d["alias"])
                cur = exist.get(norm(nm))
                kbps = d["speed"] // 1000 if d["speed"] >= 1_000_000 else None
                if cur:
                    up = {}
                    if desc and not cur.get("description"):
                        up["description"] = desc
                    if kbps and not cur.get("speed"):
                        up["speed"] = kbps
                    if up:
                        updated += 1
                        if not DRY:
                            try:
                                nb_write("PATCH", "/api/dcim/interfaces/%d/" % cur["id"], up)
                            except Exception as e:  # noqa: BLE001
                                print("  FAILED interface update:", nm, "->", e)
                    continue
                payload = {"device": dev["id"], "name": nm[:64],
                           "type": iftype(ifn, d["speed"]), "description": desc}
                if kbps:
                    payload["speed"] = kbps
                exist[norm(nm)] = {"id": 0}
                added += 1
                if not DRY:
                    try:
                        nb_write("POST", "/api/dcim/interfaces/", payload)
                    except Exception as e:  # noqa: BLE001
                        print("  FAILED interface:", nm, "->", e)
            notes.append("interfaces: new %d, updated %d (in Zabbix %d)" % (added, updated, len(zif)))

        chs = parse_channels(its) if CAMERAS else {}
        if chs:
            c1, c2, c3 = sync_cameras(dev, name, chs)
            notes.append("cameras: new %d, updated %d, skipped %d (in Zabbix %d)" % (c1, c2, c3, len(chs)))
        hd = sync_hdds(dev, its)
        if hd:
            notes.append("HDD: new/updated %d" % hd)

        print("%s%s [%s]" % (name, " (dry)" if DRY else "", rule["name"] if rule else "no rule matched"))
        for n in notes:
            print("   -", n)
        if not notes:
            print("   - nothing new")


if __name__ == "__main__":
    main()
