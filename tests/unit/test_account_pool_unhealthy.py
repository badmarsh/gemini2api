"""issue #11 回归守卫：账号 status=ACTIVE 但 client 会话已失效时的池行为。

线上现象：跑一阵后所有请求 529 `All accounts busy (max_concurrent=8), waited 60.0s`，
面板显示 cookie 没过期、换新 cookie 才恢复，且只有 1 个账号（并发槽位根本不可能打满）。

真因不是槽位泄漏，而是 `AccountPool` 的两个谓词不一致：
  - `_find_available()` 要求 `status == ACTIVE` **且** `client.is_healthy`
  - "是不是只是忙"的判断只看 `status == ACTIVE`
于是 `status=ACTIVE` 但 `client._healthy=False` 的账号：对 `_find_available` 不可见、却被算作
"有活跃账号" → 每个请求都进排队分支 → 空等满 acquire_timeout → 报一条槽位占用为 0 的假 529。
三条恢复路径（救 EXPIRED / client 自愈 / PSIDTS 轮换）在这个状态下全部够不着，故永久卡死。

本文件按修复项分组守卫：
  F1+F3 → test 1（精确消息、快速失败）、test 2（路由级状态码/文案 + 无槽位泄漏）
  F1 零回归 → test 4（真·健康且满载的池行为必须与修复前逐字一致）
  F2 → test 3（自愈可达性：reload_cookies 恰好被调用一次且请求随后成功）
  F4 → test 5（刷新失败不得把本来健康的 client 永久拉黑）
  F6 → test 6（客户端断连不得算作账号失败）

以下为对抗式复核（reviewer FIX_FIRST）返工后新增的回归守卫：
  H1 → 自愈被取消时 healing 标志必须清掉，即使清标志那一刻锁正被别人占着
  H2 → EXPIRED + ACTIVE-不健康 混合场景不得再对 EXPIRED 账号做持锁 check_account()
  M1 → acquire() 的总耗时不得被自愈的网络 I/O 顶穿 operator 配置的 acquire_timeout
  M2 → release_disconnected() 必须与 release(success=True) 语义可区分，且仍会唤醒排队者
  M3 → 池级自愈必须过 client 自己的 _heal_lock，不能绕开它
"""
import asyncio
import time

import pytest

from app.core.account_pool import Account, AccountPool, AccountStatus
from app.core.gemini_client import NO_HEALTHY_ACCOUNT_MSG, classify_error

_AUTH = {"Authorization": "Bearer sk-test-key"}


class _FakeClient:
    """最小 GeminiWebClient 替身：只实现池会用到的健康/重载/生成三件事。"""

    def __init__(self, healthy: bool = True, reload_succeeds: bool = False):
        self._healthy = healthy
        self._reload_succeeds = reload_succeeds
        self.reload_calls = 0
        self.generate_calls = 0
        self.check_account_calls = 0
        # 每次进入 reload_cookies 时回调（用于探测调用现场，比如池锁是否被持有）
        self.on_reload = None
        # 每次进入 check_account 时回调，同上用途
        self.on_check_account = None
        # 模拟 reload_cookies 的网络耗时（真实实现是 60s 级超时的网络 I/O）
        self.reload_delay = 0.0
        # 池级自愈（M3）现在会过 client 自己的单飞锁，真 GeminiWebClient 上是
        # asyncio.Lock()，这里的替身也得有，否则 `async with client._heal_lock` 会
        # AttributeError。
        self._heal_lock = asyncio.Lock()

    @property
    def is_healthy(self) -> bool:
        return self._healthy

    async def reload_cookies(self, psid=None, psidts=None) -> dict:
        self.reload_calls += 1
        if self.on_reload is not None:
            self.on_reload()
        if self.reload_delay:
            await asyncio.sleep(self.reload_delay)
        if self._reload_succeeds:
            self._healthy = True
            return {"success": True}
        return {"success": False, "error": "Cookie expired - redirected to Google login page"}

    async def check_account(self) -> dict:
        self.check_account_calls += 1
        if self.on_check_account is not None:
            self.on_check_account()
        return {"valid": self._healthy}

    async def generate(self, prompt, model, conversation_id="", attachments=None, gem_id=None,
                       extended_thinking=False) -> dict:
        self.generate_calls += 1
        return {"text": "ok", "images": [], "conversation_id": "c1"}

    async def generate_stream(self, prompt, model, conversation_id="", attachments=None, gem_id=None,
                              extended_thinking=False):
        self.generate_calls += 1
        yield {"type": "delta", "text": "hello"}
        yield {"type": "final", "text": "hello", "images": [], "conversation_id": "c1"}


def _make_pool(clients, *, max_concurrent: int = 8, acquire_timeout: float = 5.0) -> AccountPool:
    pool = AccountPool()
    pool._accounts = [
        Account(id=f"account-{i}", psid=f"psid-{i}", psidts="", label=f"account-{i}",
                status=AccountStatus.ACTIVE, client=c)
        for i, c in enumerate(clients)
    ]
    pool._max_concurrent = max_concurrent
    pool._acquire_timeout = acquire_timeout
    return pool


# ---------------------------------------------------------------------------
# 测试 1（F1+F3）：精确消息守卫
# ---------------------------------------------------------------------------

def test_unhealthy_active_account_fails_fast_with_accurate_message():
    """单账号 ACTIVE + 不健康：acquire 必须立刻失败（不是等满 5s 超时），
    文案不能是 "All accounts busy"（槽位占用是 0，那是假消息），映射也不能是 529。"""
    client = _FakeClient(healthy=False)

    async def _run():
        pool = _make_pool([client], acquire_timeout=5.0)
        t0 = time.monotonic()
        with pytest.raises(RuntimeError) as ei:
            await pool.acquire()
        return time.monotonic() - t0, str(ei.value), pool.accounts[0]

    elapsed, msg, account = asyncio.run(_run())

    # 快速失败：修复前这里会老老实实排队等满 acquire_timeout(5s) 才抛错
    assert elapsed < 1.0, f"acquire 等了 {elapsed:.2f}s，应当立即失败"
    assert "All accounts busy" not in msg
    assert msg == NO_HEALTHY_ACCOUNT_MSG
    # 槽位从头到尾就没被占用过 —— 证明"忙"是假的
    assert account.active_requests == 0

    status, err_type, retry_after = classify_error(RuntimeError(msg))
    assert status != 529 and err_type != "overloaded_error"
    assert (status, err_type, retry_after) == (503, "api_error", None)


# ---------------------------------------------------------------------------
# 测试 2（F1+F3）：路由级
# ---------------------------------------------------------------------------

def test_route_reports_session_expired_not_overloaded(gem_client, monkeypatch):
    """POST /v1/chat/completions 打到"ACTIVE 但会话失效"的池：
    必须回准确文案 + 503，而不是 529 overloaded；并且账号槽位仍是 0（不是泄漏）。"""
    import app.routers.openai as oai

    client = _FakeClient(healthy=False)
    pool = _make_pool([client], acquire_timeout=5.0)
    monkeypatch.setattr(oai, "gemini_client", pool)

    t0 = time.monotonic()
    r = gem_client.post("/v1/chat/completions", json={
        "model": "gemini-pro", "stream": False,
        "messages": [{"role": "user", "content": "hi"}],
    }, headers=_AUTH)
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0, f"请求耗时 {elapsed:.2f}s，不应空等 acquire_timeout"
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error"]["message"] == NO_HEALTHY_ACCOUNT_MSG
    assert body["error"]["type"] == "api_error"
    assert "All accounts busy" not in r.text
    # 529 会让客户端无限重试一个不会自愈的池，故绝不能带 Retry-After
    assert "retry-after" not in {k.lower() for k in r.headers}
    # 不是并发槽位泄漏：请求结束后占用仍为 0
    assert pool.accounts[0].active_requests == 0


# ---------------------------------------------------------------------------
# 测试 3（F2）：自愈可达性
# ---------------------------------------------------------------------------

def test_unhealthy_account_self_heals_via_reload_cookies_exactly_once():
    """ACTIVE 但会话失效 + reload_cookies 能成功 → 必须恰好自愈一次并让请求成功。
    修复前这里是 0 次：acquire 从不把不健康账号交出去，client 内的自愈是死代码。"""
    client = _FakeClient(healthy=False, reload_succeeds=True)
    lock_held_during_reload = []

    async def _run():
        pool = _make_pool([client], acquire_timeout=5.0)
        # 锁安全探针：reload_cookies 是 60s 级网络 I/O，持池锁调用会卡死所有账号的所有请求
        client.on_reload = lambda: lock_held_during_reload.append(pool._cond.locked())
        result = await pool.generate("hi", "gemini-pro")
        return result, pool.accounts[0]

    result, account = asyncio.run(_run())

    assert client.reload_calls == 1
    assert client.generate_calls == 1
    assert result["text"] == "ok"
    assert account.status == AccountStatus.ACTIVE
    assert account.active_requests == 0
    assert lock_held_during_reload == [False], "reload_cookies 绝不能在持有 self._cond 时调用"


def test_concurrent_requests_trigger_at_most_one_heal():
    """单飞：N 个并发请求撞上同一个失效账号，最多只触发一次 reload_cookies（不能是 N 次
    网络风暴）；没抢到自愈的那些请求立刻拿到准确报错，而不是陪等一次网络 I/O。"""
    client = _FakeClient(healthy=False, reload_succeeds=True)
    client.reload_delay = 0.2

    async def _run():
        pool = _make_pool([client], acquire_timeout=5.0)
        t0 = time.monotonic()
        results = await asyncio.gather(
            *[pool.generate("hi", "gemini-pro") for _ in range(5)],
            return_exceptions=True,
        )
        return results, time.monotonic() - t0, pool.accounts[0]

    results, elapsed, account = asyncio.run(_run())

    assert client.reload_calls == 1, f"自愈被触发了 {client.reload_calls} 次，应当单飞"
    oks = [r for r in results if isinstance(r, dict)]
    errs = [r for r in results if isinstance(r, Exception)]
    assert len(oks) == 1 and len(errs) == 4
    # 没抢到自愈的请求给的是准确文案，不是假的"忙"
    assert all(str(e) == NO_HEALTHY_ACCOUNT_MSG for e in errs)
    assert elapsed < 2.0, f"并发自愈耗时 {elapsed:.2f}s，不应串行叠加"
    assert account.active_requests == 0


def test_failed_heal_does_not_retry_on_every_request():
    """自愈失败要进冷却：后续请求不能每一个都再打一次 Google（既慢又抬高风控面），
    且必须立刻报准确错误，不能自旋。"""
    client = _FakeClient(healthy=False, reload_succeeds=False)

    async def _run():
        pool = _make_pool([client], acquire_timeout=5.0)
        msgs = []
        t0 = time.monotonic()
        for _ in range(3):
            with pytest.raises(RuntimeError) as ei:
                await pool.acquire()
            msgs.append(str(ei.value))
        return msgs, time.monotonic() - t0, pool.accounts[0]

    msgs, elapsed, account = asyncio.run(_run())

    assert client.reload_calls == 1, "自愈失败后应进入冷却，不能每个请求都重试一次"
    assert msgs == [NO_HEALTHY_ACCOUNT_MSG] * 3
    assert elapsed < 1.0
    assert account.healing is False
    assert account.heal_cooldown_until > 0


# ---------------------------------------------------------------------------
# 测试 4（F1 零回归）：真·健康且满载的池，行为必须与修复前逐字一致
# ---------------------------------------------------------------------------

def test_saturated_healthy_pool_still_queues_and_is_woken_by_release():
    """全部 ACTIVE 且健康、槽位打满 → 必须排队等待，并在 release() 时被唤醒拿到槽位。"""
    async def _run():
        pool = _make_pool([_FakeClient(healthy=True)], max_concurrent=1, acquire_timeout=5.0)
        first = await pool.acquire()
        assert first.active_requests == 1

        waiter = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0.1)
        assert not waiter.done(), "健康但满载的池必须排队，不能立即失败"

        await pool.release(first, success=True)
        second = await asyncio.wait_for(waiter, timeout=2.0)
        return second

    account = asyncio.run(_run())
    assert account.active_requests == 1
    assert account.status == AccountStatus.ACTIVE


def test_saturated_healthy_pool_timeout_keeps_original_busy_error_and_529():
    """健康但满载且无人 release → 超时后仍是原来的 All accounts busy 文案，仍映射 529。"""
    async def _run():
        pool = _make_pool([_FakeClient(healthy=True)], max_concurrent=2, acquire_timeout=0.3)
        await pool.acquire()
        await pool.acquire()
        t0 = time.monotonic()
        with pytest.raises(RuntimeError) as ei:
            await pool.acquire()
        return time.monotonic() - t0, str(ei.value), pool.accounts[0]

    elapsed, msg, account = asyncio.run(_run())

    # 真忙就该真等：等满 acquire_timeout 才报错（不能被 F1 改成秒失败）
    # Windows timers tick at ~15.6 ms, so a 0.3s asyncio timeout can fire a few ms early
    # as seen by time.monotonic(); allow that slack instead of flaking (~1 in 5 runs).
    assert elapsed >= 0.3 - 0.03, f"只等了 {elapsed:.2f}s，健康满载的池必须排队等满超时"
    assert msg == "All accounts busy (max_concurrent=2), waited 0.3s"
    assert account.active_requests == 2
    assert classify_error(RuntimeError(msg)) == (529, "overloaded_error", 30)


def _prepare_client_for_reload(monkeypatch, *, token_after_reload: str):
    """造一个"当前健康、会话可用"的真 GeminiWebClient，并把 reload_cookies 里的网络动作打桩。

    token_after_reload 为空串表示这次重载拿不到 SNlM0e token（重载失败）。
    """
    import app.core.gemini_client as gc

    class _Jar:
        def __init__(self):
            self.sets = []

        def set(self, name, value, **kwargs):
            self.sets.append((name, value))

    class _Session:
        def __init__(self, *args, **kwargs):
            pass

        async def close(self):
            return None

    monkeypatch.setattr(gc, "AsyncSession", _Session)
    client = gc.GeminiWebClient(psid="good-psid", psidts="good-psidts")
    client._cookie_jar = _Jar()
    client._http = _Session()
    client._session_token = "GOOD-TOKEN"
    client._healthy = True

    async def _obtain():
        client._session_token = token_after_reload
        if not token_after_reload:
            client._last_reload_error = "Cookie expired - redirected to Google login page"

    async def _rotate():
        return False

    async def _heartbeat():
        return None

    monkeypatch.setattr(client, "_obtain_session_token", _obtain)
    monkeypatch.setattr(client, "_rotate_cookies", _rotate)
    monkeypatch.setattr(client, "_send_heartbeat", _heartbeat)
    monkeypatch.setattr(client, "_ensure_refresh_task", lambda: None)
    return client


# ---------------------------------------------------------------------------
# 测试 5（F4）：刷新失败不得把本来健康的 client 永久拉黑
# ---------------------------------------------------------------------------

def test_failed_reload_does_not_down_a_healthy_client(monkeypatch):
    """reload_cookies 在任何网络动作前就 _healthy = False，失败返回路径却不恢复：
    一次失败的刷新（面板误填 cookie / Google 临时抽风）就永久少掉一个健康账号。
    修复后失败必须"原样不动"：健康标志、会话 token、cookie 全部回滚。"""
    client = _prepare_client_for_reload(monkeypatch, token_after_reload="")

    result = asyncio.run(client.reload_cookies(psid="bad-psid", psidts="bad-psidts"))

    assert result["success"] is False
    assert client.is_healthy is True, "刷新失败不得把本来健康的 client 拉黑"
    # 只回滚 _healthy 是不够的：token/cookie 也得回滚，否则是个"健康但发不出请求"的假账号
    assert client._session_token == "GOOD-TOKEN"
    assert client._psid == "good-psid"
    assert client._psidts == "good-psidts"
    assert ("__Secure-1PSID", "good-psid") in client._cookie_jar.sets


def test_failed_reload_keeps_an_already_unhealthy_client_unhealthy(monkeypatch):
    """回滚只针对"本来就健康"的 client：本来不健康的（F2 自愈路径）失败后仍是不健康，
    不能被回滚成假健康。"""
    client = _prepare_client_for_reload(monkeypatch, token_after_reload="")
    client._healthy = False
    client._session_token = ""

    result = asyncio.run(client.reload_cookies())

    assert result["success"] is False
    assert client.is_healthy is False


def test_successful_reload_still_applies_new_cookies(monkeypatch):
    """零回归：成功的重载必须照常换上新 cookie 与新会话，不能被回滚逻辑误伤。"""
    client = _prepare_client_for_reload(monkeypatch, token_after_reload="NEW-TOKEN")

    result = asyncio.run(client.reload_cookies(psid="new-psid", psidts="new-psidts"))

    assert result["success"] is True
    assert client.is_healthy is True
    assert client._session_token == "NEW-TOKEN"
    assert client._psid == "new-psid"
    assert client._psidts == "new-psidts"


# ---------------------------------------------------------------------------
# 测试 6（F6）：客户端断连不得算作账号失败
# ---------------------------------------------------------------------------

class _DisconnectingClient(_FakeClient):
    """模拟"请求被取消 / 客户端中途断连"的 client。"""

    async def generate(self, prompt, model, conversation_id="", attachments=None, gem_id=None,
                       extended_thinking=False):
        self.generate_calls += 1
        raise asyncio.CancelledError()

    async def generate_stream(self, prompt, model, conversation_id="", attachments=None, gem_id=None,
                              extended_thinking=False):
        self.generate_calls += 1
        yield {"type": "delta", "text": "partial"}
        await asyncio.sleep(30)  # 客户端在这里断开，生成器被 aclose
        yield {"type": "final", "text": "partial", "images": [], "conversation_id": "c1"}


def test_client_disconnect_is_not_an_account_failure():
    """断连 3 次（取消 + 流中途 aclose）：账号必须仍是 ACTIVE、连续失败没涨、槽位归 0。
    修复前 finally 里统一 release(success=False)，3 次就把单账号池标成 EXPIRED
    —— 用户点 3 次"停止"就能把服务搞挂。"""
    client = _DisconnectingClient(healthy=True)

    async def _run():
        pool = _make_pool([client])
        account = pool.accounts[0]

        # ① 非流式：请求被取消
        for _ in range(3):
            try:
                await pool.generate("hi", "gemini-pro")
            except asyncio.CancelledError:
                pass
            assert account.active_requests == 0

        # ② 流式：吐了一块之后客户端断连（async generator 被 aclose → GeneratorExit）
        for _ in range(3):
            agen = pool.generate_stream("hi", "gemini-pro")
            first = await agen.__anext__()
            assert first["type"] == "delta"
            await agen.aclose()
            assert account.active_requests == 0

        return account

    account = asyncio.run(_run())

    assert account.status == AccountStatus.ACTIVE, "断连不该把账号标成 EXPIRED"
    assert account.consecutive_failures == 0
    assert account.error_count == 0
    assert account.active_requests == 0


def test_real_failures_still_count_against_the_account():
    """零回归反向守卫：真正的失败仍要累加，满 3 次仍标 EXPIRED —— F6 只豁免断连。"""
    class _BrokenClient(_FakeClient):
        async def generate(self, prompt, model, conversation_id="", attachments=None, gem_id=None,
                           extended_thinking=False):
            raise ValueError("upstream exploded")

    async def _run():
        pool = _make_pool([_BrokenClient(healthy=True)])
        account = pool.accounts[0]
        for _ in range(3):
            with pytest.raises(ValueError):
                await pool.generate("hi", "gemini-pro")
        return account

    account = asyncio.run(_run())

    assert account.consecutive_failures == 3
    assert account.error_count == 3
    assert account.status == AccountStatus.EXPIRED
    assert account.active_requests == 0


# ---------------------------------------------------------------------------
# H1（reviewer FIX_FIRST 复核）：自愈被取消时 healing 标志必须清掉，
# 即使清标志那一刻 self._cond 正被别的协程占着
# ---------------------------------------------------------------------------

def test_healing_flag_cleared_even_when_cancelled_while_finally_waits_on_contended_lock():
    """H1：account.healing 只在 `_try_heal_unhealthy` 的 finally 里被清掉。旧实现把清
    标志这一步塞进了 `async with self._cond:` 内部——Starlette 客户端断连触发的取消会
    在这个锁竞争点上直接把协程打断，标志永远清不掉，自愈通道被永久焊死：单账号池从此
    永久 503，且没有任何后续请求能再触发一次 reload_cookies —— 这正是 issue #11 本身，
    被我们自己的修复以另一种形态重新引入（reviewer 复核发现）。
    修复后：healing=False 是不需要锁的普通属性写，必须无条件跑到，不依赖抢赢这把锁；
    需要锁的记账部分（冷却/notify_all）做成不阻塞取消传播的 best-effort。"""
    client = _FakeClient(healthy=False, reload_succeeds=True)
    client.reload_delay = 0.3

    async def _run():
        pool = _make_pool([client], acquire_timeout=5.0)
        account = pool.accounts[0]

        async with pool._cond:
            heal_target = pool._pick_heal_candidate()
        assert heal_target is account
        assert account.healing is True

        heal_task = asyncio.create_task(pool._try_heal_unhealthy(heal_target))
        # 放行到 reload_cookies 的网络等待里（此时 pool._cond 已经不在它手上了）
        await asyncio.sleep(0.05)

        # 制造"finally 里要用的锁正被别人占着"的场景：把 pool._cond 死死占住
        lock_free = asyncio.Event()

        async def _hold_lock():
            async with pool._cond:
                await lock_free.wait()

        holder = asyncio.create_task(_hold_lock())
        await asyncio.sleep(0.03)  # 确保 holder 先拿到锁

        heal_task.cancel()
        # 给事件循环几个节拍去投递取消、跑 finally 里不需要锁的同步部分
        await asyncio.sleep(0.03)

        healing_while_lock_still_contended = account.healing

        lock_free.set()
        await holder
        with pytest.raises(asyncio.CancelledError):
            await heal_task

        return pool, account, healing_while_lock_still_contended

    pool, account, healing_while_lock_still_contended = asyncio.run(_run())

    assert healing_while_lock_still_contended is False, (
        "healing 标志必须在取消发生时立刻清掉，不能等到抢回被占用的锁"
    )
    # 没被判成"失败"（取消不是失败）：不该背上 60s 冷却
    assert account.heal_cooldown_until == 0.0

    # 自愈通道没被焊死：下一次请求必须能再次触发 reload_cookies 并成功
    result = asyncio.run(pool.generate("hi", "gemini-pro"))
    assert client.reload_calls == 2, "被取消的那次也算一次调用，第二次应是全新的自愈尝试"
    assert result["text"] == "ok"


def test_acquire_timeout_bounds_heal_duration_and_shields_background_completion():
    """M1（reviewer MEDIUM）：acquire() 的总耗时不该被自愈的网络 I/O（最长 ~60s）顶穿
    operator 配置的 acquire_timeout；同时 shield 必须保证背后的自愈没有被腰斩——它会在
    后台跑完，healing 标志与 client 健康状态仍会被正确落盘。"""
    client = _FakeClient(healthy=False, reload_succeeds=True)
    client.reload_delay = 0.5

    async def _run():
        pool = _make_pool([client], acquire_timeout=0.1)
        t0 = time.monotonic()
        with pytest.raises(RuntimeError) as ei:
            await pool.acquire()
        elapsed = time.monotonic() - t0
        account = pool.accounts[0]
        # 等后台自愈（被 shield 保护，acquire() 超时不取消它）真正跑完
        await asyncio.sleep(client.reload_delay + 0.2)
        return elapsed, str(ei.value), account

    elapsed, msg, account = asyncio.run(_run())

    assert elapsed < 0.5, (
        f"acquire() 耗时 {elapsed:.2f}s，被自愈的网络 I/O 顶穿了 acquire_timeout(0.1s)"
    )
    assert msg == NO_HEALTHY_ACCOUNT_MSG
    assert client.reload_calls == 1
    # 后台自愈跑完之后：flag 清掉、client 真的变健康了（shield 生效，没被腰斩）
    assert account.healing is False
    assert account.client.is_healthy is True


def test_heal_skipped_entirely_when_acquire_budget_already_exhausted():
    """M1 边界：acquire_timeout 预算已经耗尽时，压根不发起自愈——发起了也没意义
    （调用方早就不想再等了），直接原样归还单飞标志、报出准确错误。"""
    client = _FakeClient(healthy=False, reload_succeeds=True)

    async def _run():
        pool = _make_pool([client], acquire_timeout=-1.0)
        with pytest.raises(RuntimeError) as ei:
            await pool.acquire()
        return str(ei.value), pool.accounts[0]

    msg, account = asyncio.run(_run())
    assert msg == NO_HEALTHY_ACCOUNT_MSG
    assert client.reload_calls == 0, "预算已耗尽不该再发起自愈"
    assert account.healing is False


# ---------------------------------------------------------------------------
# H2（reviewer FIX_FIRST）：EXPIRED + ACTIVE-不健康 混合场景
# 不得再对 EXPIRED 账号做持锁 check_account()
# ---------------------------------------------------------------------------

def test_unhealthy_active_with_expired_sibling_does_not_hit_locked_check_account():
    """H2：池里同时有 EXPIRED 账号和 ACTIVE-但-不健康 账号时，F1 收紧后的
    has_available_account 判定会让请求撞进"没有可用账号"分支——修复前会在这里对 EXPIRED
    账号跑持锁的 check_account()：没有冷却、没有单飞，逐请求触发，等于给 Google 开了个
    敲门风暴，还把整个池（包括本该走的 F2 自愈路径）一起卡住。修复后必须绕开
    _try_recover_expired()，直接走 F2 已有的、锁外/单飞/带冷却的自愈路径。"""
    unhealthy = _FakeClient(healthy=False, reload_succeeds=True)
    expired_sibling = _FakeClient(healthy=True)

    async def _run():
        pool = _make_pool([unhealthy, expired_sibling], acquire_timeout=5.0)
        pool.accounts[1].status = AccountStatus.EXPIRED

        lock_states = []
        unhealthy.on_reload = lambda: lock_states.append(pool._cond.locked())

        result = await pool.generate("hi", "gemini-pro")
        return result, expired_sibling.check_account_calls, lock_states

    result, check_calls, lock_states = asyncio.run(_run())

    assert check_calls == 0, "ACTIVE-不健康 分支不该再碰 EXPIRED 账号的 check_account()（H2）"
    assert lock_states == [False], "reload_cookies 仍必须锁外调用"
    assert result["text"] == "ok"


def test_all_expired_pool_still_recovers_via_check_account():
    """零回归：真正"一个 ACTIVE 账号都没有"（清一色 EXPIRED）时，_try_recover_expired()
    仍要被触发且仍能救活账号——H2 只收窄了"EXPIRED + ACTIVE-不健康混合"这一种场景，
    不能连这条本来就有的恢复路径也一起关掉。"""
    client = _FakeClient(healthy=True)

    async def _run():
        pool = _make_pool([client], acquire_timeout=5.0)
        pool.accounts[0].status = AccountStatus.EXPIRED
        result = await pool.generate("hi", "gemini-pro")
        return result, client.check_account_calls, pool.accounts[0]

    result, check_calls, account = asyncio.run(_run())
    assert check_calls == 1
    assert account.status == AccountStatus.ACTIVE
    assert result["text"] == "ok"


# ---------------------------------------------------------------------------
# M2：release_disconnected() 必须与 release(success=True) 语义可区分，
# 且断连仍会唤醒排队者
# ---------------------------------------------------------------------------

def test_disconnect_release_is_neutral_not_success():
    """M2：区分 release_disconnected() 与 release(success=True)——先用一次真实失败把
    consecutive_failures 顶到 1，再制造一次断连；若断连误走了 success=True 的清零逻辑，
    这个值会被冲掉，旧测试断言不出这个差异（只看 status/error_count 两边都是 0，看不出
    区别）。"""
    class _FlakyThenDisconnectClient(_FakeClient):
        def __init__(self):
            super().__init__(healthy=True)
            self._calls = 0

        async def generate(self, prompt, model, conversation_id="", attachments=None, gem_id=None,
                           extended_thinking=False):
            self._calls += 1
            if self._calls == 1:
                raise ValueError("upstream exploded")
            raise asyncio.CancelledError()

    async def _run():
        pool = _make_pool([_FlakyThenDisconnectClient()])
        account = pool.accounts[0]

        with pytest.raises(ValueError):
            await pool.generate("hi", "gemini-pro")
        assert account.consecutive_failures == 1

        try:
            await pool.generate("hi", "gemini-pro")
        except asyncio.CancelledError:
            pass
        return account

    account = asyncio.run(_run())
    assert account.consecutive_failures == 1, (
        "断连必须走中性释放：不能把已有的真实失败计数清零（那是 success=True 的语义）"
    )
    assert account.status == AccountStatus.ACTIVE
    assert account.active_requests == 0


def test_disconnect_wakes_a_queued_waiter():
    """M2：断连必须唤醒排队等待者（不只是归还槽位）——否则排队请求会一路等到
    acquire_timeout 超时才醒，即便槽位其实已经空出来了。"""
    client = _FakeClient(healthy=True)

    async def _run():
        pool = _make_pool([client], max_concurrent=1, acquire_timeout=5.0)
        holder = await pool.acquire()  # 占住唯一槽位
        assert holder.active_requests == 1

        waiter = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0.05)
        assert not waiter.done(), "唯一槽位被占，等待者理应排队"

        await pool.release_disconnected(holder)  # 模拟断连归还
        second = await asyncio.wait_for(waiter, timeout=1.0)
        return second

    account = asyncio.run(_run())
    assert account.active_requests == 1


# ---------------------------------------------------------------------------
# M3（reviewer MEDIUM）：池级自愈必须过 client 自己的 _heal_lock
# ---------------------------------------------------------------------------

def test_pool_heal_routes_through_clients_own_heal_lock():
    """M3：池级自愈必须过 client 自己的 _heal_lock，不能绕开它去裸调 reload_cookies()——
    否则池级单飞（Account.healing）和 client 自己内部的自愈（generate/generate_stream
    开头那段）用的是两把不同的锁，能在同一个 client 上并发跑两次 reload_cookies，
    互相踩会话状态（_http 被关两次、cookie/token 写串）。"""
    client = _FakeClient(healthy=False, reload_succeeds=True)
    heal_lock_states = []
    client.on_reload = lambda: heal_lock_states.append(client._heal_lock.locked())

    async def _run():
        pool = _make_pool([client], acquire_timeout=5.0)
        return await pool.generate("hi", "gemini-pro")

    result = asyncio.run(_run())
    assert result["text"] == "ok"
    # reload_cookies 被调用的那一刻，client 自己的 _heal_lock 必须已经被池级自愈持有
    assert heal_lock_states == [True], "池级自愈没有过 client._heal_lock（M3）"


def test_pool_heal_double_checks_after_acquiring_client_lock():
    """M3 双重检查：拿到 client._heal_lock 后如果发现 client 已经被治好了（比如 client
    自己另一条调用链先跑赢了），就不该再跑一次 reload_cookies()。"""
    client = _FakeClient(healthy=False, reload_succeeds=True)

    async def _run():
        pool = _make_pool([client], acquire_timeout=5.0)
        async with pool._cond:
            heal_target = pool._pick_heal_candidate()

        async with client._heal_lock:
            # 模拟别的路径已经在我们前面把它治好了
            client._healthy = True

        return await pool._try_heal_unhealthy(heal_target)

    ok = asyncio.run(_run())
    assert ok is True
    assert client.reload_calls == 0, "client 已经健康时不该再跑一次 reload_cookies（M3 双重检查）"
