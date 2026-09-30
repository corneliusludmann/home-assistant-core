"""Exercise real KNX/IP packets, service context, live Activity and recorder history."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import aiohttp

HA = "http://127.0.0.1:8123"
ENTITY = "light.lab_light"
REPORT = Path("/config/smoke-report.json")


async def main():
    async with aiohttp.ClientSession() as session:
        tokens = json.loads(Path("/config/lab-auth.json").read_text())
        async with session.post(
            HA + "/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": "http://localhost:18124/",
            },
        ) as response:
            response.raise_for_status()
            token = (await response.json())["access_token"]
        session.headers["Authorization"] = "Bearer " + token

        async def state():
            async with session.get(HA + "/api/states/" + ENTITY) as response:
                response.raise_for_status()
                return await response.json()

        async def wait_state(expected):
            for _ in range(100):
                result = await state()
                if result["state"] == expected:
                    return result
                await asyncio.sleep(0.1)
            raise AssertionError(f"Expected {expected}, got {result}")

        async def send(source, group, value, response=False):
            async with session.post(
                "http://127.0.0.1:8080/telegram",
                json={
                    "source": source,
                    "group": group,
                    "value": value,
                    "response": response,
                },
            ) as reply:
                reply.raise_for_status()

        async def connect():
            ws = await session.ws_connect(HA + "/api/websocket")
            assert (await ws.receive_json())["type"] == "auth_required"
            await ws.send_json({"type": "auth", "access_token": token})
            assert (await ws.receive_json())["type"] == "auth_ok"
            return ws

        async with await connect() as ws:
            msg_id = 0

            async def command(payload):
                nonlocal msg_id
                msg_id += 1
                await ws.send_json({"id": msg_id, **payload})
                result = await asyncio.wait_for(ws.receive_json(), 15)
                assert result.get("success"), result
                return result["result"]

            if "--history-only" in sys.argv:
                report = json.loads(REPORT.read_text())
                entries = await command(
                    {
                        "type": "logbook/get_events",
                        "start_time": report["start"],
                        "entity_ids": [ENTITY],
                    }
                )
                names = {e.get("context_name") for e in entries}
                assert set(report["expected_names"]) <= names, entries
                print("PASS: recorded sender names survived a container restart")
                return
            await send("1.1.10", "1/1/2", 0, True)
            await wait_state("off")
            await asyncio.sleep(1.2)
            start = datetime.now(timezone.utc).isoformat()
            checks = []
            async with await connect() as live:
                await live.send_json(
                    {
                        "id": 1,
                        "type": "logbook/event_stream",
                        "start_time": start,
                        "entity_ids": [ENTITY],
                    }
                )
                assert (await live.receive_json())["success"]
                await send("1.1.23", "1/1/1", 1)
                switched = await wait_state("on")
                async with asyncio.timeout(20):
                    while True:
                        message = await live.receive_json()
                        entries = message.get("event", {}).get("events", [])
                        if any(
                            e.get("context_name")
                            == "Simulated Living room wall switch (1.1.23)"
                            for e in entries
                        ):
                            checks.append(
                                "Live Activity: named wall switch received over KNX/IP"
                            )
                            break
            await send("1.1.23", "1/1/4", 101)
            for _ in range(100):
                dimmed = await state()
                if dimmed["attributes"].get("brightness") == 101:
                    break
                await asyncio.sleep(0.1)
            assert dimmed["attributes"].get("brightness") == 101, dimmed
            assert dimmed["context"]["id"] != switched["context"]["id"], dimmed
            checks.append("Brightness update does not reuse the wall-switch context")
            await send("1.1.24", "1/1/1", 0)
            await wait_state("off")
            await send("1.1.10", "1/1/2", 1, True)
            await wait_state("on")
            async with session.post(
                HA + "/api/services/light/turn_off", json={"entity_id": ENTITY}
            ) as response:
                response.raise_for_status()
            service = await wait_state("off")
            assert service["context"]["user_id"], service
            checks.append("Home Assistant service keeps the authenticated user context")
            # Let the simulated actuator finish acknowledging the service first.
            await asyncio.sleep(0.2)
            await send("1.1.23", "1/1/1", 1)
            external = await wait_state("on")
            assert external["context"]["user_id"] is None, external
            checks.append("Incoming telegram replaces the prior user attribution")
            expected = [
                "Simulated Living room wall switch (1.1.23)",
                "1.1.24",
                "Simulated Lighting actuator (1.1.10)",
            ]
            for _ in range(30):
                entries = await command(
                    {
                        "type": "logbook/get_events",
                        "start_time": start,
                        "entity_ids": [ENTITY],
                    }
                )
                names = {e.get("context_name") for e in entries}
                if set(expected) <= names and any(
                    e.get("context_user_id") for e in entries
                ):
                    break
                await asyncio.sleep(1)
            assert set(expected) <= names, entries
            assert any(e.get("context_user_id") for e in entries), entries
            checks.extend(
                [
                    "Recorded Activity: named switch",
                    "Recorded Activity: unknown sender address",
                    "Recorded Activity: actuator response sender",
                    "Recorded Activity: user attribution",
                ]
            )
            report = {
                "start": start,
                "expected_names": expected,
                "checks": checks,
                "entries": entries,
            }
            REPORT.write_text(json.dumps(report, indent=2))
            for check in checks:
                print("PASS:", check)
            print("Report: /config/smoke-report.json")


if __name__ == "__main__":
    asyncio.run(main())
