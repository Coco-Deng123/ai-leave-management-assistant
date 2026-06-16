import csv
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_DIR = PROJECT_ROOT / "database"
DATABASE_PATH = DATABASE_DIR / "leave_management.db"


def read_csv(filename):
    with (DATA_DIR / filename).open(
        "r", encoding="utf-8-sig", newline=""
    ) as csv_file:
        return list(csv.DictReader(csv_file))


def import_data():
    employees = read_csv("employees.csv")
    leave_balances = read_csv("leave_balances.csv")
    departments = read_csv("departments.csv")

    DATABASE_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DATABASE_PATH) as connection:
        cursor = connection.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS employees (
                employee_id TEXT PRIMARY KEY,
                name TEXT,
                department TEXT,
                position_level TEXT,
                is_manager INTEGER,
                hire_date TEXT,
                province TEXT,
                supervisor TEXT,
                director TEXT,
                hrbp TEXT
            );

            CREATE TABLE IF NOT EXISTS leave_balances (
                employee_id TEXT,
                year INTEGER,
                annual_leave_balance REAL,
                sick_leave_balance REAL,
                personal_leave_balance REAL,
                PRIMARY KEY (employee_id, year)
            );

            CREATE TABLE IF NOT EXISTS departments (
                department TEXT PRIMARY KEY,
                total_count INTEGER
            );

            DELETE FROM employees;
            DELETE FROM leave_balances;
            DELETE FROM departments;
            """
        )

        cursor.executemany(
            """
            INSERT INTO employees (
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["employee_id"],
                    row["name"],
                    row["department"],
                    row["position_level"],
                    1 if row["is_manager"].strip().lower() == "true" else 0,
                    row["hire_date"],
                    row["province"],
                    row["supervisor"],
                    row["director"],
                    row["hrbp"],
                )
                for row in employees
            ],
        )

        cursor.executemany(
            """
            INSERT INTO leave_balances (
                employee_id,
                year,
                annual_leave_balance,
                sick_leave_balance,
                personal_leave_balance
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    row["employee_id"],
                    int(row["year"]),
                    float(row["annual_leave_balance"]),
                    float(row["sick_leave_balance"]),
                    float(row["personal_leave_balance"]),
                )
                for row in leave_balances
            ],
        )

        cursor.executemany(
            """
            INSERT INTO departments (department, total_count)
            VALUES (?, ?)
            """,
            [
                (row["department"], int(row["total_count"]))
                for row in departments
            ],
        )

    print(f"Imported {len(employees)} employees")
    print(f"Imported {len(leave_balances)} leave balances")
    print(f"Imported {len(departments)} departments")
    print(f"Database: {DATABASE_PATH}")


if __name__ == "__main__":
    import_data()
