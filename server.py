import csv
import json
import os
import sqlite3
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request, send_from_directory


PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
POLICY_UPLOAD_DIR = PROJECT_ROOT / "uploads" / "policies"
POLICY_FILE_PATH = POLICY_UPLOAD_DIR / "请假管理制度.txt"
DATABASE_PATH = PROJECT_ROOT / "database" / "leave_management.db"
UPLOAD_EMPLOYEES_DIR = PROJECT_ROOT / "uploads" / "employees"
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

EMPLOYEE_FIELDS = (
    "employee_id",
    "name",
    "department",
    "annual_leave",
    "sick_leave",
    "personal_leave",
)


def mask_key(api_key):
    if len(api_key) <= 10:
        return "***"
    return f"{api_key[:6]}...{api_key[-4:]}"


def print_key_debug(api_key):
    print(f"Dify API Key: {mask_key(api_key)}", flush=True)
    print(f"Dify API Key length: {len(api_key)}", flush=True)
    print(f"Dify API Key contains ellipsis: {'...' in api_key}", flush=True)


def parse_leave_balance(value, field, row_number):
    try:
        balance = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"第 {row_number} 行 {field} 必须是数字")

    if balance < 0:
        raise ValueError(f"第 {row_number} 行 {field} 不能小于 0")
    return balance


def ensure_employee_upload_schema(connection):
    def primary_key_columns(table_name):
        columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [
            column[1]
            for column in sorted(
                (column for column in columns if column[5]),
                key=lambda column: column[5],
            )
        ]

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            company_id TEXT DEFAULT 'default_company',
            employee_id TEXT,
            name TEXT,
            department TEXT,
            position_level TEXT,
            is_manager INTEGER,
            hire_date TEXT,
            province TEXT,
            supervisor TEXT,
            director TEXT,
            hrbp TEXT,
            PRIMARY KEY (company_id, employee_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS leave_balances (
            company_id TEXT DEFAULT 'default_company',
            employee_id TEXT,
            year INTEGER,
            annual_leave_balance REAL,
            sick_leave_balance REAL,
            personal_leave_balance REAL,
            PRIMARY KEY (company_id, employee_id, year)
        )
        """
    )

    employee_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(employees)")
    }
    leave_balance_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(leave_balances)")
    }

    if "company_id" not in employee_columns:
        connection.execute(
            "ALTER TABLE employees ADD COLUMN company_id TEXT DEFAULT 'default_company'"
        )
    if "company_id" not in leave_balance_columns:
        connection.execute(
            "ALTER TABLE leave_balances ADD COLUMN company_id TEXT DEFAULT 'default_company'"
        )

    if primary_key_columns("employees") != ["company_id", "employee_id"]:
        connection.execute("ALTER TABLE employees RENAME TO employees_old")
        connection.execute(
            """
            CREATE TABLE employees (
                company_id TEXT DEFAULT 'default_company',
                employee_id TEXT,
                name TEXT,
                department TEXT,
                position_level TEXT,
                is_manager INTEGER,
                hire_date TEXT,
                province TEXT,
                supervisor TEXT,
                director TEXT,
                hrbp TEXT,
                PRIMARY KEY (company_id, employee_id)
            )
            """
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO employees (
                company_id,
                employee_id,
                name,
                department,
                position_level,
                is_manager,
                hire_date,
                province,
                supervisor,
                director,
                hrbp
            )
            SELECT
                COALESCE(company_id, 'default_company'),
                employee_id,
                name,
                department,
                position_level,
                is_manager,
                hire_date,
                province,
                supervisor,
                director,
                hrbp
            FROM employees_old
            WHERE employee_id IS NOT NULL
            """
        )
        connection.execute("DROP TABLE employees_old")

    if primary_key_columns("leave_balances") != ["company_id", "employee_id", "year"]:
        connection.execute("ALTER TABLE leave_balances RENAME TO leave_balances_old")
        connection.execute(
            """
            CREATE TABLE leave_balances (
                company_id TEXT DEFAULT 'default_company',
                employee_id TEXT,
                year INTEGER,
                annual_leave_balance REAL,
                sick_leave_balance REAL,
                personal_leave_balance REAL,
                PRIMARY KEY (company_id, employee_id, year)
            )
            """
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO leave_balances (
                company_id,
                employee_id,
                year,
                annual_leave_balance,
                sick_leave_balance,
                personal_leave_balance
            )
            SELECT
                COALESCE(company_id, 'default_company'),
                employee_id,
                year,
                annual_leave_balance,
                sick_leave_balance,
                personal_leave_balance
            FROM leave_balances_old
            WHERE employee_id IS NOT NULL AND year IS NOT NULL
            """
        )
        connection.execute("DROP TABLE leave_balances_old")


def import_employees_csv(csv_path, company_id):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = reader.fieldnames or []
        missing_fields = [
            field for field in EMPLOYEE_FIELDS
            if field not in fieldnames
        ]
        if missing_fields:
            raise ValueError(f"CSV 缺少字段：{', '.join(missing_fields)}")

        rows = []
        for row_number, row in enumerate(reader, start=2):
            employee_id = (row.get("employee_id") or "").strip()
            if not employee_id:
                raise ValueError(f"第 {row_number} 行 employee_id 不能为空")

            rows.append(
                {
                    "employee_id": employee_id,
                    "name": row.get("name", "").strip(),
                    "department": row.get("department", "").strip(),
                    "annual_leave": parse_leave_balance(
                        row.get("annual_leave"), "annual_leave", row_number
                    ),
                    "sick_leave": parse_leave_balance(
                        row.get("sick_leave"), "sick_leave", row_number
                    ),
                    "personal_leave": parse_leave_balance(
                        row.get("personal_leave"), "personal_leave", row_number
                    ),
                }
            )

    inserted = 0
    updated = 0
    with sqlite3.connect(DATABASE_PATH) as connection:
        ensure_employee_upload_schema(connection)

        for row in rows:
            existing = connection.execute(
                """
                SELECT 1
                FROM employees
                WHERE company_id = ? AND employee_id = ?
                """,
                (company_id, row["employee_id"]),
            ).fetchone()

            if existing:
                connection.execute(
                    """
                    UPDATE employees
                    SET name = ?, department = ?
                    WHERE company_id = ? AND employee_id = ?
                    """,
                    (
                        row["name"],
                        row["department"],
                        company_id,
                        row["employee_id"],
                    ),
                )
                updated += 1
            else:
                connection.execute(
                    """
                    INSERT INTO employees (
                        company_id,
                        employee_id,
                        name,
                        department
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        row["employee_id"],
                        row["name"],
                        row["department"],
                    ),
                )
                inserted += 1

            leave_balance_exists = connection.execute(
                """
                SELECT 1
                FROM leave_balances
                WHERE company_id = ? AND employee_id = ? AND year = ?
                """,
                (company_id, row["employee_id"], 2026),
            ).fetchone()

            if leave_balance_exists:
                connection.execute(
                    """
                    UPDATE leave_balances
                    SET annual_leave_balance = ?,
                        sick_leave_balance = ?,
                        personal_leave_balance = ?
                    WHERE company_id = ? AND employee_id = ? AND year = ?
                    """,
                    (
                        row["annual_leave"],
                        row["sick_leave"],
                        row["personal_leave"],
                        company_id,
                        row["employee_id"],
                        2026,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO leave_balances (
                        company_id,
                        employee_id,
                        year,
                        annual_leave_balance,
                        sick_leave_balance,
                        personal_leave_balance
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        row["employee_id"],
                        2026,
                        row["annual_leave"],
                        row["sick_leave"],
                        row["personal_leave"],
                    ),
                )

    return inserted, updated


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


def get_uploaded_policy_path():
    if POLICY_FILE_PATH.exists():
        return POLICY_FILE_PATH

    if not POLICY_UPLOAD_DIR.exists():
        return None

    policy_files = sorted(POLICY_UPLOAD_DIR.glob("*.txt"))
    return policy_files[-1] if policy_files else None


def read_leave_rules(leave_type):
    leave_name = LEAVE_TYPE_NAMES.get(leave_type)
    if leave_name is None:
        return None

    policy_path = get_uploaded_policy_path()
    if policy_path is None:
        return None

    policy_text = policy_path.read_text(encoding="utf-8")
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

    if get_uploaded_policy_path() is None:
        return jsonify(success=False, message="暂无请假制度"), 200

    try:
        rule_info = read_leave_rules(leave_type)
    except UnicodeDecodeError:
        return jsonify(success=False, message="请假管理制度文件必须使用 UTF-8 编码"), 500

    return jsonify(success=True, **rule_info)


@app.post("/api/upload-employees")
def upload_employees():
    uploaded_file = request.files.get("file")
    if uploaded_file is None or not uploaded_file.filename:
        return jsonify(success=False, message="请上传员工 CSV 文件"), 400
    company_id = (request.form.get("company_id") or "default_company").strip()
    if not company_id:
        company_id = "default_company"

    original_name = Path(uploaded_file.filename).name
    if not original_name.lower().endswith(".csv"):
        return jsonify(success=False, message="只支持上传 CSV 文件"), 400

    UPLOAD_EMPLOYEES_DIR.mkdir(parents=True, exist_ok=True)
    saved_path = UPLOAD_EMPLOYEES_DIR / original_name
    uploaded_file.save(saved_path)

    try:
        inserted, updated = import_employees_csv(saved_path, company_id)
    except (UnicodeDecodeError, csv.Error, sqlite3.Error, ValueError) as error:
        return jsonify(success=False, message=str(error)), 400

    return jsonify(
        success=True,
        inserted=inserted,
        updated=updated,
    )


@app.post("/api/upload-policy")
def upload_policy():
    uploaded_file = request.files.get("file")
    if uploaded_file is None or not uploaded_file.filename:
        return jsonify(success=False, message="请上传请假制度 txt 文件"), 400

    original_name = Path(uploaded_file.filename).name
    if not original_name.lower().endswith(".txt"):
        return jsonify(success=False, message="暂时只支持上传 txt 文件"), 400

    POLICY_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    uploaded_file.save(POLICY_FILE_PATH)

    return jsonify(success=True, message="请假制度上传成功")


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
