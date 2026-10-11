
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE_URL = "http://127.0.0.1:8000"
EMP_CODE = "CONCURRENCY_TEST_03"


def request(method, path, body=None):
    """Send an HTTP request and return its status and response."""
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
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
        return exc.code, body


def concurrent_requests(method, path, body, workers=10):
    """Launch requests together using a thread pool."""
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
    print("HTTP status counts:")
    for code in sorted(set(codes)):
        print(f"  {code}: {codes.count(code)}")

    success_count = codes.count(success_code)
    conflict_count = codes.count(409)

    passed = success_count == 1 and conflict_count == len(codes) - 1
    print("RESULT:", "PASS" if passed else "FAIL")
    if not passed:
        print("Response details:")
        for status, body in results:
            print(status, body)
    return passed


def main():
    # Use a dedicated test employee; don't modify EMP001 or EMP002.
    employee = {
        "emp_code": EMP_CODE,
        "name": "Concurrency Test Employee",
        "email": "concurrency.test@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": time.strftime("%Y-%m-%d", time.gmtime()),
    }

    print("Checking API health...")
    status, body = request("GET", "/health")
    print(status, body)
    if status != 200:
        print("API is not healthy. Start Uvicorn and retry.")
        return

    print("\nCreating dedicated test employee...")
    status, body = request("POST", "/employees", employee)
    print(status, body)

    if status == 409:
        print(f"Employee {EMP_CODE} already exists; reusing it.")
    elif status != 201:
        print("Could not create test employee. Stopping.")
        return

    # Punch in 2 minutes ago, in epoch milliseconds.
    # All 10 concurrent requests use the exact same timestamp.
    punch_in_ms = int(time.time() * 1000) - 120_000
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

    # Allow the winning punch-in to become visible before punch-out.
    time.sleep(0.5)

    # Ensure punch-out is after punch-in.
    punch_out_ms = int(time.time() * 1000)
    punch_out_body = {
        "emp_code": EMP_CODE,
        "punched_at": punch_out_ms,
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
    else:
        print(
            "At least one check failed. Inspect the status codes and "
            "response details above."
        )


if __name__ == "__main__":
    main()
