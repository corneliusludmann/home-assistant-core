"""Install pinned core and requirements of the integrations used in the lab."""

import json
from pathlib import Path
import subprocess
import sys

components = Path("/opt/homeassistant/homeassistant/components")
seen = set()
requirements = set()


def collect(domain):
    if domain in seen:
        return
    seen.add(domain)
    data = json.loads((components / domain / "manifest.json").read_text())
    requirements.update(data.get("requirements", []))
    for dependency in data.get("dependencies", []):
        collect(dependency)


for domain in [
    "frontend",
    "logbook",
    "history",
    "knx",
    "rest_command",
    "light",
    "person",
    "stream",
    "radio_frequency",
    "infrared",
    "hassio",
    "conversation",
    "tts",
    "assist_pipeline",
]:
    collect(domain)
subprocess.run(
    [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "-c",
        "/opt/lab/requirements.lock",
        "-e",
        ".",
        *sorted(requirements),
    ],
    check=True,
)
subprocess.run(
    [sys.executable, "-m", "script.translations", "develop", "--all"], check=True
)
