"""Fetch forecasts on the server; send only compact cached data to the speaker."""
import json
import pathlib
import threading
import time
import urllib.parse
import urllib.request

import yaml

LOCK = threading.Lock()

def fetch(config):
    query = urllib.parse.urlencode({
        "latitude": config["latitude"], "longitude": config["longitude"],
        "current": "temperature_2m,weather_code",
        "hourly": "temperature_2m,weather_code,precipitation_probability",
        "forecast_days": 3, "timeformat": "unixtime", "timezone": "UTC",
    })
    req = urllib.request.Request("https://api.open-meteo.com/v1/forecast?" + query,
                                 headers={"User-Agent": "mini2-local-weather/1.0"})
    with urllib.request.urlopen(req, timeout=5) as response:
        raw = json.loads(response.read(1024*128))
    now = int(time.time())
    current = raw["current"]
    hourly = raw["hourly"]
    hours = []
    for index, stamp in enumerate(hourly["time"]):
        if now-3600 <= stamp <= now+48*3600:
            hours.append({"t": stamp, "c": hourly["temperature_2m"][index],
                          "code": hourly["weather_code"][index],
                          "rain": hourly["precipitation_probability"][index]})
    if not hours:
        raise ValueError("empty forecast")
    rain = next((h["rain"] for h in hours if h["t"] <= current["time"] < h["t"]+3600), 0)
    return {"location": config["location"], "source":"Open-Meteo", "fetched": now,
            "current": {"t": current["time"], "c": current["temperature_2m"],
                        "code": current["weather_code"], "rain": rain},
            "hours": hours}

def refresh(send):
    if not LOCK.acquire(blocking=False):
        raise RuntimeError("weather refresh already running")
    try:
        with open("/config/weather.yaml") as f:
            config = yaml.safe_load(f)
        cache = fetch_met(config) if config.get("provider")=="metno" else fetch(config)
        send(["weather", "update", json.dumps(cache, separators=(",", ":"))])
        return {"ok": True, "location": cache["location"], "hours": len(cache["hours"])}
    finally:
        LOCK.release()

def loop(send):
    while True:
        try:
            refresh(send)
        except Exception:
            print("Weather refresh failed; speaker keeps its previous cache.", flush=True)
        time.sleep(1800)

MET_CACHE = {}
def fetch_met(config):
    import datetime
    import email.utils
    import gzip
    import urllib.error

    lat = "%.4f" % config["latitude"]
    lon = "%.4f" % config["longitude"]
    key = (lat, lon)
    cached = MET_CACHE.get(key)
    if not cached:
        try:
            saved=json.loads(pathlib.Path("/state/met-http-cache.json").read_text())
            if saved.get("coordinates")==list(key):
                cached=saved["cache"];MET_CACHE[key]=cached
        except (OSError,ValueError,KeyError):pass
    now = int(time.time())
    raw = cached["raw"] if cached else None
    if not cached or now >= cached["expires"]:
        headers = {
            "User-Agent": "mini2-local-weather/1.0 (https://github.com/equa-tory)",
            "Accept-Encoding": "gzip",
        }
        if cached and cached.get("modified"):
            headers["If-Modified-Since"] = cached["modified"]
        req = urllib.request.Request(
            "https://api.met.no/weatherapi/locationforecast/2.0/compact?lat="+lat+"&lon="+lon,
            headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            if error.code != 304 or not cached:
                raise
            response = error
        with response:
            if response.code != 304:
                body = response.read(256*1024)
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                raw = json.loads(body)
            expires = response.headers.get("Expires")
            until = int(email.utils.parsedate_to_datetime(expires).timestamp()) if expires else now+1800
            MET_CACHE[key] = {"raw": raw, "expires": max(now+60, until),
                              "modified": response.headers.get("Last-Modified", cached.get("modified") if cached else None)}
    try:
        target=pathlib.Path("/state/met-http-cache.json")
        temporary=target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"coordinates":list(key),"cache":MET_CACHE[key]}))
        temporary.replace(target)
    except OSError:pass
    hours = []
    for entry in raw["properties"]["timeseries"]:
        stamp = int(datetime.datetime.fromisoformat(entry["time"].replace("Z","+00:00")).timestamp())
        if now-3600 <= stamp <= now+48*3600:
            hours.append({"t":stamp,"c":entry["data"]["instant"]["details"]["air_temperature"],
                          "code":-1,"rain":-1})
    if not hours:
        raise ValueError("empty MET forecast")
    current = next((h for h in hours if h["t"] <= now < h["t"]+3600),hours[0])
    return {"location":config["location"],"source":"MET Norway (CC BY 4.0)",
            "fetched":now,"current":current,"hours":hours}
