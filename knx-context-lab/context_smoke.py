"""Real KNX/IP -> HA automation -> cover -> live/recorded Activity regression."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import aiohttp

HA='http://127.0.0.1:8123'
CLIENT='http://localhost:18124/'
REPORT=Path('/config/context-report.json')
COVER='cover.lab_cover'
TRIGGER='switch.lab_trigger'
AUTOMATION='automation.knx_switch_moves_cover'

async def main():
    async with aiohttp.ClientSession() as s:
        tokens=json.loads(Path('/config/lab-auth.json').read_text())
        async with s.post(HA+'/auth/token',data={'grant_type':'refresh_token','refresh_token':tokens['refresh_token'],'client_id':CLIENT}) as r:
            r.raise_for_status(); token=(await r.json())['access_token']
        s.headers['Authorization']='Bearer '+token
        async def connect():
            ws=await s.ws_connect(HA+'/api/websocket')
            await ws.receive_json();await ws.send_json({'type':'auth','access_token':token})
            assert (await ws.receive_json())['type']=='auth_ok'
            return ws
        async def send(group,value,source='1.1.23',response=False):
            async with s.post('http://127.0.0.1:8080/telegram',json=dict(source=source,group=group,value=value,response=response)) as r:r.raise_for_status()
        async def state(entity):
            async with s.get(HA+'/api/states/'+entity) as r:r.raise_for_status();return await r.json()
        async def wait(entity,expected):
            for _ in range(100):
                v=await state(entity)
                if v['state']==expected:return v
                await asyncio.sleep(.1)
            raise AssertionError(v)
        async def history(start):
            async with await connect() as ws:
                await ws.send_json({'id':1,'type':'logbook/get_events','start_time':start,'entity_ids':[TRIGGER,COVER]})
                r=await ws.receive_json();assert r.get('success'),r;return r['result']
        def verify_history(entries):
            cover=[e for e in entries if e.get('entity_id')==COVER]
            assert any(e.get('state')=='closing' and e.get('context_entity_id')==AUTOMATION for e in cover),cover
            assert any(e.get('state')=='closing' and e.get('context_name')=='1.1.24' for e in cover),cover
        if '--history-only' in sys.argv:
            report=json.loads(REPORT.read_text());verify_history(await history(report['start']))
            print('PASS: automation and KNX sender attribution survive restart');return
        await send('3/0/1',0);await wait(TRIGGER,'off')
        await send('2/0/2',1);await send('2/0/4',0,source='1.1.10',response=True);await wait(COVER,'open')
        await asyncio.sleep(1.2)
        start=datetime.now(timezone.utc).isoformat();events=[];live_entries=[]
        async with await connect() as ws, await connect() as live:
            await ws.send_json({'id':1,'type':'subscribe_events'});assert (await ws.receive_json())['success']
            await live.send_json({'id':1,'type':'logbook/event_stream','start_time':start,'entity_ids':[TRIGGER,COVER]});assert (await live.receive_json())['success']
            async def collect_events():
                async for message in ws:
                    if message.type==aiohttp.WSMsgType.TEXT:
                        data=json.loads(message.data)
                        if 'event' in data:events.append(data['event'])
            async def collect_live():
                async for message in live:
                    if message.type==aiohttp.WSMsgType.TEXT:
                        live_entries.extend(json.loads(message.data).get('event',{}).get('events',[]))
            readers=[asyncio.create_task(collect_events()),asyncio.create_task(collect_live())]
            try:
                await send('3/0/1',1)
                switched=await wait(TRIGGER,'on');moving=await wait(COVER,'closing')
                for _ in range(30):
                    auto=[e for e in events if e['event_type']=='automation_triggered' and e['data']['entity_id']==AUTOMATION]
                    if auto:break
                    await asyncio.sleep(.1)
                assert auto,events
                ctx=auto[-1]['context'];assert ctx['parent_id']==switched['context']['id']
                assert moving['context']['id']==ctx['id'],moving
                await asyncio.sleep(6)
                moving=await state(COVER)
                assert moving['state']=='closing' and moving['context']['id']==ctx['id'],moving
                stopped=await wait(COVER,'open')
                assert stopped['attributes']['current_position']==50,stopped
                assert stopped['context']['id']==ctx['id'],stopped
                await asyncio.sleep(.3)
                updates=[e['data']['new_state'] for e in events if e['event_type']=='state_changed' and e['data']['entity_id']==COVER]
                assert len(updates)>=3
                assert all(v['context']['id']==ctx['id'] for v in updates),updates
                credited=[e for e in events if e['event_type']=='knx_state_changed']
                assert len(credited)==1,credited
                async with s.get('http://127.0.0.1:8080/packets') as r:packets=await r.json()
                assert any(p['group']=='2/0/1' for p in packets),packets
                assert any(p['group']=='2/0/2' for p in packets),packets
                assert any(e.get('entity_id')==COVER and e.get('context_entity_id')==AUTOMATION for e in live_entries),live_entries
                print('PASS: KNX/IP switch -> real HA automation -> cover, including travel beyond five seconds and auto-stop')
                print('PASS: live Activity identifies the automation; only one KNX attribution event')
                await send('2/0/1',1,source='1.1.24');direct=await wait(COVER,'closing')
                assert direct['context']['id']!=ctx['id']
                await asyncio.sleep(1.2)
                assert (await state(COVER))['context']['id']==direct['context']['id']
                await send('2/0/2',1,source='1.1.24');await wait(COVER,'open');await asyncio.sleep(1)
                assert any(e.get('entity_id')==COVER and e.get('context_name')=='1.1.24' for e in live_entries),live_entries
                print('PASS: direct KNX cover command has sender attribution, preserved during travel')
                for attempt in range(20):
                    entries=await history(start)
                    try:verify_history(entries);break
                    except AssertionError:
                        if attempt==19:raise
                        await asyncio.sleep(1)
                REPORT.write_text(json.dumps({'start':start,'automation_context':ctx,'cover_updates':updates,'live_entries':live_entries,'recorded_entries':entries,'outgoing_packets':packets},indent=2))
                print('PASS: recorded Activity; report at /config/context-report.json')
            finally:
                for task in readers:task.cancel()
                await asyncio.gather(*readers,return_exceptions=True)

asyncio.run(main())
