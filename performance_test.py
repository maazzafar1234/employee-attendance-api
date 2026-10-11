
import os
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB")

if not MONGO_URI or not MONGO_DB:
    raise RuntimeError("MONGO_URI and MONGO_DB must be set in .env")

client = MongoClient(
    MONGO_URI,
    tz_aware=True,
    serverSelectionTimeoutMS=10000,
)
db = client[MONGO_DB]

# Dedicated collection; never touches attendance_logs.
collection = db["attendance_performance_test"]

TOTAL_RECORDS = 100_000
BATCH_SIZE = 2_000


def main():
    try:
        client.admin.command("ping")
        print("MongoDB connection: OK")

        # Stop rather than deleting any pre-existing test data.
        if collection.estimated_document_count() > 0:
            raise RuntimeError(
                "Test collection is not empty. Rename the collection "
                "or inspect it before running this script again."
            )

        start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        batch = []
        inserted = 0

        print(f"Inserting {TOTAL_RECORDS:,} synthetic records...")

        for i in range(TOTAL_RECORDS):
            emp_number = (i % 1_000) + 1
            day_number = (i // 1_000) % 365
            attendance_date = (
                start_date + timedelta(days=day_number)
            ).strftime("%Y-%m-%d")

            batch.append({
                "emp_code": f"PERF{emp_number:04d}",
                "date": attendance_date,
                "status": "PRESENT",
                "punch_in": start_date + timedelta(days=day_number),
                "punch_out": None,
                "work_hours": 8.0,
                "late_minutes": i % 20,
                "overtime_minutes": 0,
                "half_day": False,
            })

            if len(batch) >= BATCH_SIZE:
                collection.insert_many(batch, ordered=False)
                inserted += len(batch)
                batch.clear()
                print(f"Inserted {inserted:,} records")

        if batch:
            collection.insert_many(batch, ordered=False)
            inserted += len(batch)

        print(f"Inserted total: {inserted:,}")

        collection.create_index(
            [("emp_code", ASCENDING), ("date", ASCENDING)],
            name="perf_emp_date",
        )
        collection.create_index(
            [("date", ASCENDING), ("emp_code", ASCENDING)],
            name="perf_date_emp",
        )

        query = {
            "emp_code": "PERF0001",
            "date": {
                "$gte": "2026-01-01",
                "$lt": "2026-02-01",
            },
        }

        explain = collection.database.command(
            "explain",
            {
                "find": collection.name,
                "filter": query,
            },
            verbosity="executionStats",
        )

        stats = explain.get("executionStats", {})

        print("\n--- Query performance ---")
        print("Collection:", collection.name)
        print("Records:", collection.count_documents({}))
        print("Documents examined:", stats.get("totalDocsExamined"))
        print("Keys examined:", stats.get("totalKeysExamined"))
        print("Execution time (ms):", stats.get("executionTimeMillis"))

        start = time.perf_counter()
        result_count = collection.count_documents(query)
        elapsed_ms = (time.perf_counter() - start) * 1000

        print("Matching records:", result_count)
        print(f"Measured query time: {elapsed_ms:.2f} ms")
        print("\nTest data is isolated in attendance_performance_test.")
        print("Run the cleanup step only after reviewing the results.")

    finally:
        client.close()


if __name__ == "__main__":
    main()
