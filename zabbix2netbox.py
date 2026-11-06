#!/usr/bin/env python3
"""
Zabbix -> NetBox sync, step 1: create devices (one way).

Reads every host from Zabbix and creates a device + `mgmt` interface + primary IP in NetBox.
- Existing NetBox devices / IPs are never modified (skipped).
- First Zabbix host group -> NetBox device role. Device type is "Generic Device"
  (z2n_sync.py sets the real model afterwards).
- Dry run: DRY_RUN=1 python3 zabbix2netbox.py

Environment variables (see .env.example):
  ZBX_URL       http://zabbix.example.com/zabbix/api_jsonrpc.php
  ZBX_TOKEN     Zabbix API token
  NB_URL        http://netbox.example.com:8000
  NB_AUTH       "Token xxxx" (v1 token) or "Bearer nbt_xxx.yyy" (v2 token)
  NB_SITE       default site (created if missing)          default: Main Site
  SITE_MAP      "Branch1=Site One;HQ=Site Two": if the host name contains the keyword
                (case-insensitive), the device goes to that site
  SITE_FROM     "host": derive the site from the host name (fallback after SITE_MAP)
  SITE_SEP      name separator                              default: -
  SITE_PARTS    leading name parts used as site             default: 1
                e.g. Plant-Office-SW1 with PARTS=1 -> Plant
  PREFIXLEN     mask length for new IPs                     default: 32
  SKIP_IPS      IPs to skip, comma separated                default: 127.0.0.1
  GROUP_FILTER  (optional) only these Zabbix host groups, comma separated
"""
import os
import re
import sys
import json
import requests

ZBX_URL = os.environ.get("ZBX_URL", "").strip()
ZBX_TOKEN = os.environ.get("ZBX_TOKEN", "").strip()
NB_URL = os.environ.get("NB_URL", "").rstrip("/")
NB_AUTH = os.environ.get("NB_AUTH", "").strip()
NB_SITE = os.environ.get("NB_SITE", "Main Site")
PREFIXLEN = os.environ.get("PREFIXLEN", "32")
GROUP_FILTER = [g.strip() for g in os.environ.get("GROUP_FILTER", "").split(",") if g.strip()]
SITE_FROM = os.environ.get("SITE_FROM", "")
SITE_SEP = os.environ.get("SITE_SEP", "-")
SITE_PARTS = int(os.environ.get("SITE_PARTS", "1"))
SKIP_IPS = [x.strip() for x in os.environ.get("SKIP_IPS", "127.0.0.1").split(",") if x.strip()]
# SITE_MAP="Branch1=Site One;HQ=Site Two"  (keyword in host name -> site)
SITE_MAP = {}
for _pair in os.environ.get("SITE_MAP", "").split(";"):
    if "=" in _pair:
        _k, _v = _pair.split("=", 1)
        SITE_MAP[_k.strip().lower()] = _v.strip()
DRY = os.environ.get("DRY_RUN", "0") == "1"

if not (ZBX_URL and ZBX_TOKEN and NB_URL and NB_AUTH):
    sys.exit("ZBX_URL, ZBX_TOKEN, NB_URL, NB_AUTH must be set (see .env.example)")

nb = requests.Session()
nb.headers.update({
    "Authorization": NB_AUTH,
    "Content-Type": "application/json",
    "Accept": "application/json",
})


def slugify(s):
    s = re.sub(r"[^a-z0-9_-]+", "-", s.lower()).strip("-")
    return s[:50] or "x"


def zbx(method, params):
    r = requests.post(
        ZBX_URL,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers={
            "Content-Type": "application/json-rpc",
            "Authorization": "Bearer " + ZBX_TOKEN,
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        sys.exit("Zabbix error: " + json.dumps(data["error"]))
    return data["result"]


def nb_get(path, **params):
    r = nb.get(NB_URL + path, params=params, timeout=60)
    r.raise_for_status()
    return r.json().get("results", [])


def nb_post(path, payload):
    r = nb.post(NB_URL + path, data=json.dumps(payload), timeout=60)
    if r.status_code >= 300:
        raise RuntimeError("%s %s -> %s %s" % ("POST", path, r.status_code, r.text[:300]))
    return r.json()


def nb_patch(path, payload):
    r = nb.patch(NB_URL + path, data=json.dumps(payload), timeout=60)
    if r.status_code >= 300:
        raise RuntimeError("%s %s -> %s %s" % ("PATCH", path, r.status_code, r.text[:300]))
    return r.json()


_cache = {}


def ensure(path, name, extra=None):
    """Find an object by name, create it if missing. Returns its id."""
    key = (path, name)
    if key in _cache:
        return _cache[key]
    found = nb_get(path, name=name)
    found = [x for x in found if x.get("name") == name]
    if found:
        _cache[key] = found[0]["id"]
        return _cache[key]
    payload = {"name": name, "slug": slugify(name)}
    if extra:
        payload.update(extra)
    if DRY:
        print("  [dry] create", path, name)
        _cache[key] = 0
        return 0
    obj = nb_post(path, payload)
    _cache[key] = obj["id"]
    return obj["id"]


def ensure_device_type():
    mf = ensure("/api/dcim/manufacturers/", "Generic")
    key = ("/api/dcim/device-types/", "Generic Device")
    if key in _cache:
        return _cache[key]
    found = nb_get("/api/dcim/device-types/", model="Generic Device")
    found = [x for x in found if x.get("model") == "Generic Device"]
    if found:
        _cache[key] = found[0]["id"]
        return _cache[key]
    if DRY:
        print("  [dry] create device-type Generic Device")
        _cache[key] = 0
        return 0
    obj = nb_post("/api/dcim/device-types/",
                  {"manufacturer": mf, "model": "Generic Device", "slug": "generic-device"})
    _cache[key] = obj["id"]
    return obj["id"]


def main():
    params = {
        "output": ["hostid", "host", "name", "status"],
        "selectInterfaces": ["ip", "type", "main", "dns", "useip"],
        "selectHostGroups": ["name"],
    }
    hosts = zbx("host.get", params)
    print("Zabbix hosts:", len(hosts))

    default_site = ensure("/api/dcim/sites/", NB_SITE, {"status": "active"})
    dtype_id = ensure_device_type()

    created = skipped = failed = 0
    for h in hosts:
        name = h.get("name") or h.get("host")
        groups = [g["name"] for g in h.get("hostgroups", [])]
        if GROUP_FILTER and not any(g in GROUP_FILTER for g in groups):
            continue

        ifaces = [i for i in h.get("interfaces", []) if i.get("ip") and i["ip"] != "0.0.0.0"]
        ifaces.sort(key=lambda i: 0 if i.get("main") == "1" else 1)
        ip = ifaces[0]["ip"] if ifaces else None

        if ip and ip in SKIP_IPS:
            print("skip (SKIP_IPS):", name, ip)
            skipped += 1
            continue

        # already in NetBox?
        if nb_get("/api/dcim/devices/", name=name):
            print("skip (device exists):", name)
            skipped += 1
            continue
        hits = []  # UNASSIGNED_IP_OK
        if ip:
            hits = [x for x in nb_get("/api/ipam/ip-addresses/", address=ip)
                    if x["address"].split("/")[0] == ip]
            if any((x.get("assigned_object") or {}).get("device") for x in hits):
                print("skip (IP already assigned):", name, ip)
                skipped += 1
                continue

        site_id = default_site
        for _kw, _site in SITE_MAP.items():
            if _kw in name.lower():
                site_id = ensure("/api/dcim/sites/", _site, {"status": "active"})
                break
        else:
            _kw = None
        if _kw is None and SITE_FROM == "host":
            parts = name.split(SITE_SEP)
            if len(parts) > SITE_PARTS:
                site_id = ensure("/api/dcim/sites/", SITE_SEP.join(parts[:SITE_PARTS]),
                                 {"status": "active"})

        role_name = groups[0] if groups else "Unclassified"
        try:
            role_id = ensure("/api/dcim/device-roles/", role_name, {"color": "9e9e9e"})
            status = "active" if h.get("status") == "0" else "offline"
            print("create:", name, ip or "(no ip)", "role=" + role_name)
            if DRY:
                created += 1
                continue
            dev = nb_post("/api/dcim/devices/", {
                "name": name,
                "device_type": dtype_id,
                "role": role_id,
                "site": site_id,
                "status": status,
                "comments": "Synced from Zabbix (hostid %s)" % h["hostid"],
            })
            if ip:
                ifc = nb_post("/api/dcim/interfaces/",
                              {"device": dev["id"], "name": "mgmt", "type": "other"})
                if hits:
                    addr = nb_patch("/api/ipam/ip-addresses/%d/" % hits[0]["id"], {
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": ifc["id"],
                    })
                else:
                    addr = nb_post("/api/ipam/ip-addresses/", {
                        "address": "%s/%s" % (ip, PREFIXLEN),
                        "status": "active",
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": ifc["id"],
                    })
                nb_patch("/api/dcim/devices/%d/" % dev["id"], {"primary_ip4": addr["id"]})
            created += 1
        except Exception as e:  # noqa: BLE001
            print("FAILED:", name, "->", e)
            failed += 1

    print("\nDone. created=%d skipped=%d failed=%d%s" %
          (created, skipped, failed, " (DRY RUN)" if DRY else ""))


if __name__ == "__main__":
    main()
