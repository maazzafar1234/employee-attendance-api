
"""
Employee Attendance & Analytics API

Run: uvicorn app.main:app --port 8000
Environment: MONGO_URI, MONGO_DB
"""

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB")

if not MONGO_URI or not MONGO_DB:
    raise RuntimeError("MONGO_URI and MONGO_DB must be configured in .env")

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000

client = MongoClient(
    MONGO_URI,
    tz_aware=True,
    serverSelectionTimeoutMS=10_000,
)

db = client[MONGO_DB]
employees = db["employees"]
attendance_logs = db["attendance_logs"]


# ---------------------------------------------------------------------------
# Startup and shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        client.admin.command("ping")

        employees.create_index(
            [("emp_code", ASCENDING)],
            unique=True,
            name="uq_employees_emp_code",
        )

        attendance_logs.create_index(
            [("emp_code", ASCENDING), ("date", ASCENDING)],
            unique=True,
            name="uq_attendance_emp_code_date",
        )

        # Supports the attendance list's date/status ordering.
        attendance_logs.create_index(
            [("date", DESCENDING), ("emp_code", ASCENDING)],
            name="ix_attendance_date_emp_code",
        )
    except Exception:
        client.close()
        raise

    yield
    client.close()


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(422, "Date must use YYYY-MM-DD format")


def parse_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
        if parsed.tzinfo is not None:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise HTTPException(422, "Time must use HH:MM format")


def epoch_ms_to_datetime(value: int) -> datetime:
    if type(value) is not int:
        raise HTTPException(422, "Timestamp must be an integer in milliseconds")

    if not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
        raise HTTPException(422, "Timestamp is outside the allowed range")

    return datetime.fromtimestamp(value / 1000, tz=UTC).replace(
        microsecond=0
    )


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise HTTPException(422, "Datetime must include a timezone")

    return value.astimezone(UTC).replace(microsecond=0)


def attendance_date_for_punch(
    punch_in: datetime,
    shift_start: str,
    shift_end: str,
) -> str:
    local_punch = punch_in.astimezone(IST)
    local_date = local_punch.date()

    start = parse_time(shift_start)
    end = parse_time(shift_end)
    local_clock = local_punch.time().replace(tzinfo=None)

    # For overnight shifts, early punches before shift_end belong
    # to the previous attendance date.
    if end <= start and local_clock < end:
        local_date -= timedelta(days=1)

    return local_date.isoformat()


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    local_punch = punch_in.astimezone(IST)
    start_time = parse_time(shift_start)

    shift_start_dt = datetime.combine(
        local_punch.date(), start_time, tzinfo=IST
    )

    elapsed_seconds = (local_punch - shift_start_dt).total_seconds()
    elapsed_minutes = int(elapsed_seconds // 60)

    # Late only when strictly more than 10 minutes after shift start.
    return elapsed_minutes if elapsed_minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = (punch_out - punch_in).total_seconds()
    hours = Decimal(str(seconds)) / Decimal("3600")

    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    attendance_date: str,
    shift_start: str,
) -> int:
    end_time = parse_time(shift_end)
    start_time = parse_time(shift_start)
    work_date = parse_date(attendance_date)

    end_date = work_date
    if end_time <= start_time:
        end_date += timedelta(days=1)

    shift_end_dt = datetime.combine(end_date, end_time, tzinfo=IST)
    local_punch_out = punch_out.astimezone(IST)

    elapsed_minutes = int(
        (local_punch_out - shift_end_dt).total_seconds() // 60
    )

    # Overtime is counted only when at least 30 whole minutes are reached.
    return elapsed_minutes if elapsed_minutes >= 30 else 0


def public_document(document: dict) -> dict:
    """Return a document without MongoDB's internal identifier."""
    document.pop("_id", None)
    return document


def validate_page(page: int, page_size: int) -> None:
    if page < 1:
        raise HTTPException(422, "page must be at least 1")

    if not 1 <= page_size <= 100:
        raise HTTPException(422, "page_size must be between 1 and 100")


def require_employee(emp_code: str) -> dict:
    employee = employees.find_one({"emp_code": emp_code})

    if employee is None:
        raise HTTPException(404, "Employee not found")

    return employee


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class EmployeeIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    emp_code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    email: str = Field(min_length=1)
    department: str = Field(min_length=1)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value: str) -> str:
        parse_time(value)
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        parse_date(value)
        return value

    @field_validator("shift_end")
    @classmethod
    def validate_shift_pair(cls, value: str, info):
        start_value = info.data.get("shift_start")
        if start_value is not None and value == start_value:
            raise ValueError("shift_start and shift_end cannot be equal")
        return value


class PunchInIn(BaseModel):
    emp_code: str = Field(min_length=1)
    punched_at: int | None = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def validate_timestamp(cls, value: int | None) -> int | None:
        if value is not None:
            if type(value) is not int:
                raise ValueError("punched_at must be an integer")
            if not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
                raise ValueError("punched_at is outside the allowed range")
        return value


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "Database unavailable")


# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC).replace(microsecond=0)

    try:
        employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    except PyMongoError:
        raise HTTPException(503, "Database operation failed")

    return doc


@app.get("/employees")
def list_employees(
    department: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    validate_page(page, page_size)

    query = {}
    if department is not None:
        query["department"] = department

    total = employees.count_documents(query)
    skip = (page - 1) * page_size

    cursor = (
        employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": list(cursor),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# Attendance punch-in
# ---------------------------------------------------------------------------

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = require_employee(body.emp_code)

    if body.punched_at is None:
        punch_time = datetime.now(UTC).replace(microsecond=0)
    else:
        punch_time = epoch_ms_to_datetime(body.punched_at)

    attendance_date = attendance_date_for_punch(
        punch_time,
        employee["shift_start"],
        employee["shift_end"],
    )

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": punch_time,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            punch_time, employee["shift_start"]
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "Already punched in for this attendance date")
    except PyMongoError:
        raise HTTPException(503, "Database operation failed")

    return doc


# ---------------------------------------------------------------------------
# Attendance list
# ---------------------------------------------------------------------------

@app.get("/attendance")
def list_attendance(
    emp_code: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    validate_page(page, page_size)

    query = {}

    if emp_code is not None:
        query["emp_code"] = emp_code

    if status is not None:
        allowed_statuses = {
            "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
        }
        if status not in allowed_statuses:
            raise HTTPException(422, "Invalid attendance status")
        query["status"] = status

    date_query = {}
    if date_from is not None:
        parse_date(date_from)
        date_query["$gte"] = date_from

    if date_to is not None:
        parse_date(date_to)
        date_query["$lte"] = date_to

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")

    if date_query:
        query["date"] = date_query

    total = attendance_logs.count_documents(query)
    skip = (page - 1) * page_size

    cursor = (
        attendance_logs.find(query, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": list(cursor),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# Remaining endpoints to implement in subsequent steps
# ---------------------------------------------------------------------------
# POST  /attendance/punch-out
# PATCH /attendance/{emp_code}/{date}
# GET   /analytics/employees/{emp_code}/monthly
# GET   /analytics/departments/summary
# GET   /analytics/leaderboard/late
# GET   /analytics/departments/{department}/trend
# GET   /admin/explain/{endpoint}
