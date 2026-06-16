import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "database" / "leave_management.db"
DATABASE_PATH = Path(os.environ.get("LEAVE_DATABASE_PATH", DEFAULT_DATABASE_PATH))
LEAVE_YEAR = 2025

LEAVE_COLUMNS = {
    "annual_leave": "annual_leave_balance",
    "sick_leave": "sick_leave_balance",
    "personal_leave": "personal_leave_balance",
}

app = Flask(__name__)


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


@app.get("/employee/<employee_id>")
def get_employee(employee_id):
    with get_connection() as connection:
        employee = connection.execute(
            "SELECT * FROM employees WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()

    if employee is None:
        return jsonify(success=False, message="未找到员工"), 404

    return jsonify(success=True, employee=dict(employee))


@app.get("/leave-balance/<employee_id>")
def get_leave_balance(employee_id):
    with get_connection() as connection:
        balance = connection.execute(
            """
            SELECT *
            FROM leave_balances
            WHERE employee_id = ? AND year = ?
            """,
            (employee_id, LEAVE_YEAR),
        ).fetchone()

    if balance is None:
        return jsonify(success=False, message="未找到员工的 2025 年假期余额"), 404

    return jsonify(success=True, leave_balance=dict(balance))


@app.post("/approve-leave")
def approve_leave():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(success=False, message="请求体必须是 JSON 对象"), 400

    employee_id = payload.get("employee_id")
    leave_type = payload.get("leave_type")
    leave_days = payload.get("leave_days")

    if not isinstance(employee_id, str) or not employee_id.strip():
        return jsonify(success=False, message="employee_id 不能为空"), 400

    balance_column = LEAVE_COLUMNS.get(leave_type)
    if balance_column is None:
        return jsonify(success=False, message="不支持的 leave_type"), 400

    if isinstance(leave_days, bool):
        return jsonify(success=False, message="leave_days 必须是大于 0 的数字"), 400

    try:
        leave_days = float(leave_days)
    except (TypeError, ValueError):
        return jsonify(success=False, message="leave_days 必须是大于 0 的数字"), 400

    if leave_days <= 0:
        return jsonify(success=False, message="leave_days 必须是大于 0 的数字"), 400

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        balance = connection.execute(
            f"""
            SELECT {balance_column}
            FROM leave_balances
            WHERE employee_id = ? AND year = ?
            """,
            (employee_id.strip(), LEAVE_YEAR),
        ).fetchone()

        if balance is None:
            connection.rollback()
            return (
                jsonify(
                    success=False,
                    message="未找到员工的 2025 年假期余额",
                    old_balance=None,
                    new_balance=None,
                ),
                404,
            )

        old_balance = float(balance[balance_column])
        if old_balance < leave_days:
            connection.rollback()
            return (
                jsonify(
                    success=False,
                    message="余额不足",
                    old_balance=old_balance,
                    new_balance=old_balance,
                ),
                400,
            )

        new_balance = old_balance - leave_days
        connection.execute(
            f"""
            UPDATE leave_balances
            SET {balance_column} = ?
            WHERE employee_id = ? AND year = ?
            """,
            (new_balance, employee_id.strip(), LEAVE_YEAR),
        )
        connection.commit()

    return jsonify(
        success=True,
        message="审批通过",
        old_balance=old_balance,
        new_balance=new_balance,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
