import argparse
import json
import os
from collections import defaultdict

REQUIRED = ["intent", "source", "mode", "reason", "leg", "cycle_id"]
TERMINAL_REASONS = {"ticket_done", "hedge_unwind", "ticket_failed"}
ORDER_REASONS = {"ticket_order", "hedge_chase"}
TICKET_REASONS = {"ticket_open", "ticket_done", "hedge_unwind", "ticket_failed", "ticket_order", "hedge_chase"}
HALT_ALLOWED_EVENTS = {"order_cancel", "fill", "risk", "state", "halted", "shutdown"}


def iter_paths(path: str):
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for name in files:
                if name.endswith(".jsonl"):
                    yield os.path.join(root, name)
    else:
        yield path


def load_records(paths):
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield path, json.loads(line)
                    except json.JSONDecodeError:
                        yield path, {"_bad_json": line[:200]}
        except FileNotFoundError:
            yield path, {"_missing_file": True}


def norm_inst_type(rec: dict) -> str | None:
    value = rec.get("inst_type") or rec.get("instType")
    if not value:
        return None
    return str(value)


def to_ms(value) -> int | None:
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:
        return int(ts)
    return int(ts * 1000)


def is_quote_client_oid(client_oid: str) -> bool:
    if not client_oid:
        return False
    client = client_oid.upper()
    return client.startswith("QUOTE_BID") or client.startswith("QUOTE_ASK") or client.startswith("Q")


def as_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_active_tick(rec: dict) -> bool:
    mode = str(rec.get("mode") or "")
    state = str(rec.get("state") or "")
    reason = str(rec.get("reason") or "")
    if mode.upper() == "HALTED" or state.upper() == "HALTED" or reason == "halted":
        return False
    return True


def _strict_required_files_ok(present_filenames: set[str]) -> bool:
    # Strict required files: accept unified decision.jsonl or legacy mm_*.jsonl.
    if "decision.jsonl" in present_filenames:
        return True
    return any(name.startswith("mm_") and name.endswith(".jsonl") for name in present_filenames)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate JSONL logs")
    parser.add_argument("path", help="Log file or directory")
    parser.add_argument("--hedge-max-tries", type=int, default=None)
    parser.add_argument("--max-details", type=int, default=5)
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--halt-strict", action="store_true")
    parser.add_argument("--book-filter-strict", action="store_true")
    parser.add_argument("--controlled-grace-sec", type=float, default=None)
    parser.add_argument(
        "--require-files",
        default=None,
        help="Comma-separated names or glob patterns (match basename or path).",
    )
    parser.add_argument("--require-ticket-events", action="store_true")
    args = parser.parse_args()

    paths = list(iter_paths(args.path))
    loaded_paths = [path for path in paths if os.path.exists(path)]
    loaded_files = sorted({os.path.basename(path) for path in loaded_paths})

    missing_key = defaultdict(int)
    warn_missing = 0
    bad_json = 0
    missing_file = 0
    event_counts = defaultdict(int)

    perp_fills = 0
    perp_quote_fills = 0
    hedge_orders = 0
    ticket_open = 0
    ticket_done = 0
    ticket_unwind = 0
    ticket_failed = 0

    ticket_events: dict[str, list[tuple[int, str, dict]]] = defaultdict(list)
    ticket_remain_max: dict[str, float] = defaultdict(float)
    ticket_tries_max: dict[str, int] = defaultdict(int)
    ticket_last: dict[str, dict] = {}
    quote_fill_ids: set[str] = set()
    quote_fill_dupes = 0
    order_new_events: list[tuple[int, dict]] = []
    timed_events: list[tuple[int, dict]] = []
    halt_events: list[tuple[int, str | None]] = []
    ticket_ref: dict[str, str] = {}
    book_fallback_count = 0
    book_fallback_failed_count = 0
    book_fallback_failed_ts: int | None = None
    book_fallback_ts: int | None = None
    book_fallback_to_channel: str | None = None
    book_filter_unavailable_count = 0
    tick_events: list[tuple[int, dict]] = []
    ws_disconnect_controlled_events: list[tuple[int, dict]] = []
    ws_disconnect_events: list[tuple[int, dict]] = []
    book_store_cleared_count = 0
    book_store_cleared_filter_unavailable = 0
    book_store_cleared_failed = 0
    ticket_events_present = False

    for path, rec in load_records(paths):
        if rec.get("_missing_file"):
            missing_file += 1
            continue
        if rec.get("_bad_json"):
            bad_json += 1
            continue

        if "event" in rec:
            event_counts[rec["event"]] += 1

        for key in REQUIRED:
            if key not in rec:
                missing_key[key] += 1

        if rec.get("warn_missing"):
            warn_missing += 1

        event = rec.get("event")
        reason = rec.get("reason")
        intent = rec.get("intent")
        ts_ms = to_ms(rec.get("ts"))
        if reason in TICKET_REASONS or "ticket_id" in rec:
            ticket_events_present = True
        if event == "book_fallback":
            book_fallback_count += 1
            if ts_ms is not None:
                if book_fallback_ts is None or ts_ms < book_fallback_ts:
                    book_fallback_ts = ts_ms
                    to_channel = rec.get("to_channel") or rec.get("toChannel")
                    if to_channel is not None:
                        book_fallback_to_channel = str(to_channel)
        if event == "book_fallback_failed":
            book_fallback_failed_count += 1
            if ts_ms is not None:
                if book_fallback_failed_ts is None or ts_ms < book_fallback_failed_ts:
                    book_fallback_failed_ts = ts_ms
        if event == "book_channel_filter_unavailable":
            book_filter_unavailable_count += 1
        if event == "ws_disconnect_controlled" and ts_ms is not None:
            ws_disconnect_controlled_events.append((ts_ms, rec))
        if event == "ws_disconnect" and ts_ms is not None:
            ws_disconnect_events.append((ts_ms, rec))
        if event == "book_store_cleared":
            book_store_cleared_count += 1
            if reason == "filter_unavailable":
                book_store_cleared_filter_unavailable += 1
            if rec.get("cleared") is False or rec.get("error"):
                book_store_cleared_failed += 1

        inst_type = norm_inst_type(rec)
        if event == "fill" and inst_type == "USDT-FUTURES":
            perp_fills += 1
            fill_id = rec.get("fill_id") or rec.get("tradeId") or rec.get("trade_id")
            if fill_id:
                if fill_id in quote_fill_ids:
                    quote_fill_dupes += 1
                else:
                    quote_fill_ids.add(fill_id)
            client_oid = rec.get("client_oid") or rec.get("clientOid") or ""
            if is_quote_client_oid(str(client_oid)):
                perp_quote_fills += 1

        if event == "order_new" and str(intent).upper() == "HEDGE":
            hedge_orders += 1

        if ts_ms is not None and event:
            timed_events.append((ts_ms, rec))
        if event == "order_new" and ts_ms is not None:
            order_new_events.append((ts_ms, rec))
        if event == "tick" and ts_ms is not None:
            tick_events.append((ts_ms, rec))

        if event == "halted" and ts_ms is not None:
            halt_events.append((ts_ms, rec.get("reason")))
        if rec.get("mode") == "HALTED" and ts_ms is not None:
            halt_events.append((ts_ms, rec.get("reason")))
        if rec.get("state") == "HALTED" and ts_ms is not None:
            halt_events.append((ts_ms, rec.get("reason")))

        ticket_id = rec.get("ticket_id")
        if ticket_id and reason in TICKET_REASONS:
            if ts_ms is None:
                ts_ms = 0
            ticket_events[str(ticket_id)].append((ts_ms, str(reason), rec))
            ticket_last[str(ticket_id)] = rec
            remain = rec.get("remain") or rec.get("remain_qty")
            if remain is not None:
                try:
                    ticket_remain_max[str(ticket_id)] = max(ticket_remain_max[str(ticket_id)], float(remain))
                except (TypeError, ValueError):
                    pass
            tries = as_int(rec.get("tries"))
            if tries is not None:
                ticket_tries_max[str(ticket_id)] = max(ticket_tries_max[str(ticket_id)], tries)
            client_oid = rec.get("client_oid") or rec.get("clientOid")
            order_id = rec.get("order_id") or rec.get("orderId")
            if client_oid:
                ticket_ref[str(client_oid)] = str(ticket_id)
            if order_id:
                ticket_ref[str(order_id)] = str(ticket_id)
        if reason == "ticket_open" and ticket_id:
            ticket_open += 1
        if reason == "ticket_done" and ticket_id:
            ticket_done += 1
        if reason == "hedge_unwind" and ticket_id:
            ticket_unwind += 1
        if reason == "ticket_failed" and ticket_id:
            ticket_failed += 1

    errors: list[str] = []
    required_files = []
    missing_required = []
    strict = bool(args.require_files)
    if args.require_files:
        required_files = [name.strip() for name in args.require_files.split(",") if name.strip()]
    present_filenames = set(loaded_files)
    if strict and (not _strict_required_files_ok(present_filenames)):
        missing_required = ["decision.jsonl"]
        errors.append(f"missing_required_files={','.join(missing_required)}")
    if warn_missing != 0:
        errors.append(f"warn_missing_lines={warn_missing}")
    if bad_json != 0:
        errors.append(f"bad_json_lines={bad_json}")

    open_tickets = set()
    chase_tickets = 0
    chase_count_total = 0
    done_latency = []
    unwind_latency = []
    ticket_errors: list[str] = []
    terminal_ts: dict[str, int] = {}
    terminal_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for ticket_id, events in ticket_events.items():
        events_sorted = sorted(events, key=lambda x: x[0])
        seen_open = False
        terminal = None
        open_ts = None
        last_reason = None
        chase_count = 0
        last_tries = None
        for ts_ms, reason, rec in events_sorted:
            last_reason = reason
            tries = as_int(rec.get("tries"))
            if tries is not None:
                if last_tries is not None and tries < last_tries:
                    ticket_errors.append(f"tries_non_monotonic:{ticket_id}")
                last_tries = tries
                if args.hedge_max_tries is not None and tries > args.hedge_max_tries:
                    ticket_errors.append(f"tries_exceed:{ticket_id}:{tries}")
            if reason == "ticket_open":
                if seen_open:
                    ticket_errors.append(f"duplicate_open:{ticket_id}")
                if terminal:
                    ticket_errors.append(f"open_after_terminal:{ticket_id}")
                seen_open = True
                open_ts = ts_ms
                continue
            if reason in ORDER_REASONS:
                if not seen_open:
                    ticket_errors.append(f"order_before_open:{ticket_id}:{reason}")
                if terminal:
                    ticket_errors.append(f"order_after_terminal:{ticket_id}:{reason}")
                if reason == "hedge_chase":
                    chase_count += 1
                continue
            if reason in TERMINAL_REASONS:
                if not seen_open:
                    ticket_errors.append(f"terminal_before_open:{ticket_id}:{reason}")
                if terminal:
                    ticket_errors.append(f"duplicate_terminal:{ticket_id}:{reason}")
                terminal = reason
                terminal_counts[ticket_id][reason] += 1
                if open_ts is not None:
                    latency = ts_ms - open_ts
                    if reason == "ticket_done":
                        done_latency.append(latency)
                    elif reason == "hedge_unwind":
                        unwind_latency.append(latency)
                if ticket_id not in terminal_ts:
                    terminal_ts[ticket_id] = ts_ms
                continue
        if not seen_open:
            ticket_errors.append(f"missing_open:{ticket_id}")
            continue
        if terminal is None:
            open_tickets.add(ticket_id)
        if chase_count > 0:
            chase_tickets += 1
            chase_count_total += chase_count
            if terminal is None:
                ticket_errors.append(f"chase_without_terminal:{ticket_id}")
        if terminal in TERMINAL_REASONS and last_reason in ORDER_REASONS:
            ticket_errors.append(f"order_after_terminal:{ticket_id}")
        terminal_total = sum(terminal_counts[ticket_id].values())
        if terminal_total > 1:
            ticket_errors.append(f"terminal_conflict:{ticket_id}")
        for reason, count in terminal_counts[ticket_id].items():
            if count > 1:
                ticket_errors.append(f"terminal_duplicate:{ticket_id}:{reason}")

    for ts_ms, rec in order_new_events:
        intent = str(rec.get("intent") or "").upper()
        if intent != "HEDGE":
            continue
        client_oid = rec.get("client_oid") or rec.get("clientOid")
        order_id = rec.get("order_id") or rec.get("orderId")
        ticket_id = None
        if client_oid:
            ticket_id = ticket_ref.get(str(client_oid))
        if ticket_id is None and order_id:
            ticket_id = ticket_ref.get(str(order_id))
        if ticket_id is None and client_oid:
            if str(client_oid) in ticket_events:
                ticket_id = str(client_oid)
        if ticket_id and ticket_id in terminal_ts and ts_ms > terminal_ts[ticket_id]:
            ticket_errors.append(f"order_new_after_terminal:{ticket_id}")

    if ticket_open != (ticket_done + ticket_unwind + ticket_failed):
        errors.append("ticket_open_mismatch")
    if open_tickets:
        errors.append(f"open_tickets_at_end={len(open_tickets)}")
    if ticket_errors:
        errors.append(f"ticket_errors={len(ticket_errors)}")
    ticket_events_missing = False
    if perp_quote_fills:
        if ticket_events_present:
            if ticket_open != perp_quote_fills:
                errors.append(f"perp_quote_fill_mismatch={perp_quote_fills}/{ticket_open}")
        else:
            ticket_events_missing = True
            if args.require_ticket_events:
                errors.append("ticket_events_missing")
    if quote_fill_dupes:
        errors.append(f"perp_fill_dupes={quote_fill_dupes}")
    if book_fallback_count > 1:
        errors.append(f"book_fallback_count={book_fallback_count}")
    if args.book_filter_strict and book_filter_unavailable_count:
        errors.append(f"book_channel_filter_unavailable={book_filter_unavailable_count}")

    halt_ts = None
    halt_reasons: dict[str, int] = defaultdict(int)
    for ts_ms, reason in halt_events:
        if halt_ts is None or ts_ms < halt_ts:
            halt_ts = ts_ms
        if reason:
            halt_reasons[str(reason)] += 1
    halt_violations = 0
    if halt_ts is not None:
        for ts_ms, rec in order_new_events:
            if ts_ms <= halt_ts:
                continue
            intent = str(rec.get("intent") or "").upper()
            if intent in {"QUOTE_BID", "QUOTE_ASK", "HEDGE"}:
                halt_violations += 1
        if halt_violations:
            errors.append(f"order_after_halt={halt_violations}")
        if args.halt_strict:
            strict_violations = 0
            for ts_ms, rec in timed_events:
                if ts_ms <= halt_ts:
                    continue
                event = rec.get("event")
                intent = str(rec.get("intent") or "").upper()
                mode = str(rec.get("mode") or "")
                reason = str(rec.get("reason") or "")
                if event == "order_new" and intent in {"UNWIND", "FLATTEN"}:
                    continue
                if event == "tick" and mode == "HALTED":
                    continue
                if event == "state" and mode == "HALTED":
                    continue
                if event in HALT_ALLOWED_EVENTS:
                    continue
                if reason == "halted":
                    continue
                strict_violations += 1
            if strict_violations:
                errors.append(f"halt_strict_violation={strict_violations}")
    if book_fallback_failed_count > 0 and book_fallback_failed_ts is not None:
        fallback_violations = 0
        for ts_ms, rec in order_new_events:
            if ts_ms <= book_fallback_failed_ts:
                continue
            intent = str(rec.get("intent") or "").upper()
            if intent in {"QUOTE_BID", "QUOTE_ASK", "HEDGE"}:
                fallback_violations += 1
        if fallback_violations:
            errors.append(f"order_after_book_fallback_failed={fallback_violations}")
        if halt_ts is None or halt_ts < book_fallback_failed_ts:
            errors.append("book_fallback_failed_without_halt")

    tick_events_sorted = sorted(tick_events, key=lambda x: x[0])
    tick_book_channel_mismatch = 0
    tick_book_channel_missing = 0
    tick_book_channel_samples: list[dict] = []
    if book_fallback_ts is not None and book_fallback_to_channel is not None:
        for ts_ms, rec in tick_events_sorted:
            if ts_ms <= book_fallback_ts:
                continue
            book_channel = rec.get("book_channel")
            if book_channel is None:
                tick_book_channel_missing += 1
                continue
            if str(book_channel) != book_fallback_to_channel:
                tick_book_channel_mismatch += 1
                if len(tick_book_channel_samples) < args.max_details:
                    tick_book_channel_samples.append(
                        {"ts": ts_ms, "book_channel": book_channel}
                    )
        if tick_book_channel_mismatch:
            errors.append(f"tick_book_channel_mismatch={tick_book_channel_mismatch}")

    ws_disconnect_controlled_bad_reason = 0
    ws_disconnect_controlled_bad_scope = 0
    for _, rec in ws_disconnect_controlled_events:
        reason = rec.get("reason")
        if reason is None or not str(reason).startswith("book_fallback"):
            ws_disconnect_controlled_bad_reason += 1
        scope = rec.get("scope") or rec.get("side")
        if scope is None or str(scope) != "public":
            ws_disconnect_controlled_bad_scope += 1
    if ws_disconnect_controlled_bad_reason:
        errors.append(f"ws_disconnect_controlled_reason={ws_disconnect_controlled_bad_reason}")
    if ws_disconnect_controlled_bad_scope:
        errors.append(f"ws_disconnect_controlled_scope={ws_disconnect_controlled_bad_scope}")

    private_disconnect_without_halt = 0
    if halt_ts is None:
        for _, rec in ws_disconnect_events:
            scope = rec.get("scope") or rec.get("side")
            if scope is None or str(scope) != "private":
                continue
            private_disconnect_without_halt += 1
    if private_disconnect_without_halt:
        errors.append(f"private_disconnect_without_halt={private_disconnect_without_halt}")

    tick_after_fallback = False
    if book_fallback_ts is not None and book_fallback_failed_count == 0:
        for ts_ms, rec in tick_events_sorted:
            if ts_ms <= book_fallback_ts:
                continue
            if not is_active_tick(rec):
                continue
            if book_fallback_to_channel is not None:
                book_channel = rec.get("book_channel")
                if book_channel is None or str(book_channel) != book_fallback_to_channel:
                    continue
            tick_after_fallback = True
            break
        if not tick_after_fallback:
            errors.append("tick_missing_after_book_fallback")

    if book_filter_unavailable_count > 0 and book_fallback_ts is not None:
        if book_store_cleared_filter_unavailable == 0:
            errors.append("book_store_clear_missing")
        if book_store_cleared_failed:
            errors.append(f"book_store_clear_failed={book_store_cleared_failed}")

    controlled_reconnect_timeout = 0
    if (
        args.controlled_grace_sec is not None
        and args.controlled_grace_sec > 0
        and ws_disconnect_controlled_events
    ):
        grace_ms = int(args.controlled_grace_sec * 1000)
        for ts_ms, _ in ws_disconnect_controlled_events:
            deadline = ts_ms + grace_ms
            if (
                book_fallback_failed_ts is not None
                and ts_ms <= book_fallback_failed_ts <= deadline
            ):
                continue
            recovered = False
            for tick_ts, rec in tick_events_sorted:
                if tick_ts < ts_ms:
                    continue
                if tick_ts > deadline:
                    break
                if not is_active_tick(rec):
                    continue
                if book_fallback_to_channel is not None:
                    book_channel = rec.get("book_channel")
                    if book_channel is None or str(book_channel) != book_fallback_to_channel:
                        continue
                recovered = True
                break
            if not recovered:
                controlled_reconnect_timeout += 1
        if controlled_reconnect_timeout:
            errors.append(f"controlled_reconnect_timeout={controlled_reconnect_timeout}")

    print("warn_missing_lines:", warn_missing)
    print("missing_key_counts:", dict(missing_key))
    print("bad_json_lines:", bad_json)
    print("missing_files:", missing_file)
    print("loaded_files_count:", len(loaded_files))
    print("loaded_files_sample:", loaded_files[: args.max_details])
    print("required_files:", required_files)
    print("missing_required_files:", missing_required)
    print("event_counts:", dict(event_counts))
    print("perp_fills:", perp_fills)
    print("perp_quote_fills:", perp_quote_fills)
    print("hedge_orders:", hedge_orders)
    print("ticket_open:", ticket_open)
    print("ticket_done:", ticket_done)
    print("ticket_unwind:", ticket_unwind)
    print("ticket_failed:", ticket_failed)
    print("open_tickets_at_end:", len(open_tickets))
    print("chase_tickets:", chase_tickets)
    print("chase_avg_count:", 0 if chase_tickets == 0 else round(chase_count_total / chase_tickets, 2))
    print("ticket_max_remain:", max(ticket_remain_max.values()) if ticket_remain_max else 0.0)
    print("hedge_latency_ms_avg:", 0 if not done_latency else int(sum(done_latency) / len(done_latency)))
    print("unwind_latency_ms_avg:", 0 if not unwind_latency else int(sum(unwind_latency) / len(unwind_latency)))
    print("halt_count:", len(halt_events))
    print("halt_reasons:", dict(halt_reasons))
    print("ws_disconnect_controlled_count:", len(ws_disconnect_controlled_events))
    print("ws_disconnect_count:", len(ws_disconnect_events))
    print("book_fallback_count:", book_fallback_count)
    print("book_fallback_failed_count:", book_fallback_failed_count)
    print("book_filter_unavailable_count:", book_filter_unavailable_count)
    print("book_store_cleared_count:", book_store_cleared_count)
    print("book_store_cleared_filter_unavailable:", book_store_cleared_filter_unavailable)
    print("book_store_cleared_failed:", book_store_cleared_failed)
    print("tick_book_channel_mismatch:", tick_book_channel_mismatch)
    print("tick_book_channel_missing:", tick_book_channel_missing)
    print("controlled_reconnect_timeout:", controlled_reconnect_timeout)
    print("ticket_events_present:", ticket_events_present)
    print("ticket_events_missing:", ticket_events_missing)

    perp_quote_check = {"status": "PASS", "reason": "ok"}
    if perp_quote_fills == 0:
        perp_quote_check = {"status": "PASS", "reason": "no_perp_quote_fills"}
    elif ticket_events_missing:
        perp_quote_check = {"status": "SKIPPED", "reason": "ticket_events_missing"}
    elif ticket_open != perp_quote_fills:
        perp_quote_check = {
            "status": "FAIL",
            "reason": "count_mismatch",
            "details": f"{perp_quote_fills}/{ticket_open}",
        }
    else:
        perp_quote_check = {"status": "PASS", "reason": "count_match"}
    gating_mode = (
        "strict"
        if (
            args.require_ticket_events
            or bool(args.require_files)
            or args.halt_strict
            or args.book_filter_strict
            or (args.controlled_grace_sec is not None and args.controlled_grace_sec > 0)
        )
        else "loose"
    )

    if ticket_errors:
        print("ticket_errors_sample:", ticket_errors[: args.max_details])
    if open_tickets:
        details = []
        for ticket_id in list(open_tickets)[: args.max_details]:
            last = ticket_last.get(ticket_id, {})
            details.append(
                {
                    "ticket_id": ticket_id,
                    "last_reason": last.get("reason"),
                    "tries": last.get("tries"),
                    "remain": last.get("remain") or last.get("remain_qty"),
                }
            )
        print("open_ticket_details:", details)

    report = {
        "status": "FAIL" if errors else "PASS",
        "warn_missing_lines": warn_missing,
        "missing_key_counts": dict(missing_key),
        "bad_json_lines": bad_json,
        "missing_files": missing_file,
        "loaded_files_count": len(loaded_files),
        "loaded_files": loaded_files,
        "required_files": required_files,
        "missing_required_files": missing_required,
        "event_counts": dict(event_counts),
        "perp_fills": perp_fills,
        "perp_quote_fills": perp_quote_fills,
        "hedge_orders": hedge_orders,
        "ticket_open": ticket_open,
        "ticket_done": ticket_done,
        "ticket_unwind": ticket_unwind,
        "ticket_failed": ticket_failed,
        "open_tickets_at_end": len(open_tickets),
        "chase_tickets": chase_tickets,
        "chase_avg_count": 0 if chase_tickets == 0 else round(chase_count_total / chase_tickets, 2),
        "ticket_max_remain": max(ticket_remain_max.values()) if ticket_remain_max else 0.0,
        "hedge_latency_ms_avg": 0 if not done_latency else int(sum(done_latency) / len(done_latency)),
        "unwind_latency_ms_avg": 0 if not unwind_latency else int(sum(unwind_latency) / len(unwind_latency)),
        "halt_count": len(halt_events),
        "halt_reasons": dict(halt_reasons),
        "ws_disconnect_controlled_count": len(ws_disconnect_controlled_events),
        "ws_disconnect_count": len(ws_disconnect_events),
        "book_fallback_count": book_fallback_count,
        "book_fallback_failed_count": book_fallback_failed_count,
        "book_filter_unavailable_count": book_filter_unavailable_count,
        "book_store_cleared_count": book_store_cleared_count,
        "book_store_cleared_filter_unavailable": book_store_cleared_filter_unavailable,
        "book_store_cleared_failed": book_store_cleared_failed,
        "tick_book_channel_mismatch": tick_book_channel_mismatch,
        "tick_book_channel_missing": tick_book_channel_missing,
        "controlled_reconnect_timeout": controlled_reconnect_timeout,
        "ticket_events_present": ticket_events_present,
        "ticket_events_missing": ticket_events_missing,
        "gating_mode": gating_mode,
        "checks": {
            "perp_quote_fill_mismatch": perp_quote_check,
        },
        "errors": errors,
        "ticket_errors_sample": ticket_errors[: args.max_details],
    }
    if tick_book_channel_samples:
        report["tick_book_channel_mismatch_sample"] = tick_book_channel_samples
    if open_tickets:
        report["open_ticket_details"] = details
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=True, indent=2)

    if errors:
        print("FAIL:", errors)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
