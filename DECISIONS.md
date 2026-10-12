# Design Decisions — Employee Attendance & Analytics API

This document explains the main implementation choices and how they support the attendance API requirements.

## 1. Framework and Request Validation

**Decision:** FastAPI with Pydantic request models.

**Reason:** FastAPI provides REST endpoints, request validation, OpenAPI documentation, and Swagger UI. Pydantic helps reject invalid request data before business logic runs.

**Trade-off:** Type validation alone does not guarantee that an attendance operation satisfies all business rules. The application must also validate dates, shifts, statuses, and operation state.

## 2. Database and Driver

**Decision:** MongoDB with PyMongo.

**Reason:** Employee and attendance information can be stored as documents. MongoDB aggregation pipelines support database-side filtering, grouping, joins, date filling, and window calculations.

**Trade-off:** Pipeline design, indexes, and atomic update conditions must be considered together to achieve correctness and performance.

## 3. Attendance Uniqueness

**Decision:** A unique compound index on employee code and attendance date.

**Reason:** It enforces the one-attendance-document-per-employee-per-date rule at the database level, including when requests arrive concurrently.

**Implication:** Application-level existence checks alone are insufficient. Duplicate-key errors must be translated into an appropriate conflict response.

## 4. Indexing Strategy

**Decision:** Index employee identifiers, employee join dates, department and join-date filters, attendance employee/date lookups, and attendance date filtering.

**Reason:** These indexes support common lookup and reporting patterns.

**Implication:** The presence of an index does not guarantee that every query uses it. Explain plans should be inspected for all required endpoint query paths, including aggregation lookups.

## 5. Database-Side Analytics

**Decision:** Use MongoDB aggregation pipelines for reporting.

**Reason:** Database-side operations avoid loading an entire attendance dataset into Python for record-by-record calculations and allow MongoDB to apply its query engine and indexes.

**Implication:** The actual analytics pipelines should be benchmarked on representative datasets. A successful indexed lookup is not proof that all reporting pipelines meet a performance target.

## 6. Concurrent Punch-in

**Decision:** Combine the unique database constraint with duplicate-key conflict handling.

**Reason:** Several requests may attempt to create an attendance record for the same employee and date simultaneously.

**Expected behavior:** One request can create the record, while duplicate attempts are rejected with an appropriate conflict response.

## 7. Concurrent Punch-out

**Decision:** Use an atomic conditional update that matches only an attendance record whose punch-out is still unset.

**Reason:** Two simultaneous requests must not both successfully close the same open attendance record.

**Implication:** Check the update result. If the record is no longer open or no longer matches, handle the request as a conflict or according to the required endpoint contract.

## 8. Regularization and Audit History

**Decision:** Store regularization changes together with an audit-history append.

**Reason:** Attendance corrections must be traceable. Audit entries include the actor, timestamp, reason, and previous/new values for changed fields.

**Concurrency approach:** Conditional updates, atomic append operations, and conflict handling help preserve history when updates overlap.

**Implication:** Tests should check both the final attendance values and whether every concurrent update has its own audit entry.

## 9. Timezones and Overnight Shifts

**Decision:** Accept API instants as epoch milliseconds, convert them to datetime values for database processing, and apply the specified Asia/Kolkata attendance-date rules.

**Reason:** Absolute timestamps and local attendance dates serve different purposes. Explicit conversion helps avoid calendar-day errors.

**Overnight handling:** A punch-in before the overnight shift's end can belong to the previous shift date. The shift-end instant belongs to the following calendar date.

**Implication:** Boundary tests should cover midnight, exact shift end, late-arrival grace thresholds, and shifts that cross a date boundary.

## 10. Late Arrivals, Overtime, and Work Hours

**Decision:** Apply the assignment's explicit thresholds and rounding rules consistently.

- Late arrival applies only when the 10-minute grace threshold is exceeded.
- Late minutes use whole elapsed minutes.
- Overtime counts whole minutes after shift end and is recorded only when at least 30 minutes qualify.
- Work hours are rounded to two decimal places using half-up rounding.
- Half-day classification uses the rounded duration and the 4.50-hour threshold.

**Reason:** Explicit thresholds reduce inconsistent results between endpoints.

**Implication:** Boundary-value tests are necessary because threshold comparisons and rounding can change outcomes.

## 11. Status and Analytics Semantics

**Decision:** Use the assignment's defined attendance statuses consistently.

- `PRESENT`, `WFH`, and `ON_DUTY` count as presence for applicable analytics.
- `ABSENT` and `LEAVE` do not count as presence.
- Half-day records contribute `0.5` to the specified trend presence count.
- Monthly present-day calculations exclude weekends, while qualifying late-arrival and overtime totals can include weekends.
- Department headcount includes eligible employees even when they have no attendance logs.

**Reason:** Consistent definitions are required for meaningful analytics.

**Implication:** Test employees with no logs, weekend attendance, half-days, leave, and days with no attendance records.

## 12. Department Trend and Moving Average

**Decision:** Use MongoDB `$densify` to fill missing daily rows and `$setWindowFields` to calculate the 7-day moving average.

**Reason:** The response should contain continuous daily rows and calculate the moving average over the current row and up to six preceding rows.

**Rules:** The date range is inclusive and limited to 92 days. Attendance rates and moving averages are rounded to four decimal places. Rates are ratios, not percentages multiplied by 100. Non-working days and zero-headcount days have null rates, and the moving average ignores null rates.

**Implication:** Validate empty days, weekends, zero headcount, date-range boundaries, and moving-average windows containing null rates.

## 13. Pagination

**Decision:** Paginate attendance listing and limit the maximum page size.

**Reason:** Pagination keeps responses manageable and avoids returning unnecessarily large datasets.

**Implication:** Validate page numbering, page-size limits, filtering, ordering, and the response contract.

## 14. Error Handling

**Decision:** Distinguish invalid requests, missing resources, and conflicting operations with appropriate HTTP status codes.

**Reason:** API clients need to understand whether a request was invalid, a resource was absent, or another operation caused a conflict.

**Implication:** Expected failures should not become unhandled HTTP 500 responses. Database errors also require controlled handling.

## 15. Configuration and Secrets

**Decision:** Read MongoDB configuration from environment variables and exclude `.env` from version control.

**Reason:** Deployment configuration should be separate from application source code, and credentials must not be committed.

**Implication:** Never include real credentials in documentation, source files, logs, or test output.

## 16. Performance Testing

**Decision:** Use explain endpoints and a separate synthetic benchmark collection.

**Reason:** Explain plans help inspect index usage and the number of examined documents. A separate collection reduces the risk of modifying ordinary attendance records during benchmark setup.

**Recorded evidence:** A read-only query against a 100,000-record benchmark collection used `IXSCAN` and examined 31 documents and 31 index keys.

**Limitation:** The result applies to that query only. It does not establish that every required analytics pipeline meets a 100,000-record performance target.

## 17. Testing Strategy

The project includes automated validation tests, punch-in/punch-out concurrency tests, a regularization concurrency test, a synthetic performance-data script, and a read-only benchmark script.

Recorded runs verified selected validation scenarios, concurrent attendance operations, audit-history preservation, and one indexed benchmark query.

Full compliance should be claimed only after the assignment's business rules, status codes, response contracts, concurrency cases, and performance targets have been checked against the original specification.
