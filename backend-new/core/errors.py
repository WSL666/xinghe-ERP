"""统一错误码定义。"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    API_KEY_INVALID = "E001"
    RATE_LIMITED = "E002"
    MODEL_TIMEOUT = "E003"
    INSUFFICIENT_QUOTA = "E004"
    NETWORK_ERROR = "E005"
    PARSE_ERROR = "E006"
    INTERNAL_ERROR = "E007"


HTTP_TO_ERROR = {
    401: ErrorCode.API_KEY_INVALID,
    403: ErrorCode.API_KEY_INVALID,
    429: ErrorCode.RATE_LIMITED,
    402: ErrorCode.INSUFFICIENT_QUOTA,
}


ERROR_MESSAGES = {
    ErrorCode.API_KEY_INVALID: "API Key 无效，请在设置中检查配置",
    ErrorCode.RATE_LIMITED: "请求太频繁，请稍后重试",
    ErrorCode.MODEL_TIMEOUT: "模型响应超时，请重试",
    ErrorCode.INSUFFICIENT_QUOTA: "模型额度不足，请充值",
    ErrorCode.NETWORK_ERROR: "网络错误，请检查连接",
    ErrorCode.PARSE_ERROR: "结果解析失败",
    ErrorCode.INTERNAL_ERROR: "内部错误",
}
