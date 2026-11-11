import platform, re, subprocess

def ping_host(ip_address):
    command = ["ping", "-n", "1", "-w", "1200", ip_address] if platform.system().lower() == "windows" else ["ping", "-c", "1", "-W", "2", ip_address]
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=4, shell=False)
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        reachable = result.returncode == 0
        match = re.search(r"time[=<]\s*([\d.]+)\s*ms", output, re.I)
        latency = float(match.group(1)) if match else (0.1 if reachable else None)
        return {"reachable": reachable, "latency_ms": latency, "detail": "Ping successful" if reachable else "No ping response"}
    except subprocess.TimeoutExpired:
        return {"reachable": False, "latency_ms": None, "detail": "Ping command timed out"}
    except Exception as exc:
        return {"reachable": False, "latency_ms": None, "detail": f"Monitoring error: {type(exc).__name__}"}
