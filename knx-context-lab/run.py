"""Disposable lab supervisor; seeds only a new lab-owned configuration volume."""

import asyncio
import json
from pathlib import Path
import shutil
import signal
import sys
import aiohttp
from aiohttp import web
from xknx.cemi import CEMIFrame, CEMILData, CEMIMessageCode
from xknx.dpt import DPTArray, DPTBinary
from xknx.knxip import KNXIPFrame, RoutingIndication
from xknx.telegram import TelegramDirection, Telegram, GroupAddress, IndividualAddress
from xknx.telegram.apci import GroupValueWrite, GroupValueResponse, GroupValueRead
import socket

CONFIG = Path("/config")
GROUP = "239.255.77.1"
PORT = 3671
HA = "http://127.0.0.1:8123"
CLIENT = "http://localhost:18124/"


def store(key, data, version=1, minor_version=1):
    target = CONFIG / ".storage" / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "version": version,
                "minor_version": minor_version,
                "key": key,
                "data": data,
            }
        )
    )


def seed():
    CONFIG.mkdir(exist_ok=True)
    if (CONFIG / ".knx-lab").exists():
        return
    if any(CONFIG.iterdir()):
        raise RuntimeError(
            "Use an empty lab volume. Refusing to overwrite an existing configuration."
        )
    for name in ["configuration.yaml", "ui-lovelace.yaml"]:
        shutil.copyfile("/opt/lab/" + name, CONFIG / name)
    from homeassistant.config_entries import ConfigEntry
    from types import MappingProxyType

    entry = ConfigEntry(
        domain="knx",
        version=2,
        minor_version=2,
        title="Isolated KNX simulation",
        source="user",
        unique_id=None,
        data={
            "connection_type": "routing",
            "individual_address": "1.1.250",
            "local_ip": "127.0.0.1",
            "multicast_group": GROUP,
            "multicast_port": PORT,
        },
        options={
            "rate_limit": 0,
            "state_updater": False,
            "telegram_db_backend": "sqlite",
            "telegram_db_retention_days": 1,
            "telegram_db_load_hours": 1,
        },
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
    )
    store("core.config_entries", {"entries": [entry.as_dict()]})
    # Avoid unrelated onboarding integrations and analytics in this isolated lab.
    store(
        "onboarding", {"done": ["core_config", "integration", "analytics"]}, version=4
    )
    devices = {}
    for address, name in [
        ("1.1.23", "Living room wall switch"),
        ("1.1.10", "Lighting actuator"),
    ]:
        devices[address] = dict(
            name=name,
            manufacturer_name="Simulated",
            hardware_name="Lab device",
            order_number="",
            description="Synthetic ETS metadata for UI testing",
            individual_address=address,
            application=None,
            project_uid=None,
            communication_object_ids=[],
            channels={},
        )
    # This is explicitly synthetic parsed-project metadata, not an ETS import test.
    store(
        "knx/knx_project.json",
        {
            "info": dict(
                project_id="P-LAB",
                name="KNX Activity Lab",
                last_modified=None,
                group_address_style="ThreeLevel",
                guid="lab",
                created_by="lab",
                schema_version="23",
                tool_version="lab",
                xknxproject_version="3.10.0",
                language_code="en-US",
            ),
            "devices": devices,
            "communication_objects": {},
            "topology": {},
            "locations": {},
            "group_addresses": {},
            "group_ranges": {},
            "functions": {},
        },
    )
    (CONFIG / ".knx-lab").write_text("f28862c6133d588c99b08f495d097403d8d1a32e\n")


class Bus(asyncio.DatagramProtocol):
    def __init__(self):
        self.state = 0
        self.brightness = 180
        self.ready = False
        self.sent = 0
        self.received = 0
        self.outgoing = []

    def connection_made(self, transport):
        self.transport = transport

    def send(self, source, group, value, response=False):
        if source not in {"1.1.23", "1.1.24", "1.1.10"} or group not in {
            "1/1/1",
            "1/1/2",
            "1/1/3",
            "1/1/4",
            "2/0/1", "2/0/2", "2/0/4", "3/0/1",
        }:
            raise ValueError("Only lab addresses are supported")
        if group in {"1/1/1", "1/1/2", "2/0/1", "2/0/2", "3/0/1"}:
            if value not in (0, 1):
                raise ValueError("Switch value must be 0 or 1")
            if group in {"1/1/1", "1/1/2"}:
                self.state = value
            data = DPTBinary(value)
        else:
            if not isinstance(value, int) or not 0 <= value <= 255:
                raise ValueError("Brightness must be 0..255")
            if group in {"1/1/3", "1/1/4"}:
                self.brightness = value
            data = DPTArray((value,))
        telegram = Telegram(
            destination_address=GroupAddress(group),
            source_address=IndividualAddress(source),
            payload=(GroupValueResponse if response else GroupValueWrite)(data),
        )
        cemi = CEMIFrame(
            code=CEMIMessageCode.L_DATA_IND, data=CEMILData.init_from_telegram(telegram)
        )
        packet = KNXIPFrame.init_from_body(
            RoutingIndication(raw_cemi=cemi.to_knx())
        ).to_knx()
        self.transport.sendto(packet, (GROUP, PORT))
        self.sent += 1
        print(
            f"KNX/IP UDP: {source} -> {group} {type(telegram.payload).__name__} {value}",
            flush=True,
        )

    def datagram_received(self, data, addr):
        try:
            frame, _ = KNXIPFrame.from_knx(data)
            if not isinstance(frame.body, RoutingIndication):
                return
            telegram = CEMIFrame.from_knx(frame.body.raw_cemi).data.telegram(direction=TelegramDirection.INCOMING)
            if str(telegram.source_address) != "1.1.250":
                return
            self.received += 1
            group = str(telegram.destination_address)
            self.outgoing.append({"group": group, "type": type(telegram.payload).__name__, "value": str(getattr(telegram.payload, "value", None))})
            self.outgoing = self.outgoing[-100:]
            if isinstance(telegram.payload, GroupValueRead):
                if group == "1/1/2":
                    self.send("1.1.10", group, self.state, True)
                elif group == "1/1/4":
                    self.send("1.1.10", group, self.brightness, True)
            elif isinstance(telegram.payload, GroupValueWrite):
                if group == "1/1/1":
                    self.state = telegram.payload.value.value
                    # Let HA process its outgoing command before actuator feedback.
                    asyncio.get_running_loop().call_later(
                        0.1, self.send, "1.1.10", "1/1/2", self.state
                    )
                elif group == "1/1/3":
                    self.brightness = telegram.payload.value.value[0]
                    asyncio.get_running_loop().call_later(
                        0.1, self.send, "1.1.10", "1/1/4", self.brightness
                    )
        except Exception as err:
            print(f"Simulator receive error: {err}", flush=True)


async def bootstrap(bus):
    async with aiohttp.ClientSession() as session:
        for _ in range(180):
            try:
                async with session.get(HA + "/api/onboarding") as response:
                    if response.status in (200, 404):
                        break
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(1)
        else:
            raise RuntimeError("Home Assistant did not start within 180 seconds")
        token_file = CONFIG / "lab-auth.json"
        if not token_file.exists():
            async with session.post(
                HA + "/api/onboarding/users",
                json={
                    "name": "Lab Reviewer",
                    "username": "reviewer",
                    "password": "knx-lab-only",
                    "client_id": CLIENT,
                    "language": "en",
                },
            ) as response:
                response.raise_for_status()
                code = (await response.json())["auth_code"]
            async with session.post(
                HA + "/auth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": CLIENT,
                },
            ) as response:
                response.raise_for_status()
                tokens = await response.json()
            token_file.write_text(json.dumps(tokens))
            token_file.chmod(0o600)
        tokens = json.loads(token_file.read_text())
        async with session.post(
            HA + "/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": CLIENT,
            },
        ) as response:
            response.raise_for_status()
            auth = await response.json()
        headers = {"Authorization": "Bearer " + auth["access_token"]}
        for _ in range(120):
            async with session.get(
                HA + "/api/states/light.lab_light", headers=headers
            ) as response:
                if response.status == 200 and (await response.json())["state"] in (
                    "on",
                    "off",
                ):
                    bus.ready = True
                    print(
                        "LAB READY: http://localhost:18124 — reviewer / knx-lab-only",
                        flush=True,
                    )
                    return
            await asyncio.sleep(1)
        raise RuntimeError("KNX light did not become available")


async def main():
    seed()
    bus = Bus()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(
        socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1")
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_ADD_MEMBERSHIP,
        socket.inet_aton(GROUP) + socket.inet_aton("127.0.0.1"),
    )
    sock.bind((GROUP, PORT))
    sock.setblocking(False)
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: bus, sock=sock
    )

    async def telegram(request):
        try:
            data = await request.json()
            bus.send(**data)
        except (TypeError, ValueError) as err:
            return web.json_response({"error": str(err)}, status=400)
        return web.json_response({"sent": True})

    async def health(request):
        return web.json_response(
            {"ready": bus.ready, "sent": bus.sent, "received": bus.received},
            status=200 if bus.ready else 503,
        )

    app = web.Application()
    app.router.add_post("/telegram", telegram)
    app.router.add_get("/health", health)
    async def packets(request):
        return web.json_response(bus.outgoing)
    app.router.add_get("/packets", packets)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 8080).start()
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "homeassistant", "--config", "/config", "--skip-pip"
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    startup = asyncio.create_task(bootstrap(bus))
    exited = asyncio.create_task(process.wait())
    stopped = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            [startup, exited, stopped], return_when=asyncio.FIRST_COMPLETED
        )
        if startup in done:
            await startup
            await asyncio.wait([exited, stopped], return_when=asyncio.FIRST_COMPLETED)
        elif exited in done:
            raise RuntimeError(f"Home Assistant exited: {process.returncode}")
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(exited, 35)
            except TimeoutError:
                process.kill()
                await exited
        startup.cancel()
        stopped.cancel()
        transport.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
