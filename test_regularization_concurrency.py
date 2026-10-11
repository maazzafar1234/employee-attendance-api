
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE_URL = "http://127.0.0.1:8000"
EMP_CODE = "CONCURRENCY_TEST"
ATTENDANCE_DATE = time.strftime("%Y-%m-%d", time.gmtime())


def request(method, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = raw
        return exc.code, result


def main():
    # Read the test employee's current attendance record.
    status, page = request(
        "GET",
        f"/attendance?emp_code={EMP_CODE}&date_from={ATTENDANCE_DATE}"
        f"&date_to={ATTENDANCE_DATE}&page=1&page_size=100",
    )

    if status != 200:
        print("Could not read attendance:", status, page)
        return

    items = page.get("items", [])
    if not items:
        print("No attendance record found for today's date.")
        print("Run test_concurrency.py successfully first.")
        return

    record = items[0]
    history_before = len(record.get("history", []))
    print("History entries before:", history_before)

    # Use two different reasons so we can verify that both entries survive.
    requests = [
       
{
    "status": "WFH",
    "reason": "Concurrency audit test A",
    "regularized_by": "Maaz",
},
{
    "status": "ON_DUTY",
    "reason": "Concurrency audit test B",
    "regularized_by": "Maaz",
},

    ]

    path = f"/attendance/{EMP_CODE}/{ATTENDANCE_DATE}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(request, "PATCH", path, body)
            for body in requests
        ]
        results = [future.result() for future in futures]

    print("\nConcurrent PATCH responses:")
    for status_code, body in results:
        print(status_code, body)

    # Read the record again and inspect the persisted history.
    status, page = request(
        "GET",
        f"/attendance?emp_code={EMP_CODE}&date_from={ATTENDANCE_DATE}"
        f"&date_to={ATTENDANCE_DATE}&page=1&page_size=100",
    )

    if status != 200 or not page.get("items"):
        print("Could not verify the final record:", status, page)
        return

    record_after = page["items"][0]
    history_after = record_after.get("history", [])
    print("\nHistory entries after:", len(history_after))

    reasons = {entry.get("reason") for entry in history_after}
    both_preserved = (
        "Concurrency audit test A" in reasons
        and "Concurrency audit test B" in reasons
    )

    if both_preserved:
        print("PASS: Both concurrent audit entries were preserved.")
    else:
        print("FAIL: One or both concurrent audit entries are missing.")
        print("Final history:", json.dumps(history_after, indent=2))


if __name__ == "__main__":
    main()
