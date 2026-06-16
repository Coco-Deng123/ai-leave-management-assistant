import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "database" / "leave_management.db"


def test_queries():
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row

        employee = connection.execute(
            "SELECT * FROM employees WHERE employee_id = ?",
            ("E001",),
        ).fetchone()
        leave_balance = connection.execute(
            """
            SELECT *
            FROM leave_balances
            WHERE employee_id = ? AND year = ?
            """,
            ("E001", 2025),
        ).fetchone()

    print("E001 employee:")
    print(dict(employee) if employee else "Not found")
    print("E001 2025 leave balance:")
    print(dict(leave_balance) if leave_balance else "Not found")


if __name__ == "__main__":
    test_queries()
