import subprocess
import time
import os
import csv
import re
import shutil
import sys
import uuid
from urllib.parse import quote_plus

# --- CONFIGURATION ---
INPUT_FILE = "numbers.txt"
OUTPUT_FILE = "scan_results.csv"

PLATFORMS = ["instagram"]

# --- OXYLABS DATACENTER PROXY CONFIGURATION ---
DC_USERNAME = "dc_47ab6707"
DC_PASSWORD = "pVg=Sj2xe0hO"
DC_COUNTRY = "US"
DC_ENDPOINT = "dc.oxylabs.io:8000"

# URL-encode the password to safely handle special characters
_encoded_pass = quote_plus(DC_PASSWORD)

RATE_LIMIT_PATTERNS = [r"\b429\b", r"rate[\s_-]?limit", r"too\s+many\s+requests", r"throttl"]
PLATFORM_LINE_RE = re.compile(r"\[.*?\]\s*(instagram)\b.*?:\s*(.*)$", re.IGNORECASE)

def is_rate_limited(text):
    if not text: return False
    return any(re.search(p, text, re.IGNORECASE) for p in RATE_LIMIT_PATTERNS)

def parse_output(output):
    results = {p: {"status": "UNKNOWN", "method": "", "error": ""} for p in PLATFORMS}
    if not output: return results

    current = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line: continue

        m = PLATFORM_LINE_RE.search(line)
        if m:
            current = m.group(1).lower()
            tail = m.group(2).strip()
            full = line

            if is_rate_limited(full):
                results[current]["status"] = "RATE_LIMITED"
                results[current]["error"] = full
            elif "error" in tail.lower() or "[!]" in full:
                results[current]["status"] = "ERROR"
                results[current]["error"] = full
            elif "available" in tail.lower() or "not registered" in tail.lower():
                results[current]["status"] = "NOT_FOUND"
            elif "registered" in tail.lower():
                results[current]["status"] = "FOUND"
            continue

        if current:
            if is_rate_limited(line):
                results[current]["status"] = "RATE_LIMITED"
                results[current]["error"] = line

            kv = re.match(r"[├└─\s]*([\w_-]+)\s*:\s*(.+)$", line)
            if kv:
                key = kv.group(1).lower()
                val = kv.group(2).strip()
                if current == "instagram":
                    if key == "recovery_method": results[current]["method"] = val

    return results

def combine_results(per_platform):
    ig_status = per_platform.get("instagram", {}).get("status")
    ig_method = per_platform.get("instagram", {}).get("method", "")

    if ig_status == "FOUND": ig_status_text = "Registered"
    elif ig_status == "NOT_FOUND": ig_status_text = "Not Registered"
    elif ig_status == "RATE_LIMITED": ig_status_text = "Rate Limited"
    else: ig_status_text = "Unknown"

    if ig_status == "RATE_LIMITED":
        return (ig_status_text, ig_method, "instagram")

    if ig_status not in ("FOUND", "NOT_FOUND"):
        err_msg = per_platform.get("instagram", {}).get("error", "Unparseable output")
        return (ig_status_text, ig_method, err_msg)

    return (ig_status_text, ig_method, "")

def check_once(phone, proxy_url):
    cmd = ["phonsint", "-p", phone, "-m", ",".join(PLATFORMS)]
    
    env = os.environ.copy()
    env["HTTP_PROXY"] = proxy_url
    env["HTTPS_PROXY"] = proxy_url
    env["http_proxy"] = proxy_url
    env["https_proxy"] = proxy_url

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False, env=env)
    except subprocess.TimeoutExpired:
        return "Unknown", "", "TIMEOUT"
    except Exception as e:
        return "Unknown", "", "ERROR"

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    per_platform = parse_output(combined)
    return combine_results(per_platform)

def get_processed_numbers():
    done = set()
    if not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) == 0:
        return done
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            phone = (row.get("Phone") or "").strip()
            rl = (row.get("Rate_Limited_Platforms") or "").strip()
            if phone and "instagram" not in rl and "TIMEOUT" not in rl and "ERROR" not in rl:
                done.add(phone)
    return done

def main():
    if shutil.which("phonsint") is None:
        sys.exit("phonsint not found in PATH.")

    if not os.path.exists(INPUT_FILE):
        sys.exit(f"{INPUT_FILE} not found.")

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        numbers = [line.strip() for line in f if line.strip()]
    numbers = list(dict.fromkeys(numbers))

    already = get_processed_numbers()
    todo = [n for n in numbers if n not in already]

    if not todo:
        print("All numbers already finalised.")
        return

    print(f"Scanning {len(todo)} numbers | Datacenter Proxy | Per-Request IP | Zero Delay")
    print(f"Writing to {OUTPUT_FILE}\n")

    write_header = (not os.path.exists(OUTPUT_FILE)) or os.path.getsize(OUTPUT_FILE) == 0
    fields = ["Phone", "IG_Status", "IG_Method", "Rate_Limited_Platforms"]

    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()

        for i, num in enumerate(todo):
            # Create a unique session string just to force Oxylabs to rotate the IP 
            # (Datacenter proxies rotate per request, but this guarantees it)
            session_id = str(uuid.uuid4())[:8]
            
            # Format exactly as Oxylabs Datacenter requires:
            # http://user-USERNAME-country-COUNTRY:PASSWORD@dc.oxylabs.io:8000
            proxy_user = f"user-{DC_USERNAME}-country-{DC_COUNTRY}-sessid-{session_id}"
            proxy_url = f"http://{proxy_user}:{_encoded_pass}@{DC_ENDPOINT}"

            print(f"[{i+1}/{len(todo)}] {num} ...", end=" ", flush=True)

            ig_stat, ig_method, rl = check_once(num, proxy_url)

            msg = ""
            if rl == "instagram": msg = f"RATE LIMIT ({rl})"
            elif rl == "TIMEOUT": msg = "TIMEOUT"
            elif rl: msg = f"ERROR ({rl})"
            elif ig_stat == "Registered":
                if ig_method: msg = f"IG: {ig_method}"
                else: msg = "IG: Registered"
            else:
                msg = "Not Found"
            
            print(msg)

            writer.writerow({
                "Phone": num, 
                "IG_Status": ig_stat, 
                "IG_Method": ig_method, 
                "Rate_Limited_Platforms": rl,
            })
            f.flush()

    print("\nDone.")

if __name__ == "__main__":
    main()
