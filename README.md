<<<<<<< HEAD
# Employee Attendance & Analytics API

A REST API built using **Python, FastAPI, and MongoDB Atlas** to manage employee attendance, punch-in and punch-out records, attendance regularization, and department analytics.

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
├── .env
├── .gitignore
├── requirements.txt
├── README.md
├── REVIEW.md
└── DECISIONS.md
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

Create a `.env` file in the project root and configure the MongoDB connection string and database name expected by the application.

Do not commit database credentials or other secrets to version control.

### 4. Start the API

```powershell
python -m uvicorn app.main:app --port 8000
```

### 5. Open the API documentation

Visit:

`http://127.0.0.1:8000/docs`

## API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/employees` | Create an employee |
| GET | `/employees` | List employees |
| POST | `/attendance/punch-in` | Record punch-in |
| POST | `/attendance/punch-out` | Record punch-out |
| GET | `/attendance` | List attendance records |
| PATCH | `/attendance/{emp_code}/{date}` | Regularize attendance |
| GET | `/analytics/employees/{emp_code}/monthly` | Monthly employee analytics |
| GET | `/analytics/departments/summary` | Department summary |
| GET | `/analytics/leaderboard/late` | Late-arrival leaderboard |
| GET | `/analytics/departments/{department}/trend` | Department attendance trend |
| GET | `/admin/explain/{endpoint}` | Inspect MongoDB query execution plans |

Use Swagger UI to review request parameters and schemas for each endpoint.

## Database and Indexing

The application uses MongoDB collections for employees and attendance logs. Unique and compound indexes support employee identification, attendance uniqueness, date filtering, and analytics queries.

The explain endpoints expose MongoDB execution statistics to help inspect index usage and collection scans.

## Error Handling

The API uses appropriate HTTP status codes for successful requests, validation failures, missing resources, and conflicting attendance operations.

## Notes

- Configure your own MongoDB connection string through environment variables.
- Keep `.env` and `.venv/` out of version control.
- Verify all assignment-specific business rules and concurrency requirements before submission.
=======
# employee-attendance-api
>>>>>>> fc53ef8a3c9039a52b3905e006dd44153819ab66
