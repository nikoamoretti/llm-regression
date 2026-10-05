"""Docker egress allowlist for agent containers.

Agent containers join an ``--internal`` Docker network with no route out. The
only other member is a proxy container (``runner/egress_proxy.py`` running in
the toolchain image) that is also on the default bridge and tunnels
``CONNECT`` requests to allowlisted ``host:port`` targets only. Agents reach
it through ``HTTPS_PROXY``; anything else (direct sockets, other hosts) fails.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
EGRESS_CONFIG = ROOT / "configs" / "egress.yaml"
PROXY_SCRIPT = Path(__file__).resolve().with_name("egress_proxy.py")
NETWORK = "llmreg-agent-net"
PROXY_NAME = "llmreg-egress"
PROXY_PORT = 3128


@dataclass(frozen=True)
class Egress:
    network: str
    proxy_url: str
    allow: tuple[str, ...]
    spec_sha256: str


def allowlist(provider: str) -> tuple[str, ...]:
    data = yaml.safe_load(EGRESS_CONFIG.read_text(encoding="utf-8")) or {}
    entries = (data.get("providers") or {}).get(provider)
    if not entries:
        raise KeyError(f"no egress allowlist for provider {provider!r} in {EGRESS_CONFIG}")
    return tuple(sorted(str(item) for item in entries))


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120, check=check)


def _spec(image: str, allow: tuple[str, ...]) -> str:
    blob = json.dumps({"image": image, "allow": allow}, sort_keys=True).encode() + PROXY_SCRIPT.read_bytes()
    return hashlib.sha256(blob).hexdigest()


def proxy_name(provider: str) -> str:
    """One proxy per product, so series with different allowlists can run side by side."""
    return PROXY_NAME if provider == "claude_code_cli" else f"{PROXY_NAME}-{provider.replace('_', '-')}"


def _running_spec(name: str = PROXY_NAME) -> str | None:
    proc = _docker(
        "container", "inspect", "--format", '{{.State.Running}} {{index .Config.Labels "llmreg.spec"}}', name,
        check=False,
    )
    if proc.returncode != 0:
        return None
    running, _, spec = proc.stdout.strip().partition(" ")
    return spec if running == "true" else None


def ensure_egress(
    image: str, allow: tuple[str, ...], *, timeout_seconds: float = 20.0, name: str = PROXY_NAME
) -> Egress:
    """Create the internal network and (re)start the proxy if its spec changed."""
    spec = _spec(image, allow)
    if _docker("network", "inspect", NETWORK, check=False).returncode != 0:
        _docker("network", "create", "--internal", "--label", "llmreg=agent-egress", NETWORK)
    if _running_spec(name) != spec:
        _docker("rm", "-f", name, check=False)
        _docker(
            "run", "-d", "--name", name,
            "--label", f"llmreg.spec={spec}",
            "--network", "bridge",
            "--user", "65534:65534",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--memory", "256m",
            "--pids-limit", "128",
            "-v", f"{PROXY_SCRIPT}:/opt/egress_proxy.py:ro",
            image,
            "python3", "/opt/egress_proxy.py", "--listen", f"0.0.0.0:{PROXY_PORT}",
            *[arg for target in allow for arg in ("--allow", target)],
        )
        deadline = time.monotonic() + timeout_seconds
        while '"listening"' not in _docker("logs", name, check=False).stdout:
            if time.monotonic() > deadline:
                raise RuntimeError("egress proxy did not start: " + _docker("logs", name, check=False).stderr[-500:])
            time.sleep(0.2)
    # Idempotent: also re-attaches a running proxy to a recreated network.
    connect = _docker("network", "connect", NETWORK, name, check=False)
    if connect.returncode != 0 and "already exists" not in connect.stderr:
        raise RuntimeError(f"could not attach egress proxy to {NETWORK}: {connect.stderr.strip()}")
    return Egress(network=NETWORK, proxy_url=f"http://{name}:{PROXY_PORT}", allow=allow, spec_sha256=spec)


def egress_down() -> None:
    for provider in sorted(yaml.safe_load(EGRESS_CONFIG.read_text(encoding="utf-8")).get("providers") or {}):
        _docker("rm", "-f", proxy_name(provider), check=False)
    _docker("network", "rm", NETWORK, check=False)


def decisions(since_epoch: float | None = None, *, name: str = PROXY_NAME) -> list[dict[str, Any]]:
    """Proxy decisions (allow/deny/error) logged since ``since_epoch``."""
    proc = _docker("logs", name, check=False)
    records = []
    for line in proc.stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "decision" not in record:
            continue
        if since_epoch is not None and float(record.get("ts") or 0) < since_epoch:
            continue
        records.append(record)
    return records


def status() -> dict[str, Any]:
    return {
        "network": _docker("network", "inspect", NETWORK, check=False).returncode == 0,
        "proxy_running": _running_spec() is not None,
        "proxy_spec": _running_spec(),
    }
