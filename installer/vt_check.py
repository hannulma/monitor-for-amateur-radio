"""VirusTotal-tarkistus julkaisun Windows-tiedostoille (valinnainen, ei estä julkaisua).

Lähettää tiedostot VirusTotaliin, odottaa analyysin valmistumista ja kirjoittaa tuloksen GitHubin
ajon yhteenvetoon: montako virustorjuntaa hälytti ja millä nimellä (erityisesti Microsoft Defender).

Käyttö:  VT_API_KEY=... python installer/vt_check.py tiedosto1 [tiedosto2 ...]
"""
import hashlib
import json
import os
import sys
import time
import urllib.request
import uuid

API = "https://www.virustotal.com/api/v3"
KEY = os.environ.get("VT_API_KEY", "")


def req(method, url, data=None, headers=None):
    r = urllib.request.Request(url, data=data, method=method, headers={"x-apikey": KEY, **(headers or {})})
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read().decode())


def upload(path):
    size = os.path.getsize(path)
    url = f"{API}/files"
    if size > 32 * 1024 * 1024:                      # isot tiedostot erillisen latausosoitteen kautta
        url = req("GET", f"{API}/files/upload_url")["data"]
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{os.path.basename(path)}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode() + f.read() + f"\r\n--{boundary}--\r\n".encode()
    return req("POST", url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})["data"]["id"]


def main(files):
    out = ["### VirusTotal", ""]
    for path in files:
        sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
        name = os.path.basename(path)
        try:
            aid = upload(path)
            res = None
            for _ in range(40):                       # enintään ~10 min
                time.sleep(15)
                res = req("GET", f"{API}/analyses/{aid}")["data"]["attributes"]
                if res.get("status") == "completed":
                    break
            stats = (res or {}).get("stats", {})
            hits = {eng: r.get("result") for eng, r in (res or {}).get("results", {}).items()
                    if r.get("category") in ("malicious", "suspicious")}
            line = (f"- `{name}`: {stats.get('malicious', '?')} haitalliseksi, {stats.get('suspicious', '?')} epäilyttäväksi, "
                    f"{stats.get('undetected', '?')} puhdas – https://www.virustotal.com/gui/file/{sha}")
            out.append(line)
            for eng, what in sorted(hits.items()):
                out.append(f"  - {'**' if eng == 'Microsoft' else ''}{eng}: {what}{'**' if eng == 'Microsoft' else ''}")
            print(line, hits)
        except Exception as e:  # noqa: BLE001
            out.append(f"- `{name}`: tarkistus epäonnistui ({e}) – https://www.virustotal.com/gui/file/{sha}")
            print(name, "virhe:", e)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    if not KEY:
        sys.exit("VT_API_KEY puuttuu")
    main(sys.argv[1:])
