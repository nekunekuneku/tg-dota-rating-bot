import asyncio
import copy

import httpx

from bot.db import DB
from bot.opendota import BASE, OpenDota
from bot.service import Service
from tests.conftest import BASE_MATCH


def run(coro):
    return asyncio.run(coro)


def make_service(tmp_path, handler):
    async def build():
        db = DB(str(tmp_path / "t.sqlite3"))
        await db.open()
        od = OpenDota(db)
        od.client = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(handler))
        return db, od, Service(db, od)

    return build()


def test_last_match_flow_and_cache(tmp_path):
    calls = []
    raw = copy.deepcopy(BASE_MATCH)
    raw["players"][0]["huge_log"] = ["x"] * 1000  # should be stripped before caching

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        if req.url.path.endswith("/recentMatches"):
            return httpx.Response(200, json=[{"match_id": 1, "start_time": 1}, {"match_id": 8000000001, "start_time": 2}])
        if req.url.path.endswith("/matches/8000000001"):
            return httpx.Response(200, json=raw)
        return httpx.Response(404)

    async def go():
        db, od, svc = await make_service(tmp_path, handler)
        await db.link(42, 1000, "Danil", "danil")
        await db.add_member(-100, 42)
        assert await svc.friends_map(-100) == {1000: "Danil"}

        mid = await svc.last_match_id(1000)
        assert mid == 8000000001
        m = await svc.rate(mid)
        assert m.player_by_account(1000).verdict == "Threw?"

        cached = await db.cache_get(f"match:{mid}", None)
        assert "huge_log" not in cached["players"][0]

        await svc.rate(mid)  # second call served from cache
        assert calls.count("/api/matches/8000000001") == 1

        assert (await db.get_user_by_username("@Danil")).tg_id == 42
        assert await db.unlink(42)
        assert await svc.friends_map(-100) == {}
        await od.close()
        await db.close()

    run(go())


def test_rate_limit_retry(tmp_path):
    n = {"hits": 0}

    def handler(req):
        n["hits"] += 1
        if n["hits"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=[{"id": 1, "localized_name": "Anti-Mage"}])

    async def go():
        db, od, _ = await make_service(tmp_path, handler)
        import bot.opendota as mod

        real_sleep = mod.asyncio.sleep
        mod.asyncio.sleep = lambda s: real_sleep(0)
        try:
            assert await od.hero_name(1) == "Anti-Mage"
        finally:
            mod.asyncio.sleep = real_sleep
        await od.close()
        await db.close()

    run(go())
