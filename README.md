# Employee Attendance & Analytics API

A REST API built with **Python, FastAPI, and MongoDB Atlas** to manage employee attendance, punch-in and punch-out records, attendance regularization, and department analytics.

## Features

- Employee creation and listing
- Attendance punch-in and punch-out
- Attendance listing with pagination and filters
- Attendance regularization with audit history
- Monthly employee attendance analytics
- Department-wise attendance summary
- Late-arrival leaderboard
- Department attendance trends with a 7-day moving average
- MongoDB query execution plans through explain endpoints

## Tech Stack

- Python 3.11+
- FastAPI
- PyMongo
- MongoDB Atlas
- Pydantic
- Uvicorn

## Project Structure

```text
employee-attendance-api/
├── app/
│   ├── __init__.py
│   └── main.py
├── .gitignore
├── requirements.txt
├── README.md
├── REVIEW.md
├── DECISIONS.md
├── test_basic.py
├── test_concurrency.py
├── test_regularization_concurrency.py
├── performance_test.py
└── benchmark_readonly.py
```

## Setup

### 1. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the project root with the MongoDB settings expected by the application:

```dotenv
MONGO_URI=your_mongodb_connection_string
MONGO_DB=your_database_name
```

Replace the placeholder values with your own configuration. Do not commit database credentials or other secrets to version control.

### 4. Start the API

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 5. Open the API documentation

Visit [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## API Endpoints

| Method | Endpoint                                    | Purpose                               |
| ------ | ------------------------------------------- | ------------------------------------- |
| GET    | `/health`                                   | Health check                          |
| POST   | `/employees`                                | Create an employee                    |
| GET    | `/employees`                                | List employees                        |
| POST   | `/attendance/punch-in`                      | Record punch-in                       |
| POST   | `/attendance/punch-out`                     | Record punch-out                      |
| GET    | `/attendance`                               | List attendance records               |
| PATCH  | `/attendance/{emp_code}/{attendance_date}`  | Regularize attendance                 |
| GET    | `/analytics/employees/{emp_code}/monthly`   | Monthly employee analytics            |
| GET    | `/analytics/departments/summary`            | Department summary                    |
| GET    | `/analytics/leaderboard/late`               | Late-arrival leaderboard              |
| GET    | `/analytics/departments/{department}/trend` | Department attendance trend           |
| GET    | `/admin/explain/{endpoint}`                 | Inspect MongoDB query execution plans |

Use Swagger UI to review each endpoint's parameters, request schemas, and response formats.

## Database and Indexing

The application uses MongoDB collections for employees and attendance logs. Unique and compound indexes support employee identification, attendance uniqueness, date filtering, and analytics queries.

The explain endpoints expose MongoDB execution statistics to help inspect index usage and collection scans. An index being used by one query does not establish that every analytics pipeline meets the performance target.

## Error Handling

The API uses HTTP status codes for successful requests, validation failures, missing resources, and conflicting attendance operations. Confirm endpoint-specific status codes and edge cases against the assignment requirements before submission.

## Testing

Install the test dependencies:

```powershell
python -m pip install pytest httpx2
```

Run the automated tests:

```powershell
python -m pytest -q
```

For concurrency tests, start the API in a separate terminal with MongoDB configured, then run:

```powershell
python .\test_concurrency.py
python .\test_regularization_concurrency.py
```

These tests create or modify dedicated test records. Run them against a suitable test database and preserve existing records.

To inspect the indexed benchmark query against the existing performance-test collection, run:

```powershell
python .\benchmark_readonly.py
```

The benchmark script performs read-only queries. Its results validate the specific query tested; they do not independently establish the performance of every analytics pipeline at 100,000 records.

`performance_test.py` is intended to populate a dedicated benchmark collection. Review its safeguards and target database before running it. Do not run it against production data.

## Notes

- Configure your own MongoDB connection string through environment variables.
- Keep `.env` and `.venv/` out of version control.
- Review `DECISIONS.md` for implementation choices and `REVIEW.md` for verification status.
- Verify all assignment-specific business rules, analytics pipelines, concurrency requirements, and performance targets before final submission.
