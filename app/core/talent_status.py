"""Shared labels for the talent workflow and its reports."""
from typing import Literal

Status = Literal["uncontacted", "interviewing", "pending_onboard", "hired", "rejected", "blacklist", "custom"]
STATUS_OPTIONS = [
    {"code": "uncontacted", "label": "待跟进", "color": "#64748B"},
    {"code": "interviewing", "label": "推进中", "color": "#2563B5"},
    {"code": "pending_onboard", "label": "待入职", "color": "#946116"},
    {"code": "hired", "label": "已入职", "color": "#168357"},
    {"code": "rejected", "label": "淘汰", "color": "#B93848"},
    {"code": "blacklist", "label": "黑名单", "color": "#3C4049"},
    {"code": "custom", "label": "自定义", "color": "#7738A8"},
]
