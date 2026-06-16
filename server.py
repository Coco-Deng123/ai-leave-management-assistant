import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request, send_from_directory


PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
RULE_FILE_PATH = PROJECT_ROOT / "docs" / "请假管理制度.txt"
DIFY_API_BASE_URL = os.environ.get(
    "DIFY_API_BASE_URL",
    "https://dify.vongcloud.com/v1",
).rstrip("/")
DIFY_WORKFLOW_URL = f"{DIFY_API_BASE_URL}/workflows/run"

# 本地演示时可以把 Dify API Key 填在这里。
# 更推荐使用环境变量 DIFY_API_KEY；环境变量会优先于这个常量。
DIFY_API_KEY = "app-eOkojW2UU8IE058RmqjZ6bjb"

app = Flask(__name__)

LEAVE_TYPE_NAMES = {
    "annual_leave": "年假",
    "sick_leave": "病假",
    "personal_leave": "事假",
}


def mask_key(api_key):
    if len(api_key) <= 10:
        return "***"
    return f"{api_key[:6]}...{api_key[-4:]}"


def print_key_debug(api_key):
    print(f"Dify API Key: {mask_key(api_key)}", flush=True)
    print(f"Dify API Key length: {len(api_key)}", flush=True)
    print(f"Dify API Key contains ellipsis: {'...' in api_key}", flush=True)


def extract_dify_output(dify_result):
    outputs = dify_result.get("data", {}).get("outputs", {})
    if isinstance(outputs, dict):
        result_text = (
            outputs.get("result")
            or outputs.get("message")
            or outputs.get("text")
            or outputs.get("output")
            or outputs.get("answer")
        )
        return outputs, str(result_text or "").strip()
    return {"result": outputs}, str(outputs or "").strip()


def parse_approval(dify_result):
    outputs, result_text = extract_dify_output(dify_result)
    combined_text = " ".join(
        str(value) for value in outputs.values()
    ).lower()

    approved = None
    if "approved" in outputs and isinstance(outputs["approved"], bool):
        approved = outputs["approved"]
    elif "success" in outputs and isinstance(outputs["success"], bool):
        approved = outputs["success"]
    elif any(word in combined_text for word in ("拒绝", "驳回", "不通过", "余额不足", "rejected", "denied", "failed")):
        approved = False
    elif any(word in combined_text for word in ("✅", "审批通过", "通过", "批准", "已通过", "approved", "succeeded", "success")):
        approved = True

    if approved is True:
        status_text = "审批通过"
        reason = result_text or "假期申请已通过审批。"
    elif approved is False:
        status_text = "审批拒绝"
        reason = result_text or "假期申请未通过审批。"
    else:
        status_text = "审批结果待确认"
        reason = result_text or "Dify 未返回明确的审批通过或拒绝字段。"

    return {
        "approved": approved,
        "status_text": status_text,
        "reason": reason,
        "display_text": result_text,
        "outputs": outputs,
    }


def split_rule_sentence(text):
    parts = []
    for sentence in text.replace("。", "\n").splitlines():
        for clause in sentence.split("，"):
            clause = clause.strip(" 。")
            if clause:
                parts.append(clause)
    return parts


def normalize_rule(rule, leave_type):
    if "需提供医院证明" in rule and "超过2天" in rule:
        return "超过2天需医院证明"
    if "业务冻结期" in rule:
        leave_name = LEAVE_TYPE_NAMES[leave_type]
        return f"业务冻结期不批{leave_name}"
    if "法定节假日前后三天" in rule:
        leave_name = LEAVE_TYPE_NAMES[leave_type]
        return f"法定节假日前后三天原则上不批{leave_name}"
    return rule


def read_leave_rules(leave_type):
    leave_name = LEAVE_TYPE_NAMES.get(leave_type)
    if leave_name is None:
        return None

    policy_text = RULE_FILE_PATH.read_text(encoding="utf-8")
    rules = []

    for line in policy_text.splitlines():
        stripped = line.strip()
        prefix = f"- {leave_name}："
        if stripped.startswith(prefix):
            rule_text = stripped.removeprefix(prefix)
            rules.extend(split_rule_sentence(rule_text))

    if leave_type in {"annual_leave", "personal_leave"}:
        for line in policy_text.splitlines():
            stripped = line.strip()
            if "业务冻结期" in stripped or "法定节假日前后三天" in stripped:
                rules.extend(split_rule_sentence(stripped.lstrip("- ")))

    rules = [normalize_rule(rule, leave_type) for rule in rules]
    rules = [
        rule for rule in rules
        if "当前冻结期" not in rule
        and not rule.startswith("例如：")
        and not rule.startswith("则")
    ]
    deduped_rules = list(dict.fromkeys(rules))

    return {
        "leave_type": leave_type,
        "title": f"{leave_name}规则",
        "rules": deduped_rules,
    }


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/api/leave-rule/<leave_type>")
def leave_rule(leave_type):
    if leave_type not in LEAVE_TYPE_NAMES:
        return jsonify(success=False, message="不支持的请假类型"), 400

    if not RULE_FILE_PATH.exists():
        return jsonify(success=False, message="请假管理制度文件不存在"), 404

    try:
        rule_info = read_leave_rules(leave_type)
    except UnicodeDecodeError:
        return jsonify(success=False, message="请假管理制度文件必须使用 UTF-8 编码"), 500

    return jsonify(success=True, **rule_info)


@app.post("/api/leave-request")
def leave_request():
    api_key = os.environ.get("DIFY_API_KEY", DIFY_API_KEY).strip()
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:].strip()
    if not api_key:
        return jsonify(success=False, message="未配置 DIFY_API_KEY"), 500
    print(f"Dify API URL: {DIFY_WORKFLOW_URL}", flush=True)
    print_key_debug(api_key)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(success=False, message="请求体必须是 JSON"), 400

    required_fields = (
        "employee_id",
        "leave_type",
        "leave_days",
        "start_date",
        "end_date",
    )
    missing_fields = [field for field in required_fields if data.get(field) in (None, "")]
    if missing_fields:
        return (
            jsonify(
                success=False,
                message=f"缺少字段：{', '.join(missing_fields)}",
            ),
            400,
        )

    dify_payload = {
        "inputs": {
            "employee_id": data["employee_id"],
            "leave_type": data["leave_type"],
            "leave_days": data["leave_days"],
            "start_date": data["start_date"],
            "end_date": data["end_date"],
        },
        "response_mode": "blocking",
        "user": "leave-system-user",
    }

    dify_request = Request(
        DIFY_WORKFLOW_URL,
        data=json.dumps(dify_payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "leave-management-assistant/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(dify_request, timeout=60) as response:
            dify_result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(response_body)
        except json.JSONDecodeError:
            details = response_body
        print(f"Dify API error status: {error.code}", flush=True)
        print(f"Dify API error body: {details}", flush=True)
        return jsonify(success=False, message="Dify API 调用失败", details=details), error.code
    except URLError as error:
        return (
            jsonify(
                success=False,
                message="无法连接 Dify API",
                details=str(error.reason),
            ),
            502,
        )
    except (TimeoutError, json.JSONDecodeError) as error:
        return (
            jsonify(
                success=False,
                message="Dify API 响应超时或格式无效",
                details=str(error),
            ),
            502,
        )

    approval = parse_approval(dify_result)
    return jsonify(success=True, approval=approval, result=dify_result)


if __name__ == "__main__":
    startup_key = os.environ.get("DIFY_API_KEY", DIFY_API_KEY).strip()
    if startup_key.lower().startswith("bearer "):
        startup_key = startup_key[7:].strip()
    print(
        f"DIFY_API_KEY loaded: {'yes, ' + mask_key(startup_key) if startup_key else 'no'}",
        flush=True,
    )
    if startup_key:
        print(f"DIFY_API_KEY length: {len(startup_key)}", flush=True)
        print(f"DIFY_API_KEY contains ellipsis: {'...' in startup_key}", flush=True)
    print(f"DIFY_API_BASE_URL: {DIFY_API_BASE_URL}", flush=True)
    app.run(host="0.0.0.0", port=5002, debug=False)
