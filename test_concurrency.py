
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

BASE_URL = "http://127.0.0.1:8000"
EMP_CODE = "CONCURRENCY_TEST_04"
IST = timezone(timedelta(hours=5, minutes=30))


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
    except Exception as exc:
        return 0, {"error": str(exc)}


def concurrent_requests(method, path, body, workers=10):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(request, method, path, body)
            for _ in range(workers)
        ]
        return [future.result() for future in futures]


def show_results(title, results, success_code):
    codes = [status for status, _ in results]

    print(f"\n{title}")
    print("-" * len(title))
    for code in sorted(set(codes)):
        print(f"  HTTP {code}: {codes.count(code)}")

    passed = (
        len(results) == 10
        and codes.count(success_code) == 1
        and codes.count(409) == 9
    )
    print("RESULT:", "PASS" if passed else "FAIL")

    if not passed:
        for status, body in results:
            print(status, body)

    return passed


def choose_unused_date():
    """Check exact date filters using the real /attendance API."""
    today = datetime.now(IST).date()

    for offset in range(1, 366):
        candidate = (today + timedelta(days=offset)).isoformat()
        query = urllib.parse.urlencode({
            "emp_code": EMP_CODE,
            "date_from": candidate,
            "date_to": candidate,
            "page": 1,
            "page_size": 100,
        })

        status, body = request("GET", f"/attendance?{query}")

        if status != 200:
            raise RuntimeError(
                f"Attendance lookup failed for {candidate}: "
                f"HTTP {status}: {body}"
            )

        if not isinstance(body, dict) or not isinstance(
            body.get("items"), list
        ):
            raise RuntimeError(
                f"Unexpected /attendance response for {candidate}: {body}"
            )

        if body["items"]:
            continue

        if body.get("total", 0) != 0:
            raise RuntimeError(
                f"Could not verify that {candidate} is unused: {body}"
            )

        return candidate

    raise RuntimeError("No unused date found within the next 365 days.")


def main():
    employee = {
        "emp_code": EMP_CODE,
        "name": "Concurrency Test Employee",
        "email": "concurrency.test@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": datetime.now(IST).date().isoformat(),
    }

    print("Checking API health...")
    status, body = request("GET", "/health")
    print(status, body)

    if status != 200:
        print("API is not healthy. Start Uvicorn and retry.")
        return

    print("\nChecking test employee...")
    status, body = request("POST", "/employees", employee)
    print(status, body)

    if status == 409:
        print(f"Reusing existing test employee: {EMP_CODE}")
    elif status != 201:
        print("Could not create test employee. Stopping.")
        return

    try:
        test_date = choose_unused_date()
    except RuntimeError as exc:
        print(f"\nSAFE STOP: {exc}")
        print("No concurrency requests were sent.")
        return

    print("Selected unused attendance date:", test_date)

    punch_in_dt = datetime.combine(
        datetime.fromisoformat(test_date).date(),
        datetime.strptime("10:00", "%H:%M").time(),
        tzinfo=IST,
    )
    punch_in_ms = int(punch_in_dt.timestamp() * 1000)

    punch_in_body = {
        "emp_code": EMP_CODE,
        "punched_at": punch_in_ms,
        "status": "PRESENT",
    }

    print("\nSending 10 simultaneous punch-in requests...")
    punch_in_results = concurrent_requests(
        "POST", "/attendance/punch-in", punch_in_body
    )
    punch_in_pass = show_results(
        "PUNCH-IN CONCURRENCY", punch_in_results, 201
    )

    if not punch_in_pass:
        print("Stopping: punch-in concurrency did not pass.")
        return

    time.sleep(0.5)

    punch_out_body = {
        "emp_code": EMP_CODE,
        "punched_at": punch_in_ms + 8 * 60 * 60 * 1000,
    }

    print("\nSending 10 simultaneous punch-out requests...")
    punch_out_results = concurrent_requests(
        "POST", "/attendance/punch-out", punch_out_body
    )
    punch_out_pass = show_results(
        "PUNCH-OUT CONCURRENCY", punch_out_results, 200
    )

    print("\nFINAL RESULT")
    print("Punch-in:", "PASS" if punch_in_pass else "FAIL")
    print("Punch-out:", "PASS" if punch_out_pass else "FAIL")

    if punch_in_pass and punch_out_pass:
        print("Both concurrency checks passed.")


if __name__ == "__main__":
    main()
