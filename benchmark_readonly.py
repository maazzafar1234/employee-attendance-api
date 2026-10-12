import os
import time
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

uri = os.getenv("MONGO_URI")
db_name = os.getenv("MONGO_DB")

if not uri or not db_name:
    raise RuntimeError("MONGO_URI and MONGO_DB must be set in .env")

client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=10000)

try:
    client.admin.command("ping")
    collection = client[db_name]["attendance_performance_test"]

    print("MongoDB connection: OK")
    print("Collection:", collection.name)
    print("Record count:", collection.count_documents({}))
    print("Indexes:", list(collection.index_information().keys()))

    query = {
        "emp_code": "PERF0001",
        "date": {"$gte": "2026-01-01", "$lt": "2026-02-01"},
    }

    explain = collection.database.command(
        "explain",
        {"find": collection.name, "filter": query},
        verbosity="executionStats",
    )

    stats = explain.get("executionStats", {})
    print("\n--- Execution statistics ---")
    print("Execution successful:", explain.get("ok") == 1)
    print("Documents examined:", stats.get("totalDocsExamined"))
    print("Index keys examined:", stats.get("totalKeysExamined"))
    print("Documents returned:", stats.get("nReturned"))
    print("MongoDB execution time (ms):", stats.get("executionTimeMillis"))

    start = time.perf_counter()
    matched = collection.count_documents(query)
    elapsed = (time.perf_counter() - start) * 1000

    print("Matching records:", matched)
    print(f"Measured query time (ms): {elapsed:.2f}")

    def find_stages(value):
        if isinstance(value, dict):
            if value.get("stage") in {"IXSCAN", "COLLSCAN"}:
                print("Query-plan stage:", value["stage"])
            for child in value.values():
                find_stages(child)
        elif isinstance(value, list):
            for child in value:
                find_stages(child)

    print("\n--- Query-plan stages ---")
    find_stages(explain.get("queryPlanner", {}).get("winningPlan", {}))

finally:
    client.close()
