"""Metadata for changes to a GPT account. Never keep superseded secrets."""
from datetime import datetime, timezone
from uuid import uuid4


FIELD_LABELS = {
    "login": "登录账号",
    "password": "密码",
    "secret": "2FA 密钥",
    "codex": "Codex 授权文件",
    "subscription_started_on": "手填订阅开通日",
    "subscription_expires_on": "手填订阅到期日",
    "automatic_expiry": "订阅到期日",
}
SOURCE_LABELS = {"admin": "管理员", "user": "用户", "system": "系统", "review": "已审核"}
SECRET_FIELDS = {"password", "secret", "codex"}


def describe_change(field, before, after):
    if before == after:
        return ""
    label = FIELD_LABELS[field]
    if field in SECRET_FIELDS:
        if not before:
            return f"添加了{label}（已配置）"
        if not after:
            return f"移除了{label}（未配置）"
        verb = "替换" if field == "codex" else "更新"
        return f"{verb}了{label}（已配置）"
    if field == "automatic_expiry":
        return f"根据 Codex 授权文件的 ID Token 更新了{label}：{before or '未设置'} → {after or '未设置'}"
    if not before:
        return f"添加了{label}：{after}"
    if not after:
        return f"清除了{label}：{before}"
    return f"将{label}由 {before} 修改为 {after}"


def event(actor, descriptions, *, review_of=None):
    return {
        "id": uuid4().hex,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "review" if review_of else actor["source"],
        "actor": actor["name"],
        "actor_id": actor["id"],
        "descriptions": descriptions,
        **({"review_of": review_of} if review_of else {}),
    }


def history_page(entries, query, source, offset, limit):
    reviewed = {item.get("review_of") for item in entries if item["source"] == "review"}
    terms = query.casefold().split()
    matches = []
    total = 0
    for item in reversed(entries):
        if source and item["source"] != source:
            continue
        searchable = " ".join((SOURCE_LABELS[item["source"]], item["source"], item["actor"],
                               *item["descriptions"], item["at"])).casefold()
        if all(term in searchable for term in terms):
            if offset <= total < offset + limit:
                matches.append({**item, "reviewed": item["id"] in reviewed})
            total += 1
    return {"items": matches, "total": total}
