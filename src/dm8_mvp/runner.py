from __future__ import annotations

import base64
import json
import subprocess
import time
from dataclasses import dataclass


class QgaUnknown(RuntimeError):
    """Guest command state is unknown; callers must not retry non-idempotent work."""


@dataclass
class GuestResult:
    exitcode: int | None
    stdout: str
    stderr: str
    pid: int
    exited: bool


class QgaRunner:
    def __init__(self, domain: str, timeout: float = 120.0, agent_timeout: float = 30.0):
        self.domain = domain
        self.timeout = timeout
        self.agent_timeout = agent_timeout

    def _agent(self, payload: dict) -> dict:
        try:
            p = subprocess.run(
                ["virsh", "qemu-agent-command", self.domain, json.dumps(payload)],
                text=True, capture_output=True, check=False,
                timeout=self.agent_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise QgaUnknown("virsh qemu-agent-command timed out") from exc
        if p.returncode:
            raise QgaUnknown(p.stderr.strip() or "qemu-agent-command failed")
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError as exc:
            raise QgaUnknown("invalid qemu-agent response") from exc

    def run(self, script: str) -> GuestResult:
        encoded = base64.b64encode(script.encode()).decode()
        command = f"echo {encoded} | base64 -d | /bin/bash"
        start = self._agent({
            "execute": "guest-exec",
            "arguments": {"path": "/bin/bash", "arg": ["-lc", command], "capture-output": True},
        })
        try:
            pid = int(start["return"]["pid"])
        except (KeyError, TypeError, ValueError) as exc:
            raise QgaUnknown("guest-exec did not return a pid") from exc
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            status = self._agent({"execute": "guest-exec-status", "arguments": {"pid": pid}})
            result = status.get("return", {})
            if result.get("exited"):
                if "exitcode" not in result:
                    raise QgaUnknown(f"guest command pid {pid} exited without an exit code")
                dec = lambda key: base64.b64decode(result.get(key, "")).decode(errors="replace")
                return GuestResult(result.get("exitcode"), dec("out-data"), dec("err-data"), pid, True)
            time.sleep(0.5)
        raise QgaUnknown(f"guest command pid {pid} timed out; state unknown")

