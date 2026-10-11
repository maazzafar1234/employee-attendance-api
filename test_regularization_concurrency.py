
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE_URL = "http://127.0.0.1:8000"
EMP_CODE = "CONCURRENCY_TEST"
ATTENDANCE_DATE = "2026-10-11"


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


def get_record():
    path = (
        f"/attendance?emp_code={EMP_CODE}"
        f"&date_from={ATTENDANCE_DATE}"
        f"&date_to={ATTENDANCE_DATE}&page=1&page_size=100"
    )
    code, page = request("GET", path)

    if code != 200 or not page.get("items"):
        raise RuntimeError(
            f"Could not find attendance record: HTTP {code}, {page}"
        )

    return page["items"][0]


def main():
    endpoint = f"/attendance/{EMP_CODE}/{ATTENDANCE_DATE}"

    try:
        record = get_record()
    except RuntimeError as exc:
        print("FAIL:", exc)
        return

    # Prepare a known status before measuring the test history.
    if record.get("status") != "PRESENT":
        code, body = request(
            "PATCH",
            endpoint,
            {
                "status": "PRESENT",
                "reason": "Prepare regularization concurrency test",
                "regularized_by": "Maaz",
            },
        )
        if code != 200:
            print("FAIL: Could not prepare record:", code, body)
            return

    record = get_record()
    initial_history = record.get("history", [])
    before_count = len(initial_history)
    original_punch_in = record.get("punch_in")

    if original_punch_in is None:
        print("FAIL: Attendance record has no punch-in timestamp.")
        return

    print("History entries before concurrent requests:", before_count)

    # These requests update different fields.
    payload_a = {
        "status": "WFH",
        "reason": "Concurrency audit test A",
        "regularized_by": "Maaz",
    }
    payload_b = {
        "punch_in": original_punch_in + 1000,
        "reason": "Concurrency audit test B",
        "regularized_by": "Maaz",
    }

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(request, "PATCH", endpoint, payload_a),
            pool.submit(request, "PATCH", endpoint, payload_b),
        ]
        results = [future.result() for future in futures]

    print("\nConcurrent PATCH response codes:")
    for code, body in results:
        print(code, body if code != 200 else {
            "emp_code": body.get("emp_code"),
            "status": body.get("status"),
            "punch_in": body.get("punch_in"),
        })

    try:
        final_record = get_record()
    except RuntimeError as exc:
        print("FAIL:", exc)
        return

    final_history = final_record.get("history", [])
    new_history = final_history[before_count:]
    new_reasons = [item.get("reason") for item in new_history]

    print("\nHistory entries after:", len(final_history))
    print("New audit reasons:", new_reasons)

    if len(final_history) < before_count:
        print("FAIL: Existing audit history was lost.")
        return

    expected_reasons = {
        "Concurrency audit test A",
        "Concurrency audit test B",
    }
    if expected_reasons.issubset(set(new_reasons)):
        print("PASS: Both concurrent updates have audit entries.")
    else:
        print("FAIL: Both audit entries were not preserved.")
        print("Check the response codes and API logs.")


if __name__ == "__main__":
    main()
