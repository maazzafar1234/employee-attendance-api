# Employee Attendance & Analytics API

A REST API built with **Python, FastAPI, PyMongo, and MongoDB Atlas** to manage employee records, attendance punch-in and punch-out operations, attendance regularization, and department-level analytics.

This project focuses on request validation, attendance-record consistency, concurrent request handling, audit history, database-side analytics, and query-plan inspection.

## 1. Features

- Create and list employees.
- Record attendance punch-in and punch-out.
- List attendance records with pagination and supported filters.
- Regularize attendance and retain an audit history.
- Retrieve monthly employee attendance analytics.
- Retrieve department-wise attendance summaries.
- Retrieve a late-arrival leaderboard.
- Retrieve department attendance trends with a 7-day moving average.
- Inspect selected MongoDB query execution plans.
- Run automated validation tests and dedicated concurrency test scripts.
- Inspect a representative indexed query against a separate 100,000-record benchmark collection.

## 2. Technology Stack

| Component          | Technology                               |
| ------------------ | ---------------------------------------- |
| Language           | Python 3.11+                             |
| API framework      | FastAPI                                  |
| Request validation | Pydantic                                 |
| Database           | MongoDB Atlas / MongoDB                  |
| Database driver    | PyMongo                                  |
| ASGI server        | Uvicorn                                  |
| Tests              | pytest and HTTP client test dependencies |
| Version control    | Git and GitHub                           |

## 3. Project Structure

```text
employee-attendance-api/
├── app/
│   ├── __init__.py
│   └── main.py
├── .gitignore
├── requirements.txt
├── README.md
├── DECISIONS.md
├── REVIEW.md
├── test_basic.py
├── test_concurrency.py
├── test_regularization_concurrency.py
├── performance_test.py
└── benchmark_readonly.py
```

The `.env` file and `.venv/` directory are local development resources and should remain excluded from version control.

## 4. Setup and Installation

### Prerequisites

- Python 3.11 or later.
- A reachable MongoDB deployment, such as MongoDB Atlas.
- Git.
- Network access to install Python packages and connect to the database.

### Step 1: Clone the repository

```powershell
git clone https://github.com/maazzafar1234/employee-attendance-api.git
cd employee-attendance-api
```

### Step 2: Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Step 3: Install application dependencies

```powershell
python -m pip install -r requirements.txt
```

### Step 4: Configure MongoDB

Create a `.env` file in the project root:

```dotenv
MONGO_URI=your_mongodb_connection_string
MONGO_DB=your_database_name
```

Replace the placeholders with your own settings. Do not put real credentials in this document or commit them to Git. Ensure that your Atlas network access and database credentials allow the application to connect.

### Step 5: Start the API

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Step 6: Open API documentation

- Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- OpenAPI schema: [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json)

Swagger UI lists the implemented request parameters, schemas, and response definitions.

## 5. API Endpoints

| Method  | Endpoint                                    | Purpose                              |
| ------- | ------------------------------------------- | ------------------------------------ |
| `GET`   | `/health`                                   | Health check                         |
| `POST`  | `/employees`                                | Create an employee                   |
| `GET`   | `/employees`                                | List employees                       |
| `POST`  | `/attendance/punch-in`                      | Record punch-in                      |
| `POST`  | `/attendance/punch-out`                     | Record punch-out                     |
| `GET`   | `/attendance`                               | List attendance records              |
| `PATCH` | `/attendance/{emp_code}/{attendance_date}`  | Regularize attendance                |
| `GET`   | `/analytics/employees/{emp_code}/monthly`   | Monthly employee analytics           |
| `GET`   | `/analytics/departments/summary`            | Department attendance summary        |
| `GET`   | `/analytics/leaderboard/late`               | Late-arrival leaderboard             |
| `GET`   | `/analytics/departments/{department}/trend` | Department attendance trend          |
| `GET`   | `/admin/explain/{endpoint}`                 | Inspect selected MongoDB query plans |

Consult Swagger UI for the exact parameters and request/response schemas.

## 6. Attendance Rules

The following describes the business rules used in the implementation and should be checked against the original assignment specification before submission.

### Punch-in and punch-out

- Attendance records are identified by employee code and attendance date.
- A compound unique index on employee code and attendance date prevents duplicate attendance documents for the same employee and date.
- Concurrent punch-in requests are protected against duplicate creation.
- Punch-out requires a valid open attendance record.
- The implementation validates duration and uses a conditional update so simultaneous requests cannot both successfully close the same open record.
- Missing records and conflicting operations should return the status codes required by the assignment.

### Dates and shifts

- API instants are accepted as epoch milliseconds and converted to datetime values for database processing.
- Attendance dates and shift times follow the assignment's Asia/Kolkata timezone rules.
- Overnight shifts require the correct shift date: an early-morning punch-in before the shift end may belong to the previous shift date, while the shift-end instant belongs to the following calendar date.

### Late arrival, overtime, and work hours

- A 10-minute grace period applies to late arrivals.
- Late minutes are calculated as whole elapsed minutes, and late status applies only when the grace threshold is exceeded.
- Overtime counts whole minutes after shift end and is recorded only when at least 30 minutes qualify.
- Work hours are rounded to two decimal places using half-up rounding.
- Half-day classification uses the rounded duration and the specified 4.50-hour threshold.

### Status and reporting rules

- `PRESENT`, `WFH`, and `ON_DUTY` are presence statuses for the applicable analytics.
- `ABSENT` and `LEAVE` do not count as presence.
- Monthly present-day calculations exclude weekends, while qualifying late-arrival and overtime totals can include weekend records.
- Department headcount includes eligible employees who have no attendance logs in the reporting period.

## 7. Analytics

Analytics are implemented using MongoDB aggregation pipelines to perform database-side filtering and calculations instead of retrieving all records for record-by-record Python processing.

### Monthly employee analytics

Returns the employee's monthly attendance metrics using the implemented attendance, work-hour, late-arrival, overtime, and rounding rules.

### Department summary

Returns department-level metrics, including headcount and applicable attendance summaries. Eligible employees without attendance logs remain part of headcount.

### Late-arrival leaderboard

Returns the leaderboard according to the endpoint's implemented filters, ranking, and response fields. See Swagger UI and the source code for the exact contract.

### Department trend

Returns daily rows for an inclusive date range, subject to a maximum range of 92 days.

- MongoDB `$densify` fills missing calendar dates.
- `present_count` represents presence-status records for the day, with half-day records contributing `0.5`.
- `late_count` counts records where `late_minutes > 0`.
- `attendance_rate` is `present_count` divided by applicable department headcount, rounded to four decimal places.
- Attendance rate is `null` on non-working days or when headcount is zero.
- A working day with positive headcount and no presence records has a rate of `0.0`.
- `moving_avg_7d` averages the non-null attendance-rate values in the current row and up to six preceding rows within the requested range. It is `null` when that window contains no non-null rates.
- Rates are represented as ratios, not multiplied by 100.

These definitions should be validated against expected examples from the original assignment.

## 8. Database Indexes and Performance

The application creates indexes for employee identification, employee join dates, department and join-date queries, attendance uniqueness, and attendance date filtering.

Selected explain plans previously inspected for attendance listing, monthly employee analytics, department summary, and department trend showed indexed access on the examined query paths. The inspected lookup stages reported zero collection scans.

A separate benchmark collection contains 100,000 synthetic attendance documents. A recorded read-only benchmark query used `IXSCAN`, examined 31 index keys and 31 documents, and returned 31 matching records.

**Important:** This benchmark demonstrates the performance of the specific query tested. It does not establish that every analytics aggregation pipeline meets a 100,000-record performance target.

## 9. Error Handling

The assignment expects appropriate status codes for successful requests, validation failures, missing resources, and conflicts.

| Status | Intended meaning                   |
| ------ | ---------------------------------- |
| `200`  | Successful retrieval or update     |
| `201`  | Successful creation                |
| `404`  | Required resource not found        |
| `409`  | Duplicate or conflicting operation |
| `422`  | Request validation failure         |

Check each endpoint against the original specification, including database failures and cases where an unhandled `500` response must be avoided.

## 10. Testing

### Run syntax compilation and automated tests

```powershell
python -m compileall -q app
python -m pytest -q
git diff --check
```

The latest reported pytest run completed with **6 passed**.

### Run concurrency tests

Start the API in one terminal, then run these scripts in a second terminal:

```powershell
python .\test_concurrency.py
python .\test_regularization_concurrency.py
```

Previously recorded results:

- Concurrent punch-in: one HTTP `201` and nine HTTP `409` responses.
- Concurrent punch-out: one HTTP `200` and nine HTTP `409` responses.
- Concurrent regularization: both audit-history entries were preserved.

These scripts create or modify test records. Use an appropriate test database and preserve existing records.

### Run the read-only benchmark

```powershell
python .\benchmark_readonly.py
```

This script performs read-only queries against the existing performance-test collection.

### Populate benchmark data

`performance_test.py` is intended to populate a dedicated benchmark collection. Review its target database and safeguards before running it. Do not run it against production data or delete existing data without a specific reason.

## 11. Security Notes

- Keep `.env` and credentials out of version control.
- Do not expose connection strings or tokens in source code, logs, or documentation.
- Use appropriate database credentials and network-access restrictions.
- Run data-generating tests only against suitable test collections.
- Restrict diagnostic explain endpoints appropriately in production.

## 12. Supporting Documents

- `DECISIONS.md` explains the reasoning behind the technical approach.
- `REVIEW.md` records the test evidence and the remaining verification limitations.

## 13. Repository

GitHub: [employee-attendance-api](https://github.com/maazzafar1234/employee-attendance-api)
