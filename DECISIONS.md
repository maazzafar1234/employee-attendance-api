# Design Decisions — Employee Attendance & Analytics API

## 1. Framework
FastAPI was selected to implement REST endpoints with request validation and interactive API documentation.

## 2. Database
MongoDB is used to store employee records and attendance logs. PyMongo provides database access.

## 3. Unique Attendance Records
A compound unique index on employee code and attendance date helps prevent duplicate attendance records for the same employee and date.

## 4. Indexing
Indexes are used for employee identification, attendance date filtering, and analytics queries. Explain endpoints expose MongoDB execution statistics for inspection.

## 5. Analytics Processing
Analytics calculations are implemented through MongoDB aggregation pipelines to keep filtering and aggregation in the database rather than loading all records into Python.

## 6. Attendance Audit History
Attendance regularization stores the reason, actor, timestamp, and changed values so updates can be traced.

## 7. Pagination
Attendance listing supports pagination to limit the number of records returned in a single request.

## 8. Time Handling
API timestamps representing instants are handled as epoch milliseconds, while attendance dates and shift times follow the assignment's specified date and timezone rules.

## 9. Configuration and Secrets
Environment variables are used for configuration. The `.env` file is excluded from version control to avoid committing database credentials.

## 10. Limitations
Concurrency behavior, performance at scale, and all assignment edge cases require dedicated tests before they can be considered fully verified.