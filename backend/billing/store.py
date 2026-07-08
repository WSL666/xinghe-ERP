"""金豆计费层: 预扣(hold) + 结算(settle) 双阶段计费。

设计思想(企业级预授权模型, 同酒店预授权 / 云厂商按量计费):
  - 金豆是后付费(跑完才知道扣几颗, 视觉可能失败、图可能只成功一半),
    而"可用余额"必须即时反映未结算任务的占用, 否则并发跑时多个任务
    互不知道彼此消耗, 必然超扣。
  - 解决: 入队时先"冻结"(hold)这笔的悲观上限; 跑完按实际成功数"结算"(settle),
    多冻的退还; 全失败则"释放"(release)冻结。冻结即时降低可用余额,
    因此天然支持任意并发, 数学上不可能超扣。

数据模型:
  users.beans         真实余额(已结算, 前端展示的金豆数)
  users.frozen_beans  冻结中(预扣占住, 未真扣)
  可用余额 = beans - frozen_beans

计费规则(成功才计):
  - 视觉解析成功: 1 金豆
  - 每张成功图:   各 1 金豆
  - 悲观预扣上限: HOLD_VISION + HOLD_PER_IMAGE * 输入图数
  - 允许欠到 BEANS_FLOOR(-10), 即可用余额 > -10 才能预扣。
"""
from __future__ import annotations

from typing import Any

from store import db_conn

# ── 计费常量 ──
BEANS_FLOOR = -10              # 可用余额下限(允许欠到此)
COST_TITLE = 1                 # AI标题(翻译)固定扣 1
HOLD_IMAGES = 10               # AI生图固定 hold 10(多了退少了扣)
HOLD_VISION = 1                # 视觉解析的冻结额度(成功必扣 1)
HOLD_PER_IMAGE = 1             # 每张成功图的扣费(结算时按实际成功数)


def hold_amount_for(features: list[str]) -> int:
    """按选中的 AI 模块算固定 hold 额度。

    - ["title"]            → 1 (标题)
    - ["images"]           → 10 (生图: 视觉1 + 生图上限, 固定10)
    - ["title","images"]   → 11 (全链路)
    """
    amt = 0
    if "title" in features:
        amt += COST_TITLE
    if "images" in features:
        amt += HOLD_IMAGES
    return amt


def init_billing_tables() -> None:
    """建金豆流水表 + 冻结字段(幂等)。在应用启动时调一次。"""
    with db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bean_transactions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                amount INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                import_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bean_tx_user ON bean_transactions(user_id, created_at DESC)"
        )
        # 老库兼容: 补 import_id 列
        conn.execute(
            "ALTER TABLE bean_transactions ADD COLUMN IF NOT EXISTS import_id BIGINT"
        )
        # 幂等防重复结算: 同一 import_id 的消费(amount<0)记录唯一。
        # 仅约束金额为负(消费)的行, 充值/hold(amount=0)/release 不受影响。
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bean_charge_import "
            "ON bean_transactions (import_id) WHERE import_id IS NOT NULL AND amount < 0"
        )
        # 冻结字段(预扣占位)。真实余额 = beans, 可用余额 = beans - frozen_beans。
        conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS frozen_beans INTEGER NOT NULL DEFAULT 0"
        )


# ─────────────────────────────────────────────────────────
# 余额查询
# ─────────────────────────────────────────────────────────

def get_beans(user_id: int) -> int:
    """查真实余额(已结算)。前端展示金豆数用这个。"""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT beans FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    return int(row["beans"]) if row else 0


def get_available_beans(user_id: int) -> int:
    """查可用余额 = 真实余额 - 冻结。预扣前用它判断够不够。"""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT beans, frozen_beans FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    if not row:
        return 0
    return int(row["beans"]) - int(row.get("frozen_beans") or 0)



def get_hold_amount_for_import(user_id: int, import_id: int) -> int:
    """查某条 import 当初预扣了多少(用于删除时退还冻结)。

    从 raw_json.ai_features 算固定 hold 额度。
    返回 0 表示无需退还。
    """
    with db_conn() as conn:
        held = conn.execute(
            "SELECT 1 FROM bean_transactions WHERE import_id = %s AND reason = 'hold' LIMIT 1",
            (import_id,),
        ).fetchone()
        if not held:
            return 0
        done = conn.execute(
            "SELECT 1 FROM bean_transactions WHERE import_id = %s AND amount <> 0 LIMIT 1",
            (import_id,),
        ).fetchone()
        if done:
            return 0
    try:
        from store import get_raw_import
        raw = get_raw_import(user_id, import_id)
        if raw:
            return hold_amount_for(raw.get("ai_features") or [])
    except Exception:
        pass
    # raw_json 可能不包含 ai_features, 兜底直接查 imports 表
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT ai_features FROM imports WHERE id = %s AND user_id = %s",
                (import_id, user_id),
            ).fetchone()
            if row:
                import json as _json
                feats = row["ai_features"] if isinstance(row["ai_features"], list) else _json.loads(row["ai_features"] or "[]")
                return hold_amount_for(feats)
    except Exception:
        pass
    return 0


# ─────────────────────────────────────────────────────────
# 预扣 / 结算 / 释放  (均幂等, 绑 import_id)
# ─────────────────────────────────────────────────────────

def hold_beans(user_id: int, amount: int, import_id: int) -> dict[str, Any] | None:
    """入队时预扣: 把 amount 冻结到 frozen_beans(不动 beans)。

    条件(原子): beans - frozen_beans - amount >= BEANS_FLOOR, 即扣后可用余额仍 >= 下限。
    幂等: 同一 import_id 已 hold 过则跳过(防重复入队重复冻结)。
    成功返回 {frozen, available}; 余额不足返回 None。

    hold 流水: amount 字段存本次冻结额度(审计 + 结算时反查), 但不进账面。
    """
    if amount <= 0:
        return None
    with db_conn() as conn:
        # 幂等: 同一 import 已 hold → 跳过
        dup = conn.execute(
            "SELECT 1 FROM bean_transactions WHERE import_id = %s AND reason = 'hold' LIMIT 1",
            (import_id,),
        ).fetchone()
        if dup:
            row = conn.execute(
                "SELECT beans, frozen_beans FROM users WHERE id = %s", (user_id,)
            ).fetchone()
            if not row:
                return None
            return {"frozen": int(row.get("frozen_beans") or 0),
                    "available": int(row["beans"]) - int(row.get("frozen_beans") or 0),
                    "dedup": True}
        # 原子冻结: 仅当预扣后可用余额仍 >= FLOOR 才 frozen += amount
        row = conn.execute(
            """
            UPDATE users
            SET frozen_beans = frozen_beans + %s, updated_at = now()
            WHERE id = %s AND beans - frozen_beans - %s >= %s
            RETURNING beans, frozen_beans
            """,
            (amount, user_id, amount, BEANS_FLOOR),
        ).fetchone()
        if not row:
            return None
        beans, frozen = int(row["beans"]), int(row["frozen_beans"])
        # hold 流水: amount=0(不进账面; 真实余额未变, 仅 frozen_beans 变了)。
        # 冻结额度由 frozen_beans 字段跟踪; settle 时调用方重新算 hold_amount_for() 传入。
        # amount 必须为 0, 否则 release 的 "amount <> 0" 防重检查会被 hold 记录误命中。
        conn.execute(
            """
            INSERT INTO bean_transactions (user_id, amount, balance_after, reason, import_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, 0, beans, "hold", import_id),
        )
    return {"frozen": frozen, "available": beans - frozen}


def settle_beans(user_id: int, import_id: int, hold_amount: int,
                 vision_ok: bool, success_images: int,
                 title_ok: bool = False) -> dict[str, Any] | None:
    """任务跑完结算: 解冻预扣额度, 按实际成功数真扣, 多冻的退还。

    hold_amount = 当初 hold 的额度(hold_amount_for 算出的上限)。
    actual(实际成本) = 视觉成功1 + 成功图数(各项失败不计)。
    操作(FOR UPDATE 锁行, 原子):
      frozen_beans -= hold_amount   (解冻全部预扣)
      beans        -= actual        (真扣)
      多冻的 (hold_amount - actual) 随解冻自动回到可用余额。
    幂等: 同 import 已结算(有 amount<0 流水)则跳过。
    返回: {charged, balance_after}; 无 hold 记录返回 None。
    """
    actual = 0
    if title_ok:
        actual += COST_TITLE
    if vision_ok:
        actual += HOLD_VISION
    actual += HOLD_PER_IMAGE * max(0, success_images)
    with db_conn() as conn:
        # 幂等: 已有消费结算 → 跳过
        settled = conn.execute(
            "SELECT 1 FROM bean_transactions WHERE import_id = %s AND amount < 0 LIMIT 1",
            (import_id,),
        ).fetchone()
        if settled:
            row = conn.execute("SELECT beans FROM users WHERE id = %s", (user_id,)).fetchone()
            return {"charged": 0, "balance_after": int(row["beans"]) if row else 0, "dedup": True}
        # 确认有 hold 记录(否则不结算)
        hold_row = conn.execute(
            "SELECT 1 FROM bean_transactions WHERE import_id = %s AND reason = 'hold' LIMIT 1",
            (import_id,),
        ).fetchone()
        if not hold_row:
            return None
        # 锁行读当前值
        cur = conn.execute(
            "SELECT beans, frozen_beans FROM users WHERE id = %s FOR UPDATE", (user_id,)
        ).fetchone()
        if not cur:
            return None
        # 解冻: 不超过当前冻结额(hold 记录丢失时防 frozen 打成负数)
        release = min(hold_amount, int(cur["frozen_beans"]))
        new_frozen = int(cur["frozen_beans"]) - release
        new_beans = int(cur["beans"]) - actual
        conn.execute(
            """
            UPDATE users SET beans = %s, frozen_beans = %s, updated_at = now()
            WHERE id = %s
            """,
            (new_beans, new_frozen, user_id),
        )
        # 一条消费流水(实际成本), 多冻的已随解冻回到可用余额, 无需单独记录
        conn.execute(
            """
            INSERT INTO bean_transactions (user_id, amount, balance_after, reason, import_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, -actual, new_beans, "TEMU采集", import_id),
        )
    return {"charged": actual, "balance_after": new_beans}


def release_beans(user_id: int, import_id: int, hold_amount: int) -> dict[str, Any] | None:
    """全失败时释放冻结(不真扣, 把预扣额度全额退还可用余额)。

    用于: 视觉失败且无任何成功图 → 不扣, 退还全部冻结。
    幂等: 同 import 已结算/已释放则跳过。
    """
    with db_conn() as conn:
        # 已有消费或已释放 → 跳过
        done = conn.execute(
            "SELECT 1 FROM bean_transactions WHERE import_id = %s AND amount <> 0 LIMIT 1",
            (import_id,),
        ).fetchone()
        if done:
            row = conn.execute("SELECT beans FROM users WHERE id = %s", (user_id,)).fetchone()
            return {"balance_after": int(row["beans"]) if row else 0, "dedup": True}
        hold_row = conn.execute(
            "SELECT 1 FROM bean_transactions WHERE import_id = %s AND reason = 'hold' LIMIT 1",
            (import_id,),
        ).fetchone()
        if not hold_row:
            return None
        cur = conn.execute(
            "SELECT beans, frozen_beans FROM users WHERE id = %s FOR UPDATE", (user_id,)
        ).fetchone()
        if not cur:
            return None
        release = min(hold_amount, int(cur["frozen_beans"]))
        new_frozen = int(cur["frozen_beans"]) - release
        conn.execute(
            "UPDATE users SET frozen_beans = %s, updated_at = now() WHERE id = %s",
            (new_frozen, user_id),
        )
        conn.execute(
            """
            INSERT INTO bean_transactions (user_id, amount, balance_after, reason, import_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, 0, int(cur["beans"]), "release", import_id),
        )
    return {"balance_after": int(cur["beans"])}


# ─────────────────────────────────────────────────────────
# 充值 + 流水查询
# ─────────────────────────────────────────────────────────

def add_beans(user_id: int, amount: int, reason: str = "充值") -> dict[str, Any] | None:
    """增加金豆(管理员充值用)。成功返回 {balance_after}。

    充值后恢复 insufficient 任务的逻辑由路由层调 restore_insufficient 完成。
    """
    if amount <= 0:
        return None
    with db_conn() as conn:
        row = conn.execute(
            """
            UPDATE users SET beans = beans + %s, updated_at = now()
            WHERE id = %s
            RETURNING beans
            """,
            (amount, user_id),
        ).fetchone()
        if not row:
            return None
        balance = int(row["beans"])
        conn.execute(
            """
            INSERT INTO bean_transactions (user_id, amount, balance_after, reason)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, amount, balance, reason),
        )
    return {"balance_after": balance}


def restore_insufficient(user_id: int) -> list[int]:
    """充值后调用: 把该用户所有 status='insufficient' 的任务重新尝试预扣入队。

    返回成功恢复(重新 hold + 入队)的 import_id 列表。
    仍不够 hold 的继续留在 insufficient。
    """
    import pipeline_queue
    from store import db_conn as _dbc, update_status
    import json as _json
    resumed: list[int] = []
    with _dbc() as conn:
        rows = conn.execute(
            "SELECT id, raw_json FROM imports WHERE user_id = %s AND status = 'insufficient' ORDER BY id",
            (user_id,),
        ).fetchall()
    for row in rows:
        import_id = int(row["id"])
        raw = row.get("raw_json")
        if isinstance(raw, str):
            try:
                raw = _json.loads(raw)
            except Exception:
                raw = {}
        raw = raw or {}
        product = raw.get("product", {}) or {}
        gallery = product.get("galleryImages", []) or []
        total_images = len(gallery[:10])
        hold_amount = hold_amount_for(total_images)
        held = hold_beans(user_id, hold_amount, import_id)
        if held:
            update_status(user_id, import_id, "queued", "restored after recharge")
            try:
                pipeline_queue.enqueue_pipeline(user_id, import_id)
                resumed.append(import_id)
            except Exception:
                # 入队失败回滚 hold
                release_beans(user_id, import_id, hold_amount)
                update_status(user_id, import_id, "insufficient", "re-enqueue failed")
        # 仍不够 hold 的: 保持 insufficient, 等下次充值
    return resumed


def list_transactions(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """查消费/充值记录(最近的 limit 条)。hold/release(amount=0)也显示。"""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT t.amount, t.balance_after, t.reason, t.created_at,
                   t.import_id, i.user_seq, u.uid AS owner_uid
            FROM bean_transactions t
            LEFT JOIN imports i ON i.id = t.import_id
            LEFT JOIN users u ON u.id = t.user_id
            WHERE t.user_id = %s
            ORDER BY t.created_at DESC
            LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        seq = d.get("user_seq")
        uid = d.get("owner_uid") or ""
        d["ref_code"] = f"{uid}{seq}" if (uid and seq) else ""
        out.append(d)
    return out
