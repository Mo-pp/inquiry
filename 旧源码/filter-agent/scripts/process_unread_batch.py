"""调度器：扫描待回复客户，唤起 claude CLI（短命进程）回复。

规则：
- 待回复 = 该客户最后一条消息是 direction='in' 且已静默 SILENCE_MINUTES 分钟
- 不同客户逐个串行处理；运行级 lockfile 防止轮次重叠
- 老客户用 --resume 续接 session；session 超过 TTL_DAYS 天自动重开
- 新客户若库里有历史，首条 prompt 附带最近 20 条作为种子
"""
from __future__ import annotations

import json
import hashlib
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import mysql.connector

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from filter_agent.scoring import ScoreDecision, ScoreParseError, parse_score_result
from filter_agent.conversation import (
    infer_customer_level,
    question_codes_in_messages,
    render_lead_context,
    render_question_candidates,
)

LOCK_FILE = ROOT / "scheduler.lock"
ENV_FILE = ROOT / ".env"

SILENCE_MINUTES = 2
SESSION_TTL_DAYS = 3
CLAUDE_TIMEOUT_SECONDS = 300
STALE_LOCK_MINUTES = 30
ALLOWED_TOOLS = (
    "mcp__lintratek-whatsapp__whatsapp_send_reply,"
    "mcp__lintratek-whatsapp__whatsapp_health"
)
ANTHROPIC_MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com/anthropic"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


ENV = load_env()


def connect():
    return mysql.connector.connect(
        host=ENV.get("MYSQL_HOST", "127.0.0.1"),
        port=int(ENV.get("MYSQL_PORT", "3306")),
        user=ENV.get("MYSQL_USER", "root"),
        password=ENV.get("MYSQL_PASSWORD", ""),
        database=ENV.get("MYSQL_DATABASE", "lintratek_chat"),
        charset="utf8mb4",
    )


def acquire_lock() -> bool:
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < STALE_LOCK_MINUTES * 60:
            return False
        LOCK_FILE.unlink(missing_ok=True)
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


def find_pending(conn, allowed_phones=None):
    cursor = conn.cursor(dictionary=True)
    sql = """
        SELECT m.customer_phone, m.message_at
        FROM customer_messages m
        JOIN (SELECT customer_phone, MAX(id) AS max_id
              FROM customer_messages GROUP BY customer_phone) last
          ON last.max_id = m.id
        WHERE m.direction = 'in'
          AND m.message_at < NOW() - INTERVAL %s MINUTE
    """
    params = [SILENCE_MINUTES]
    if allowed_phones:
        placeholders = ", ".join(["%s"] * len(allowed_phones))
        sql += f" AND m.customer_phone IN ({placeholders})\n"
        params.extend(allowed_phones)
    sql += " ORDER BY m.message_at"
    cursor.execute(sql, tuple(params))
    rows = cursor.fetchall()
    cursor.close()
    return rows


def fetch_customer_state(conn, phone):
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT agent_session_id, session_updated_at, score, level_code, score_updated_at FROM customers "
        "WHERE customer_phone = %s",
        (phone,),
    )
    state = cursor.fetchone() or {}

    cursor.execute(
        """
        SELECT message_key, message_text, message_at FROM customer_messages
        WHERE customer_phone = %s
          AND id > COALESCE((SELECT MAX(id) FROM customer_messages
                              WHERE customer_phone = %s AND direction = 'out'), 0)
        ORDER BY id
        """,
        (phone, phone),
    )
    fresh = cursor.fetchall()

    history_sql = (
        "SELECT message_key, sender, direction, message_text FROM customer_messages "
        "WHERE customer_phone = %s ORDER BY id DESC"
    )
    if state.get("score_updated_at") is not None:
        history_sql += " LIMIT 20"
    cursor.execute(history_sql, (phone,))
    history = list(reversed(cursor.fetchall()))

    # Proactive outreach starts from a form row.  Carry only the five
    # customer-provided answers into the private reply prompt; raw lead JSON
    # and unrelated ad metadata never go to the model.
    cursor.execute(
        """
        SELECT source_lead_id, full_name, business_role_answer,
               target_country_answer, coverage_area_answer,
               purchase_purpose_answer, frequency_budget_quantity_answer
        FROM customer_leads
        WHERE whatsapp_number IN (%s, %s)
        ORDER BY COALESCE(created_time, imported_at) DESC, source_lead_id DESC
        LIMIT 1
        """,
        (phone, phone[1:]),
    )
    lead = cursor.fetchone()
    if lead:
        state["_lead_context"] = lead
    cursor.close()
    return state, fresh, history


def _customer_uses_english(messages) -> bool:
    """Use the customer's language for the visible score message."""

    text = " ".join(str(item.get("message_text") or "") for item in messages)
    return not bool(re.search(r"[\u3400-\u9fff]", text))


def build_prompt(phone: str, fresh, history, state, first_session: bool) -> str:
    parts = [f"客户手机号：{phone}"]
    score_message_template = (
        "Score result\\n\\nCustomer level: {level}\\nCurrent score: {score} points\\n\\n"
        "Matched scoring items:\\n{items; join with +, or None}\\n\\n"
        "Evidence from the customer:\\n{numbered customer quotes, or None}"
        if _customer_uses_english(fresh)
        else "评分结果\\n\\n客户等级：{等级}\\n当前评分：{评分}分\\n\\n"
        "命中评分项：\\n{每个命中项及分数，使用 + 连接；没有则写暂无}\\n\\n"
        "原话证据：\\n{按序列出精简后的用户原话；没有则写暂无}"
    )
    parts.append(
        "【当前评分状态】\n"
        f"当前总分：{state.get('score', 0)}\n"
        f"当前等级：{state.get('level_code') or 'UNKNOWN'}\n"
        f"是否已有评分：{'是' if state.get('score_updated_at') else '否'}"
    )
    lead = state.get("_lead_context")
    if lead:
        level_hint = infer_customer_level(lead)
        asked_codes = question_codes_in_messages(history, level=level_hint)
        parts.append(
            "【潜在客户表单上下文】\n"
            "以下内容来自客户提交的表单，只能作为待确认事实；不要把空白字段补成猜测。\n"
            f"{render_lead_context(lead)}\n"
            f"类别提示（仅供选题，不是最终评分）：{level_hint}\n"
            "【多轮追问流程】\n"
            "1. 首轮主动联系已经展示表单答案；客户回复后，先确认客户指出的错误或不理解字段。\n"
            "2. 表单信息仍不清楚时，优先问一个最关键的通用澄清问题；不要重复已经确认的字段。\n"
            "3. 信息清楚后，从下列正向问题中选择尚未问过且最能区分客户价值的问题，单轮最多 3 个。\n"
            "4. 客户只回答其中一部分时，下一轮保留未回答的问题；不要重新发送已回答的问题。\n"
            "5. 正向问题用于收集事实，只有客户明确回答后才能计分。\n"
            "6. 当前测试流程以 80 分为目标；但如果关键信息已经足以确定唯一等级，或命中垃圾信号，应立即结束追问。\n"
            f"已由模板精确发送过的正向问题代码（模型还需识别改写）：{', '.join(asked_codes) or '暂无'}\n"
            "候选正向问题：\n"
            f"{render_question_candidates(lead)}"
        )
    # A customer without score_updated_at may be an existing conversation that
    # predates scoring. Include its complete available history on the first
    # scoring turn even when the Claude session can be resumed.
    include_history = state.get("score_updated_at") is None or first_session
    if include_history and len(history) > len(fresh):
        lines = [
            f"{'客户' if m['direction'] == 'in' else '我方'}：{m['message_text']}"
            for m in history
        ]
        parts.append("【历史对话】\n" + "\n".join(lines))
    parts.append("【客户新消息】\n" + "\n".join(m["message_text"] for m in fresh))
    parts.append(
        "【评分要求】\n"
        "本轮回复必须同时完成客户评分。根据已有评分状态和完整可见对话，"
        "输出当前总分、等级（A/B/C/D/E/K/Z/UNKNOWN）、本轮分数变化、命中评分项和用户原话证据。"
        "已有评分的客户只对本轮新确认且此前未计入的评分点计算 score_delta，不要重复累计历史评分；"
        "首次评分的老客户才根据完整历史建立初始总分。当前 score 必须等于已有 score 加 score_delta。"
        "正常客服回复最多 3 条，最后一条消息必须是评分结果；即使分数不变也要发送。"
        "UNKNOWN 表示信息不足，Z 表示垃圾/低质客户，不要把二者混淆。\n"
         "评分结果消息必须使用客户当前语言，并作为 whatsapp_send_reply 的最后一个 messages 元素发送：\n"
         f"{score_message_template}；评分消息总长度不得超过 1000 字符。\n"
        "评分结果发送后，在本次 CLI 最终结果末尾追加机器可读标记（不要放入客户消息）：\n"
        "<SCORE_RESULT>{\"score\":整数,\"score_delta\":整数,"
        "\"level_code\":\"A|B|C|D|E|K|Z|UNKNOWN\","
        "\"level_before\":字符串或null,\"matched_points\":[{\"code\":字符串,\"name\":字符串,\"score\":整数}],"
        "\"user_evidence\":[{\"rule_code\":字符串,\"message_key\":字符串或null,\"quote\":字符串}],"
        "\"information_sufficient\":布尔值}</SCORE_RESULT>"
    )
    parts.append("请回复这位客户。")
    return "\n\n".join(parts)


def run_claude(prompt: str, session_id: str | None):
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI 未安装或不在 PATH 中")
    # prompt 必须走 stdin：Windows 的 .CMD shim 会把含换行的命令行参数截断
    cmd = [
        exe, "-p",
        "--output-format", "json",
        "--allowedTools", ALLOWED_TOOLS,
        "--max-turns", "8",
    ]
    if session_id:
        cmd += ["--resume", session_id]
    env = {
        **os.environ,
        "ANTHROPIC_BASE_URL": BASE_URL,
        "ANTHROPIC_AUTH_TOKEN": ENV.get("DEEPSEEK_API_KEY", ""),
        "ANTHROPIC_MODEL": ANTHROPIC_MODEL,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": ANTHROPIC_MODEL,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": ANTHROPIC_MODEL,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": ANTHROPIC_MODEL,
    }
    # stdin/stdout/stderr 全部走临时文件而非管道：claude 卡死时管道会让
    # communicate 永久阻塞（孤儿进程占住句柄、stdin 写满缓冲区），文件重定向
    # 加 wait(timeout) 才能保证超时后强杀进程树并正常返回，不拖死调度器。
    fd, prompt_path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(prompt)
        with open(prompt_path, "r", encoding="utf-8") as stdin_f, \
             tempfile.TemporaryFile("w+", encoding="utf-8") as out_f, \
             tempfile.TemporaryFile("w+", encoding="utf-8") as err_f:
            proc = subprocess.Popen(
                cmd,
                stdin=stdin_f,
                stdout=out_f,
                stderr=err_f,
                cwd=str(ROOT),
                env=env,
            )
            try:
                proc.wait(timeout=CLAUDE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                # 只杀直接子进程会留下 node 孤儿，必须 /T 杀整棵进程树
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                )
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
                raise RuntimeError(
                    f"claude 超时（>{CLAUDE_TIMEOUT_SECONDS} 秒）已强制结束，"
                    "本轮跳过，下一轮自动重试"
                )
            out_f.seek(0)
            stdout = out_f.read()
            err_f.seek(0)
            stderr = err_f.read()
    finally:
        os.unlink(prompt_path)
    if proc.returncode != 0:
        raise RuntimeError(f"claude 退出码 {proc.returncode}: {stderr[-500:]}")
    data = json.loads(stdout)
    return data.get("session_id"), data.get("result", "")


def session_expired(state) -> bool:
    sid = state.get("agent_session_id")
    updated = state.get("session_updated_at")
    if not sid or not updated:
        return True
    return updated < datetime.now() - timedelta(days=SESSION_TTL_DAYS)


def save_session(conn, phone: str, session_id: str | None) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE customers SET agent_session_id = %s, session_updated_at = NOW() "
        "WHERE customer_phone = %s",
        (session_id, phone),
    )
    conn.commit()
    cursor.close()


def score_event_key(fresh) -> str:
    """Return a stable key for one batch of inbound messages."""

    source = "score\x1f" + "\x1f".join(sorted(m["message_key"] for m in fresh))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def save_score(conn, phone: str, fresh, decision: ScoreDecision) -> bool:
    """Append one score event and update the customer's current snapshot atomically."""

    event_key = score_event_key(fresh)
    cursor = conn.cursor()
    try:
        conn.start_transaction()
        cursor.execute(
            "SELECT score, level_code FROM customers WHERE customer_phone = %s FOR UPDATE",
            (phone,),
        )
        current = cursor.fetchone()
        if current is None:
            raise RuntimeError(f"Customer does not exist: {phone}")
        if decision.score != current[0] + decision.score_delta:
            raise ValueError(
                "Claude score is inconsistent with the persisted score "
                f"({current[0]} + {decision.score_delta} != {decision.score})"
            )
        cursor.execute(
            """
            INSERT IGNORE INTO customer_score_logs
              (customer_phone, trigger_message_key, score_before, score_delta,
               score_after, level_before, level_after, matched_points,
               user_evidence, scoring_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                phone,
                event_key,
                current[0],
                decision.score_delta,
                decision.score,
                current[1] or decision.level_before,
                decision.level_code,
                json.dumps([point.model_dump() for point in decision.matched_points], ensure_ascii=False),
                json.dumps([evidence.model_dump() for evidence in decision.user_evidence], ensure_ascii=False),
                "v1",
            ),
        )
        inserted = cursor.rowcount > 0
        if inserted:
            cursor.execute(
                "UPDATE customers SET score = %s, level_code = %s, score_updated_at = NOW() "
                "WHERE customer_phone = %s",
                (decision.score, decision.level_code, phone),
            )
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Process quiet inbound customer replies")
    parser.add_argument(
        "--allow-phone",
        action="append",
        dest="allowed_phones",
        help="process only this E.164 phone (repeat for an explicit allowlist)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    allowed_phones = tuple(dict.fromkeys(args.allowed_phones or ()))
    for phone in allowed_phones:
        if not re.fullmatch(r"\+[1-9]\d{5,14}", phone):
            raise SystemExit(f"invalid --allow-phone value: {phone}")
    if not acquire_lock():
        print("上一轮仍在运行，本轮退出。")
        return 0
    conn = connect()
    exit_code = 0
    try:
        pending = find_pending(conn, allowed_phones)
        print(f"[{datetime.now():%H:%M:%S}] 待回复客户：{len(pending)}")
        for row in pending:
            phone = row["customer_phone"]
            state, fresh, history = fetch_customer_state(conn, phone)
            if not fresh:
                continue
            resume_sid = (
                state.get("agent_session_id")
                if not session_expired(state)
                else None
            )
            mode = f"resume {resume_sid[:8]}" if resume_sid else "新 session"
            print(f"[{phone}] 唤起 claude（{mode}，新消息 {len(fresh)} 条）")
            try:
                session_id, answer = run_claude(
                    build_prompt(phone, fresh, history, state, resume_sid is None),
                    resume_sid,
                )
            except Exception as exc:
                print(f"[{phone}] 失败：{exc}")
                exit_code = 1
                continue
            if session_id:
                save_session(conn, phone, session_id)
            try:
                decision = parse_score_result(answer or "")
                inserted = save_score(conn, phone, fresh, decision)
                print(
                    f"[{phone}] 评分：{decision.level_code} {decision.score}分 "
                    f"（{'新增' if inserted else '重复'}记录）"
                )
            except (ScoreParseError, ValueError, RuntimeError, mysql.connector.Error) as exc:
                print(f"[{phone}] 评分保存失败：{exc}")
                exit_code = 1
            print(f"[{phone}] 完成：{(answer or '')[:150]}")
    finally:
        conn.close()
        release_lock()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
