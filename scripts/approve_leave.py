import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "database" / "leave_management.db"

LEAVE_COLUMNS = {
    "annual_leave": "annual_leave_balance",
    "sick_leave": "sick_leave_balance",
    "personal_leave": "personal_leave_balance",
}


def format_days(value):
    return f"{value:g}"


def approve_leave(employee_id, leave_type, requested_days):
    balance_column = LEAVE_COLUMNS.get(leave_type)
    if balance_column is None:
        print(f"不支持的假期类型：{leave_type}")
        return 1

    if requested_days <= 0:
        print("申请天数必须大于 0")
        return 1

    with sqlite3.connect(DATABASE_PATH) as connection:
        balance_row = connection.execute(
            f"""
            SELECT year, {balance_column}
            FROM leave_balances
            WHERE employee_id = ?
            ORDER BY year DESC
            LIMIT 1
            """,
            (employee_id,),
        ).fetchone()

        if balance_row is None:
            print(f"未找到员工 {employee_id} 的假期余额")
            return 1

        year, original_balance = balance_row

        print(f"员工：{employee_id}")
        print(f"假期类型：{leave_type}")
        print()
        print(f"原余额：{format_days(original_balance)}")
        print(f"申请天数：{format_days(requested_days)}")

        if original_balance < requested_days:
            print()
            print("审批失败")
            print("余额不足")
            return 1

        new_balance = original_balance - requested_days
        connection.execute(
            f"""
            UPDATE leave_balances
            SET {balance_column} = ?
            WHERE employee_id = ? AND year = ?
            """,
            (new_balance, employee_id, year),
        )

        print(f"新余额：{format_days(new_balance)}")
        print()
        print("审批通过")

    return 0


def main():
    if len(sys.argv) != 4:
        print(
            "用法：python3 scripts/approve_leave.py "
            "<员工编号> <假期类型> <申请天数>"
        )
        return 1

    employee_id = sys.argv[1]
    leave_type = sys.argv[2]

    try:
        requested_days = float(sys.argv[3])
    except ValueError:
        print("申请天数必须是数字")
        return 1

    return approve_leave(employee_id, leave_type, requested_days)


if __name__ == "__main__":
    raise SystemExit(main())
