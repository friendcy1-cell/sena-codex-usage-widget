"""Read-only quotas and display-only credits. No model turns or raw credentials."""
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time


class UsageError(Exception):
    pass


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalize_credits(value):
    if not isinstance(value, dict):
        return None
    if value.get("unlimited") is True:
        return {"balance": None, "unlimited": True}
    raw_balance = value.get("balance")
    if isinstance(raw_balance, bool) or not isinstance(raw_balance, (str, int, float)):
        return None
    text = str(raw_balance).strip()
    if not text or len(text) > 64:
        return None
    try:
        balance = Decimal(text)
        if (not balance.is_finite() or balance < 0 or balance >= Decimal("1e15")
                or abs(balance.as_tuple().exponent) > 18):
            return None
    except InvalidOperation:
        return None
    # Keep the provider's decimal value without converting through a float.
    exact = format(balance, "f")
    if "." in exact:
        exact = exact.rstrip("0").rstrip(".")
    return {"balance": "0" if balance == 0 else exact, "unlimited": False}


def credit_amount(snapshot):
    credits = normalize_credits(snapshot.get("credits")) if snapshot else None
    if credits is None:
        return "미제공"
    if credits["unlimited"]:
        return "무제한"
    # Display up to two decimals, never round up the available balance.
    balance = Decimal(credits["balance"])
    if 0 < balance < Decimal("0.01"):
        return "<0.01"
    amount = balance.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return format(amount, ",.2f").rstrip("0").rstrip(".")


def normalize(raw, now=None):
    now = time.time() if now is None else now
    buckets = raw.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        fallback = raw.get("rateLimits")
        buckets = {fallback.get("limitId") or "codex": fallback} if isinstance(fallback, dict) else {}
    # Never substitute Spark or another model's quota for the ordinary Codex quota.
    quota = buckets.get("codex")
    if not isinstance(quota, dict):
        raise UsageError("일반 Codex 한도를 찾지 못했습니다.")
    windows = []
    for key in ("primary", "secondary"):
        w = quota.get(key)
        if not isinstance(w, dict):
            continue
        used, duration, reset = w.get("usedPercent"), w.get("windowDurationMins"), w.get("resetsAt")
        if not numeric(used) or not 0 <= used <= 100:
            continue
        windows.append({"slot": key, "remaining": 100 - used,
                        "minutes": duration if numeric(duration) and duration > 0 else None,
                        "reset": reset if numeric(reset) and reset > 0 else None})
    if not windows:
        raise UsageError("서버가 유효한 한도 수치를 반환하지 않았습니다.")
    resets = raw.get("rateLimitResetCredits") or {}
    count = resets.get("availableCount")
    count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
    expiry = [r["expiresAt"] for r in (resets.get("credits") or [])
              if isinstance(r, dict) and r.get("status") == "available"
              and numeric(r.get("expiresAt")) and r["expiresAt"] > now]
    account = str(raw.get("accountId") or "unknown")
    return {"at": now, "account": hashlib.sha256(account.encode()).hexdigest()[:16],
            "plan": quota.get("planType"), "windows": windows,
            "coupons": count, "coupon_expiry": min(expiry) if expiry else None,
            "credits": normalize_credits(quota.get("credits"))}


def headline(snapshot):
    """Show the limiting ordinary quota, not a misleading 100% weekly number."""
    return min(snapshot["windows"], key=lambda w: w["remaining"])


def window_name(window):
    minutes = window.get("minutes")
    if minutes == 10080:
        return "주간"
    if minutes == 300:
        return "5시간"
    if minutes:
        return f"{minutes / 60:g}시간"
    return "사용"


def pct(value):
    # Preserve provider precision; never fabricate decimal quota data.
    return f"{value:.2f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))


def codex_path():
    found = shutil.which("codex.exe") or shutil.which("codex")
    if found:
        return found
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    paths = sorted(root.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    if paths:
        return str(paths[0])
    raise UsageError("Codex 실행 파일을 찾을 수 없습니다. Codex 설치를 확인해 주세요.")


class QuotaClient:
    """Short-lived stdio connection; initialization and account/quota reads only."""
    def __init__(self):
        self.process = None
        self.cancel = threading.Event()

    def close(self):
        self.cancel.set()
        p = self.process
        if p and p.poll() is None:
            p.terminate()

    def fetch(self):
        if self.cancel.is_set():
            raise UsageError("조회가 종료되었습니다.")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            p = subprocess.Popen([codex_path(), "app-server", "--listen", "stdio://"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError as e:
            raise UsageError("조회 연결을 시작하지 못했습니다. Codex 설치·보안 알림을 확인해 주세요.") from e
        self.process = p
        messages = queue.Queue(maxsize=64)
        stop = threading.Event()

        def read():
            try:
                while not stop.is_set():
                    line = p.stdout.readline(2 * 1024 * 1024)
                    if not line:
                        break
                    try:
                        obj = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(obj, dict) and obj.get("id") in (1, 2, 3):
                        try:
                            messages.put(obj, timeout=0.2)
                        except queue.Full:
                            break
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        deadline = time.monotonic() + 35

        def send(method, params, ident=None):
            if method not in ("initialize", "initialized", "account/read", "account/rateLimits/read"):
                raise UsageError("읽기 전용 범위를 벗어난 요청입니다.")
            obj = {"method": method, "params": params}
            if ident is not None:
                obj["id"] = ident
            p.stdin.write((json.dumps(obj) + "\n").encode())
            p.stdin.flush()

        def receive(ident):
            while time.monotonic() < deadline and not self.cancel.is_set():
                try:
                    obj = messages.get(timeout=0.2)
                except queue.Empty:
                    if p.poll() is not None:
                        raise UsageError("Codex 조회 연결이 종료되었습니다.")
                    continue
                if obj.get("id") != ident:
                    continue
                if "error" in obj:
                    # Never log upstream error payloads: they can contain private data.
                    error = obj["error"]
                    message = str(error.get("message", "")).lower() if isinstance(error, dict) else ""
                    markers = [s for s in ("401", "403", "429", "not logged in", "not authenticated",
                                           "chatgpt authentication", "api key", "attestation", "network",
                                           "error sending request", "connect", "unsupported", "unauthorized",
                                           "credentials", "timed out") if s in message]
                    reason = ", ".join(markers) or "응답 거절"
                    raise UsageError(f"한도 조회 실패 ({reason}). Codex 로그인·네트워크를 확인해 주세요.")
                return obj.get("result", {})
            raise UsageError("조회 시간이 초과되었습니다. 잠시 후 다시 확인해 주세요.")

        try:
            send("initialize", {"clientInfo": {"name": "sena_usage_widget", "version": "1.0.0"}}, 1)
            receive(1)
            send("initialized", {})
            # Quota responses may omit accountId. Read identity separately so two
            # users do not share an 'unknown' history marker after switching login.
            send('account/read', {'refreshToken': False}, 2)
            account = receive(2).get('account')
            identity = account_marker(account)
            send("account/rateLimits/read", {}, 3)
            limits = receive(3)
            quota_account = limits.get('accountId')
            if isinstance(quota_account, str) and quota_account:
                identity = json.dumps([identity, quota_account], ensure_ascii=False)
            return normalize({**limits, 'accountId': identity})
        except (OSError, ValueError) as e:
            raise UsageError("한도 응답을 읽지 못했습니다.") from e
        finally:
            stop.set()
            try:
                p.stdin.close()
                p.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait(timeout=2)
            reader.join(timeout=1)
            p.stdout.close()
            self.process = None


def account_marker(account):
    if not isinstance(account, dict):
        raise UsageError('계정 확인이 필요합니다.')
    fields = {key: account[key] for key in ('id', 'accountId', 'email', 'workspaceId')
              if isinstance(account.get(key), str) and account[key].strip()}
    if not fields:
        raise UsageError('계정 확인이 필요합니다.')
    # Returned only to normalize(), which hashes it; never log/store this identity.
    return json.dumps(fields, sort_keys=True, ensure_ascii=False)


def load_history(path):
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            return []
        rows = json.loads(path.read_text(encoding="utf-8"))
        return [r for r in rows if valid_snapshot(r)][-8000:]
    except (OSError, ValueError, TypeError):
        return []


def valid_snapshot(row):
    if not isinstance(row, dict) or not numeric(row.get("at")) or not isinstance(row.get("account"), str):
        return False
    windows = row.get("windows")
    if not isinstance(windows, list) or not 1 <= len(windows) <= 2:
        return False
    for w in windows:
        if not isinstance(w, dict) or w.get("slot") not in ("primary", "secondary"):
            return False
        if not numeric(w.get("remaining")) or not 0 <= w["remaining"] <= 100:
            return False
        if any(w.get(k) is not None and (not numeric(w[k]) or w[k] <= 0) for k in ("minutes", "reset")):
            return False
    if len({w["slot"] for w in windows}) != len(windows):
        return False
    for key in ("coupons", "coupon_expiry"):
        if row.get(key) is not None and (not numeric(row[key]) or row[key] < 0):
            return False
    return True


def save_history(path, rows):
    # Widget-owned file only; bounded retention, no raw server response.
    cutoff = time.time() - 8 * 86400
    rows[:] = [r for r in rows if r["at"] >= cutoff][-8000:]
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    # Credit balances are for the live display, not a financial-history database.
    stored = [{k: v for k, v in row.items() if k != "credits"} for row in rows]
    temp.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def change_since(rows, current, since):
    """Observed endpoint change, never billed credits or inferred task attribution."""
    target = headline(current)
    samples = [r for r in rows if since <= r["at"] < current["at"]
               and r.get("account") == current.get("account") and r.get("plan") == current.get("plan")]
    if not samples:
        return None
    points = []
    for r in [*samples, current]:
        w = next((w for w in r["windows"] if w.get("slot") == target["slot"]), None)
        if not w or w.get("reset") != target.get("reset") or w.get("minutes") != target.get("minutes"):
            return {"reset": True}
        points.append((r["at"], w["remaining"]))
    if any(b[1] > a[1] for a, b in zip(points, points[1:])):
        return {"reset": True}
    return {"reset": False, "drop": points[0][1] - points[-1][1], "start": points[0][0],
            "gap": any(b[0] - a[0] > 900 for a, b in zip(points, points[1:]))}


if __name__ == "__main__":
    # Diagnostic prints sanitized quota/credit counts, never raw auth data.
    print(json.dumps(QuotaClient().fetch(), ensure_ascii=False, indent=2))
