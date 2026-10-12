import os
import time

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

uri = os.getenv("MONGO_URI")
db_name = os.getenv("MONGO_DB")

if not uri or not db_name:
    raise RuntimeError("MONGO_URI and MONGO_DB must be set in .env")

client = MongoClient(
    uri,
    tz_aware=True,
    serverSelectionTimeoutMS=10000,
)

try:
    client.admin.command("ping")
    collection = client[db_name]["attendance_performance_test"]

    total = collection.estimated_document_count()
    print("MongoDB connection: OK")
    print("Collection:", collection.name)
    print("Estimated records:", total)

    if total == 0:
        raise RuntimeError("The benchmark collection is empty.")

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

    def find_stages(value):
        stages = []

        if isinstance(value, dict):
            stage = value.get("stage")
            if stage:
                stages.append(stage)

            for child in value.values():
                stages.extend(find_stages(child))

        elif isinstance(value, list):
            for child in value:
                stages.extend(find_stages(child))

        return stages

    stages = find_stages(explain.get("queryPlanner", {}))

    print("\n--- Explain results ---")
    print("Stages:", sorted(set(stages)))
    print("Documents examined:", stats.get("totalDocsExamined"))
    print("Keys examined:", stats.get("totalKeysExamined"))
    print("Execution time (ms):", stats.get("executionTimeMillis"))

    start = time.perf_counter()
    matching = collection.count_documents(query)
    elapsed_ms = (time.perf_counter() - start) * 1000

    print("\n--- Measured query ---")
    print("Matching records:", matching)
    print(f"Elapsed time: {elapsed_ms:.2f} ms")

    if "COLLSCAN" in stages:
        print("WARNING: Explain plan contains COLLSCAN.")
    elif "IXSCAN" in stages:
        print("PASS: Explain plan contains IXSCAN.")
    else:
        print("NOTE: Inspect the plan stages above.")

finally:
    client.close()