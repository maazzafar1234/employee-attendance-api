import json
from bson import json_util
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
               [("department", ASCENDING), ("joined_on", ASCENDING)],
             name="ix_employees_department_joined",
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

    # Find the latest open attendance record.
    record = attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None},
            "punch_out": None,
        },
        sort=[("punch_in", DESCENDING)],
    )

    if record is None:
        # Check whether the employee has any attendance record.
        existing_record = attendance_logs.find_one(
            {"emp_code": body.emp_code},
            sort=[("punch_in", DESCENDING)],
        )

        if existing_record is None:
            raise HTTPException(
                status_code=404,
                detail="No attendance record found",
            )

        # The latest record is already closed.
        if existing_record.get("punch_out") is not None:
            raise HTTPException(
                status_code=409,
                detail="Attendance record already punched out",
            )

        raise HTTPException(
            status_code=404,
            detail="No open attendance record found",
        )

    punch_in_time = record["punch_in"]

    if punched_at <= punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    if (punched_at - punch_in_time).total_seconds() > 86400:
        raise HTTPException(
            status_code=422,
            detail="Punch duration cannot exceed 24 hours",
        )

    hours = compute_work_hours(punch_in_time, punched_at)

    result = attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punched_at,
                "work_hours": hours,
                "half_day": hours < 4.50,
                "overtime_minutes": compute_overtime(
                    punched_at,
                    employee["shift_end"],
                    record["date"],
                    employee["shift_start"],
                ),
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record already punched out",
        )

    return attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )





@app.patch("/attendance/{emp_code}/{attendance_date}")
def regularize_attendance(
    emp_code: str,
    attendance_date: str,
    body: AttendancePatchIn,
):
    parse_date(attendance_date)
    employee = require_employee(emp_code)

    record = attendance_logs.find_one({
        "emp_code": emp_code,
        "date": attendance_date,
    })

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("reason")
    regularized_by = changes.pop("regularized_by")

    if not any(
        key in changes for key in ("status", "punch_in", "punch_out")
    ):
        raise HTTPException(
            status_code=422,
            detail="Supply at least one attendance field",
        )

    new_status = changes.get("status", record["status"])

    if new_status is None:
        raise HTTPException(
            status_code=422,
            detail="Status cannot be null",
        )

    if new_status in {"ABSENT", "LEAVE"}:
        if (
            changes.get("punch_in") is not None
            or changes.get("punch_out") is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot have punch times",
            )

        changes.update({
            "status": new_status,
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        })

    else:
        punch_in_raw = changes.get(
            "punch_in", record.get("punch_in")
        )
        punch_out_raw = changes.get(
            "punch_out", record.get("punch_out")
        )

        if punch_in_raw is None:
            raise HTTPException(
                status_code=422,
                detail="Presence status requires punch_in",
            )

        punch_in_time = (
            epoch_ms_to_datetime(punch_in_raw)
            if "punch_in" in changes
            else punch_in_raw
        )

        punch_out_time = (
            epoch_ms_to_datetime(punch_out_raw)
            if "punch_out" in changes and punch_out_raw is not None
            else punch_out_raw
        )

        if attendance_date_for_punch(
            punch_in_time,
            employee["shift_start"],
            employee["shift_end"],
        ) != attendance_date:
            raise HTTPException(
                status_code=422,
                detail="Punch-in does not match attendance date",
            )

        if punch_out_time is not None:
            if punch_out_time <= punch_in_time:
                raise HTTPException(
                    status_code=422,
                    detail="Punch-out must be after punch-in",
                )

            if (
                punch_out_time - punch_in_time
            ).total_seconds() > 86400:
                raise HTTPException(
                    status_code=422,
                    detail="Punch duration cannot exceed 24 hours",
                )

            if attendance_date_for_punch(
                punch_out_time,
                employee["shift_start"],
                employee["shift_end"],
            ) != attendance_date:
                raise HTTPException(
                    status_code=422,
                    detail="Punch-out does not match attendance date",
                )

            hours = compute_work_hours(
                punch_in_time, punch_out_time
            )

            changes.update({
                "punch_out": punch_out_time,
                "work_hours": hours,
                "half_day": hours < 4.50,
                "overtime_minutes": compute_overtime(
                    punch_out_time,
                    employee["shift_end"],
                    attendance_date,
                    employee["shift_start"],
                ),
            })
        else:
            changes.update({
                "punch_out": None,
                "work_hours": None,
                "overtime_minutes": 0,
                "half_day": False,
            })

        changes["punch_in"] = punch_in_time
        changes["late_minutes"] = compute_late_minutes(
            punch_in_time,
            employee["shift_start"],
            attendance_date,
        )

    actual = {
        key: value
        for key, value in changes.items()
        if record.get(key) != value
    }

    if not actual:
        raise HTTPException(
            status_code=422,
            detail="No changes to apply",
        )

    # Required audit-history format.
    history_item = {
        "at": datetime.now(UTC).replace(microsecond=0),
        "by": regularized_by,
        "reason": reason,
        "changes": {
            key: {
                "from": record.get(key),
                "to": value,
            }
            for key, value in actual.items()
        },
    }

    # Compare the old values and append history atomically.
    # $push preserves existing history entries.
    result = attendance_logs.update_one(
        {
            "_id": record["_id"],
            **{
                key: record.get(key)
                for key in actual
            },
        },
        {
            "$set": actual,
            "$push": {"history": history_item},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry",
        )

    updated = attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    # Convert every datetime in the response to epoch milliseconds.
    def convert_datetimes(value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return int(value.timestamp() * 1000)

        if isinstance(value, dict):
            return {
                key: convert_datetimes(item)
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [convert_datetimes(item) for item in value]

        return value

    return convert_datetimes(updated)






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
        if status not in {
            "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
        }:
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
        raise HTTPException(
            422,
            "date_from must be on or before date_to",
        )

    if date_filter:
        query["date"] = date_filter

    total = attendance_logs.count_documents(query)

    items = list(
        attendance_logs.find(query, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    def convert_datetimes(value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return int(value.timestamp() * 1000)

        if isinstance(value, dict):
            # Normalize legacy history field names in the response.
            if "regularized_by" in value:
                value["by"] = value.pop("regularized_by")

            if "changed_at" in value:
                value["at"] = value.pop("changed_at")

            if "old" in value:
                value["from"] = value.pop("old")

            if "new" in value:
                value["to"] = value.pop("new")

            return {
                key: convert_datetimes(item)
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [convert_datetimes(item) for item in value]

        return value

    items = convert_datetimes(items)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }






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



@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=100),
    department: str | None = None,
):
    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1)
    month_end = (
        date(year + 1, 1, 1)
        if month_number == 12
        else date(year, month_number + 1, 1)
    )

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": month_end.isoformat(),
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
            }
        },
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append(
            {"$match": {"employee.department": department}}
        )

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
            }
        },
    ])

    try:
        items = list(db.attendance_logs.aggregate(pipeline))
    except PyMongoError:
        raise HTTPException(503, "Database operation failed")

    return {"month": month, "items": items}



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



@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
):
    if to_date < from_date:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'",
        )

    if (to_date - from_date).days > 91:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(status_code=404, detail="Department not found")

    start_str = from_date.isoformat()
    end_str = to_date.isoformat()

    # $densify requires BSON dates, not YYYY-MM-DD strings.
    start_dt = datetime.combine(from_date, time.min, tzinfo=UTC)
    end_exclusive_dt = datetime.combine(
        to_date + timedelta(days=1),
        time.min,
        tzinfo=UTC,
    )

    pipeline = [
        {
            "$match": {
                "department": department,
                "joined_on": {"$lte": end_str},
            }
        },
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
                                    {"$gte": ["$date", start_str]},
                                    {"$lte": ["$date", end_str]},
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "date": {
                                "$dateFromString": {
                                    "dateString": "$date",
                                    "format": "%Y-%m-%d",
                                    "timezone": "UTC",
                                }
                            },
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1,
                            "_seed": {"$literal": False},
                        }
                    },
                ],
                "as": "logs",
            }
        },
        {
            "$project": {
                "emp_code": 1,
                "joined_on": 1,
                "logs": {
                    "$concatArrays": [
                        "$logs",
                        [
                            {
    "date": start_dt,
    "status": None,
    "half_day": False,
    "late_minutes": 0,
    "_seed": True,
}
                        ],
                    ]
                },
            }
        },
        {"$unwind": "$logs"},
{"$sort": {"logs._seed": 1}},
{
    "$group": {
        "_id": {
            "date": "$logs.date",
            "emp_code": "$emp_code",
        },
                "joined_on": {"$first": "$joined_on"},
                "status": {"$first": "$logs.status"},
                "half_day": {"$first": "$logs.half_day"},
                "late_minutes": {"$first": "$logs.late_minutes"},
            }
        },
        {
            "$group": {
                "_id": "$_id.date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$match": {
                "_id": {
                    "$gte": start_dt,
                    "$lt": end_exclusive_dt,
                }
            }
        },
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_exclusive_dt],
                },
            }
        },
        {
            "$set": {
                "date": {
                    "$dateToString": {
                        "date": "$_id",
                        "format": "%Y-%m-%d",
                        "timezone": "UTC",
                    }
                },
                "is_working_day": {
                    "$lte": [{"$isoDayOfWeek": "$_id"}, 5]
                },
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"trend_date": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {
                                "$lte": ["$joined_on", "$$trend_date"]
                            },
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_result",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_result.count", 0]},
                        0,
                    ]
                }
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
    "$round": [
        {"$divide": ["$present_count", "$headcount"]},
        4,
    ]
},
                        {
                            "$cond": [
                                "$is_working_day",
                                0,
                                None,
                            ]
                        },
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": {
                    "$round": ["$moving_avg_7d", 4]
                },
            }
        },
        {"$sort": {"date": 1}},
    ]

    try:
        items = list(db.employees.aggregate(pipeline))
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    return {
        "department": department,
        "from": start_str,
        "to": end_str,
        "items": items,
    }



@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: str | None = None,
    department: str | None = None,
    month: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    limit: int = Query(10, ge=1, le=100),
):
    allowed_endpoints = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed_endpoints:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Unsupported endpoint",
                "allowed_endpoints": sorted(allowed_endpoints),
            },
        )




    def run_explain(collection_name: str, command: dict) -> dict:
        try:
            result = db.command(
                "explain",
                command,
                verbosity="executionStats",
            )

            # Convert BSON-specific values into JSON-safe values.
            extended_json = json_util.dumps(result)
            plain_result = json.loads(extended_json)

            return plain_result

        except PyMongoError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Could not explain MongoDB query: {str(exc)}",
            )




    if endpoint == "attendance_list":
        query = {}

        if emp_code is not None:
            query["emp_code"] = emp_code

        if department is not None:
            matching_employees = employees.find(
                {"department": department},
                {"emp_code": 1, "_id": 0},
            )
            codes = [item["emp_code"] for item in matching_employees]
            query["emp_code"] = {"$in": codes}

        if date_from is not None:
            query.setdefault("date", {})["$gte"] = date_from.isoformat()

        if date_to is not None:
            query.setdefault("date", {})["$lte"] = date_to.isoformat()

        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from must be on or before date_to",
            )

        command = {
            "find": "attendance_logs",
            "filter": query,
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }

        return {
            "endpoint": endpoint,
            "explain": run_explain("attendance_logs", command),
        }

    if endpoint == "employee_monthly":
        if not emp_code:
            raise HTTPException(422, "emp_code is required")

        if not month:
            raise HTTPException(422, "month is required in YYYY-MM format")

        if not __import__("re").fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])", month
        ):
            raise HTTPException(422, "month must use YYYY-MM format")

        year, month_number = map(int, month.split("-"))
        period_start = date(year, month_number, 1)

        if month_number == 12:
            period_end = date(year + 1, 1, 1)
        else:
            period_end = date(year, month_number + 1, 1)

        require_employee(emp_code)

        command = {
            "find": "attendance_logs",
            "filter": {
                "emp_code": emp_code,
                "date": {
                    "$gte": period_start.isoformat(),
                    "$lt": period_end.isoformat(),
                },
            },
        }

        return {
            "endpoint": endpoint,
            "explain": run_explain("attendance_logs", command),
        }

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required in YYYY-MM format")

        if not __import__("re").fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])", month
        ):
            raise HTTPException(422, "month must use YYYY-MM format")

        year, month_number = map(int, month.split("-"))
        period_start = date(year, month_number, 1)

        if month_number == 12:
            period_end = date(year + 1, 1, 1)
        else:
            period_end = date(year, month_number + 1, 1)

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
                                        {
                                            "$gte": [
                                                "$date",
                                                period_start.isoformat(),
                                            ]
                                        },
                                        {
                                            "$lt": [
                                                "$date",
                                                period_end.isoformat(),
                                            ]
                                        },
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "logs",
                }
            },
        ]

        command = {
            "aggregate": "employees",
            "pipeline": pipeline,
            "cursor": {},
        }

        return {
            "endpoint": endpoint,
            "explain": run_explain("employees", command),
        }

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required in YYYY-MM format")

        if not __import__("re").fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])", month
        ):
            raise HTTPException(422, "month must use YYYY-MM format")

        year, month_number = map(int, month.split("-"))
        period_start = date(year, month_number, 1)

        if month_number == 12:
            period_end = date(year + 1, 1, 1)
        else:
            period_end = date(year, month_number + 1, 1)

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": period_start.isoformat(),
                        "$lt": period_end.isoformat(),
                    },
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                }
            },
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "employee",
                }
            },
            {"$unwind": "$employee"},
        ]

        if department is not None:
            pipeline.append(
                {"$match": {"employee.department": department}}
            )

        pipeline.extend([
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
        ])

        command = {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }

        return {
            "endpoint": endpoint,
            "explain": run_explain("attendance_logs", command),
        }

    if endpoint == "department_trend":
        if not department:
            raise HTTPException(422, "department is required")

        if from_date is None or to_date is None:
            raise HTTPException(
                422,
                "Both 'from' and 'to' dates are required",
            )

        if to_date < from_date:
            raise HTTPException(422, "'to' must be on or after 'from'")

        if (to_date - from_date).days > 91:
            raise HTTPException(422, "Date range cannot exceed 92 days")

        pipeline = [
            {
                "$match": {
                    "department": department,
                    "joined_on": {"$lte": to_date.isoformat()},
                }
            },
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
                                        {
                                            "$gte": [
                                                "$date",
                                                from_date.isoformat(),
                                            ]
                                        },
                                        {
                                            "$lte": [
                                                "$date",
                                                to_date.isoformat(),
                                            ]
                                        },
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "logs",
                }
            },
        ]

        command = {
            "aggregate": "employees",
            "pipeline": pipeline,
            "cursor": {},
        }

        return {
            "endpoint": endpoint,
            "explain": run_explain("employees", command),
        }
