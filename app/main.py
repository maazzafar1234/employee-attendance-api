
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

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB")
if not MONGO_URI or not MONGO_DB:
    raise RuntimeError("MONGO_URI and MONGO_DB must be set in .env")

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000

client = MongoClient(MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=10000)
db = client[MONGO_DB]
employees = db["employees"]
attendance_logs = db["attendance_logs"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        client.admin.command("ping")
        employees.create_index(
            [("emp_code", ASCENDING)], unique=True,
            name="uq_employees_emp_code",
        )
        attendance_logs.create_index(
            [("emp_code", ASCENDING), ("date", ASCENDING)],
            unique=True, name="uq_attendance_emp_code_date",
        )
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


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise HTTPException(422, "Date must use YYYY-MM-DD format")


def parse_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
        if parsed.tzinfo is not None or len(value) != 5:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise HTTPException(422, "Time must use HH:MM format")


def epoch_ms_to_datetime(value: int) -> datetime:
    if type(value) is not int or not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
        raise HTTPException(422, "Invalid epoch-millisecond timestamp")
    return datetime.fromtimestamp(value / 1000, UTC).replace(microsecond=0)


def attendance_date_for_punch(
    punch_in: datetime, shift_start: str, shift_end: str
) -> str:
    local = punch_in.astimezone(IST)
    start, end = parse_time(shift_start), parse_time(shift_end)
    if end <= start and local.time().replace(tzinfo=None) < end:
        local_date = local.date() - timedelta(days=1)
    else:
        local_date = local.date()
    return local_date.isoformat()


def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    attendance_date: str,
) -> int:
    shift_start_dt = datetime.combine(
        parse_date(attendance_date),
        parse_time(shift_start),
        tzinfo=IST,
    )
    elapsed = int(
        (punch_in.astimezone(IST) - shift_start_dt).total_seconds() // 60
    )
    return elapsed if elapsed > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = seconds / Decimal("3600")
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(
    punch_out: datetime, shift_end: str, attendance_date: str,
    shift_start: str,
) -> int:
    start, end = parse_time(shift_start), parse_time(shift_end)
    end_date = parse_date(attendance_date)
    if end <= start:
        end_date += timedelta(days=1)
    shift_end_dt = datetime.combine(end_date, end, tzinfo=IST)
    minutes = int(
        (punch_out.astimezone(IST) - shift_end_dt).total_seconds() // 60
    )
    return minutes if minutes >= 30 else 0


def require_employee(emp_code: str) -> dict:
    employee = employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    return employee


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
    def validate_times(cls, value: str) -> str:
        parse_time(value)
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        parse_date(value)
        return value

    @field_validator("shift_end")
    @classmethod
    def different_shift_times(cls, value: str, info):
        if info.data.get("shift_start") == value:
            raise ValueError("Shift start and end cannot be equal")
        return value


class PunchInIn(BaseModel):
    emp_code: str = Field(min_length=1)
    punched_at: int | None = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None:
            epoch_ms_to_datetime(value)
        return value


class PunchOutIn(BaseModel):
    emp_code: str = Field(min_length=1)
    punched_at: int | None = None

    @field_validator("punched_at")
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None:
            epoch_ms_to_datetime(value)
        return value


class AttendancePatchIn(BaseModel):
    status: Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"] | None = None
    punch_in: int | None = None
    punch_out: int | None = None
    reason: str = Field(min_length=1)
    regularized_by: str = Field(min_length=1)

    @field_validator("punch_in", "punch_out")
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None:
            epoch_ms_to_datetime(value)
        return value


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "Database unavailable")


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
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(
    department: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {"department": department} if department is not None else {}
    total = employees.count_documents(query)
    items = list(
        employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = require_employee(body.emp_code)
    punched_at = (
        datetime.now(UTC).replace(microsecond=0)
        if body.punched_at is None
        else epoch_ms_to_datetime(body.punched_at)
    )
    attendance_date = attendance_date_for_punch(
        punched_at, employee["shift_start"], employee["shift_end"]
    )
    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": punched_at,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            punched_at, employee["shift_start"], attendance_date
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
    doc.pop("_id", None)
    return doc


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = require_employee(body.emp_code)
    punched_at = (
        datetime.now(UTC).replace(microsecond=0)
        if body.punched_at is None
        else epoch_ms_to_datetime(body.punched_at)
    )
    record = attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None},
            "punch_out": None,
        },
        sort=[("punch_in", DESCENDING)],
    )
    if record is None:
        raise HTTPException(404, "No open attendance record found")
    punch_in_time = record["punch_in"]
    if punched_at <= punch_in_time:
        raise HTTPException(422, "Punch-out must be after punch-in")
    if (punched_at - punch_in_time).total_seconds() > 86400:
        raise HTTPException(422, "Punch duration cannot exceed 24 hours")

    hours = compute_work_hours(punch_in_time, punched_at)
    result = attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": punched_at,
            "work_hours": hours,
            "half_day": hours < 4.50,
            "overtime_minutes": compute_overtime(
                punched_at, employee["shift_end"], record["date"],
                employee["shift_start"],
            ),
        }},
    )
    if result.modified_count != 1:
        raise HTTPException(409, "Attendance record already punched out")
    return attendance_logs.find_one({"_id": record["_id"]}, {"_id": 0})


@app.patch("/attendance/{emp_code}/{attendance_date}")
def regularize_attendance(
    emp_code: str, attendance_date: str, body: AttendancePatchIn
):
    parse_date(attendance_date)
    employee = require_employee(emp_code)
    record = attendance_logs.find_one({
        "emp_code": emp_code, "date": attendance_date
    })
    if record is None:
        raise HTTPException(404, "Attendance record not found")

    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("reason")
    regularized_by = changes.pop("regularized_by")
    if not any(k in changes for k in ("status", "punch_in", "punch_out")):
        raise HTTPException(422, "Supply at least one attendance field")

    new_status = changes.get("status", record["status"])
    if new_status in {"ABSENT", "LEAVE"}:
        if changes.get("punch_in") is not None or changes.get("punch_out") is not None:
            raise HTTPException(422, "ABSENT and LEAVE cannot have punch times")
        changes.update({
            "punch_in": None, "punch_out": None, "work_hours": None,
            "late_minutes": 0, "overtime_minutes": 0, "half_day": False,
        })
    else:
        punch_in_raw = changes.get("punch_in", record.get("punch_in"))
        punch_out_raw = changes.get("punch_out", record.get("punch_out"))
        if punch_in_raw is None:
            raise HTTPException(422, "Presence status requires punch_in")

        punch_in_time = (
            epoch_ms_to_datetime(punch_in_raw)
            if "punch_in" in changes else punch_in_raw
        )
        punch_out_time = (
            epoch_ms_to_datetime(punch_out_raw)
            if "punch_out" in changes and punch_out_raw is not None
            else punch_out_raw
        )
        if attendance_date_for_punch(
            punch_in_time, employee["shift_start"], employee["shift_end"]
        ) != attendance_date:
            raise HTTPException(422, "Punch-in does not match attendance date")

        changes["punch_in"] = punch_in_time
        changes["late_minutes"] = compute_late_minutes(
            punch_in_time, employee["shift_start"], attendance_date
        )
        if punch_out_time is None:
            changes.update({
                "punch_out": None, "work_hours": None,
                "overtime_minutes": 0, "half_day": False,
            })
        else:
            if punch_out_time <= punch_in_time:
                raise HTTPException(422, "Punch-out must be after punch-in")
            if (punch_out_time - punch_in_time).total_seconds() > 86400:
                raise HTTPException(422, "Punch duration cannot exceed 24 hours")
            hours = compute_work_hours(punch_in_time, punch_out_time)
            changes.update({
                "punch_out": punch_out_time,
                "work_hours": hours,
                "half_day": hours < 4.50,
                "overtime_minutes": compute_overtime(
                    punch_out_time, employee["shift_end"], attendance_date,
                    employee["shift_start"],
                ),
            })

    actual = {k: v for k, v in changes.items() if record.get(k) != v}
    if not actual:
        raise HTTPException(422, "No changes to apply")

    history_item = {
        "regularized_by": regularized_by,
        "reason": reason,
        "changed_at": datetime.now(UTC).replace(microsecond=0),
        "changes": {
            k: {"old": record.get(k), "new": v} for k, v in actual.items()
        },
    }
    result = attendance_logs.update_one(
        {"_id": record["_id"], **{k: record.get(k) for k in actual}},
        {"$set": actual, "$push": {"history": history_item}},
    )
    if result.modified_count != 1:
        raise HTTPException(409, "Attendance record changed concurrently; retry")
    return attendance_logs.find_one({"_id": record["_id"]}, {"_id": 0})


@app.get("/attendance")
def list_attendance(
    emp_code: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if status is not None:
        if status not in {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}:
            raise HTTPException(422, "Invalid attendance status")
        query["status"] = status
    date_filter = {}
    if date_from is not None:
        parse_date(date_from)
        date_filter["$gte"] = date_from
    if date_to is not None:
        parse_date(date_to)
        date_filter["$lte"] = date_to
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")
    if date_filter:
        query["date"] = date_filter

    total = attendance_logs.count_documents(query)
    items = list(
        attendance_logs.find(query, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}



@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    employee = require_employee(emp_code)

    year, month_number = map(int, month.split("-"))
    period_start = date(year, month_number, 1)
    if month_number == 12:
        period_end = date(year + 1, 1, 1)
    else:
        period_end = date(year, month_number + 1, 1)

    joined_on = parse_date(employee["joined_on"])
    effective_start = max(period_start, joined_on)

    working_days = sum(
        1
        for day_offset in range((period_end - effective_start).days)
        if (effective_start + timedelta(days=day_offset)).weekday() < 5
    )

    records = list(
        attendance_logs.find({
            "emp_code": emp_code,
            "date": {
                "$gte": effective_start.isoformat(),
                "$lt": period_end.isoformat(),
            },
        })
    )

    present_days = 0.0
    leave_days = 0
    late_count = 0
    total_late_minutes = 0
    total_overtime_minutes = 0

    for record in records:
        record_date = parse_date(record["date"])
        status = record["status"]

        if status == "LEAVE":
            if record_date.weekday() < 5:
                leave_days += 1

        if status in {"PRESENT", "WFH", "ON_DUTY"}:
            if record_date.weekday() < 5:
                present_days += 0.5 if record.get("half_day", False) else 1.0

        late_minutes = int(record.get("late_minutes") or 0)
        if late_minutes > 0:
            late_count += 1
            total_late_minutes += late_minutes

        total_overtime_minutes += int(record.get("overtime_minutes") or 0)

    attendance_pct = (
        float(
            (Decimal(str(present_days)) / Decimal(working_days) * 100)
            .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
        if working_days > 0
        else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }


@app.get("/")
def root():
    return {
        "message": "Employee Attendance & Analytics API",
        "docs": "/docs",
        "health": "/health"
    }



@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
):
    year, month_number = map(int, month.split("-"))
    period_start = date(year, month_number, 1)

    period_end = (
        date(year + 1, 1, 1)
        if month_number == 12
        else date(year, month_number + 1, 1)
    )

    pipeline = [
        {"$match": {"joined_on": {"$lt": period_end.isoformat()}}},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", period_start.isoformat()]},
                                    {"$lt": ["$date", period_end.isoformat()]},
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "date": 1,
                            "status": 1,
                            "work_hours": 1,
                            "half_day": 1,
                            "late_minutes": 1,
                        }
                    },
                ],
                "as": "logs",
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$addFields": {
                "is_present": {
                    "$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]
                },
                "is_weekday": {
                    "$and": [
                        {"$ne": [{"$ifNull": ["$logs.date", None]}, None]},
                        {
                            "$lte": [
                                {
                                    "$dayOfWeek": {
                                        "$dateFromString": {
                                            "dateString": "$logs.date"
                                        }
                                    }
                                },
                                6,
                            ]
                        },
                        {
                            "$gte": [
                                {
                                    "$dayOfWeek": {
                                        "$dateFromString": {
                                            "dateString": "$logs.date"
                                        }
                                    }
                                },
                                2,
                            ]
                        },
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": {"department": "$department", "emp_code": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$and": ["$is_present", "$is_weekday"]},
                            {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1]},
                            0,
                        ]
                    }
                },
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_present",
                                    {"$ne": ["$logs.work_hours", None]},
                                ]
                            },
                            "$logs.work_hours",
                            0,
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_present",
                                    {"$ne": ["$logs.work_hours", None]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$present_days"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "work_hours_sum": {"$sum": "$work_hours_sum"},
                "work_hours_count": {"$sum": "$work_hours_count"},
            }
        },
        {"$sort": {"_id": 1}},
    ]

    try:
        results = list(employees.aggregate(pipeline))
    except PyMongoError:
        raise HTTPException(503, "Database operation failed")

    return {
        "month": month,
        "items": [
            {
                "department": item["_id"],
                "headcount": item["headcount"],
                "present_days": item["present_days"],
                "avg_work_hours": (
                    float(
                        (Decimal(str(item["work_hours_sum"]))
                         / Decimal(item["work_hours_count"]))
                        .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    )
                    if item["work_hours_count"]
                    else None
                ),
                "late_count": item["late_count"],
                "total_late_minutes": item["total_late_minutes"],
                "leave_count": item["leave_count"],
                "on_duty_count": item["on_duty_count"],
            }
            for item in results
            if item["headcount"] > 0
        ],
    }
